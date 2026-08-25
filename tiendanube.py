import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv
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

load_dotenv()

STORE_ID = os.getenv("TN_STORE_ID")
TOKEN = os.getenv("TN_TOKEN", "")
USER_AGENT = os.getenv("TN_USER_AGENT", "TableroQuo (correo@ejemplo.com)")

URL_BASE = f"https://api.tiendanube.com/v1/{STORE_ID}/"
HEADERS = {
    "Authentication": f"bearer {TOKEN}",
    "User-Agent": USER_AGENT,
}

PAUSA = 0.6

# Desde cuando se piden los pedidos, y con que estados.
#
# El `status: any` NO es decorativo: en bronze.tn_pedidos_items no habia UNA
# SOLA orden `closed` -- solo `open` y `cancelled`. En una tienda con ventas,
# las ventas concretadas terminan cerradas, asi que si no aparece ninguna es
# que la consulta no las estaba trayendo. Con `any` se piden los tres estados
# y despues se filtra en el tablero, que es donde hay que decidir que cuenta
# como venta.
PARAMS_PEDIDOS = {
    "created_at_min": "2026-01-01T00:00:00+00:00",
    "status": "any",
}


def llamar_tn_paginado(endpoint, params=None):
    """Trae todos los registros de un endpoint paginando.
       Tienda Nube usa page / per_page (max 200) y avisa el fin con lista vacia."""
    if params is None:
        params = {}
    params["per_page"] = 200
    todos = []
    pagina = 1
    while True:
        params["page"] = pagina
        r = requests.get(URL_BASE + endpoint, headers=HEADERS, params=params, timeout=TIMEOUT_HTTP)
        if r.status_code == 429:          # demasiadas peticiones
            print("  429: esperando 2s...")
            time.sleep(2)
            continue
        r.raise_for_status()
        datos = r.json()
        if not datos:                     # lista vacia = no hay mas
            break
        todos.extend(datos)
        print(f"  Pagina {pagina}: {len(datos)} registros (acumulado: {len(todos)})")
        if len(datos) < 200:              # ultima pagina
            break
        pagina += 1
        time.sleep(PAUSA)
    return todos


def tipo_sql(serie):
    """El tipo de columna que le pondria pandas, para que ALTER y CREATE coincidan."""
    import pandas.api.types as t
    if t.is_bool_dtype(serie):
        return "boolean"
    if t.is_integer_dtype(serie):
        return "bigint"
    if t.is_float_dtype(serie):
        return "double precision"
    return "text"


def agregar_columnas_nuevas(engine, tabla, df):
    """Le agrega a la tabla las columnas que trae el DataFrame y ella todavia no.

    Hace falta porque guardamos con DELETE + append en vez de DROP + CREATE
    (ver abajo): el append inserta contra las columnas que YA existen, asi que
    el dia que se empieza a traer un campo nuevo -- por ejemplo el costo de
    envio -- el INSERT falla con `column "envio_costo_tienda" does not exist`.

    Solo AGREGA. Nunca borra ni cambia el tipo de una columna existente, que es
    lo unico que romperia una vista apoyada en la tabla.
    """
    with engine.begin() as con:
        existentes = {
            f[0] for f in con.exec_driver_sql(
                "select column_name from information_schema.columns "
                "where table_schema = 'bronze' and table_name = %s",
                (tabla,),
            ).fetchall()
        }
    if not existentes:          # la tabla no existe: la crea el to_sql
        return
    faltan = [c for c in df.columns if c not in existentes]
    for col in faltan:
        with engine.begin() as con:
            con.exec_driver_sql(
                f'ALTER TABLE bronze."{tabla}" ADD COLUMN "{col}" {tipo_sql(df[col])};'
            )
    if faltan:
        print(f"  columnas nuevas en {tabla}: {', '.join(faltan)}")


def guardar_en_bd(df, tabla, modo="replace"):
    if df.empty:
        print(f"  (sin datos para {tabla})")
        return
    import json as _json
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
            df[col] = df[col].apply(
                lambda x: _json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x
            )
    engine = crear_engine()
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")
    agregar_columnas_nuevas(engine, tabla, df)
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


# ============================================================
#  EXTRACCIONES
# ============================================================

