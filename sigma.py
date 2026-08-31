import argparse
import os
import time
import json
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine
from conexion import crear_engine
from contextlib import nullcontext
from sqlalchemy.engine import Connection

# Cuanto se espera una respuesta de la API: (CONECTAR, LEER), en segundos.
#
# No es una optimizacion: sin `timeout`, `requests` espera PARA SIEMPRE si el
# servidor acepta la conexion y despues no contesta. El proceso no falla ni
# reintenta -- se queda colgado, el orquestador se cuelga con el, y el
# Programador de tareas de Windows saltea en silencio todas las corridas
# siguientes porque para el la tarea "todavia esta ejecutandose".
#
# SON DOS NUMEROS Y NO UNO, y la diferencia se nota cuando el servidor del otro
# lado esta caido. Con un solo valor, `timeout=120` es tambien el de conexion:
# tres intentos contra un host que no contesta se van SEIS MINUTOS antes de
# rendirse. Separados, el intento muere en 10 segundos si no hay con quien
# hablar, y sigue teniendo su tiempo largo para una consulta pesada que si
# arranco.
#
# El de conexion es corto a proposito: un servidor sano acepta la conexion en
# milisegundos. Si tarda diez segundos, no es que este pensando -- no esta.
TIMEOUT_HTTP = (10, 120)

load_dotenv()

# --- URL base de Sigma ---
URL_BASE = (
    f"https://{os.getenv('SIGMA_URL_CLIENTE')}"
    f"/{os.getenv('SIGMA_BASEALIAS')}"
    f"/{os.getenv('SIGMA_ID_CLIENTE')}"
    f"/sigma/api/v10/"
)
TOKEN = os.getenv("SIGMA_TOKEN", "")
HEADERS = {"X-Auth-Token": TOKEN}

# Fecha de corte ABSOLUTA (piso historico, nunca se pide nada anterior a esto)
FECHA_INICIO_VENTAS = date(2026, 5, 6)

# Desde cuando hay facturas de compra cargadas. Es el piso de la ventana de
# compras: si la tabla esta vacia, se pide desde aca y no desde el principio de
# los tiempos.
FECHA_INICIO_COMPRAS = date(2026, 1, 1)

# Ventana movil: cada corrida solo re-pide (y reemplaza) los ultimos N dias.
# El resto del historial en bronze.sigma_ventas queda intacto.
WINDOW_DAYS = 7

# Pausa base entre llamadas (segundos). Subila si te bloquean seguido.
PAUSA = 1.0


def llamar_sigma(endpoint, params=None):
    """Llama a un endpoint respetando limites (429 y 403 por saturacion)."""
    url = URL_BASE + endpoint
    intentos_403 = 0
    while True:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT_HTTP)

        if r.status_code == 429:
            espera_ms = int(r.headers.get("X-Retry-After-ms", 1000))
            print(f"  429: esperando {espera_ms} ms...")
            time.sleep(espera_ms / 1000)
            continue

        if r.status_code == 403:
            intentos_403 += 1
            if intentos_403 > 5:
                print("  403 persistente. El servidor sigue bloqueando.")
                r.raise_for_status()
            espera = 60 * intentos_403   # 60s, 120s, 180s...
            print(f"  403 (saturacion?). Esperando {espera}s y reintentando ({intentos_403}/5)...")
            time.sleep(espera)
            continue

        r.raise_for_status()
        time.sleep(PAUSA)                # pausa corta entre llamadas exitosas
        return r.json()


def _crear_engine():
    return crear_engine(
        connect_args={
            "client_encoding": "utf8",
            "options": "-c client_encoding=UTF8"
        }
    )


def _listas_a_texto(df):
    """Convierte a texto cualquier columna que contenga listas o diccionarios (para poder guardarla)."""
    import json as _json
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
            df[col] = df[col].apply(
                lambda x: _json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x
            )
    return df


