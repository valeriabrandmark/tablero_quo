import argparse
import os
import json
import time
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from mercadolibre import token_ml
from conexion import crear_engine
import relleno

load_dotenv()

# `statement_timeout` propio, mas largo que el de la base.
#
# Supabase corta cualquier consulta a los 2 minutos, y esta bien que lo haga:
# es la defensa contra una consulta suelta que cuelgue el tablero. Pero esto es
# un proceso de fondo que corre solo, no una pagina esperando a un usuario, y
# tenerlo cortado por el limite pensado para paginas no protege nada -- el
# techo real de este paso lo pone el orquestador, que lo mata a los 45 minutos.
ENGINE_OPCIONES = {"options": "-c statement_timeout=300000"}   # 5 min

engine = crear_engine(connect_args=ENGINE_OPCIONES)

MI_USER_ID = int(os.getenv("ML_USER_ID"))
FECHA_CORTE = date(2026, 5, 6)


# ---------------------------------------------------------------------------
#  COMO SE DESPACHO CADA VENTA
# ---------------------------------------------------------------------------
#
# Full, Flex, Colecta o un punto de despacho. El dato NO VIENE EN LA ORDEN:
# `/orders/search` trae `shipping.id` y nada mas. Vive en el envio, y hay que
# pedirlo aparte.
#
# POR QUE NO ALCANZA CON LA PUBLICACION. `bronze.ml_publicaciones` ya guarda
# `shipping.logistic_type` y es tentador usar eso, que sale gratis. Pero es el
# modo que la publicacion tiene HOY, no como salio cada orden -- y el caso que
# importa es justo el que ahi no se ve: una publicacion de Full que se queda sin
# stock y se despacha desde el deposito sigue figurando `fulfillment`. Medido el
# 06/11/2026 sobre dos semanas, la publicacion decia Full en 5.182 de 5.198
# ordenes, asi que un grafico armado con eso dice "99,7% Full" siempre.
#
# POR QUE SON DOS LLAMADAS POR ENVIO. `/shipments/{id}/costs` --la que ya se
# hacia-- trae la plata y nada de logistica; `/shipments/{id}` trae la logistica
# y no el costo del vendedor. No hay un endpoint con las dos cosas. Para los
# envios nuevos son dos llamadas en vez de una, y son los del dia: el historico
# se rellena una sola vez con --solo-logistica.
URL_COSTOS = "https://api.mercadolibre.com/shipments/{}/costs"
URL_ENVIO = "https://api.mercadolibre.com/shipments/{}"

# Columnas que se agregaron despues de que la tabla ya existia.
#
# `to_sql(if_exists="append")` NO agrega columnas: inserta contra las que hay.
# Sin este ALTER, la primera corrida tras el deploy muere con
# `column "logistica" of relation "ml_envios" does not exist`, que es lo mismo
# que paso con `envio_costo_tienda` en bronze.
ALTER_LOGISTICA = (
    "ALTER TABLE bronze.ml_envios "
    "  ADD COLUMN IF NOT EXISTS logistica text, "
    "  ADD COLUMN IF NOT EXISTS modo_envio text"
)


def to_date(valor):
    ts = pd.to_datetime(valor, errors="coerce", utc=True)
    return None if pd.isna(ts) else ts.date()


def texto_o_nada(valor):
    """El valor como texto, o None si vino vacio."""
    if valor is None:
        return None
    s = str(valor).strip()
    return s or None


def logistica_de_envio(data):
    """(logistic_type, mode) del JSON de `/shipments/{id}`.

    DEVUELVE LOS VALORES CRUDOS DE ML --`fulfillment`, `self_service`,
    `cross_docking`, `drop_off`, `xd_drop_off`, `default`-- y no una etiqueta
    para mostrar. Traducirlos es cosa del tablero: el dia que ML agregue un modo
    nuevo, entra solo y se ve como texto desconocido, en vez de caer en un
    "Otros" que tapa que hay algo que no conociamos.

    SE MIRAN DOS LUGARES. `logistic_type` y `mode` viajan sueltos en la raiz, y
    en el formato nuevo de la API van adentro de `logistic`. ML fue moviendo el
    campo y las dos formas conviven segun el envio, asi que se prueba la raiz
    primero y se cae al objeto nuevo.
    """
    if not isinstance(data, dict):
        return None, None
    anidado = data.get("logistic")
    if not isinstance(anidado, dict):
        anidado = {}
    tipo = data.get("logistic_type") or anidado.get("type")
    modo = data.get("mode") or anidado.get("mode")
    return texto_o_nada(tipo), texto_o_nada(modo)


