import datetime
import itertools
import os
import json
import estado
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import argparse
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from errores_bd import es_tabla_inexistente
from sqlalchemy import text
from conexion import crear_engine


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
TIMEOUT_HTTP = (10, 60)

# Cuantas consultas de stock Full van a la vez.
#
# La API no deja pedir varios inventarios juntos, asi que son ~3.800 llamadas de
# a una. En fila tardan casi 30 minutos; de a 12 tardan poco mas de uno.
#
# Arranco en 12 y se subio a 24 despues de medirlo: con 12 el paso tardo 13,8
# minutos y NINGUN inventario fallo (0 filas con `error` sobre 3.830). Cero
# errores significa que el limite de la API no se estaba tocando -- lo que
# frenaba era la latencia de la red, no Mercado Libre.
#
# Con 24 deberia quedar cerca de 7 minutos. Si aparecen 429 seguidos en el log,
# o si la corrida empieza a dejar filas con `error`, bajarlo a 12 es el primer
# ajuste y ahi ya sabemos que ese es el techo real.
#
# COMO DARSE CUENTA de que hay que bajarlo, sin leer el log entero:
#     select count(*) filter (where error is not null) from bronze.ml_stock_full;
# Tiene que dar 0. Si da otra cosa, la API empezo a rechazar.
HILOS_STOCK = 24

# `renovar_access_token` ESCRIBE el archivo de tokens, y Mercado Libre entrega
# un refresh_token nuevo cada vez. Con varios hilos, dos renovaciones a la vez
# pisarian ese archivo y podrian dejar guardado un refresh_token que ya no vale
# -- y ahi hay que rehacer la autorizacion a mano. El candado hace que renueve
# uno solo por vez.
_CANDADO_TOKEN = threading.Lock()

load_dotenv()

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
    """El token de Mercado Libre. Vive en Postgres, no en un archivo.

    ML entrega un refresh_token NUEVO en cada renovacion y anula el anterior,
    asi que con el token en un archivo local dos maquinas se pisan: la que
    renueva deja a la otra afuera. Paso el 20/08/2026 -- correr el orquestador
    desde la notebook dejo a la PC de la oficina sin poder autenticarse.

    En la base hay uno solo y el problema desaparece. La primera vez,
    `estado.leer` importa `ml_tokens.json` si todavia esta al lado del script.
    """
    tokens = estado.leer("ml_tokens")
    if not tokens:
        raise RuntimeError(
            "No hay token de Mercado Libre guardado (ops.estado['ml_tokens'])\n"
            "  ni un ml_tokens.json que importar. Hay que autorizar la app con\n"
            "  ml_token.py, que ahora lo guarda directo en la base."
        )
    return tokens


def guardar_tokens(tokens):
    estado.guardar("ml_tokens", tokens)


def renovar_access_token():
    """Usa el refresh_token para obtener un access_token nuevo.
       OJO: ML devuelve un refresh_token NUEVO cada vez, hay que guardarlo."""
    faltan = [v for v in ("ML_CLIENT_ID", "ML_CLIENT_SECRET") if not os.getenv(v)]
    if faltan:
        # Sin esto, `os.getenv` devuelve None, requests lo manda como el texto
        # "None", y ML contesta un 400 que se lee igual que el de un token
        # vencido. Son dos problemas distintos y se arreglan distinto.
        raise RuntimeError(
            f"Faltan variables en el .env de {os.getcwd()}: {', '.join(faltan)}"
        )

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

    # `raise_for_status()` tira a la basura el cuerpo de la respuesta, y ahi
    # esta la unica pista de que paso: el error quedaba en "400 Bad Request"
    # pelado, que se lee igual para dos causas que no tienen nada que ver.
    if not r.ok:
        raise RuntimeError(
            f"Mercado Libre rechazo la renovacion del token (HTTP {r.status_code}).\n"
            f"  Respuesta: {r.text[:300]}\n"
            "  invalid_grant  -> el refresh_token ya se uso o vencio.\n"
            "     ML entrega un refresh_token NUEVO en cada renovacion y anula el\n"
            "     anterior, asi que dos maquinas no pueden compartir el archivo:\n"
            "     la que renueva deja a la otra afuera. Se arregla reautorizando\n"
            "     con ml_token.py, en LA maquina que corre el orquestador.\n"
            "  invalid_client -> revisar ML_CLIENT_ID y ML_CLIENT_SECRET en el .env."
        )

    nuevos = r.json()
    guardar_tokens(nuevos)        # guardamos el refresh_token nuevo
    print("  Token renovado OK")
    return nuevos["access_token"]