def _sincronizar_columnas(destino, tabla, df):
    """Agrega a la tabla las columnas que el origen empezo a mandar y no estan.

    POR QUE EXISTE. El 31/08/2026 SIGMA sumo el campo `operacionId` a
    ExportArticulosVendidos. `to_sql(if_exists="append")` no lo tolera: falla
    con "Unconsumed column names" y se cae la extraccion ENTERA. Las ventas
    mayoristas dejaron de actualizarse por una columna nueva que ni siquiera
    usamos.

    BRONZE ES LA ZONA DE ATERRIZAJE: su trabajo es aguantar lo que el origen
    mande, no discutirlo. Un campo nuevo tiene que entrar solo; el tipado fino y
    las reglas de negocio son tarea de gold.

    SE AGREGA Y NO SE DESCARTA. Tirar las columnas desconocidas tambien evitaria
    el error, pero en silencio: el dia que el origen mande algo que SI importa,
    nadie se enteraria hasta necesitarlo. Asi queda guardado y avisado en el log.

    Todo como `text`, que acepta cualquier cosa que venga. Convertirlo despues es
    barato; perder el dato no.

    `destino` PUEDE SER UN ENGINE O UNA CONEXION YA ABIERTA. Cuando el llamador
    esta dentro de una transaccion hay que pasarle ESA conexion: abrir otra por
    dentro pediria un ACCESS EXCLUSIVE sobre una tabla que la de afuera ya tiene
    tomada, y las dos se quedarian esperando para siempre.
    """
    ctx = nullcontext(destino) if isinstance(destino, Connection) else destino.begin()
    faltantes = []
    with ctx as con:
        existentes = {
            f[0] for f in con.exec_driver_sql(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'bronze' AND table_name = %(t)s""",
                {"t": tabla},
            ).fetchall()
        }
        if not existentes:
            return          # la tabla no existe todavia: la crea el to_sql
        for col in df.columns:
            if col not in existentes:
                faltantes.append(col)
                con.exec_driver_sql(
                    f'ALTER TABLE bronze."{tabla}" ADD COLUMN IF NOT EXISTS "{col}" text'
                )
    if faltantes:
        print(f"  COLUMNAS NUEVAS en bronze.{tabla}: {', '.join(faltantes)}")
        print("  (agregadas como text: el origen cambio y quedo registrado)")


def guardar_en_bd(df, tabla, modo="replace"):
    """Para CATALOGOS (articulos, clientes, ofertas, etc): reemplaza la tabla entera.
       Tiene sentido acá porque representan el estado ACTUAL, no un historial que crece."""
    if df.empty:
        print(f"  (sin datos nuevos para {tabla})")
        return
    df = _listas_a_texto(df)
    engine = _crear_engine()
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")

    if modo == "replace":
        # REEMPLAZO ATOMICO: vacia la tabla sin borrarla -> no rompe vistas que dependan de ella
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
                _sincronizar_columnas(con, tabla, df)
                df.to_sql(tabla, con, schema="bronze", if_exists="append", index=False)
            print(f"  Guardado (reemplazo atomico): bronze.{tabla} ({len(df)} filas)")
            return
        except Exception as e:
            print(f"  (no se pudo truncate: {str(e)[:80]}... -> creando tabla)")

    _sincronizar_columnas(engine, tabla, df)
    df.to_sql(tabla, engine, schema="bronze", if_exists=modo, index=False)
    print(f"  Guardado ({modo}): bronze.{tabla} ({len(df)} filas)")


def guardar_ventana_en_bd(df, tabla, col_fecha, cutoff):
    """Para datos TRANSACCIONALES que crecen con el tiempo (ventas): reemplaza SOLO
       las filas dentro de la ventana movil (fecha >= cutoff). Todo lo anterior a
       cutoff en la tabla queda intacto -- no se toca ni se vuelve a pedir a la API."""
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
        print(f"  (sin datos nuevos para {tabla} en esta ventana)")
        return

    df = _listas_a_texto(df)
    _sincronizar_columnas(engine, tabla, df)
    df.to_sql(tabla, engine, schema="bronze", if_exists="append", index=False)
    print(f"  Guardado (ventana): bronze.{tabla} ({len(df)} filas)")


# ============================================================
#  EXTRACCIONES
# ============================================================

def extraer_articulos():
    """Catalogo completo. Se reemplaza entero (corre 1 vez al dia)."""
    print("\n=== ARTICULOS (catalogo con costos) ===")
    datos = llamar_sigma("ExportArticulos")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} articulos")
    guardar_en_bd(df, "sigma_articulos", modo="replace")


def extraer_clientes():
    """Catalogo de clientes (datos completos)."""
    print("\n=== CLIENTES ===")
    datos = llamar_sigma("ExportClientes")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} clientes")
    guardar_en_bd(df, "sigma_clientes", modo="replace")


def extraer_cuentas_corrientes():
    """Saldos de deuda por cliente (cuenta corriente)."""
    print("\n=== CUENTAS CORRIENTES (deudas) ===")
    datos = llamar_sigma("ExportClientesCtaCte")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} saldos de cliente")
    guardar_en_bd(df, "sigma_cuentas_corrientes", modo="replace")


def extraer_ofertas():
    """Politicas de descuento vigentes: % de oferta y su periodo de vigencia."""
    print("\n=== OFERTAS / POLITICAS DE DESCUENTO ===")
    datos = llamar_sigma("ExportPoliticasDescuento")
    df = pd.json_normalize(datos)
    print(f"  Recibidas {len(df)} politicas de descuento")
    if "vigenciaHasta" in df.columns:
        hoy = date.today().isoformat()
        vigentes = df[df["vigenciaHasta"] >= hoy] if len(df) else df
        print(f"  De esas, {len(vigentes)} con vigencia que llega a hoy o despues")
    guardar_en_bd(df, "sigma_ofertas", modo="replace")


def extraer_ventas():
    """Ventas: SOLO la ventana movil de los ultimos WINDOW_DAYS dias, en una sola
       llamada (no mas tramos mensuales -- con una ventana de 7 dias no hace falta
       partir en meses, el endpoint no se acerca al tope de registros)."""
    print("\n=== VENTAS (ventana movil) ===")

    hoy = date.today()
    cutoff = max(FECHA_INICIO_VENTAS, hoy - timedelta(days=WINDOW_DAYS))
    dde = cutoff.isoformat()
    hta = hoy.isoformat()
    print(f"  Ventana: {dde} a {hta}")

    datos = llamar_sigma("ExportArticulosVendidos", {"dde": dde, "hta": hta})
    cant = len(datos)
    print(f"  {cant} lineas de venta en la ventana")

    if cant >= 28000:
        print("  ATENCION: cerca del tope de registros del endpoint (28000).")
        print("  Si esto pasa seguido, achicar WINDOW_DAYS o volver a partir por quincenas.")

    df = pd.json_normalize(datos)
    guardar_ventana_en_bd(df, "sigma_ventas", "fecha", cutoff)


def _ultima_compra_cargada():
    """La fecha de la ultima factura de compra que ya esta en bronze.

    Devuelve None si la tabla no existe o esta vacia."""
    try:
        engine = _crear_engine()
        with engine.begin() as con:
            fila = con.exec_driver_sql(
                'SELECT max("fechaFactura")::date FROM bronze.sigma_compras'
            ).scalar()
        return fila
    except Exception as e:
        print(f"  (no se pudo leer la ultima compra cargada: {e})")
        return None


def extraer_compras():
    """Facturas de compra (a proveedores), por ventana movil que se auto-repara.

    LA VENTANA NO ES FIJA, y ese es el punto. Con `hoy - 7` a secas, un dia sin
    corrida deja un agujero que no se llena NUNCA: la corrida siguiente pide los
    ultimos 7 dias y lo anterior ya no se vuelve a pedir. Paso: el paso estuvo
    apagado desde el 11/06/2026 y quedaron dos meses y medio sin comprobantes.

    Asi que la ventana arranca en la mas VIEJA de dos fechas: los ultimos 7 dias,
    o la ultima factura que hay cargada. En una corrida normal las dos dan casi
    lo mismo y se piden 7 dias; despues de un corte, se pide todo lo que falto y
    el agujero se cierra solo en la primera corrida.

    El piso es FECHA_INICIO_COMPRAS para que una tabla vacia no dispare un pedido
    sin limite."""
    print("\n=== COMPRAS (facturas de compra) ===")

    hoy = date.today()
    ventana = hoy - timedelta(days=WINDOW_DAYS)
    ultima = _ultima_compra_cargada()
    if ultima:
        print(f"  Ultima compra cargada: {ultima}")
    cutoff = max(FECHA_INICIO_COMPRAS, min(ventana, ultima or ventana))

    dde = cutoff.isoformat()
    hta = hoy.isoformat()
    dias = (hoy - cutoff).days
    print(f"  Ventana: {dde} a {hta} ({dias} dias)")

    datos = llamar_sigma("ExportFacturasCompra", {"dde": dde, "hta": hta})
    print(f"  {len(datos)} facturas de compra en la ventana")

    # SIN RENGLONES NO SE SABE QUE SE COMPRO. El tablero de Stock usa `items`
    # para saber la ultima compra de cada SKU, asi que conviene ver de una si el
    # endpoint los esta trayendo: una caida silenciosa de ese campo dejaria la
    # columna vacia sin que nada falle.
    if datos:
        con_items = sum(1 for d in datos if d.get("items"))
        print(f"  Con detalle de renglones: {con_items} de {len(datos)}")

    df = pd.json_normalize(datos)
    guardar_ventana_en_bd(df, "sigma_compras", "fechaFactura", cutoff)


# ============================================================
#  EJECUCION
# ============================================================

# Las tres extracciones activas no envejecen igual ni cuestan lo mismo:
#
#   ventas   -> ventana movil de 7 dias. Es el corazon del tablero y hay que
#               pedirlo seguido.
#   catalogo -> los ~8.200 articulos, reescritos enteros. Un articulo nuevo o un
#               cambio de descripcion no pasa cada dos horas.
#   compras  -> ventana movil que se auto-repara. Alimenta la columna "ultima
#               compra" del tablero de Stock; una compra del dia no la espera
#               nadie, asi que va una vez por dia.
#
# Estaban juntos, asi que el catalogo se recargaba entero en cada corrida:
# 335.816 inserciones acumuladas para tener 8.194 filas vivas, o sea unas 41
# recargas del mismo catalogo. Separarlas deja pedir las ventas seguido y el
# catalogo una vez por dia.

def main():
    parser = argparse.ArgumentParser(
        description="Extraccion de SIGMA. Sin argumentos corre todo."
    )
    parser.add_argument("--ventas", action="store_true",
                        help="Solo las ventas (ventana movil)")
    parser.add_argument("--catalogo", action="store_true",
                        help="Solo el catalogo de articulos")
    parser.add_argument("--compras", action="store_true",
                        help="Solo las facturas de compra (ventana movil)")
    args = parser.parse_args()

    # Sin flags = todo, para no romper a quien ya lo corre a mano asi.
    todo = not (args.ventas or args.catalogo or args.compras)

    print("URL base:", URL_BASE)
    print("Token cargado:", "SI" if TOKEN else "NO")

    if todo or args.ventas:
        extraer_ventas()
    if todo or args.catalogo:
        extraer_articulos()
    if todo or args.compras:
        extraer_compras()

    # Estos siguen apagados; se activan cuando haga falta, no cada corrida.
    #extraer_clientes()
    #extraer_cuentas_corrientes()
    #extraer_ofertas()
    # extraer_stock()  -> el stock ahora viene de DIGIP, no de Sigma

    print("\n=== LISTO. Revisa las tablas en Supabase (esquema bronze). ===")


if __name__ == "__main__":
    main()