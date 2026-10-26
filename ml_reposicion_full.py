"""Recomendacion de reposicion de Full, y si el producto esta RESTRINGIDO.

QUE PREGUNTA CONTESTA

"Se puede mandar stock de este producto a Full, y cuanto conviene mandar?" Es
lo que muestra la pantalla de envios a Full de Mercado Libre: "Elimina este
producto" con un motivo, o una cantidad sugerida con su urgencia.

DE DONDE SALE

    GET /marketplace/fbm/user-products/{user_product_id}/replenishment?country=AR

La documentacion la presenta solo para Global Selling (Fulfillment China), pero
el sondeo del 22/10/2026 (explorar_ml_full.py --inventarios) mostro que con
nuestro token de Argentina CONTESTA. Detalles que costaron:

  - sin `country` da 400 "Required query parameter 'country' is missing";
  - `country=MLA` da 400 "Invalid value for: country". Va `AR`.

Los tres productos que la pantalla de Full decia "elimina este producto" --por
tres motivos distintos-- vinieron igual:

    product.tags                       = ["is_restricted"]
    recommendation.recommendation_type = "NO_REPLENISHMENT_BY_RESTRICTION"

O sea: la API dice SI esta restringido, pero NO el motivo ("prohibido en Full",
"temporalmente no lo recibimos" y "mejora las ventas" salen identicos). El
motivo sigue estando solo en la pantalla.

QUE ESCRIBE (foto actual, se reemplaza entera)

  bronze.ml_reposicion_full  una fila por producto (user_product_id) en Full.

Las respuestas crudas de `tags` y `eligibility_benefits` se guardan como JSON:
guardarlas tal cual evita tener que volver a pedirlas para enterarse.

LOS TIPOS DE RECOMENDACION, ya conocidos (26/10/2026, sobre 4.037 productos):

    NO_REPLENISHMENT                 2.292  no hace falta reponer
    REPLENISH                        1.192  conviene reponer, con cantidad
    NO_REPLENISHMENT_BY_RESTRICTION    551  restringido

Para los REPLENISH la API manda CUANTO mandar, adentro de un objeto:

    "suggested_quantity": {"type": "exact", "value": 140}

Leerlo como si fuera un numero lo convertia en NaN y la columna quedaba vacia
para todos. Ver `_sugerida`.

    python ml_reposicion_full.py              # todo
    python ml_reposicion_full.py --probar 20  # 20 productos, sin guardar
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from conexion import crear_engine
from mercadolibre import HILOS_STOCK, guardar_en_bd, llamar_ml, token_ml

PAIS = "AR"
RESTRINGIDO = "is_restricted"

NUMERICAS = ("cantidad_sugerida", "frecuencia_dias", "stock_total", "stock_minimo",
             "unidades_30d", "facturacion_30d", "semanas_historia", "dias_sin_stock")


def productos_en_full(engine):
    """Un user_product por fila, de las publicaciones que estan en Full.

    Por user_product y no por publicacion: varias publicaciones comparten el
    mismo producto (y el mismo inventario), y la recomendacion es del producto.
    Se pide igual para las pausadas: un producto sin stock y restringido es
    justo el caso que interesa ver, porque es el que NO se va a poder reponer.
    """
    sql = """
        select p.user_product_id,
               max(p.inventory_id) as inventory_id,
               max((select a->>'value_name'
                      from jsonb_array_elements(p.attributes::jsonb) a
                     where a->>'id' = 'SELLER_SKU'
                     limit 1)) as sku
        from bronze.ml_publicaciones p
        where p."shipping.logistic_type" = 'fulfillment'
          and p.user_product_id is not null
          and p.status <> 'closed'
        group by p.user_product_id
    """
    return pd.read_sql(sql, engine)


def _sugerida(reco):
    """El tipo y el valor de `suggested_quantity`, que es un OBJETO.

    ========================================================================
     EL ERROR QUE ESTO ARREGLA
    ========================================================================

    La API no manda un numero sino:

        "suggested_quantity": {"type": "exact", "value": 140}

    Leerlo como numero y pasarlo por `pd.to_numeric(errors="coerce")` --que es
    lo que hacia-- convierte el diccionario en NaN sin quejarse, asi que la
    columna quedaba vacia para los 4.037 productos. La pantalla de Stock Full
    tenia escrito "Mandar N" y nunca tenia con que llenarlo: Mercado Libre
    venia diciendo cuanto mandar de 1.192 productos y lo tirabamos.

    No se vio antes porque el sondeo del 22/10 solo encontro productos
    RESTRINGIDOS, donde la cantidad es null de verdad. El caso con numero del
    que salio la prueba estaba escrito a mano, con la forma equivocada.
    Confirmado contra la API el 26/10/2026 (LF02040: 140, LF02006: 98).

    EL TIPO SE GUARDA, no se descarta. Hoy todos vienen "exact", pero que el
    campo exista quiere decir que hay otros; si aparece uno que no sea exact,
    tomar `value` como si fuera una cantidad firme seria inventar precision.
    Guardado, se ve en la base y se decide con el dato a la vista.
    """
    cruda = reco.get("suggested_quantity")
    if isinstance(cruda, dict):
        return cruda.get("type"), cruda.get("value")
    # Por si algun dia vuelve a ser un numero suelto: no cuesta nada y evita
    # que este arreglo se convierta en el error de maniana.
    if isinstance(cruda, (int, float)):
        return "exact", cruda
    return None, None


def _primero(lista, clave):
    """El valor de `clave` del primer dict de una lista como [{"full": 0}]."""
    for x in lista or []:
        if isinstance(x, dict) and clave in x:
            return x[clave]
    return None


def leer_reposicion(user_product_id, datos):
    """Una fila de `ml_reposicion_full` a partir de la respuesta de la API.

    Todo con `.get`: para un producto sin ventas la API devuelve `sales_totals`
    vacio y la recomendacion con todo en null. Null es "no informo", no cero.
    """
    ident = datos.get("identifiers") or {}
    producto = datos.get("product") or {}
    stock = datos.get("stock") or {}
    ventas = datos.get("sales") or {}
    totales = ventas.get("sales_totals") or {}
    historia = ventas.get("sales_history") or []
    reco = datos.get("recommendation") or {}
    tipo_cantidad, cantidad = _sugerida(reco)
    tags = producto.get("tags") or []

    return {
        "user_product_id": ident.get("user_product_id") or user_product_id,
        "inventory_id": ident.get("inventory_id"),
        "sku_ml": ident.get("seller_sku"),
        "restringido": RESTRINGIDO in tags,
        "tags": json.dumps(tags, ensure_ascii=False),
        "recomendacion": reco.get("recommendation_type"),
        # LA CANTIDAD VIENE ADENTRO DE UN OBJETO, no suelta. Ver `_sugerida`.
        "cantidad_sugerida": cantidad,
        "tipo_cantidad": tipo_cantidad,
        "plazo": reco.get("replenishment_deadline"),
        "frecuencia_dias": reco.get("replenishment_frequency"),
        "urgencia": stock.get("shipping_urgency"),
        "stock_total": stock.get("total_stock"),
        "stock_minimo": stock.get("minimum_distributable_stock"),
        "unidades_30d": _primero(totales.get("units_sold"), "full"),
        "facturacion_30d": _primero(totales.get("gmv"), "full"),
        # Semanas que trae la historia (hoy seis) y cuantos dias de esas
        # semanas estuvo sin stock: sin eso, "no vendio" no se distingue de
        # "no habia para vender".
        "semanas_historia": len(historia),
        "dias_sin_stock": sum((h.get("days_out_of_stock") or 0) for h in historia
                              if isinstance(h, dict)) if historia else None,
        "beneficios": json.dumps(datos.get("eligibility_benefits") or [], ensure_ascii=False),
        "error": None,
    }


def pedir_reposicion(token, productos):
    def pedir(fila):
        up = fila["user_product_id"]
        try:
            datos = llamar_ml(f"/marketplace/fbm/user-products/{up}/replenishment",
                              token, params={"country": PAIS}, pausa=False)
            return leer_reposicion(up, datos)
        except Exception as e:
            # Sin respuesta no se supone nada: `restringido` en null es "no se
            # sabe", que no es lo mismo que "se puede mandar".
            return {"user_product_id": up, "inventory_id": fila["inventory_id"],
                    "restringido": None, "error": str(e)[:160]}

    with ThreadPoolExecutor(max_workers=HILOS_STOCK) as pool:
        filas = list(pool.map(pedir, productos.to_dict("records")))

    df = pd.DataFrame(filas)
    # Numeros como numeros aunque vengan todos en null: si no, la primera
    # corrida (donde todos los restringidos traen la sugerencia en null) crea
    # las columnas como TEXT y el tablero las lee como texto para siempre.
    for col in NUMERICAS:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    if "restringido" in df:
        df["restringido"] = df["restringido"].astype("boolean")
    fallados = int(df["error"].notna().sum()) if "error" in df else 0
    if fallados:
        print(f"  ATENCION: {fallados} productos sin respuesta (quedan con `error`)")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--probar", type=int, default=0,
                        help="Solo N productos, sin guardar nada")
    args = parser.parse_args()

    arranque = time.time()
    engine = crear_engine()
    productos = productos_en_full(engine)
    if productos.empty:
        print("  No hay productos en Full en bronze.ml_publicaciones: corre primero "
              "`mercadolibre.py --catalogo`.")
        return
    if args.probar:
        productos = productos.head(args.probar)
    print(f"=== REPOSICION FULL: {len(productos)} productos ===")

    df = pedir_reposicion(token_ml(), productos)
    # El SKU de la publicacion, por si la API no lo trae.
    df = df.merge(productos[["user_product_id", "sku"]], on="user_product_id", how="left")

    con_dato = df["restringido"].notna()
    print(f"  Restringidos: {int(df.loc[con_dato, 'restringido'].astype(bool).sum())} "
          f"de {int(con_dato.sum())} con respuesta")
    if "recomendacion" in df:
        print("  Recomendaciones:")
        for tipo, n in df["recomendacion"].value_counts(dropna=False).items():
            print(f"    {tipo}: {n}")

    if args.probar:
        print(df.head(20).to_string())
        print(f"  (--probar: no se guardo nada) {time.time() - arranque:.0f}s")
        return

    df["actualizado"] = pd.Timestamp.now(tz="America/Argentina/Buenos_Aires")
    guardar_en_bd(df, "ml_reposicion_full", modo="replace")
    print(f"=== LISTO en {time.time() - arranque:.0f}s ===")


if __name__ == "__main__":
    main()