def asegurar_columnas():
    """Agrega logistica/modo_envio si la tabla ya existia sin ellas.

    Si `bronze.ml_envios` todavia no existe no hace nada: la crea el primer
    `to_sql` con las columnas que traiga el DataFrame, que ya son las nuevas.
    """
    with engine.begin() as con:
        if con.exec_driver_sql(
                "select to_regclass('bronze.ml_envios') is not null").scalar():
            con.exec_driver_sql(ALTER_LOGISTICA)


def pedidor():
    """Un GET a la API de ML que renueva el token cuando contesta 401.

    Existe como funcion y no inline porque ahora hay TRES lugares que piden a la
    API --costos, logistica del envio nuevo y relleno de logistica-- y el manejo
    del 401 tiene una sutileza que no conviene copiar tres veces: NO SE FUERZA
    la renovacion de entrada. Puede que otro proceso ya haya renovado y el que
    tenemos en mano sea solo viejo; recien si el que vuelve es el mismo se fuerza.
    Ver token_ml en mercadolibre.py.
    """
    estado = {"token": token_ml()}

    def pedir(url):
        cabeceras = {"Authorization": f"Bearer {estado['token']}"}
        resp = requests.get(url, headers=cabeceras, timeout=15)
        if resp.status_code == 401:
            nuevo = token_ml()
            if nuevo == estado["token"]:
                nuevo = token_ml(forzar=True)
            estado["token"] = nuevo
            resp = requests.get(
                url, headers={"Authorization": f"Bearer {nuevo}"}, timeout=15)
        return resp

    return pedir


def skus_de_la_marca(marca):
    """Los SKU de esa marca, como los escribe el maestro de Sigma.

    La marca NO vive en la columna `marca` --que esta vacia para casi todos--
    sino en `attributes.marca`. Es la misma fuente que usa modelo.py para
    decidir de que marca es cada linea, asi que filtrar por acá trae
    exactamente los envios de las lineas que van a quedar en gold.
    """
    filas = pd.read_sql(
        """SELECT trim(id) AS sku FROM bronze.sigma_articulos
            WHERE upper(trim(coalesce("attributes.marca", ''))) = %(marca)s""",
        engine, params={"marca": marca.strip().upper()},
    )
    return filas["sku"].tolist()


