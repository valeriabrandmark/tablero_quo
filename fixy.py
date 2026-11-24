"""El stock que FIXY dice que tiene, foto diaria por SKU.

QUE ES FIXY

El deposito de Buenos Aires (almacenaje + envios) desde 11/2026. Desde ahi
salen las ventas de las Tiendas Nube (UniBrandCo, Diaper Genie, Hola Toys,
Litter Genie) y las de Mercado Libre Flex. FIXY se conecta SOLO a Tienda Nube
y a Mercado Libre: nosotros no le mandamos los pedidos.

EL STOCK ES UNO SOLO PARA TODOS LOS CANALES. Un SKU que esta en Flex y en dos
Tiendas Nube descuenta del mismo pozo, asi que el control es por SKU contra la
suma de las ventas de todos los canales, no canal por canal.

QUE TRAE, Y QUE NO

La API (https://argentina.fixylogistica.com/docs/index.html, v2) tiene
`consultarStock`, que contesta POR LISTA DE SKU: no hay un "listame todo".
Por eso los SKU a consultar se arman de lo que puede estar alla:

  - lo que mandamos desde Tucuman      bronze.digip_envios_fixy
  - lo que avisamos que va a entrar    app.fixy_recepciones_items
    (incluye las importaciones, que llegan directo a FIXY)
  - lo que se vendio por Flex o Tienda Nube en los ultimos 120 dias

Si un SKU esta en FIXY y no aparece en ninguna de las tres, no se consulta. Es
el caso de algo que entro sin aviso, que es justamente lo que hay que evitar:
la recepcion se avisa desde el tablero (Stock > FIXY).

LA RESPUESTA DE consultarStock NO ESTA DOCUMENTADA. La doc muestra el pedido
pero no un ejemplo de respuesta. `normalizar` acepta las formas razonables
(lista, dict por SKU, envuelta en "data"/"message"/"stock") y se guarda SIEMPRE
el json crudo de cada SKU en `crudo`: si la primera corrida real interpreta mal
un campo, se corrige aca y no se perdio nada.

QUE ESCRIBE

  bronze.fixy_stock            la foto actual, se reemplaza entera.
  bronze.fixy_stock_historico  una foto por dia (la ultima corrida del dia
                               gana). Sin esta historia no hay control: el
                               descuadre sale de comparar un dia con el otro.

SIN TOKEN NO FALLA, SE SALTEA. Mientras FIXY no entregue el api_token el paso
termina en 0 diciendo por que, para no dejar el orquestador en rojo por algo
que no es un error.

    python fixy.py              la corrida normal
    python fixy.py --probar     solo el dummy-test: valida token y servidor
    python fixy.py --sku A1 B2  consulta esos SKU e imprime la respuesta cruda
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

URL_BASE = os.getenv("FIXY_URL_BASE", "https://argentina.fixylogistica.com/api/v2/")
TOKEN = os.getenv("FIXY_API_TOKEN", "")

# Ver digip.py: (CONECTAR, LEER). Sin timeout un servidor que acepta y no
# contesta cuelga el orquestador entero.
TIMEOUT_HTTP = (10, 60)

# Cuantos SKU por llamada. La doc no fija un tope; 50 es prudente y con ~1.000
# SKU son 20 llamadas.
LOTE = 50
PAUSA = 0.5

# Cuantos dias para atras se miran las ventas para armar la lista de SKU.
DIAS_VENTAS = 120

# Nombres posibles del SKU y de la cantidad en la respuesta. El primero que
# aparezca gana. "disponible" antes que "stock": si vienen los dos, el que se
# puede vender es el que importa.
CLAVES_SKU = ("sku", "codigo", "codigo_articulo", "articulo", "producto")
CLAVES_CANTIDAD = (
    "disponible", "stock_disponible", "cantidad_disponible",
    "stock", "cantidad", "unidades", "total", "saldo",
)


def llamar(endpoint, cuerpo=None):
    """POST con el token en el cuerpo, que es como lo pide FIXY."""
    datos = {"api_token": TOKEN, **(cuerpo or {})}
    r = requests.post(URL_BASE + endpoint, json=datos, timeout=TIMEOUT_HTTP,
                      headers={"Accept": "application/json"})
    if r.status_code in (401, 403):
        raise SystemExit(f"FIXY rechazo el token ({r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    time.sleep(PAUSA)
    return r.json()


# --------------------------------------------------------------------------
# Interpretar la respuesta (sin base ni red: se prueba en probar_fixy.py)
# --------------------------------------------------------------------------

def _numero(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cantidad(d):
    for k in CLAVES_CANTIDAD:
        if k in d:
            n = _numero(d[k])
            if n is not None:
                return n
    return None


def _sku(d):
    for k in CLAVES_SKU:
        v = d.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return None


def normalizar(respuesta):
    """Lleva la respuesta de consultarStock a {sku: (cantidad, crudo)}.

    `cantidad` puede ser None si el SKU vino pero no se reconocio el campo:
    se guarda igual con su crudo, para verlo y ajustar CLAVES_CANTIDAD.
    """
    r = respuesta
    # Envoltorios tipicos de esta API: {"status": "ok", "message": ...}
    if isinstance(r, dict):
        for k in ("data", "message", "stock", "skus", "productos", "result"):
            if k in r and isinstance(r[k], (list, dict)):
                r = r[k]
                break

    salida = {}
    if isinstance(r, list):
        for item in r:
            if isinstance(item, dict):
                sku = _sku(item)
                if sku:
                    salida[sku] = (_cantidad(item), item)
    elif isinstance(r, dict):
        # {"SKU-1": 10, "SKU-2": {...}}
        for sku, v in r.items():
            if isinstance(v, dict):
                salida[str(sku).strip()] = (_cantidad(v), v)
            else:
                salida[str(sku).strip()] = (_numero(v), v)
    return salida


# --------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------

DDL = """
create schema if not exists bronze;
create table if not exists bronze.fixy_stock (
    sku            text primary key,
    disponible     double precision,
    crudo          jsonb,
    consultado_en  timestamptz not null
);
create table if not exists bronze.fixy_stock_historico (
    fecha          date not null,
    sku            text not null,
    disponible     double precision,
    crudo          jsonb,
    consultado_en  timestamptz not null,
    primary key (fecha, sku)
);
"""

# Cada fuente por separado: si una tabla todavia no existe (la de recepciones
# la crea la migracion del tablero) no tiene que tirar abajo las otras.
FUENTES_SKU = {
    "envios desde Tucuman": "select distinct trim(sku) from bronze.digip_envios_fixy where sku is not null",
    "recepciones avisadas": "select distinct trim(sku) from app.fixy_recepciones_items",
    "ventas Flex y Tienda Nube": f"""
        select distinct trim(sku) from gold.fact_ventas
         where sku is not null
           and fecha >= current_date - {DIAS_VENTAS}
           and (canal = 'Tienda Nube'
                or (canal = 'Mercado Libre' and logistica = 'self_service'))""",
}


def skus_a_consultar(engine):
    from errores_bd import es_tabla_inexistente

    todos = set()
    for nombre, sql in FUENTES_SKU.items():
        try:
            with engine.connect() as con:
                filas = con.exec_driver_sql(sql).fetchall()
        except Exception as e:
            if not es_tabla_inexistente(e):
                raise
            print(f"  {nombre}: la tabla todavia no existe, se saltea")
            continue
        skus = {f[0] for f in filas if f[0]}
        print(f"  {nombre}: {len(skus)} SKU")
        todos |= skus
    return sorted(todos)


def guardar(engine, foto, ahora):
    hoy = ahora.date()
    filas = [
        {"sku": sku, "disponible": cant, "crudo": json.dumps(crudo, ensure_ascii=False),
         "consultado_en": ahora, "fecha": hoy}
        for sku, (cant, crudo) in foto.items()
    ]
    with engine.begin() as con:
        con.exec_driver_sql(DDL)
        # La foto actual se reemplaza entera, el historico solo el dia de hoy.
        con.exec_driver_sql("delete from bronze.fixy_stock")
        con.exec_driver_sql(
            "delete from bronze.fixy_stock_historico where fecha = %(f)s", {"f": hoy})
        for f in filas:
            con.exec_driver_sql(
                """insert into bronze.fixy_stock (sku, disponible, crudo, consultado_en)
                   values (%(sku)s, %(disponible)s, %(crudo)s::jsonb, %(consultado_en)s)""", f)
            con.exec_driver_sql(
                """insert into bronze.fixy_stock_historico
                       (fecha, sku, disponible, crudo, consultado_en)
                   values (%(fecha)s, %(sku)s, %(disponible)s, %(crudo)s::jsonb, %(consultado_en)s)""", f)
    print(f"  Guardado: bronze.fixy_stock y fixy_stock_historico ({len(filas)} SKU, {hoy})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probar", action="store_true", help="Solo el dummy-test.")
    ap.add_argument("--sku", nargs="+", help="Consultar estos SKU e imprimir la respuesta cruda.")
    args = ap.parse_args()

    if not TOKEN:
        print("FIXY_API_TOKEN no esta en el .env: se saltea el stock de FIXY.")
        return

    if args.probar:
        print(json.dumps(llamar("dummy-test.json"), indent=2, ensure_ascii=False))
        return

    if args.sku:
        crudo = llamar("consultarStock", {"skus": args.sku})
        print(json.dumps(crudo, indent=2, ensure_ascii=False))
        print("\nInterpretado:")
        for sku, (cant, _) in normalizar(crudo).items():
            print(f"  {sku:<20} {cant}")
        return

    from conexion import crear_engine
    engine = crear_engine(connect_args={"client_encoding": "utf8"})

    print("=== Stock FIXY ===")
    skus = skus_a_consultar(engine)
    if not skus:
        print("  No hay SKU para consultar todavia: nada que hacer.")
        return

    ahora = datetime.now().astimezone()
    foto = {}
    for i in range(0, len(skus), LOTE):
        lote = skus[i:i + LOTE]
        parte = normalizar(llamar("consultarStock", {"skus": lote}))
        foto.update(parte)
    sin_cantidad = sum(1 for c, _ in foto.values() if c is None)

    print(f"  {len(skus)} SKU consultados, {len(foto)} con respuesta")
    if sin_cantidad:
        print(f"  ATENCION: {sin_cantidad} SKU vinieron sin un campo de cantidad "
              f"reconocible. Ver `crudo` y ajustar CLAVES_CANTIDAD.")

    # SI NO VINO NADA, NO SE BORRA. Con FIXY caido o una respuesta que no se
    # entiende, la foto quedaria vacia y se leeria como "FIXY no tiene nada".
    if not foto:
        print("  Respuesta vacia o no interpretable: NO se toca lo que ya estaba.")
        sys.exit(1)

    guardar(engine, foto, ahora)
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
