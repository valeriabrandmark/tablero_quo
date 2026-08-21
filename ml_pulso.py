"""Pulso de estado de las publicaciones de Mercado Libre.

QUE PROBLEMA RESUELVE

Mercado Libre SI dice cuando una publicacion quebro stock: la pausa sola y le
pone `sub_status = ["out_of_stock"]`. Lo que no dice es DESDE CUANDO, ni cuantas
horas estuvo asi -- y eso es justo lo que hace falta para medir elasticidad de
precios, porque un articulo que quebro el martes no vendio menos por caro, sino
porque no estaba a la venta.

`bronze.ml_publicaciones` tampoco sirve para eso: se escribe con
`modo="replace"`, o sea que sabe como esta la publicacion HOY y pisa lo de ayer.

Este script mira el estado cada vez que corre el orquestador (cada 2 horas) y
guarda TRAMOS: "el item X estuvo vendible desde el lunes 09:00 hasta el jueves
14:00". Ver esquema.py para por que tramos y no fotos.

    python ml_pulso.py                # estado + precio de todo el catalogo
    python ml_pulso.py --solo-buybox  # solo la caja de compra del experimento

POR QUE ES BARATO
Usa el multiget `/items?ids=` de a 20, o sea ~430 llamadas para las 8.509
publicaciones -- el mismo camino que ya usa `mercadolibre.py --catalogo` para
las publicaciones, y NO el de `/inventories` uno por uno (3.800 llamadas). Con
`attributes=` ademas pide solo los seis campos que mira, asi que cada respuesta
es una fraccion de la del catalogo completo. Medido: poco mas de un minuto.

POR QUE `available_quantity` DE LA PUBLICACION SI SIRVE ACA
En `extraer_stock_full()` esta documentado que no se puede usar, y es cierto:
varias publicaciones comparten `inventory_id`, asi que SUMAR sus unidades cuenta
la misma varias veces (19.211 contra 10.577 reales). Pero eso vale para sumar.
La pregunta de este script es booleana -- "esta publicacion se puede comprar
ahora?" -- y para eso el dato de la publicacion es exactamente el correcto: es
el que ve el comprador.
"""

import argparse
import datetime
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from sqlalchemy.types import Boolean, Float, Integer, Text

from esquema import asegurar_tablas, crear_engine
from mercadolibre import HILOS_STOCK, llamar_ml, renovar_access_token

# Los unicos campos que mira este script. Pedirlos por nombre en vez de traer la
# publicacion entera es lo que hace que el pulso pueda correr cada 2 horas: el
# catalogo completo trae descripciones, fotos y atributos que no se usan.
CAMPOS = ",".join([
    "id", "status", "sub_status", "available_quantity", "price",
])


def ahora():
    """Momento del pulso, en hora argentina.

    Con la del sistema, un pulso despues de las 21 quedaria fechado al dia
    siguiente en UTC -- el mismo motivo por el que `guardar_foto_stock` toma la
    fecha asi. Aca importa mas todavia, porque de estos timestamps salen las
    horas que le tocan a cada semana del experimento.
    """
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))


# ---------------------------------------------------------------------------
#  Clasificacion del estado
# ---------------------------------------------------------------------------

def clasificar(status, sub_status, cantidad):
    """(vendible, motivo) de una publicacion.

    EL ORDEN DE LOS MOTIVOS NO ES ARBITRARIO. Hay 115 publicaciones con
    `["out_of_stock", "paused_by_seller"]` a la vez, y hay que elegir cual de
    los dos se reporta. Se elige `sin_stock`, porque es lo que el experimento
    esta midiendo: que la hayamos pausado ademas no cambia que no habia
    mercaderia. Al reves -- contandola como "pausada por nosotros" -- los
    quiebres quedarian subestimados justo en los articulos que mas rotan, que
    son los que uno pausa a mano cuando se queda sin stock.
    """
    sub = sub_status or []
    if isinstance(sub, str):
        sub = [sub]

    if "out_of_stock" in sub:
        return False, "sin_stock"
    if status == "closed":
        return False, "cerrada"
    if status == "under_review":
        return False, "en_revision"
    if "paused_by_seller" in sub:
        return False, "pausado_por_nosotros"
    if status != "active":
        return False, "pausada"
    # Activa pero en cero: pasa en la ventana entre la ultima venta y el momento
    # en que ML pausa la publicacion. Es un quiebre igual, aunque todavia no
    # tenga el sub_status puesto.
    if not cantidad or cantidad <= 0:
        return False, "sin_stock"
    return True, "ok"