def extraer_envios(desde=None, hasta=None, marca=None):
    """Baja a bronze.ml_envios los costos de envio que falten.

    Sin argumentos hace lo de siempre: de FECHA_CORTE hasta hoy, todas las
    marcas. `desde`/`hasta`/`marca` existen para un relleno de meses viejos.

    POR QUE HAY UN FILTRO DE MARCA. Pedir los envios de un trimestre entero son
    ~28.000 llamadas de a una, o sea horas. Si lo que se esta rellenando en gold
    es una sola marca --que es el unico caso en que se rellena hacia atras-- los
    unicos envios que se van a usar son los de esas ordenes: catorce, no
    veintiocho mil.
    """
    print("=== Extrayendo costos de envio de ML (retomando) ===")

    piso_fecha = desde or FECHA_CORTE
    if desde is not None and desde < FECHA_CORTE:
        print(f"    OJO: se piden envios anteriores a {FECHA_CORTE}, que es el")
        print("    piso historico del tablero. Es a proposito, pero acordate de")
        print("    correr modelo.py --relleno despues para que entren a gold.")
    print(f"    Desde {piso_fecha}"
          + (f" hasta {hasta}" if hasta else " hasta hoy")
          + (f" · solo marca {marca.strip().upper()}" if marca else " · todas las marcas"))

    skus = None
    if marca:
        skus = skus_de_la_marca(marca)
        if not skus:
            print(f"    NINGUN articulo tiene marca '{marca.strip().upper()}' en el "
                  "maestro de Sigma.")
            print("    Revisa como esta escrita: sin articulos no hay envios que pedir.")
            return
        print(f"    {len(skus)} articulos de esa marca")

    # 1) Los envios que FALTAN, resueltos en SQL y no en pandas.
    #
    # Antes esta consulta se traia las 38.000 ordenes pagadas desde mayo, con
    # sus tres columnas, y recien despues filtraba en pandas por fecha y por
    # cuales ya estaban bajadas. Traia 38.000 filas para quedarse con veinte.
    #
    # Eso reventaba contra el `statement_timeout`: `bronze.ml_ventas` pesa
    # 108 MB y no tiene indices, asi que la consulta es un recorrido completo de
    # la tabla. Con el cache frio -- justo lo que pasa despues de que
    # mercadolibre.py la reescribe entera -- no entra en dos minutos y la corrida
    # se cae. Paso el 21/08.
    #
    # Ahora el filtro va en la base: la fecha, y el `NOT EXISTS` contra lo que
    # ya esta bajado. La consulta devuelve solo los que hay que pedirle a la API.
    #
    # El piso de fecha se compara como TEXTO, que es como esta guardada
    # `date_created` (ISO-8601, que ordena igual como texto que como fecha), y va
    # un dia antes del corte a proposito: el filtro fino sigue siendo el de
    # pandas, que resuelve bien el huso. Sin ese dia de más, una orden del 5 de
    # mayo a las 23 hs -- que en UTC ya es 6 de mayo -- se perderia.
    piso = (piso_fecha - timedelta(days=1)).isoformat()
    ship = 'v."shipping.id"::bigint::text'

    # `bronze.ml_envios` no existe en una instalacion nueva: la crea el primer
    # to_sql. Si todavia no esta, no hay nada bajado que saltear.
    with engine.connect() as con:
        hay_envios = con.exec_driver_sql(
            "select to_regclass('bronze.ml_envios') is not null").scalar()

    # Antes de escribir, no despues: el to_sql de esta misma corrida ya trae las
    # columnas nuevas.
    asegurar_columnas()

    filtro_ya_bajados = f"""
          AND NOT EXISTS (SELECT 1 FROM bronze.ml_envios e
                           WHERE e.shipping_id = {ship})""" if hay_envios else ""

    # EL FILTRO DE MARCA MIRA EL JSON DE LA ORDEN, que es donde esta el SKU: no
    # hay una columna con el articulo, `order_items` es la orden entera. Como se
    # arma ese pedazo de SQL --y por que no es un LIKE-- esta en relleno.py.
    filtro_marca = (
        "\n          AND " + relleno.condicion_marca_en_items("v.order_items")
        if skus else ""
    )

    # El parametro va como %(piso)s y NO como :piso.
    #
    # `pd.read_sql` con un Engine y el SQL como STRING pelado despacha por
    # `exec_driver_sql`, que le pasa la consulta derecho a psycopg2 -- y psycopg2
    # usa `pyformat`. Con `:piso` la base recibe los dos puntos literales y tira
    # "syntax error at or near :". Es el mismo estilo que usa mercadolibre.py.
    ordenes = pd.read_sql(f"""
        SELECT DISTINCT ON ({ship})
               v.id, {ship} AS shipping_id_str, v.date_created
        FROM bronze.ml_ventas v
        WHERE v.status = 'paid'
          AND v."shipping.id" IS NOT NULL
          AND v.date_created >= %(piso)s{filtro_ya_bajados}{filtro_marca}
        ORDER BY {ship}
    """, engine, params={"piso": piso, "skus": skus})

    ordenes["fecha"] = ordenes["date_created"].apply(to_date)
    # El filtro fino --el que resuelve bien el huso-- es el mismo que usa
    # modelo.py para decidir que entra en una reconstruccion. Ver relleno.py.
    adentro = ~ordenes["fecha"].apply(
        lambda f: relleno.fuera_de_ventana(f, piso_fecha, hasta))
    faltan = ordenes[adentro]
    print(f"Faltan {len(faltan)} envios por pedirle a la API.")

    if len(faltan) == 0:
        print("Ya estan todos! Nada que hacer.")
        return

    pedir = pedidor()
    errores = 0

    # Se guarda AGREGANDO al final de la tabla, no reescribiendola.
    #
    # Antes cada checkpoint hacia to_sql(..., if_exists="replace") sobre la lista
    # entera: eso borra la tabla y la vuelve a crear. Con 17.000 envios por pedir
    # son ~170 reescrituras de una tabla que crece hasta 34.000 filas, y sobre
    # todo: si el proceso se cortaba justo durante una de esas reescrituras, la
    # tabla quedaba vacia y la corrida siguiente tenia que volver a pedirle los
    # 34.000 a la API desde cero. En una tanda de dos horas eso es la diferencia
    # entre poder cortarla tranquilo y no poder.
    #
    # Agregando, lo que ya se bajo queda guardado pase lo que pase, y cortar la
    # corrida (Ctrl+C, se corta la luz, lo que sea) no cuesta mas que los ultimos
    # envios del buffer.
    nuevos = []

    def guardar_pendientes():
        if not nuevos:
            return
        pd.DataFrame(nuevos).to_sql("ml_envios", engine, schema="bronze",
                                    if_exists="append", index=False)
        nuevos.clear()

    for i, (_, r) in enumerate(faltan.iterrows(), 1):
        ship_id = r["shipping_id_str"]
        try:
            resp = pedir(URL_COSTOS.format(ship_id))
            if resp.status_code == 200:
                data = resp.json()
                costo_envio = 0
                for s in data.get("senders", []):
                    if s.get("user_id") == MI_USER_ID:
                        costo_envio = s.get("cost", 0) or 0
                        break

                # LA LOGISTICA VA EN LA MISMA PASADA, no en un relleno aparte.
                #
                # Si fallara solo esta segunda llamada, la fila se guarda igual
                # con la logistica en null: el costo de envio --que es lo que
                # entra al margen-- no se pierde por un dato de color. Lo que
                # quedo en null lo levanta despues --solo-logistica.
                tipo, modo = None, None
                try:
                    resp_envio = pedir(URL_ENVIO.format(ship_id))
                    if resp_envio.status_code == 200:
                        tipo, modo = logistica_de_envio(resp_envio.json())
                except Exception:
                    pass

                nuevos.append({
                    "shipping_id": ship_id,
                    "order_id": str(r["id"]),
                    "costo_envio": costo_envio,
                    "logistica": tipo,
                    "modo_envio": modo,
                })
            else:
                errores += 1
        except Exception:
            errores += 1

        if i % 100 == 0:
            print(f"  {i}/{len(faltan)} pedidos a la API ({errores} con error)...")
            guardar_pendientes()
        time.sleep(0.1)

    guardar_pendientes()

    # El resumen se lee de la tabla y no de una lista en memoria: asi cuenta
    # tambien lo que quedo de corridas anteriores, que es el numero que importa.
    final = pd.read_sql("SELECT costo_envio FROM bronze.ml_envios", engine)
    print(f"\nGuardado: bronze.ml_envios ({len(final)} envios totales)")
    print(f"Errores en esta corrida: {errores} (se reintentan en la proxima)")
    print(f"Envios con costo a tu cargo: {(final['costo_envio'] > 0).sum()}")
    print(f"Costo total de envios: ${final['costo_envio'].sum():,.2f}")


