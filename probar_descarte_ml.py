"""Sondeo: ¿Mercado Libre dice que stock de Full esta por DESCARTARSE?

LA PREGUNTA

El responsable de Mercado Libre revisa a mano los articulos que no venden y
llevan mucho en Full, para retirarlos antes de que Mercado Libre los descarte.
Para que eso sea una alerta del tablero hace falta saber que dice la API.

QUE SE SABE HASTA ACA

  GET /inventories/{id}/stock/fulfillment
      -> total, available_quantity, not_available_quantity,
         not_available_detail (notSupported, withdrawal, damaged, ...).
         Ninguna fecha (probar_antiguedad_ml.py, 31/08/2026).

  La antiguedad la reconstruye ml_antiguedad.py desde el libro de movimientos.

QUE FALTA PROBAR, Y ES LO QUE HACE ESTE SCRIPT

  `include_attributes=conditions` sobre el mismo endpoint. La doc dice que
  devuelve "informacion adicional de las unidades no disponibles para la
  venta", sin decir cual. Si en algun pais esconde antiguedad o descarte, es
  ahi.

Se prueba sobre dos grupos, porque la respuesta puede depender del caso:

  1. los inventarios con mas unidades NO disponibles (bronze.ml_stock_full),
     que es donde `conditions` tiene algo que contar;
  2. los inventarios con mas unidades de MAS DE 120 DIAS segun la ultima foto
     de bronze.ml_stock_antiguedad, que son los candidatos a descarte.

Y al final se listan todas las claves que aparecieron en las respuestas, y
cuales huelen a fecha, antiguedad o descarte, para no depender de leer JSON a
ojo.

NO ESCRIBE NADA EN LA BASE.

    python probar_descarte_ml.py              # 4 de cada grupo
    python probar_descarte_ml.py --muestra 8
    python probar_descarte_ml.py LSGZ75310    # uno puntual
"""

import argparse
import json
import os

import pandas as pd
import requests
from dotenv import load_dotenv

from conexion import crear_engine
from mercadolibre import token_ml

load_dotenv()
USER_ID = os.getenv("ML_USER_ID")
BASE = "https://api.mercadolibre.com"

# Claves que, si aparecen, contestan la pregunta. Se buscan como substring y
# en minusculas: la API mezcla snake_case y camelCase entre paises.
SOSPECHOSAS = ("age", "aging", "antig", "days", "dias", "date", "fecha",
               "discard", "descart", "dispos", "destroy", "expir", "venc",
               "long_term", "storage", "almacen")


def llamar(ruta, params, token):
    """Llama y MUESTRA lo que conteste, con cuerpo y todo. Devuelve el json si
    salio 200, o None. Requests directo y no el helper comun, porque el helper
    tira el cuerpo de los errores y ahi Mercado Libre dice que esta mal."""
    r = requests.get(BASE + ruta, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    print(f"\n  {r.status_code}  {ruta}  {params or ''}")
    if r.status_code != 200:
        print(f"     cuerpo: {r.text[:600]}")
        return None
    try:
        datos = r.json()
    except ValueError:
        print(f"     cuerpo (no es json): {r.text[:600]}")
        return None
    print(json.dumps(datos, indent=2, ensure_ascii=False)[:2500])
    return datos


def claves(obj, prefijo=""):
    """Todas las rutas de claves de un json, tipo `not_available_detail[].status`."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            ruta = f"{prefijo}.{k}" if prefijo else k
            yield ruta
            yield from claves(v, ruta)
    elif isinstance(obj, list):
        for v in obj:
            yield from claves(v, f"{prefijo}[]")


def variantes(inv_id):
    """De la mas probable a la menos. Las dos primeras son las documentadas;
    el resto son nombres que aparecen en la doc de otros paises y cuestan una
    llamada cada una."""
    return [
        (f"/inventories/{inv_id}/stock/fulfillment", {}),
        (f"/inventories/{inv_id}/stock/fulfillment",
         {"include_attributes": "conditions"}),
        (f"/inventories/{inv_id}/stock/fulfillment",
         {"include_attributes": "conditions,aging"}),
        (f"/inventories/{inv_id}/stock/fulfillment/conditions", {}),
        (f"/inventories/{inv_id}/stock/fulfillment/aging", {}),
    ]


def inventarios_de_prueba(n):
    engine = crear_engine()
    no_disponibles = pd.read_sql(
        """
        select inventory_id
        from bronze.ml_stock_full
        where not_available_quantity > 0
        order by not_available_quantity desc
        limit %(n)s
        """,
        engine, params={"n": n},
    )
    viejos = pd.read_sql(
        """
        select inventory_id
        from bronze.ml_stock_antiguedad
        where fecha = (select max(fecha) from bronze.ml_stock_antiguedad)
          and u_mas_120 > 0
        order by u_mas_120 desc
        limit %(n)s
        """,
        engine, params={"n": n},
    )
    print(f"Con unidades no disponibles: {list(no_disponibles.inventory_id)}")
    print(f"Con mas unidades de +120 dias: {list(viejos.inventory_id)}")
    # Sin repetir y en orden: si un inventario esta en los dos grupos, una vez.
    return list(dict.fromkeys([*no_disponibles.inventory_id, *viejos.inventory_id]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("inventarios", nargs="*")
    p.add_argument("--muestra", type=int, default=4)
    args = p.parse_args()

    token = token_ml()
    print("ML User ID:", USER_ID)
    objetivos = args.inventarios or inventarios_de_prueba(args.muestra)

    contestaron = {}   # (ruta sin inventario, params) -> set de claves
    estados = {}       # status de not_available_detail -> unidades
    for inv_id in objetivos:
        print("\n" + "=" * 74)
        print(f"INVENTARIO {inv_id}")
        print("=" * 74)
        for ruta, params in variantes(inv_id):
            datos = llamar(ruta, params, token)
            if datos is None:
                continue
            if not params:
                for d in datos.get("not_available_detail") or []:
                    estados[d.get("status")] = estados.get(d.get("status"), 0) + (d.get("quantity") or 0)
            clave = (ruta.replace(inv_id, "{id}"), json.dumps(params))
            contestaron.setdefault(clave, set()).update(claves(datos))

    print("\n" + "=" * 74)
    print("RESUMEN")
    print("=" * 74)
    print("Motivos de no disponible vistos (status: unidades):")
    for st, q in sorted(estados.items(), key=lambda x: -x[1]):
        print(f"    {st}: {q}")
    if not contestaron:
        print("Ninguna variante contesto 200.")
        return
    for (ruta, params), cs in contestaron.items():
        print(f"\n{ruta}  {params}")
        for c in sorted(cs):
            marca = "  <-- ?" if any(s in c.lower() for s in SOSPECHOSAS) else ""
            print(f"    {c}{marca}")


if __name__ == "__main__":
    main()