def llamar_ml(endpoint, access_token, params=None, max_reintentos=6, pausa=True):
    """Llama a la API de ML con el access_token. Reintenta automaticamente en 429.

    `pausa=False` saltea la espera del final. Se usa cuando las llamadas van en
    PARALELO: ahi el freno lo pone la cantidad de hilos, y dormir ademas dentro
    de cada uno seria frenar dos veces.
    """
    url = "https://api.mercadolibre.com" + endpoint
    headers = {"Authorization": f"Bearer {access_token}"}

    intento = 0
    while True:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT_HTTP)

        if r.status_code == 401:      # token vencido: renovar y reintentar
            print("  401: renovando token...")
            with _CANDADO_TOKEN:
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
        if pausa:
            time.sleep(PAUSA)
        return r.json()


def _crear_engine():
    return crear_engine()


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
            # SOLO se tolera que la tabla no exista todavia (primera corrida en
            # una base limpia). Cualquier otro error -- un timeout, un lock, la
            # conexion cortada -- tiene que EXPLOTAR: si el borrado no se hizo,
            # insertar igual duplica la tabla entera. Ver errores_bd.py.
            if not es_tabla_inexistente(e):
                raise
            print(f"  bronze.{tabla} no existe todavia -> la crea el to_sql.")

    df.to_sql(tabla, engine, schema="bronze", if_exists=modo, index=False)
    print(f"  Guardado ({modo}): bronze.{tabla} ({len(df)} filas)")


