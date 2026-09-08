"""Sondeo: la API de Mercado Libre, que dice si un articulo es APTO para Full.

NO ESCRIBE NADA. Solo pregunta e imprime.

POR QUE
-------
En la tabla de Articulos se quiere una columna que diga si el articulo puede ir
a Full o no. Lo que ya traemos NO lo contesta:

  shipping.logistic_type   dice si YA esta en Full (7.626 publicaciones), no si
                           podria estar
  tags                     tiene catalog_listing_eligible, supermarket_eligible,
                           cart_eligible... ninguno de fulfillment
  shipping.tags            tiene fbm_in_process (42, en proceso de pasar a Full),
                           fbm_me2_frozen (1) e is_flammable (418, que es
                           EXCLUYENTE: los inflamables no entran a Full)

O sea que hay señales indirectas pero no un campo que diga "apto". Este script
averigua si existe, de dos formas:

  1. COMPARANDO. Trae la publicacion entera de articulos que estan en Full y de
     otros que no, y lista que campos aparecen en un grupo y no en el otro. Es
     el camino que no depende de adivinar nombres.

  2. PROBANDO ENDPOINTS. Mercado Libre tiene un patron para elegibilidad
     --/items/{id}/catalog_listing_eligibility existe y funciona-- asi que se
     prueban las variantes analogas para fulfillment y se informa que contesta
     cada una. El de catalogo va como CONTROL: si ese da 200 y los otros 404,
     la respuesta es que el endpoint no existe, no que el token no alcanza.

Se corre a mano desde Actions (workflow "Sondeo Meli Full").
"""

import argparse
import json
import os
from collections import Counter

import pandas as pd
import requests
from dotenv import load_dotenv

from conexion import crear_engine
from mercadolibre import token_ml

load_dotenv()

USER_ID = os.getenv("ML_USER_ID")
engine = crear_engine()

# Endpoints candidatos. El primero es el CONTROL: sabemos que existe.
CANDIDATOS = [
    ("/items/{id}/catalog_listing_eligibility", "CONTROL: elegibilidad de catalogo"),
    ("/items/{id}/fulfillment_eligibility", "misma forma, para fulfillment"),
    ("/items/{id}/shipping_options", "opciones de envio del articulo"),
    ("/items/{id}/restrictions", "restricciones del articulo"),
    ("/users/{uid}/items/search?tags=fbm_eligible", "tag de elegibilidad"),
    ("/users/{uid}/items/search?logistic_type=fulfillment", "los que YA estan"),
]

# Campos que no aportan a la comparacion y ensucian la salida: cambian en cada
# publicacion por definicion.
RUIDO = {
    "id", "title", "permalink", "thumbnail", "secure_thumbnail", "price",
    "base_price", "original_price", "date_created", "last_updated",
    "start_time", "stop_time", "pictures", "descriptions", "attributes",
    "variations", "sold_quantity", "available_quantity", "initial_quantity",
    "health", "seller_custom_field", "inventory_id", "thumbnail_id",
}


