import os
import json
import time
import argparse
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Cuanto se espera COMO MAXIMO una respuesta de la API, en segundos.
#
# No es una optimizacion: sin `timeout`, `requests` espera PARA SIEMPRE si el
# servidor acepta la conexion y despues no contesta. El proceso no falla ni
# reintenta -- se queda colgado, el orquestador se cuelga con el, y el
# Programador de tareas de Windows saltea en silencio todas las corridas
# siguientes porque para el la tarea "todavia esta ejecutandose".
#
# Con timeout, una llamada trabada tira una excepcion, el paso falla, se
# reintenta, y si igual no anda queda marcado como FALLA en `--listar`. Un paso
# que falla a la vista se arregla; uno que se cuelga en silencio se descubre
# horas despues.
TIMEOUT_HTTP = 60

load_dotenv()

ARCHIVO_TOKENS = "ml_tokens.json"
USER_ID = os.getenv("ML_USER_ID")
# Segundos de espera entre llamada y llamada a la API de Mercado Libre.
#
# Estaba en 1.0, y eso no era una precaucion sino un costo escondido: el paso
# `--catalogo` hace ~4.300 llamadas (una por cada inventory_id para el stock
# Full), asi que UNA HORA Y CUARTO de ese paso era el script durmiendo. Medido
# el 19/08/2026, la primera vez que ese paso llego a correr.
#
# Bajarlo es seguro porque el limite de Mercado Libre esta MUY por encima de
# esto, y sobre todo porque `llamar_ml` ya maneja el 429: si alguna vez se pasa,
# espera lo que le pidan y reintenta. O sea que el freno real no es esta pausa,
# es la respuesta de la API -- esta pausa solo evita ir a golpear la puerta al
# pedo.
#
# Si algun dia aparecen 429 seguidos en el log, subirlo es el primer ajuste.
PAUSA = 0.3

# Fecha de corte ABSOLUTA (piso historico, nunca se pide nada anterior a esto)
FECHA_CORTE = date(2026, 5, 6)

# Ventana movil: cada corrida solo re-pide (y reemplaza) los ultimos N dias.
# El resto del historial en bronze.ml_ventas queda intacto.
WINDOW_DAYS = 7


def cargar_tokens():
    with open(ARCHIVO_TOKENS) as f:
        return json.load(f)


def guardar_tokens(tokens):
    with open(ARCHIVO_TOKENS, "w") as f:
        json.dump(tokens, f, indent=2)


def renovar_access_token():
    """Usa el refresh_token para obtener un access_token nuevo.
       OJO: ML devuelve un refresh_token NUEVO cada vez, hay que guardarlo."""
    tokens = cargar_tokens()
    r = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "client_id": os.getenv("ML_CLIENT_ID"),
            "client_secret": os.getenv("ML_CLIENT_SECRET"),
            "refresh_token": tokens["refresh_token"],
        },
        timeout=TIMEOUT_HTTP,
    )
    r.raise_for_status()
    nuevos = r.json()
    guardar_tokens(nuevos)        # guardamos el refresh_token nuevo
    print("  Token renovado OK")
    return nuevos["access_token"]


def llamar_ml(endpoint, access_token, params=None, max_reintentos=6):
    """Llama a la API de ML con el access_token. Reintenta automaticamente en 429."""
    url = "https://api.mercadolibre.com" + endpoint
    headers = {"Authorization": f"Bearer {access_token}"}

    intento = 0
    while True:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT_HTTP)

        if r.status_code == 401:      # token vencido: renovar y reintentar
            print("  401: renovando token...")
            access_token = renovar_access_token()
            headers = {"Authorization": f"Bearer {access_token}"}
            r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT_HTTP)

        if r.status_code == 429:
            intento += 1
            if intento > max_reintentos:
                r.raise_for_status()  # se rindio, que explote como antes
            espera = int(r.headers.get("Retry-After", 0)) or (5 * intento)
            print(f"  429: esperando {espera}s antes de reintentar (intento {intento}/{max_reintentos})...")
            time.sleep(espera)
            continue

        r.raise_for_status()
        time.sleep(PAUSA)
        return r.json()


def _crear_engine():
    return create_engine(
        f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )


def _listas_a_texto(df):
    import json as _json
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
            df[col] = df[col].apply(
                lambda x: _json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x
            )
    return df