def guardar_ventana_en_bd(df, tabla, col_fecha, cutoff):
    """Para VENTAS (crecen con el tiempo): reemplaza las filas de la ventana
    movil. Todo lo anterior a cutoff queda intacto -- no se toca ni se vuelve a
    pedir a la API de ML.

    POR QUE BORRA POR ID Y NO SOLO POR FECHA
    Antes borraba unicamente con `col_fecha::date >= cutoff`, y eso DUPLICABA
    ordenes. `date_created` es texto con offset (-04:00) y Postgres resuelve ese
    `::date` en la zona del SERVIDOR, que es UTC. Argentina es UTC-3, asi que
    una venta de las 21 de aca ya es del dia siguiente en UTC: quedaba fuera del
    borrado, la API la volvia a traer, y entraba de nuevo.

    Se midio el 20/08/2026 y el patron no deja lugar a dudas: de 1.195 ordenes
    duplicadas, 1.191 estaban entre las 21 y la medianoche. Eran 790 filas de
    mas en bronze y $8,5 M contados dos veces en gold.fact_ventas.

    El arreglo no es corregir el huso del DELETE sino dejar de depender de el:
    se borran los ID QUE SE ESTAN POR INSERTAR. Eso es exacto por definicion --
    la clave que se borra es la misma que se agrega -- y no hay huso, formato ni
    borde de dia que lo pueda romper.

    El borrado por ventana se mantiene ADEMAS, para que una orden que la API
    dejo de devolver (por ejemplo si se borro alla) no quede colgada para
    siempre. Los dos borrados y la insercion van en UNA transaccion, asi el
    tablero nunca lee la tabla a medio reemplazar.
    """
    engine = _crear_engine()
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")

    if df.empty:
        print(f"  (sin ventas nuevas de ML en esta ventana)")
        return

    df = _listas_a_texto(df)
    ids = [str(x) for x in df["id"].tolist()]

    try:
        with engine.begin() as con:
            # El `>= piso` es REDUNDANTE con el `::date`, y esta puesto para
            # que Postgres pueda usar el indice.
            #
            # `"{col_fecha}"::date` es un cast, y ningun indice puede servir un
            # cast: el DELETE recorria los 108 MB de la tabla entera. El 21/08
            # se paso del statement_timeout de Supabase y ese fue el principio
            # del incidente que duplico 2.548 ordenes.
            #
            # El piso va UN DIA ANTES del cutoff porque `::date` se resuelve en
            # UTC y puede correr una fila hasta un dia hacia adelante: una venta
            # de las 21 hora argentina ya es del dia siguiente alla. Con el dia
            # de colchon, el pre-filtro NUNCA deja afuera una fila que el filtro
            # exacto si querria borrar -- y el exacto sigue decidiendo, asi que
            # el conjunto borrado es identico al de antes.
            piso = (cutoff - timedelta(days=1)).isoformat()
            porFecha = con.exec_driver_sql(
                f'DELETE FROM bronze."{tabla}" '
                f'WHERE "{col_fecha}" >= %(piso)s '
                f'  AND "{col_fecha}"::date >= %(cutoff)s',
                {"piso": piso, "cutoff": cutoff},
            ).rowcount
            # El que realmente evita los duplicados: saca las que se van a
            # volver a insertar, sin importar en que dia las ubique el huso.
            #
            # Compara `id` contra un bigint[] y no `id::text` contra text[]: el
            # cast tambien impedia usar el indice. Medido, con 3 ids: Index Scan
            # de costo 5,35 contra un Seq Scan de 13.871.
            porId = con.exec_driver_sql(
                f'DELETE FROM bronze."{tabla}" WHERE id = ANY(%(ids)s::bigint[])',
                {"ids": ids},
            ).rowcount
            print(f"  Borradas antes de reinsertar: {porFecha} por ventana + {porId} por id")
            df.to_sql(tabla, con, schema="bronze", if_exists="append", index=False)
    except Exception as e:
        # ESTE except es el que duplico 2.548 ordenes el 21/08/2026.
        #
        # Atrapaba cualquier error y despues insertaba igual. Ese dia el DELETE
        # de la ventana se paso del statement_timeout de Supabase, la
        # transaccion hizo rollback -- los borrados se deshicieron -- y las
        # filas entraron por segunda vez. El paso reporto OK.
        #
        # Ahora se tolera EXACTAMENTE un error: que la tabla no exista todavia,
        # que es la primera corrida en una base limpia y ahi no hay nada que
        # duplicar. Todo lo demas explota, el orquestador lo ve, lo reintenta y
        # queda en el log. Ver errores_bd.py.
        if not es_tabla_inexistente(e):
            raise
        print(f"  bronze.{tabla} no existe todavia -> la crea el to_sql.")
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

    # De a 20 por llamada (lo maximo que acepta el multiget) y varias llamadas a
    # la vez, por lo mismo que el stock: son ~430 llamadas y en fila son minutos.
    lotes = [ids[i:i + 20] for i in range(0, len(ids), 20)]
    print(f"  {len(lotes)} lotes de hasta 20 (de a {HILOS_STOCK})")

    def pedir_lote(lote):
        try:
            return llamar_ml("/items", access_token,
                             params={"ids": ",".join(lote)}, pausa=False)
        except Exception as e:
            print(f"    lote fallado: {str(e)[:90]}")
            return []

    with ThreadPoolExecutor(max_workers=HILOS_STOCK) as pool:
        respuestas = list(pool.map(pedir_lote, lotes))

    detalles, sin_detalle = [], 0
    for datos in respuestas:
        for item in datos:
            if item.get("code") == 200:
                detalles.append(item["body"])
            else:
                sin_detalle += 1

    # Se cuenta lo que la API no devolvio en vez de descartarlo en silencio: si
    # un dia faltan mil publicaciones, tiene que verse en el log y no
    # descubrirse por un total que no cierra.
    if sin_detalle:
        print(f"  ATENCION: {sin_detalle} publicaciones sin detalle (la API no las devolvio)")

    df = pd.json_normalize(detalles)
    print(f"  Total con detalle: {len(df)} publicaciones")
    guardar_en_bd(df, "ml_publicaciones", modo="replace")