def probar(endpoint, token):
    """Una llamada cruda. Devuelve (status, json o None). NUNCA levanta."""
    try:
        r = requests.get(
            "https://api.mercadolibre.com" + endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    except Exception as e:
        return None, {"error": str(e)[:120]}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, None


def titulo(t):
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


def aplanar(d, prefijo=""):
    """Las claves de un dict anidado, como 'shipping.logistic_type'."""
    claves = set()
    for k, v in (d or {}).items():
        camino = f"{prefijo}{k}"
        if camino.split(".")[-1] in RUIDO:
            continue
        claves.add(camino)
        if isinstance(v, dict):
            claves |= aplanar(v, f"{camino}.")
    return claves


def muestras(n):
    """n publicaciones en Full y n que no, con su SKU."""
    sql = """
        SELECT id,
               "shipping.logistic_type" AS tipo,
               (SELECT a->>'value_name' FROM jsonb_array_elements(attributes::jsonb) a
                 WHERE a->>'id' = 'SELLER_SKU' LIMIT 1) AS sku
        FROM bronze.ml_publicaciones
        WHERE "shipping.logistic_type" = %(t)s
        LIMIT %(n)s
    """
    en_full = pd.read_sql(sql, engine, params={"t": "fulfillment", "n": n})
    fuera = pd.read_sql(sql, engine, params={"t": "cross_docking", "n": n})
    return en_full, fuera


def comparar(en_full, fuera, token):
    titulo("1. Que campos tienen los que ESTAN en Full y no tienen los otros")

    def claves_de(df, etiqueta):
        vistas = Counter()
        ejemplo = None
        for item_id in df["id"]:
            status, datos = probar(f"/items/{item_id}", token)
            if status != 200 or not isinstance(datos, dict):
                continue
            ks = aplanar(datos)
            vistas.update(ks)
            if ejemplo is None:
                ejemplo = datos
        print(f"  {etiqueta}: {len(df)} articulos consultados, {len(vistas)} campos distintos")
        return vistas, ejemplo

    ks_full, ej_full = claves_de(en_full, "En Full")
    ks_fuera, _ = claves_de(fuera, "Fuera de Full")

    solo_full = sorted(set(ks_full) - set(ks_fuera))
    solo_fuera = sorted(set(ks_fuera) - set(ks_full))

    print("\n  --- Solo en los que ESTAN en Full ---")
    for k in solo_full or ["(ninguno)"]:
        print(f"    {k}")
    print("\n  --- Solo en los que NO estan ---")
    for k in solo_fuera or ["(ninguno)"]:
        print(f"    {k}")

    # Lo de shipping, entero: es donde vive todo lo de logistica.
    if ej_full and isinstance(ej_full.get("shipping"), dict):
        print("\n  --- `shipping` completo de un articulo en Full ---")
        print("  " + json.dumps(ej_full["shipping"], ensure_ascii=False, indent=2)
              .replace("\n", "\n  "))


def endpoints(en_full, fuera, token):
    titulo("2. Endpoints candidatos")
    print("  El primero es el CONTROL: si ese da 200 y los demas 404, la")
    print("  respuesta es que el endpoint no existe --no que falte permiso--.\n")

    item_full = str(en_full["id"].iloc[0]) if len(en_full) else None
    item_fuera = str(fuera["id"].iloc[0]) if len(fuera) else None

    for plantilla, que_es in CANDIDATOS:
        for etiqueta, item in (("en Full", item_full), ("fuera", item_fuera)):
            if "{id}" in plantilla and not item:
                continue
            endpoint = plantilla.replace("{id}", item or "").replace("{uid}", USER_ID or "")
            status, datos = probar(endpoint, token)
            linea = f"  [{status}] {endpoint}"
            if "{id}" in plantilla:
                linea += f"   ({etiqueta})"
            print(linea)
            if status == 200 and datos is not None:
                recorte = json.dumps(datos, ensure_ascii=False)[:220]
                print(f"        {recorte}")
            if "{id}" not in plantilla:
                break
        print(f"        -> {que_es}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--muestra", type=int, default=8,
                    help="Cuantos articulos de cada grupo comparar (default 8)")
    args = ap.parse_args()

    token = token_ml()
    print("Sondeo de Mercado Libre. NO ESCRIBE NADA EN LA BASE.")

    en_full, fuera = muestras(args.muestra)
    print(f"Muestra: {len(en_full)} en Full, {len(fuera)} fuera.")
    if en_full.empty:
        raise SystemExit("Sin publicaciones en Full en bronze.ml_publicaciones.")

    comparar(en_full, fuera, token)
    endpoints(en_full, fuera, token)

    titulo("Fin")
    print("  Si aparecio un campo o endpoint de elegibilidad, la columna de")
    print("  Articulos sale de ahi. Si no, hay que aproximarla con lo que ya")
    print("  tenemos: logistic_type + fbm_in_process + is_flammable.")


if __name__ == "__main__":
    main()