def guardar_en_bd(df, tabla, modo="replace"):
    """Para CATALOGOS (publicaciones, stock full): reemplaza la tabla entera.
       Tiene sentido acá porque representan el estado ACTUAL, no un historial que crece."""
    if df.empty:
        print(f"  (sin datos para {tabla})")
        return
    df = _listas_a_texto(df)
    engine = _crear_engine()
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")
    if modo == "replace":
        # REEMPLAZO ATOMICO (borrar + insertar juntos) en vez de DROP + CREATE.
        #
        # `to_sql(if_exists="replace")` borra la tabla y la vuelve a crear, y eso
        # FALLA si alguien creo una vista encima:
        #
        #   cannot drop table bronze.X because other objects depend on it
        #
        # Es lo que dejo a tiendanube.py sin traer nada desde el 12/06: se creo
        # la vista tn_control_cancelaciones y el script murio en cada corrida.
        # Vaciar la tabla en vez de borrarla deja la vista en pie.
        try:
            # BORRAR E INSERTAR EN UNA SOLA TRANSACCION.
            #
            # Antes eran dos: primero se confirmaba el TRUNCATE y despues, por
            # separado, se insertaba. En el medio la tabla quedaba VACIA para
            # cualquiera que la consultara -- y el tablero lee estas tablas en
            # vivo. Se vio pasando: en mitad de una corrida bronze.ml_ventas
            # tenia 40.588 ordenes cuando un minuto antes tenia 43.207.
            #
            # Con las dos cosas en la misma transaccion, quien consulta sigue
            # viendo la version ANTERIOR completa hasta que la nueva esta
            # entera. Nunca ve un agujero.
            #
            # DELETE y no TRUNCATE: los dos son transaccionales, pero TRUNCATE
            # toma un lock exclusivo que ahora duraria toda la insercion y
            # dejaria al tablero esperando. DELETE usa el control de versiones
            # de Postgres, asi que los lectores no se bloquean nunca. Con estas
            # tablas (miles de filas, no millones) la diferencia de velocidad no
            # se nota.
            with engine.begin() as con:
                con.exec_driver_sql(f'DELETE FROM bronze."{tabla}";')
                df.to_sql(tabla, con, schema="bronze", if_exists="append", index=False)
            print(f"  Guardado (reemplazo atomico): bronze.{tabla} ({len(df)} filas)")
            return
        except Exception as e:
            # La tabla todavia no existe: que la cree el to_sql de abajo.
            print(f"  (no se pudo truncate: {str(e)[:80]}... -> creando tabla)")

    df.to_sql(tabla, engine, schema="bronze", if_exists=modo, index=False)
    print(f"  Guardado ({modo}): bronze.{tabla} ({len(df)} filas)")


def guardar_ventana_en_bd(df, tabla, col_fecha, cutoff):
    """Para VENTAS (crecen con el tiempo): reemplaza SOLO las filas dentro de la
       ventana movil (col_fecha >= cutoff). Todo lo anterior a cutoff queda intacto
       -- no se toca ni se vuelve a pedir a la API de ML."""
    engine = _crear_engine()
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")

    try:
        with engine.begin() as con:
            resultado = con.exec_driver_sql(
                f'DELETE FROM bronze."{tabla}" WHERE "{col_fecha}"::date >= %(cutoff)s',
                {"cutoff": cutoff}
            )
            print(f"  Filas borradas dentro de la ventana (se van a reemplazar): {resultado.rowcount}")
    except Exception:
        print(f"  (tabla bronze.{tabla} no existe todavia, se va a crear)")

    if df.empty:
        print(f"  (sin ventas nuevas de ML en esta ventana)")
        return

    df = _listas_a_texto(df)
    df.to_sql(tabla, engine, schema="bronze", if_exists="append", index=False)
    print(f"  Guardado (ventana): bronze.{tabla} ({len(df)} filas)")


# ============================================================
#  EXTRACCIONES
# ============================================================

def extraer_ventas_ml():
    """Ordenes de ML: SOLO la ventana movil de los ultimos WINDOW_DAYS dias, en una
       sola pasada (con 7 dias no hace falta partir en quincenas -- no se acerca
       al limite de offset 10.000 de la API)."""
    print("\n=== VENTAS MERCADO LIBRE (ventana movil) ===")

    access_token = renovar_access_token()

    hoy = date.today()
    cutoff = max(FECHA_CORTE, hoy - timedelta(days=WINDOW_DAYS))
    desde = f"{cutoff.isoformat()}T00:00:00.000-00:00"
    hasta = f"{hoy.isoformat()}T23:59:59.000-00:00"
    print(f"  Ventana: {cutoff.isoformat()} a {hoy.isoformat()}")

    offset = 0
    limit = 50
    ordenes = []
    while True:
        datos = llamar_ml(
            "/orders/search",
            access_token,
            params={
                "seller": USER_ID,
                "order.date_created.from": desde,
                "order.date_created.to": hasta,
                "offset": offset,
                "limit": limit,
            },
        )
        resultados = datos.get("results", [])
        if not resultados:
            break
        ordenes.extend(resultados)
        total = datos.get("paging", {}).get("total", 0)
        offset += limit
        if offset >= total or offset >= 10000:
            break

    print(f"  {len(ordenes)} ordenes en la ventana")
    if len(ordenes) >= 9999:
        print("  ATENCION: cerca del limite de offset 10.000. Si esto pasa seguido,")
        print("  achicar WINDOW_DAYS o volver a partir en tramos.")

    df = pd.json_normalize(ordenes)
    guardar_ventana_en_bd(df, "ml_ventas", "date_created", cutoff)