def extraer_stock_full():
    """Stock real en Full (fulfillment) por cada inventory_id.

    POR QUE VA EN PARALELO
    La API de Mercado Libre no tiene forma de pedir varios inventarios juntos:
    hay que preguntar de a uno, y son ~3.800. En fila, con la pausa entre
    llamadas, eso son casi 30 minutos -- el 88% de todo lo que tarda el
    catalogo. Mandandolas de a tandas baja a poco mas de un minuto.
    (La idea salio del Apps Script de la planilla de stock, que usa `fetchAll`
    por lo mismo y con el mismo resultado.)

    POR QUE NO SE PUEDE USAR EL DATO DE LAS PUBLICACIONES
    `ml_publicaciones` ya trae `available_quantity`, asi que la tentacion es
    evitarse las 3.800 llamadas. No sirve: varias publicaciones comparten el
    mismo `inventory_id`, asi que sumar por publicacion cuenta la misma unidad
    varias veces. Verificado contra los datos: da 19.211 unidades contra las
    10.577 reales, y 1.512 de 3.830 inventarios no coinciden. Ademas el desglose
    de "no disponible" (dañado, en revision, reservado) SOLO esta en este
    endpoint, y son 699 unidades que conviene ver.

    POR QUE 12 HILOS Y NO 50
    Cuantos mas, mas rapido, pero mas 429 (demasiadas peticiones). Con 12 el
    paso tarda ~1,5 min y se mantiene lejos del limite. `llamar_ml` ademas
    reintenta el 429 esperando lo que la API pida, asi que un pico se corrige
    solo en vez de perderse.
    """
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
    print(f"  {len(inventory_ids)} inventory_id unicos a consultar (de a {HILOS_STOCK})")

    hechos = itertools.count(1)

    def pedir(inv_id):
        """Un inventario. NUNCA levanta: un error se guarda como fila.

        Es la diferencia con el Apps Script de la planilla, que hace
        `if (código === 200)` y descarta todo lo demas en silencio -- incluido
        el 429. Ahi un inventario que la API no contesto desaparece del total
        sin dejar rastro, y el stock queda mas bajo que la realidad sin que nada
        lo avise. Aca queda la fila con su `error` y se puede contar.
        """
        try:
            datos = llamar_ml(
                f"/inventories/{inv_id}/stock/fulfillment", access_token, pausa=False
            )
            datos["inventory_id"] = inv_id
        except Exception as e:
            datos = {"inventory_id": inv_id, "error": str(e)}
        n = next(hechos)
        if n % 500 == 0:
            print(f"    Consultados: {n} de {len(inventory_ids)}")
        return datos

    with ThreadPoolExecutor(max_workers=HILOS_STOCK) as pool:
        filas = list(pool.map(pedir, inventory_ids))

    df = pd.json_normalize(filas)
    fallados = sum(1 for f in filas if "error" in f)
    print(f"  Total: {len(df)} registros de stock full")
    if fallados:
        # Se avisa aunque no corte: un stock que baja porque la API no contesto
        # se parece demasiado a un stock que bajo porque se vendio.
        print(f"  ATENCION: {fallados} inventarios no se pudieron consultar "
              f"(quedan con `error` en la tabla, no en cero)")
    guardar_en_bd(df, "ml_stock_full", modo="replace")
    guardar_foto_stock(df)


# La foto diaria del stock en Full. La declara ACA y no en esquema.py porque es
# del pipeline de ventas: la escribe `guardar_foto_stock` y la lee el tablero de
# Stock Full. Cuando vivia en el DDL del experimento, este archivo tenia que
# importar `asegurar_tablas` de alla, y el 21/08/2026 un `%` en un comentario de
# ese DDL rompio `--catalogo`.
#
# La clave primaria (fecha, inventory_id) no es decoracion: es lo que garantiza
# que la foto de un dia no pueda entrar dos veces aunque el borrado previo falle.
DDL_FOTO_STOCK = """
create table if not exists bronze.ml_stock_full_historico (
    fecha                  date  not null,
    inventory_id           text  not null,
    total                  double precision,
    available_quantity     double precision,
    not_available_quantity double precision,
    primary key (fecha, inventory_id)
);
"""


