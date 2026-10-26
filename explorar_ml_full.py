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

  3. RESTRICCIONES DE ENVIO A FULL (`--inventarios`). Otra pregunta: no "puede
     estar en Full" sino "por que ML no me deja MANDAR stock". En la pantalla
     de envios a Full aparecen leyendas como "temporalmente no lo estamos
     recibiendo", "prohibido para vender en Full" o "no podes enviar stock
     hasta que mejores las ventas", y ninguna esta en `tags`, `shipping.tags`
     ni en el stock de Full (verificado el 21/10/2026 con ZTHJ78898 y
     ZMJF42153). La unica API documentada que habla de "productos con
     restriccion" es la de reposicion de Global Selling
     (/marketplace/fbm/user-products/{id}/replenishment), documentada solo
     para Fulfillment China. Se prueba igual con nuestro token, mas variantes.
     El stock de Full va de CONTROL: sabemos que contesta.

     Solo GET: el alta de un envio (POST /marketplace/fbm/inbounds) no se toca.

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


# Para la seccion 3. `{inv}` es el inventory_id (el "Codigo ML" de la pantalla
# de envios a Full), `{up}` el user_product_id, `{id}` la publicacion.
CANDIDATOS_ENVIO = [
    ("/inventories/{inv}/stock/fulfillment?include_attributes=conditions",
     "CONTROL: stock de Full, documentado"),
    ("/marketplace/inventories/{inv}/stock/fulfillment?include_attributes=conditions&seller_id={uid}",
     "stock de Full, variante marketplace (documentada en Global Selling)"),
    ("/marketplace/fbm/user-products/{up}/replenishment",
     "reposicion: la que habla de 'producto con restriccion' (Global Selling)"),
    # La primera corrida (22/10/2026) contesto 400 "Required query parameter
    # 'country' is missing": el endpoint EXISTE y acepta nuestro token. No se
    # sabe si quiere el pais (AR) o el sitio (MLA), asi que van los dos.
    ("/marketplace/fbm/user-products/{up}/replenishment?country=AR",
     "reposicion con country=AR"),
    ("/marketplace/fbm/user-products/{up}/replenishment?country=MLA",
     "reposicion con country=MLA"),
    ("/marketplace/fbm/user-products/{up}/replenishment?country=AR&seller_id={uid}",
     "reposicion con country=AR y seller_id"),
    ("/fbm/user-products/{up}/replenishment", "reposicion, sin /marketplace"),
    # ── BUSCANDO EL MOTIVO DE LA RESTRICCION (26/10/2026) ──────────────────
    #
    # La API dice SI esta restringido pero no POR QUE. En la pantalla de ML el
    # motivo esta escrito --"solo habilitado para vendedores autorizados",
    # "hasta que mejores las ventas", "prohibido para vender en Full",
    # "temporalmente no lo estamos recibiendo", "excede las dimensiones"-- y
    # son cinco acciones distintas: una se arregla vendiendo mas, otra pidiendo
    # autorizacion, y otra no se arregla nunca.
    #
    # La documentacion de creacion de inbounds nombra errores RESTRICTED e
    # INVALID_SELLER_RESTRICTED, asi que el motivo existe del lado de ML. Lo
    # que falta saber es si alguna consulta lo expone. SOLO GET: crear un
    # inbound de prueba seria mandar mercaderia de verdad.
    ("/marketplace/fbm/user-products/{up}/restrictions?country=AR",
     "MOTIVO: restricciones del producto, adivinado"),
    ("/marketplace/fbm/user-products/{up}?country=AR",
     "MOTIVO: el user product por la via de fbm"),
    ("/marketplace/fbm/user-products/{up}/replenishment?country=AR&include_attributes=restrictions",
     "MOTIVO: la reposicion pidiendo las restricciones"),
    ("/marketplace/fbm/user-products/{up}/replenishment?country=AR&attributes=all",
     "MOTIVO: la reposicion pidiendo todo"),
    ("/marketplace/fbm/restrictions?country=AR&user_product_id={up}",
     "MOTIVO: restricciones como recurso propio"),
    ("/marketplace/fbm/inbounds/restrictions?country=AR&user_product_id={up}",
     "MOTIVO: restricciones del inbound (sin crear nada)"),
    ("/user-products/{up}/restrictions", "MOTIVO: restricciones sin marketplace"),
    ("/inventories/{inv}/stock/fulfillment?include_attributes=restrictions",
     "MOTIVO: el stock pidiendo restricciones"),
    # ───────────────────────────────────────────────────────────────────────
    ("/user-products/{up}", "el user product (documentado)"),
    ("/user-products/{up}/fulfillment", "adivinado"),
    ("/inventories/{inv}/restrictions", "adivinado"),
    ("/inventories/{inv}", "adivinado: el inventario en si"),
    ("/items/{id}/fulfillment", "adivinado"),
]