# ---------------------------------------------------------------------------
#  Extraccion
# ---------------------------------------------------------------------------

def ids_y_skus(engine):
    """El universo a pulsar, sacado de `bronze.ml_publicaciones`.

    POR QUE DE LA BASE Y NO DEL SCAN DE LA API
    El scan (`/users/{id}/items/search`) son ~86 llamadas paginadas mas, en cada
    pulso, para volver a enterarse de una lista que cambia una vez por dia. El
    catalogo ya la deja escrita todas las mananas.

    Lo que se paga: una publicacion dada de alta hoy no entra al pulso hasta el
    catalogo de manana. Para el experimento no molesta -- el conjunto de SKU se
    congela cuando se hace la asignacion -- pero conviene saberlo antes de usar
    esta tabla para otra cosa.

    El SKU tambien sale de aca, y por el mismo motivo por el que lo hace
    `queries-stock-full`: `seller_custom_field` esta vacio en 7.459 de 7.559
    publicaciones y el SKU real vive adentro del array `attributes`, en
    `SELLER_SKU`. Traer ese array en cada pulso seria traer la publicacion
    entera; el SKU de un item no cambia, asi que alcanza con leerlo de la foto
    diaria.
    """
    sql = """
        select p.id as item_id,
               p.inventory_id,
               (select a->>'value_name'
                  from jsonb_array_elements(p.attributes::jsonb) a
                 where a->>'id' = 'SELLER_SKU'
                 limit 1) as sku
        from bronze.ml_publicaciones p
        where p.id is not null
    """
    return pd.read_sql(sql, engine)


def pedir_estado(access_token, ids):
    """El estado de todas las publicaciones, en lotes de 20 y en paralelo."""
    lotes = [ids[i:i + 20] for i in range(0, len(ids), 20)]
    print(f"  {len(lotes)} lotes de hasta 20 (de a {HILOS_STOCK})")

    def pedir_lote(lote):
        try:
            return llamar_ml("/items", access_token,
                             params={"ids": ",".join(lote), "attributes": CAMPOS},
                             pausa=False)
        except Exception as e:
            # Un lote que no vuelve NO se puede tratar como "estas 20 estan
            # quebradas": seria inventar un quiebre. Se descarta el lote y se
            # cuenta, y esos items simplemente no tienen observacion en este
            # pulso -- su `visto_hasta` no avanza, que es exactamente lo que hay
            # que decir cuando no se miro.
            print(f"    lote fallado: {str(e)[:90]}")
            return []

    with ThreadPoolExecutor(max_workers=HILOS_STOCK) as pool:
        respuestas = list(pool.map(pedir_lote, lotes))

    filas, sin_detalle = [], 0
    for datos in respuestas:
        for item in datos:
            if item.get("code") != 200:
                sin_detalle += 1
                continue
            cuerpo = item.get("body") or {}
            vendible, motivo = clasificar(
                cuerpo.get("status"),
                cuerpo.get("sub_status"),
                cuerpo.get("available_quantity"),
            )
            sub = cuerpo.get("sub_status") or []
            filas.append({
                "item_id": cuerpo.get("id"),
                "status": cuerpo.get("status"),
                # Como texto y ordenado: `["a","b"]` y `["b","a"]` son el mismo
                # estado, y comparados como texto crudo abririan un tramo nuevo
                # cada vez que ML devuelve la lista en otro orden.
                "sub_status": ",".join(sorted(sub)) if sub else "",
                "vendible": vendible,
                "motivo": motivo,
                "unidades": cuerpo.get("available_quantity"),
                "precio": cuerpo.get("price"),
            })

    if sin_detalle:
        print(f"  ATENCION: {sin_detalle} publicaciones sin detalle (la API no las devolvio)")
    return pd.DataFrame(filas)