def completar_logistica(desde=None, hasta=None):
    """Rellena `logistica`/`modo_envio` en los envios que ya estan bajados.

    POR QUE ES UN PASO APARTE Y NO PARTE DE LA CORRIDA. Cuando se agrego la
    columna ya habia 62.616 envios bajados, y el filtro de extraer_envios()
    saltea todo lo que YA ESTA en la tabla --como corresponde, para no volver a
    pedir 62.000 costos-- asi que esas filas nunca se vuelven a mirar. Esto las
    recorre una vez. A 0,1 s por envio, el historico completo son unas tres
    horas: por eso tiene ventana de fechas y no se cuelga del orquestador, que
    mata cada paso a los 45 minutos.

    Es retomable: solo mira las filas con la logistica en null, asi que cortarlo
    y volver a correrlo sigue desde donde quedo. Y se puede repetir sin miedo --
    actualiza por shipping_id, no inserta.
    """
    print("=== Completando la logistica de los envios ya bajados ===")
    asegurar_columnas()

    piso = (desde or FECHA_CORTE).isoformat()
    print(f"    Desde {piso}" + (f" hasta {hasta}" if hasta else " hasta hoy"))

    # El techo va en SQL --no en pandas como en extraer_envios()-- porque aca no
    # hay que resolver el huso con precision de un dia: es un relleno de datos
    # viejos y un dia de mas o de menos en el borde se arregla corriendolo otra
    # vez. Lo que importa es no traer 62.000 filas cuando se pidieron 10.000.
    tope = f" AND v.date_created < '{(hasta + timedelta(days=1)).isoformat()}'" if hasta else ""

    faltan = pd.read_sql(f"""
        SELECT DISTINCT e.shipping_id
        FROM bronze.ml_envios e
        JOIN bronze.ml_ventas v
          ON v."shipping.id"::bigint::text = e.shipping_id
        WHERE e.logistica IS NULL
          AND v.date_created >= %(piso)s{tope}
    """, engine, params={"piso": piso})

    print(f"Faltan {len(faltan)} envios sin logistica.")
    if len(faltan) == 0:
        print("Ya estan todos! Nada que hacer.")
        return

    pedir = pedidor()
    errores = 0
    pendientes = []

    def guardar_pendientes():
        if not pendientes:
            return
        with engine.begin() as con:
            con.execute(
                text("UPDATE bronze.ml_envios "
                     "   SET logistica = :logistica, modo_envio = :modo "
                     " WHERE shipping_id = :shipping_id"),
                pendientes,
            )
        pendientes.clear()

    for i, ship_id in enumerate(faltan["shipping_id"], 1):
        try:
            resp = pedir(URL_ENVIO.format(ship_id))
            if resp.status_code == 200:
                tipo, modo = logistica_de_envio(resp.json())
                # Un envio que contesta 200 pero sin logistic_type NO se guarda:
                # dejarlo en null es lo correcto --no sabemos como salio-- pero
                # entonces la proxima corrida lo vuelve a pedir. Es el precio de
                # no inventar un dato, y son poquitos.
                if tipo is None:
                    errores += 1
                else:
                    pendientes.append({
                        "shipping_id": ship_id,
                        "logistica": tipo,
                        "modo": modo,
                    })
            else:
                errores += 1
        except Exception:
            errores += 1

        if i % 100 == 0:
            print(f"  {i}/{len(faltan)} pedidos a la API ({errores} con error)...")
            guardar_pendientes()
        time.sleep(0.1)

    guardar_pendientes()

    resumen = pd.read_sql("""
        SELECT coalesce(logistica, '(sin dato)') AS logistica, count(*) AS envios
        FROM bronze.ml_envios
        GROUP BY 1 ORDER BY 2 DESC
    """, engine)
    print("\nComo salieron los envios que ya tienen el dato:")
    for _, fila in resumen.iterrows():
        print(f"  {fila['logistica']:<16} {fila['envios']}")
    print(f"Errores en esta corrida: {errores} (se reintentan en la proxima)")