def extraer_pedidos_y_items():
    """Pedidos de Tienda Nube -> las DOS tablas de bronze, con una sola bajada.

    Antes eran dos funciones que llamaban al mismo endpoint por separado; se
    unieron porque bajar los pedidos dos veces solo servia para pegarle el doble
    a la API y para que las dos tablas pudieran quedar con fotos distintas.

    - bronze.tn_pedidos       : el pedido entero como lo manda la API (206 cols).
    - bronze.tn_pedidos_items : una fila por producto, que es lo que lee modelo.py.

    A cada linea de `tn_pedidos_items` se le bajan ademas los datos de plata que
    viven en la CABECERA del pedido y no en el producto. El que importa es
    `shipping_cost_owner`: el envio que absorbe la tienda. Sin el, la
    rentabilidad de Tienda Nube quedaba sin restar el flete -- el mismo agujero
    que tuvo Mercado Libre hasta que se sumo ml_envios.py.

    OJO con los dos costos de envio, que NO son lo mismo:
      shipping_cost_customer -> lo que PAGA el comprador (es ingreso)
      shipping_cost_owner    -> lo que PAGA la tienda    (es costo)
    Suelen coincidir, pero no cuando hay envio gratis o bonificado, que es
    justo cuando el margen se cae y hay que poder verlo.
    """
    print("\n=== PEDIDOS TIENDA NUBE (ventas) ===")
    pedidos = llamar_tn_paginado("orders", dict(PARAMS_PEDIDOS))
    print(f"  {len(pedidos)} pedidos")

    guardar_en_bd(pd.json_normalize(pedidos), "tn_pedidos", modo="replace")

    print("\n=== PEDIDOS ITEMS (una fila por producto vendido) ===")
    filas = []
    for pedido in pedidos:
        cliente = pedido.get("customer") or {}
        direccion = pedido.get("shipping_address") or {}

        # Se arma una vez por pedido y se repite en cada linea: son atributos
        # del pedido, no del producto.
        cabecera = {
            "pedido_id": pedido.get("id"),
            "pedido_numero": pedido.get("number"),
            "fecha": pedido.get("created_at"),
            "estado": pedido.get("status"),
            "estado_pago": pedido.get("payment_status"),
            "pagado_en": pedido.get("paid_at"),
            "cliente_id": cliente.get("id"),
            "cliente_nombre": cliente.get("name"),
            "total_pedido": pedido.get("total"),
            "subtotal_pedido": pedido.get("subtotal"),
            "descuento": pedido.get("discount"),
            "envio_costo_tienda": pedido.get("shipping_cost_owner"),
            "envio_cobrado": pedido.get("shipping_cost_customer"),
            "envio_opcion": pedido.get("shipping_option"),
            "medio_pago": pedido.get("gateway_name"),
            "provincia": direccion.get("province"),
            "ciudad": direccion.get("city"),
        }

        for prod in pedido.get("products", []):
            filas.append({
                **cabecera,
                "producto_id": prod.get("product_id"),
                "variant_id": prod.get("variant_id"),
                "sku": prod.get("sku"),
                "nombre": prod.get("name"),
                "cantidad": prod.get("quantity"),
                "precio": prod.get("price"),
                "costo": prod.get("cost"),
                "total_linea": prod.get("total"),
            })

    df = pd.DataFrame(filas)
    print(f"  Total: {len(df)} lineas de producto (de {len(pedidos)} pedidos)")
    guardar_en_bd(df, "tn_pedidos_items", modo="replace")


def extraer_productos():
    print("\n=== PRODUCTOS TIENDA NUBE ===")
    datos = llamar_tn_paginado("products")
    df = pd.json_normalize(datos)
    print(f"  Total: {len(df)} productos")
    guardar_en_bd(df, "tn_productos", modo="replace")


def extraer_clientes():
    print("\n=== CLIENTES TIENDA NUBE ===")
    datos = llamar_tn_paginado("customers")
    df = pd.json_normalize(datos)
    print(f"  Total: {len(df)} clientes")
    guardar_en_bd(df, "tn_clientes", modo="replace")


# ============================================================
#  EJECUCION
# ============================================================

if __name__ == "__main__":
    print("URL base:", URL_BASE)
    print("Token cargado:", "SI" if TOKEN else "NO")

    extraer_pedidos_y_items()
    #extraer_productos()   # activar despues
    #extraer_clientes()    # activar despues

    print("\n=== LISTO. Revisa las tablas tn_* en Supabase. ===")