def obtener_ids_publicaciones(access_token):
    """Trae TODOS los IDs de publicaciones usando scan (sin limite de offset)."""
    print("  Obteniendo IDs de publicaciones (scan)...")
    ids = []
    scroll_id = None
    while True:
        params = {"search_type": "scan", "limit": 100}
        if scroll_id:
            params["scroll_id"] = scroll_id
        datos = llamar_ml(f"/users/{USER_ID}/items/search", access_token, params=params)
        resultados = datos.get("results", [])
        if not resultados:
            break
        ids.extend(resultados)
        scroll_id = datos.get("scroll_id")
        print(f"    IDs acumulados: {len(ids)}")
        if not scroll_id:
            break
    return ids


def extraer_publicaciones_ml():
    """Trae el detalle de TODAS las publicaciones de ML (activas, pausadas, cerradas).
       Catalogo -> se reemplaza entero cada vez que corre."""
    print("\n=== PUBLICACIONES MERCADO LIBRE ===")
    access_token = renovar_access_token()

    ids = obtener_ids_publicaciones(access_token)
    print(f"  Total de publicaciones: {len(ids)}")

    detalles = []
    for i in range(0, len(ids), 20):
        lote = ids[i:i + 20]
        datos = llamar_ml("/items", access_token, params={"ids": ",".join(lote)})
        for item in datos:
            if item.get("code") == 200:
                detalles.append(item["body"])
        if i % 200 == 0:
            print(f"    Detalles traidos: {len(detalles)} de {len(ids)}")

    df = pd.json_normalize(detalles)
    print(f"  Total con detalle: {len(df)} publicaciones")
    guardar_en_bd(df, "ml_publicaciones", modo="replace")


def extraer_stock_full():
    """Trae el stock real en Full (fulfillment) por cada inventory_id.
       Catalogo (estado actual) -> se reemplaza entero cada vez que corre."""
    print("\n=== STOCK FULL (fulfillment) ===")
    access_token = renovar_access_token()

    engine = _crear_engine()
    query = """
        SELECT DISTINCT inventory_id
        FROM bronze.ml_publicaciones
        WHERE "shipping.logistic_type" = 'fulfillment'
          AND inventory_id IS NOT NULL
    """
    df_inv = pd.read_sql(query, engine)
    inventory_ids = df_inv["inventory_id"].tolist()
    print(f"  {len(inventory_ids)} inventory_id unicos a consultar")

    filas = []
    for i, inv_id in enumerate(inventory_ids):
        try:
            datos = llamar_ml(f"/inventories/{inv_id}/stock/fulfillment", access_token)
            datos["inventory_id"] = inv_id
            filas.append(datos)
        except Exception as e:
            filas.append({"inventory_id": inv_id, "error": str(e)})
        if i % 200 == 0:
            print(f"    Consultados: {i} de {len(inventory_ids)}")

    df = pd.json_normalize(filas)
    print(f"  Total: {len(df)} registros de stock full")
    guardar_en_bd(df, "ml_stock_full", modo="replace")


# ============================================================
#  EJECUCION
# ============================================================

# Las tres extracciones no cuestan lo mismo ni envejecen igual:
#
#   ventas     -> ventana movil de 7 dias, unos pocos cientos de llamadas.
#                 Es barato y es lo que mas rapido queda viejo.
#   publicaciones -> el catalogo entero.
#   stock full -> UNA llamada por inventory_id (~3.800). Es el paso lento,
#                 y por eso era el que hacia que "correr Mercado Libre"
#                 pareciera algo que no se puede hacer seguido.
#
# Separarlas deja que el orquestador corra las ventas todo el tiempo y el
# catalogo cada tanto, en vez de todo o nada.

def main():
    parser = argparse.ArgumentParser(
        description="Extraccion de Mercado Libre. Sin argumentos corre todo."
    )
    parser.add_argument("--ventas", action="store_true",
                        help="Solo las ventas (ventana movil, rapido)")
    parser.add_argument("--catalogo", action="store_true",
                        help="Solo publicaciones y stock full (lento)")
    args = parser.parse_args()

    # Sin flags = todo, para no romper a quien ya lo corre a mano asi.
    todo = not (args.ventas or args.catalogo)

    print("ML User ID:", USER_ID)
    if todo or args.ventas:
        extraer_ventas_ml()
    if todo or args.catalogo:
        extraer_publicaciones_ml()
        extraer_stock_full()
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()