def pedir_buybox(access_token, item_ids):
    """Quien gana la caja de compra, una llamada por publicacion de catalogo.

    Se pide SOLO para las publicaciones del experimento, no para las 4.047 de
    catalogo: `price_to_win` no tiene multiget, asi que el costo es lineal y no
    tiene sentido pagarlo por articulos que no se estan midiendo.
    """
    def pedir(item_id):
        try:
            datos = llamar_ml(f"/items/{item_id}/price_to_win", access_token, pausa=False)
            estado = datos.get("status")
            return {
                "item_id": item_id,
                "estado": estado,
                "ganando": estado == "winning",
                "precio_ganador": datos.get("price_to_win"),
            }
        except Exception as e:
            # Igual que arriba: sin respuesta no se supone nada. `ganando` en
            # null es "no se sabe", que no es lo mismo que "no estaba ganando".
            return {"item_id": item_id, "estado": None, "ganando": None,
                    "precio_ganador": None, "error": str(e)[:120]}

    with ThreadPoolExecutor(max_workers=HILOS_STOCK) as pool:
        filas = list(pool.map(pedir, item_ids))

    fallados = sum(1 for f in filas if f.get("error"))
    if fallados:
        print(f"  ATENCION: {fallados} publicaciones sin buy box (quedan en null, no en 'perdiendo')")
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
#  Escritura de tramos
# ---------------------------------------------------------------------------
#
# Las tres tablas de tramos se actualizan con la misma maniobra de tres pasos, y
# el ORDEN es lo unico que la hace correcta:
#
#   1. CERRAR los tramos abiertos cuyo estado cambio.
#   2. ADELANTAR `visto_hasta` en los que quedaron abiertos -- que despues del
#      paso 1 son, por construccion, exactamente los que no cambiaron.
#   3. ABRIR un tramo para cada item que quedo sin tramo abierto: los que
#      acabamos de cerrar y los que nunca tuvieron uno.
#
# Todo pasa en UNA transaccion y con UPDATE ... FROM contra una tabla de
# staging, no con 8.509 UPDATE sueltos. Es la diferencia entre un pulso de
# segundos y uno que no entra en la ventana de 2 horas.

# El tipo de cada columna de staging, dicho a mano.
#
# Sin esto, una columna con nulos llega a pandas como `object` y se crea como
# TEXT: ahi `e.ganando is distinct from p.ganando` compara boolean contra text y
# la corrida entera explota. Y es justo la columna que MAS se llena de nulos,
# porque "no se pudo consultar la caja" se guarda como null a proposito.
TIPOS = {
    "item_id": Text, "sku": Text, "inventory_id": Text,
    "status": Text, "sub_status": Text, "motivo": Text, "estado": Text,
    "vendible": Boolean, "ganando": Boolean,
    "unidades": Integer,
    "precio": Float, "precio_ganador": Float,
}


