"""Competencia en las publicaciones de CATALOGO de Mercado Libre.

QUE PREGUNTA CONTESTA

Por cada publicacion de catalogo nuestra: quien esta ganando la caja de compra,
a cuanto, cuanto tendriamos que bajar para ganarla, y que comision nos cobraria
Mercado Libre a ese precio. Con eso el tablero arma el desglose de costos y
dice si se puede competir sin perder plata.

POR QUE SALE DE MERCADO LIBRE Y NO DE FOXIE

Foxie es el repricer: con piso y techo decide el precio. Pero su API publica
(ver foxie.py) solo expone publicaciones, costos y estrategias -- no los
competidores ni el precio para ganar. Ese dato Foxie lo toma de aca mismo:
`/items/{id}/price_to_win` es el endpoint de Mercado Libre que dice que precio
gana la caja. Pedirlo directo es el mismo numero, sin intermediario.

QUE ESCRIBE (todo foto actual, se reemplaza entero en cada corrida)

  bronze.ml_competencia   una fila por publicacion de catalogo activa nuestra:
                          estado de la caja, precio para ganar, ganador,
                          boosts y motivos, comision al precio actual y al de
                          ganar.
  bronze.ml_competidores  una fila por publicacion (de cualquier vendedor) en
                          cada producto de catalogo donde estamos. La nuestra
                          va marcada con `propia`.
  bronze.ml_vendedores    apodo y reputacion de cada vendedor. Esta NO se
                          reemplaza: se agregan los que faltan. Un apodo no
                          cambia de un dia para el otro y pedir miles de
                          usuarios en cada corrida seria tirar llamadas.

CUANTAS LLAMADAS

Medido sobre la base del 30/09/2026: 1.640 publicaciones de catalogo activas
en 1.534 productos. Son ~1.640 `price_to_win` + ~1.534 `products/items` +
hasta dos `listing_prices` por publicacion (se deduplican por precio,
categoria y tipo) + los vendedores nuevos. Ninguno tiene multiget, asi que van
en paralelo con los mismos hilos que el stock Full.

    python ml_competencia.py              # todo
    python ml_competencia.py --probar 20  # solo 20 publicaciones, sin guardar
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from conexion import crear_engine
from mercadolibre import HILOS_STOCK, USER_ID, guardar_en_bd, llamar_ml, token_ml

SITIO = "MLA"


# ---------------------------------------------------------------------------
#  Universo
# ---------------------------------------------------------------------------

def publicaciones_de_catalogo(engine):
    """Nuestras publicaciones de catalogo ACTIVAS, de la foto diaria.

    Solo las activas: una pausada no compite por la caja, y `price_to_win`
    sobre ella devuelve un estado que no dice nada util.

    El SKU sale de `attributes` (SELLER_SKU) por lo mismo que en ml_pulso.py:
    `seller_custom_field` esta vacio casi siempre.
    """
    sql = """
        select p.id as item_id,
               p.catalog_product_id,
               p.price,
               p.listing_type_id,
               p.category_id,
               p."shipping.logistic_type" as logistica,
               (select a->>'value_name'
                  from jsonb_array_elements(p.attributes::jsonb) a
                 where a->>'id' = 'SELLER_SKU'
                 limit 1) as sku
        from bronze.ml_publicaciones p
        where p.catalog_listing
          and p.status = 'active'
          and p.catalog_product_id is not null
    """
    return pd.read_sql(sql, engine)


# ---------------------------------------------------------------------------
#  Lectura de respuestas (sin red: es lo que prueba probar_competencia.py)
# ---------------------------------------------------------------------------

def _boosts_texto(boosts):
    """Los boosts como JSON corto: [{id, status}]. La descripcion larga no."""
    return json.dumps(
        [{"id": b.get("id"), "status": b.get("status")} for b in (boosts or [])
         if isinstance(b, dict)],
        ensure_ascii=False,
    )


def leer_price_to_win(item_id, datos):
    """Una fila de `ml_competencia` a partir de la respuesta de price_to_win v2.

    Todo con `.get`: la API omite campos segun el estado (una publicacion que
    gana no trae `winner` distinto de ella misma, una `listed` no trae precio
    para ganar). Un campo que falta queda en null, que es "no se sabe", y no en
    cero, que se leeria como "gratis".
    """
    ganador = datos.get("winner") or {}
    motivos = datos.get("reason") or []
    if isinstance(motivos, str):
        motivos = [motivos]
    return {
        "item_id": item_id,
        "estado": datos.get("status"),
        "ganando": datos.get("status") == "winning",
        "precio_actual": datos.get("current_price"),
        "precio_para_ganar": datos.get("price_to_win"),
        "consistente": datos.get("consistent"),
        "visibilidad": datos.get("visit_share"),
        "comparten_primer_lugar": datos.get("competitors_sharing_first_place"),
        "ganador_item_id": ganador.get("item_id"),
        "ganador_precio": ganador.get("price"),
        "boosts": _boosts_texto(datos.get("boosts")),
        "ganador_boosts": _boosts_texto(ganador.get("boosts")),
        "motivos": json.dumps(motivos, ensure_ascii=False),
        "error": None,
    }


def leer_items_de_producto(product_id, datos, propio_seller_id):
    """Las filas de `ml_competidores` de un producto de catalogo."""
    filas = []
    for it in datos.get("results") or []:
        envio = it.get("shipping") or {}
        seller = it.get("seller_id")
        filas.append({
            "catalog_product_id": product_id,
            "item_id": it.get("item_id"),
            "seller_id": str(seller) if seller is not None else None,
            "propia": str(seller) == str(propio_seller_id),
            "precio": it.get("price"),
            "precio_original": it.get("original_price"),
            "tipo_publicacion": it.get("listing_type_id"),
            "condicion": it.get("condition"),
            "envio_gratis": envio.get("free_shipping"),
            "logistica": envio.get("logistic_type"),
            "tienda_oficial": it.get("official_store_id"),
        })
    return filas


def leer_comision(datos):
    """(comision_con_iva, porcentaje, fijo) de una respuesta de listing_prices.

    Con `listing_type_id` en la consulta, la API devuelve UN objeto; sin el,
    una lista. Se aceptan las dos formas por las dudas.
    """
    if isinstance(datos, list):
        datos = datos[0] if datos else {}
    detalle = datos.get("sale_fee_details") or {}
    return (
        datos.get("sale_fee_amount"),
        detalle.get("percentage_fee"),
        detalle.get("fixed_fee"),
    )


def leer_vendedor(datos):
    rep = datos.get("seller_reputation") or {}
    trans = rep.get("transactions") or {}
    return {
        "seller_id": str(datos.get("id")),
        "apodo": datos.get("nickname"),
        "reputacion": rep.get("level_id"),
        "medalla": rep.get("power_seller_status"),
        "ventas_totales": trans.get("total"),
        "tienda_oficial": bool(datos.get("official_store")),
    }


# ---------------------------------------------------------------------------
#  Llamadas
# ---------------------------------------------------------------------------

def _en_paralelo(funcion, elementos):
    with ThreadPoolExecutor(max_workers=HILOS_STOCK) as pool:
        return list(pool.map(funcion, elementos))


def pedir_precios_para_ganar(token, item_ids):
    def pedir(item_id):
        try:
            datos = llamar_ml(f"/items/{item_id}/price_to_win", token,
                              params={"version": "v2"}, pausa=False)
            return leer_price_to_win(item_id, datos)
        except Exception as e:
            # Sin respuesta no se supone nada: queda la fila con el error, para
            # que el tablero diga "no se pudo consultar" y no "perdiendo".
            return {"item_id": item_id, "error": str(e)[:160]}

    filas = _en_paralelo(pedir, item_ids)
    fallados = sum(1 for f in filas if f.get("error"))
    if fallados:
        print(f"  ATENCION: {fallados} publicaciones sin price_to_win")
    return pd.DataFrame(filas)


def pedir_competidores(token, product_ids):
    """Todas las publicaciones de cada producto, paginando.

    Se corta en 5 paginas por producto: un producto con mas de 5 paginas de
    vendedores es rarisimo, y para la pregunta de esta pantalla (quien esta
    arriba) alcanza de sobra con los primeros.
    """
    def pedir(product_id):
        filas, offset = [], 0
        try:
            for _ in range(5):
                datos = llamar_ml(f"/products/{product_id}/items", token,
                                  params={"offset": offset}, pausa=False)
                filas += leer_items_de_producto(product_id, datos, USER_ID)
                pag = datos.get("paging") or {}
                limite = pag.get("limit") or len(datos.get("results") or [])
                offset += limite
                if not limite or offset >= (pag.get("total") or 0):
                    break
        except Exception as e:
            print(f"    producto {product_id}: {str(e)[:90]}")
        return filas

    listas = _en_paralelo(pedir, product_ids)
    return pd.DataFrame([f for l in listas for f in l])


def pedir_comisiones(token, consultas):
    """Comision de ML para cada (precio, tipo de publicacion, categoria).

    Se deduplica antes de pedir: muchas publicaciones comparten categoria,
    tipo y precio, y la respuesta depende solo de esos tres.
    """
    unicas = sorted(set(consultas))

    def pedir(clave):
        precio, tipo, categoria = clave
        try:
            datos = llamar_ml(f"/sites/{SITIO}/listing_prices", token,
                              params={"price": precio, "listing_type_id": tipo,
                                      "category_id": categoria}, pausa=False)
            return clave, leer_comision(datos)
        except Exception as e:
            print(f"    comision {clave}: {str(e)[:90]}")
            return clave, (None, None, None)

    return dict(_en_paralelo(pedir, unicas))


def vendedores_conocidos(engine):
    try:
        return set(pd.read_sql("select seller_id from bronze.ml_vendedores",
                               engine)["seller_id"].astype(str))
    except Exception:
        # Primera corrida: la tabla todavia no existe y la crea el to_sql.
        return set()


def pedir_vendedores(token, seller_ids):
    def pedir(seller_id):
        try:
            return leer_vendedor(llamar_ml(f"/users/{seller_id}", token, pausa=False))
        except Exception as e:
            print(f"    vendedor {seller_id}: {str(e)[:90]}")
            return None

    return pd.DataFrame([f for f in _en_paralelo(pedir, seller_ids) if f])


# ---------------------------------------------------------------------------
#  Armado
# ---------------------------------------------------------------------------

def _redondear(precio):
    return None if precio is None or pd.isna(precio) else round(float(precio), 2)


def agregar_comisiones(comp, pubs, token):
    """Suma a `comp` la comision al precio actual y al precio para ganar."""
    base = comp.merge(pubs[["item_id", "listing_type_id", "category_id", "price"]],
                      on="item_id", how="left")
    # El precio actual de price_to_win es el que ve el comprador (con la
    # promocion aplicada). Si no vino, el de la publicacion.
    base["precio_actual"] = base["precio_actual"].fillna(base["price"])

    consultas = []
    for _, f in base.iterrows():
        for col in ("precio_actual", "precio_para_ganar"):
            p = _redondear(f.get(col))
            if p and f.get("listing_type_id") and f.get("category_id"):
                consultas.append((p, f["listing_type_id"], f["category_id"]))

    print(f"  Comisiones: {len(set(consultas))} consultas distintas")
    tabla = pedir_comisiones(token, consultas)

    def comision(f, col):
        clave = (_redondear(f.get(col)), f.get("listing_type_id"), f.get("category_id"))
        return tabla.get(clave, (None, None, None))

    actual = base.apply(lambda f: comision(f, "precio_actual"), axis=1)
    ganar = base.apply(lambda f: comision(f, "precio_para_ganar"), axis=1)
    base["comision_actual"] = [c[0] for c in actual]
    base["comision_pct"] = [c[1] for c in actual]
    base["comision_fija"] = [c[2] for c in actual]
    base["comision_ganar"] = [c[0] for c in ganar]
    return base.drop(columns=["price"])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--probar", type=int, default=0,
                        help="Solo N publicaciones, sin guardar nada")
    args = parser.parse_args()

    arranque = time.time()
    engine = crear_engine()
    pubs = publicaciones_de_catalogo(engine)
    if pubs.empty:
        print("  No hay publicaciones de catalogo activas en bronze.ml_publicaciones: "
              "corre primero `mercadolibre.py --catalogo`.")
        return
    if args.probar:
        pubs = pubs.head(args.probar)
    print(f"=== COMPETENCIA ML: {len(pubs)} publicaciones de catalogo, "
          f"{pubs['catalog_product_id'].nunique()} productos ===")

    token = token_ml()

    print("  Precio para ganar...")
    comp = pedir_precios_para_ganar(token, pubs["item_id"].tolist())
    comp = comp.merge(pubs[["item_id", "sku", "catalog_product_id", "logistica"]],
                      on="item_id", how="left")
    comp = agregar_comisiones(comp, pubs, token)

    print("  Competidores por producto...")
    rivales = pedir_competidores(token, pubs["catalog_product_id"].unique().tolist())
    print(f"  {len(rivales)} publicaciones en {rivales['catalog_product_id'].nunique() if not rivales.empty else 0} productos")

    conocidos = vendedores_conocidos(engine)
    nuevos = sorted(set(rivales["seller_id"].dropna()) - conocidos) if not rivales.empty else []
    print(f"  Vendedores nuevos: {len(nuevos)}")
    vendedores = pedir_vendedores(token, nuevos)

    if args.probar:
        print(comp.head(10).to_string())
        print(rivales.head(10).to_string())
        print(vendedores.head(10).to_string())
        print(f"  (--probar: no se guardo nada) {time.time() - arranque:.0f}s")
        return

    comp["actualizado"] = pd.Timestamp.now(tz="America/Argentina/Buenos_Aires")
    guardar_en_bd(comp, "ml_competencia", modo="replace")
    guardar_en_bd(rivales, "ml_competidores", modo="replace")
    if not vendedores.empty:
        guardar_en_bd(vendedores, "ml_vendedores", modo="append")
    print(f"=== LISTO en {time.time() - arranque:.0f}s ===")


if __name__ == "__main__":
    main()