# Lo que se busca en cualquier respuesta 200. Son las palabras que aparecerian
# si la API explicara alguna de las leyendas de la pantalla.
PISTAS = ("restric", "prohib", "inbound", "eligib", "allowed", "blocked",
          "recommend", "replenish", "receiv", "not_available", "reason")


def pistas_en(datos):
    """Los caminos 'a.b.c = valor' cuyo nombre o valor contiene una pista."""
    encontradas = []

    def recorrer(x, camino):
        if isinstance(x, dict):
            for k, v in x.items():
                recorrer(v, f"{camino}.{k}" if camino else str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x[:20]):
                recorrer(v, f"{camino}[{i}]")
        else:
            texto = f"{camino}={x}".lower()
            if any(p in texto for p in PISTAS):
                encontradas.append(f"{camino} = {str(x)[:100]}")

    recorrer(datos, "")
    return encontradas


def productos_por_inventario(inventarios):
    """inventory_id -> (item_id, user_product_id) desde la foto del catalogo."""
    sql = """
        SELECT inventory_id, id AS item_id, user_product_id
        FROM bronze.ml_publicaciones
        WHERE inventory_id = ANY(%(inv)s)
    """
    df = pd.read_sql(sql, engine, params={"inv": list(inventarios)})
    return {inv: g.iloc[0] for inv, g in df.groupby("inventory_id")}


def restricciones_envio(inventarios, token):
    titulo("3. Restricciones de envio a Full, por producto")
    print("  El primero de cada producto es el CONTROL. Solo GET.\n")
    mapa = productos_por_inventario(inventarios)

    for inv in inventarios:
        fila = mapa.get(inv)
        item = str(fila["item_id"]) if fila is not None else None
        up = fila["user_product_id"] if fila is not None else None
        up = None if up is None or pd.isna(up) else str(up)
        if item and not up:
            # La foto del catalogo puede no tenerlo: se pide a la publicacion.
            st, d = probar(f"/items/{item}?attributes=user_product_id", token)
            if st == 200 and isinstance(d, dict) and d.get("user_product_id"):
                up = str(d["user_product_id"])
        print(f"  --- {inv}  publicacion={item or '(no esta en ml_publicaciones)'}  "
              f"user_product={up or '(sin dato)'}")

        for plantilla, que_es in CANDIDATOS_ENVIO:
            if ("{id}" in plantilla and not item) or ("{up}" in plantilla and not up):
                print(f"  [--] {plantilla}   (falta el id para armarlo)")
                continue
            endpoint = (plantilla.replace("{inv}", inv).replace("{id}", item or "")
                        .replace("{up}", up or "").replace("{uid}", USER_ID or ""))
            status, datos = probar(endpoint, token)
            print(f"  [{status}] {endpoint}\n        -> {que_es}")
            if datos is None:
                continue
            if status == 200:
                print("        " + json.dumps(datos, ensure_ascii=False)[:2500])
                for p in pistas_en(datos)[:15]:
                    print(f"        PISTA: {p}")
            else:
                # El mensaje de error tambien dice cosas: "not found" no es lo
                # mismo que "forbidden for this site" o "invalid scope".
                print("        " + json.dumps(datos, ensure_ascii=False)[:200])
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--muestra", type=int, default=8,
                    help="Cuantos articulos de cada grupo comparar (default 8)")
    ap.add_argument("--inventarios", default="",
                    help="inventory_id separados por coma: corre SOLO la seccion 3")
    args = ap.parse_args()

    token = token_ml()
    print("Sondeo de Mercado Libre. NO ESCRIBE NADA EN LA BASE.")

    inventarios = [i.strip() for i in args.inventarios.split(",") if i.strip()]
    if inventarios:
        restricciones_envio(inventarios, token)
        titulo("Fin")
        print("  Si algun endpoint dio 200 con PISTAS, de ahi sale la columna de")
        print("  'no se puede enviar a Full' para Stock Full y Competencia. Si todos")
        print("  dan 404/403, la API no lo expone para Argentina.")
        return

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