def _escribir_tramos(con, momento, df, tabla, columnas_estado, columnas_extra):
    """Aplica los tres pasos sobre una de las tablas de tramos.

    `columnas_estado` son las que definen "es el mismo tramo". `columnas_extra`
    son las que se refrescan en el tramo abierto sin cortarlo (la cantidad de
    unidades cambia con cada venta: si abriera tramo, la tabla se fragmentaria
    hasta perder la gracia).
    """
    if df.empty:
        print(f"  (sin observaciones para {tabla})")
        return 0, 0

    staging = f"{tabla}_staging"
    df.to_sql(staging.split(".")[-1], con, schema="bronze",
              if_exists="replace", index=False,
              dtype={c: TIPOS[c] for c in df.columns if c in TIPOS})

    distinto = " or ".join(
        f"e.{c} is distinct from p.{c}" for c in columnas_estado
    )
    cerrados = con.exec_driver_sql(f"""
        update {tabla} e
           set hasta = %(momento)s, visto_hasta = %(momento)s
          from {staging} p
         where e.item_id = p.item_id and e.hasta is null and ({distinto})
    """, {"momento": momento}).rowcount

    refresco = ", ".join(f"{c} = p.{c}" for c in columnas_extra)
    con.exec_driver_sql(f"""
        update {tabla} e
           set visto_hasta = %(momento)s{"," if refresco else ""} {refresco}
          from {staging} p
         where e.item_id = p.item_id and e.hasta is null
    """, {"momento": momento})

    todas = ["item_id"] + columnas_estado + columnas_extra
    lista = ", ".join(todas)
    abiertos = con.exec_driver_sql(f"""
        insert into {tabla} ({lista}, desde, hasta, visto_hasta)
        select {", ".join("p." + c for c in todas)},
               %(momento)s, null, %(momento)s
          from {staging} p
          left join {tabla} e on e.item_id = p.item_id and e.hasta is null
         where e.item_id is null
    """, {"momento": momento}).rowcount

    con.exec_driver_sql(f"drop table if exists {staging}")
    print(f"  {tabla}: {cerrados} tramos cerrados, {abiertos} abiertos")
    return cerrados, abiertos


def guardar(engine, momento, estado, buybox, duracion):
    """Todo el pulso en UNA transaccion.

    Si se cayera en el medio, quedarian tramos cerrados sin su reemplazo abierto
    -- o sea articulos que desaparecen del calculo por un rato, que es peor que
    no haber corrido. Con la transaccion, el pulso entra entero o no entra.
    """
    with engine.begin() as con:
        _escribir_tramos(
            con, momento, estado[[
                "item_id", "sku", "inventory_id", "vendible", "status",
                "sub_status", "motivo", "unidades",
            ]],
            "bronze.ml_estado_item",
            columnas_estado=["vendible", "status", "sub_status", "motivo"],
            columnas_extra=["sku", "inventory_id", "unidades"],
        )

        _escribir_tramos(
            con, momento, estado[["item_id", "sku", "precio"]],
            "bronze.ml_precio_item",
            columnas_estado=["precio"],
            columnas_extra=["sku"],
        )

        if buybox is not None and not buybox.empty:
            _escribir_tramos(
                con, momento,
                buybox[["item_id", "ganando", "estado", "precio_ganador"]],
                "bronze.ml_buybox_item",
                columnas_estado=["ganando", "estado"],
                columnas_extra=["precio_ganador"],
            )

        # El registro de la corrida va al final y en la misma transaccion: si
        # figura un pulso, es porque sus tramos se escribieron. Es lo que
        # despues permite decir "estas horas no las miramos" en vez de suponer
        # que el estado se mantuvo.
        con.exec_driver_sql("""
            insert into bronze.ml_pulso_corrida
                   (momento, items, vendibles, sin_stock, duracion_seg, origen)
            values (%(momento)s, %(items)s, %(vend)s, %(sin)s, %(dur)s, 'pulso')
            on conflict (momento) do nothing
        """, {
            "momento": momento,
            "items": int(len(estado)),
            "vend": int(estado["vendible"].sum()),
            "sin": int((estado["motivo"] == "sin_stock").sum()),
            "dur": duracion,
        })


# ---------------------------------------------------------------------------

