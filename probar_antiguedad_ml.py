"""Sondeo: por donde puede venir la ANTIGUEDAD del stock en Full.

QUE SE SABE HASTA ACA (corrida del 31/08/2026)

  GET /inventories/{id}/stock/fulfillment          -> 200, pero NO trae antiguedad
      total, available_quantity, not_available_quantity,
      not_available_detail (notSupported, withdrawal, damaged),
      external_references. Ninguna fecha.

  GET /stock/fulfillment/operations/search         -> 400 Bad Request
  GET /marketplace/stock/fulfillment/operations/search -> 403 Forbidden

EL 400 ES LA PISTA BUENA. No es 404: la ruta existe y esta rechazando los
parametros. El 403 de /marketplace/ es otra cosa -- esa familia es de Global
Selling y esta aplicacion no tiene ese permiso, asi que por ahi no se entra.

LO QUE FALTABA EN LA VERSION ANTERIOR DE ESTE SCRIPT: usaba el helper comun,
que hace `raise_for_status()` y tira el cuerpo de la respuesta. Justamente ahi
Mercado Libre escribe QUE parametro le falta o esta mal. Ahora se llama con
requests directo y se imprime el cuerpo pase lo que pase.

Se prueba una matriz de variantes porque la documentacion de este endpoint esta
incompleta y no coincide entre paises: con y sin seller_id, fechas como fecha y
como timestamp, y los nombres alternativos que aparecen en la doc.

NO ESCRIBE NADA EN LA BASE.

    python probar_antiguedad_ml.py            # 3 inventarios con stock
    python probar_antiguedad_ml.py LSGZ75310  # uno puntual
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

from conexion import crear_engine
from mercadolibre import renovar_access_token

load_dotenv()
USER_ID = os.getenv("ML_USER_ID")
BASE = "https://api.mercadolibre.com"

HOY = date.today()
DESDE = HOY - timedelta(days=59)          # 60 dias contando los dos extremos
HOY_TS = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
DESDE_TS = (datetime.now(timezone.utc) - timedelta(days=59)).replace(
    microsecond=0).isoformat()


def llamar(ruta, params, token):
    """Llama y MUESTRA lo que conteste, con cuerpo y todo. Devuelve el json si
    salio 200, o None."""
    url = BASE + ruta
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    print(f"\n  {r.status_code}  {ruta}")
    print(f"     params: {params}")
    cuerpo = r.text[:900]
    print(f"     cuerpo: {cuerpo}")
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return None
    return None


def variantes(inv_id):
    """Las combinaciones a probar, de la mas probable a la menos.

    El orden no es capricho: la doc menciona `seller_id` como parametro del
    endpoint, y un 400 con date_from/date_to ya puestos huele a que falta
    justamente ese. Las de fecha con hora van despues porque varios endpoints
    de ML aceptan las dos formas y algunos exigen la larga.
    """
    return [
        ("/stock/fulfillment/operations/search",
         {"seller_id": USER_ID, "inventory_id": inv_id,
          "date_from": DESDE.isoformat(), "date_to": HOY.isoformat(), "limit": 50}),

        ("/stock/fulfillment/operations/search",
         {"seller_id": USER_ID, "inventory_id": inv_id,
          "date_from": DESDE_TS, "date_to": HOY_TS, "limit": 50}),

        ("/stock/fulfillment/operations/search",
         {"seller_id": USER_ID, "inventory_id": inv_id, "limit": 50}),

        ("/stock/fulfillment/operations/search",
         {"seller_id": USER_ID, "limit": 50}),

        # Sin fechas y sin seller: si contesta, el 400 era por un parametro
        # obligatorio y no por la ruta.
        ("/stock/fulfillment/operations/search",
         {"inventory_id": inv_id, "limit": 50}),

        # Variantes de ruta que aparecen en la doc de otros paises.
        (f"/inventories/{inv_id}/stock/fulfillment/operations",
         {"date_from": DESDE.isoformat(), "date_to": HOY.isoformat(), "limit": 50}),

        (f"/inventories/{inv_id}/stock/fulfillment/operations/search",
         {"date_from": DESDE.isoformat(), "date_to": HOY.isoformat(), "limit": 50}),

        # El stock por deposito: en algunos paises trae un desglose mas fino que
        # el resumen, y es donde podria colarse la antiguedad.
        (f"/inventories/{inv_id}/stock/fulfillment/warehouses", {}),
    ]


def inventarios_de_prueba(n=3):
    engine = crear_engine()
    df = pd.read_sql(
        """
        select inventory_id, available_quantity
        from bronze.ml_stock_full
        where available_quantity > 0
        order by available_quantity desc
        limit %(n)s
        """,
        engine, params={"n": n},
    )
    return list(df.itertuples(index=False))


def sondear(inv_id, token):
    print("=" * 74)
    print(f"INVENTARIO {inv_id}")
    print("=" * 74)

    r = llamar(f"/inventories/{inv_id}/stock/fulfillment", {}, token)
    if r:
        print("     (el que ya usamos, para tener al lado)")

    exitos = []
    for ruta, params in variantes(inv_id):
        datos = llamar(ruta, params, token)
        if datos is None:
            continue
        exitos.append((ruta, params))
        print("     >>> CONTESTO 200. Contenido:")
        print(json.dumps(datos, indent=2, ensure_ascii=False)[:2000])
        filas = datos.get("results") if isinstance(datos, dict) else datos
        if isinstance(filas, list) and filas:
            tipos = sorted({f.get("type") for f in filas if isinstance(f, dict)})
            print(f"     >>> tipos de operacion: {tipos}")
    return exitos


def main():
    token = renovar_access_token()
    print("ML User ID:", USER_ID)
    print(f"Ventana: {DESDE} a {HOY}")

    objetivos = sys.argv[1:] or [f.inventory_id for f in inventarios_de_prueba()]

    todos = []
    for inv_id in objetivos:
        todos += sondear(inv_id, token)

    print("\n" + "=" * 74)
    print("RESUMEN")
    print("=" * 74)
    if todos:
        print("Contestaron 200:")
        for ruta, params in todos:
            print(f"  {ruta}  con {sorted(params)}")
    else:
        print("Ninguna variante contesto 200.")
        print("Mirar los CUERPOS de los 400 de arriba: ahi Mercado Libre dice")
        print("que parametro falta o esta mal. Con ese texto se arma la variante")
        print("que si anda, o se confirma que el endpoint no esta habilitado")
        print("para esta aplicacion.")


if __name__ == "__main__":
    main()