def main():
    parser = argparse.ArgumentParser(
        description="Baja a bronze.ml_envios los costos de envio que falten.",
    )
    parser.add_argument("--desde", type=date.fromisoformat,
                        help=f"Primer dia a mirar (por defecto {FECHA_CORTE})")
    parser.add_argument("--hasta", type=date.fromisoformat,
                        help="Ultimo dia, incluido (por defecto hoy)")
    parser.add_argument("--marca",
                        help="Solo los envios de las ordenes que llevan algun "
                             "articulo de esa marca. Para un relleno viejo.")
    parser.add_argument("--solo-logistica", action="store_true",
                        help="No baja costos: completa la logistica (Full, "
                             "Flex, Colecta) de los envios ya bajados que la "
                             "tengan en null. Para el relleno historico.")
    args = parser.parse_args()

    if args.desde and args.hasta and args.hasta < args.desde:
        parser.error("--hasta no puede ser anterior a --desde")

    if args.solo_logistica:
        if args.marca:
            parser.error("--marca no se aplica a --solo-logistica: el relleno "
                         "mira los envios ya bajados, no las ordenes")
        completar_logistica(args.desde, args.hasta)
        return

    extraer_envios(args.desde, args.hasta, args.marca)


if __name__ == "__main__":
    main()
    print("\n=== LISTO ===")