def items_del_experimento(engine):
    """Publicaciones de catalogo de los SKU que estan bajo experimento hoy."""
    sql = """
        select distinct p.id as item_id
          from bronze.ml_publicaciones p
          join gold.experimento_markup e
            on e.sku = (select a->>'value_name'
                          from jsonb_array_elements(p.attributes::jsonb) a
                         where a->>'id' = 'SELLER_SKU'
                         limit 1)
         where p.catalog_listing is true
           and now() between e.desde and e.hasta
    """
    try:
        return pd.read_sql(sql, engine)["item_id"].tolist()
    except Exception as e:
        print(f"  (sin experimento activo: {str(e)[:80]})")
        return []


def main():
    parser = argparse.ArgumentParser(description="Pulso de estado de publicaciones de ML.")
    parser.add_argument("--buybox", action="store_true",
                        help="Ademas del estado, la caja de compra del experimento")
    parser.add_argument("--solo-buybox", action="store_true",
                        help="Solo la caja de compra (no vuelve a pulsar el estado)")
    args = parser.parse_args()

    arranque = time.time()
    engine = asegurar_tablas(crear_engine())
    momento = ahora()

    # LA CAJA DE COMPRA VA EN SU PROPIO PASO, Y MAS ESPACIADA.
    #
    # `price_to_win` no tiene multiget: es UNA llamada por publicacion. De los
    # 2.282 SKU del experimento, 2.107 estan en catalogo -- el 92% --, asi que
    # pedirla en cada pulso serian ~25.000 llamadas por dia solo para esto,
    # contra las ~5.000 de todo el resto junto.
    #
    # Cada 6 horas alcanza: la caja es una covariable para explicar una venta
    # que no ocurrio, no la medicion principal, y el experimento se lee por
    # semana. Si algun dia hace falta mas fino, lo que sube es la frecuencia de
    # ESTE paso, sin tocar el pulso de estado.
    if args.solo_buybox:
        print("\n=== BUY BOX DEL EXPERIMENTO ===")
        items = items_del_experimento(engine)
        if not items:
            print("  No hay experimento activo: nada que consultar.")
            return
        print(f"  {len(items)} publicaciones de catalogo (de a {HILOS_STOCK})")
        buybox = pedir_buybox(renovar_access_token(), items)
        with engine.begin() as con:
            _escribir_tramos(
                con, momento,
                buybox[["item_id", "ganando", "estado", "precio_ganador"]],
                "bronze.ml_buybox_item",
                columnas_estado=["ganando", "estado"],
                columnas_extra=["precio_ganador"],
            )
        print(f"\n=== LISTO en {time.time() - arranque:.0f}s ===")
        return

    print("\n=== PULSO DE PUBLICACIONES ===")
    print(f"  Momento: {momento.isoformat()}")

    catalogo = ids_y_skus(engine)
    if catalogo.empty:
        print("  bronze.ml_publicaciones esta vacia: corre primero "
              "'mercadolibre.py --catalogo'.")
        return
    print(f"  {len(catalogo)} publicaciones en el catalogo")

    access_token = renovar_access_token()
    estado = pedir_estado(access_token, catalogo["item_id"].tolist())
    if estado.empty:
        print("  ATENCION: ningun lote volvio. No se escribe nada: un pulso vacio "
              "cerraria todos los tramos como si el catalogo entero hubiera "
              "desaparecido.")
        return

    estado = estado.merge(catalogo, on="item_id", how="left")
    vendibles = int(estado["vendible"].sum())
    print(f"  {len(estado)} observadas · {vendibles} vendibles · "
          f"{len(estado) - vendibles} no ({int((estado['motivo'] == 'sin_stock').sum())} sin stock)")

    buybox = None
    if args.buybox:
        items = items_del_experimento(engine)
        if items:
            print(f"\n  Buy box: {len(items)} publicaciones de catalogo del experimento")
            buybox = pedir_buybox(access_token, items)
        else:
            print("  Buy box: no hay experimento activo, se saltea")

    guardar(engine, momento, estado, buybox, time.time() - arranque)
    print(f"\n=== LISTO en {time.time() - arranque:.0f}s ===")


if __name__ == "__main__":
    main()