def _asegurar_foto_stock(engine):
    """Crea la tabla de la foto si falta. Barato y sin efecto si ya esta."""
    with engine.begin() as con:
        con.execute(text(DDL_FOTO_STOCK))
    return engine


def guardar_foto_stock(df):
    """Guarda la foto de HOY del stock Full en bronze.ml_stock_full_historico.

    POR QUE EXISTE
    `bronze.ml_stock_full` se sobrescribe entera en cada corrida, asi que sabe
    cuanto stock hay HOY y nada mas. Con eso alcanza para "cuantas unidades
    tengo paradas", pero no para la pregunta que de verdad importa: "cuantos
    dias seguidos lleva este articulo con stock y sin venderse".

    Esa cuenta necesita saber si habia stock CADA DIA, y eso no se puede
    reconstruir hacia atras: el dato de ayer ya se piso. La unica forma es
    empezar a guardarlo. Por eso esta tabla es de las que solo CRECEN.

    Son ~3.800 filas por dia (1,4 M al ano), que para Postgres no es nada.

    IDEMPOTENTE DE VERDAD: la tabla tiene clave primaria (fecha, inventory_id),
    asi que correr el catalogo dos veces el mismo dia no puede duplicar el dia
    ni aunque falle algo en el medio.

    ANTES NO LO ERA, Y ESE ERA EL PROBLEMA. La version anterior borraba el dia y
    lo insertaba en una transaccion, pero si el DELETE fallaba -- y fallaba
    siempre la primera vez, porque la tabla todavia no existia -- el `except`
    reintentaba el INSERT SOLO, sin el borrado. En el arranque eso funcionaba de
    casualidad (no habia nada que duplicar); cualquier otro fallo del DELETE
    dejaba el dia dos veces y nada lo avisaba. La tabla ahora se crea con DDL
    explicito en esquema.py, asi que el `except` que la creaba de rebote no hace
    falta y el fallo, si lo hay, se ve.
    """
    if df.empty or "inventory_id" not in df.columns:
        print("  (sin stock que fotografiar)")
        return

    columnas = [c for c in ("inventory_id", "total", "available_quantity",
                            "not_available_quantity") if c in df.columns]
    foto = df[columnas].copy()

    # Un inventario que la API no contesto NO se fotografia. Sus columnas vienen
    # en nulo y guardarlo dejaria una foto que dice "cero disponible" para algo
    # que probablemente tenia stock: exactamente el falso quiebre que todo esto
    # viene a evitar. Se cuenta y se sigue.
    if "error" in df.columns:
        fallados = int(df["error"].notna().sum())
        if fallados:
            foto = foto[df["error"].isna()]
            print(f"  ({fallados} inventarios sin respuesta: no entran a la foto)")

    if foto.empty:
        print("  (ningun inventario respondio: no se guarda foto)")
        return

    # La fecha se toma en hora ARGENTINA y no la del sistema: el catalogo corre
    # a la manana, pero si algun dia corriera despues de las 21 un `date.today()`
    # en UTC ya seria el dia siguiente y la foto quedaria fechada mal.
    hoy = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=-3))
    ).date()
    foto.insert(0, "fecha", hoy)

    engine = _asegurar_foto_stock(_crear_engine())
    tabla = "ml_stock_full_historico"
    with engine.begin() as con:
        borradas = con.exec_driver_sql(
            f'DELETE FROM bronze."{tabla}" WHERE fecha = %(hoy)s', {"hoy": hoy}
        ).rowcount
        if borradas:
            print(f"  (foto de {hoy} ya estaba: se reemplazan {borradas} filas)")
        foto.to_sql(tabla, con, schema="bronze", if_exists="append", index=False)

    print(f"  Foto del {hoy}: bronze.{tabla} ({len(foto)} filas)")


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