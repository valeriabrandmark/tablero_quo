"""Rellena un tramo de bronze.ml_ventas que quedo sin cargar.

============================================================================
 PARA QUE EXISTE: EL AGUJERO DEL 06/08/2026
============================================================================

Entre las 10:41 y las 21:03 del 06/08/2026 no hay NI UNA venta de Mercado
Libre en bronze.ml_ventas. Son 622 minutos sin una sola orden, en pleno dia
habil. Es el unico hueco asi en toda la historia de la tabla: el segundo mas
grande son 285 minutos y cae de madrugada, como todos los demas.

Faltan unas 250 ordenes y ~$5 M, que en gold.fact_ventas se ven como un
jueves de $3,6 M entre dos dias de ~$8 M.

COMO SE FORMO, que es lo que explica los dos bordes:

  * El borde de las 10:41 lo puso el extractor VIEJO. Hasta el 14/08 las
    ventas se bajaban por quincenas y cada tramo se cacheaba en un archivo
    `cache_ml_ventas/{desde}_{hasta}.json`. El ultimo tramo era
    "04/08 a HOY", asi que el 06/08 el archivo se llamo
    `2026-08-04_2026-08-06.json`. La primera corrida de ese dia lo escribio
    con las ventas hasta las 10:41; las corridas siguientes DEL MISMO DIA
    encontraron un archivo con ese mismo nombre y leyeron el cache en vez de
    volver a preguntarle a la API. El archivo sigue en el repo y su ultima
    orden es `2026-08-06T09:41:51.000-04:00`: exactamente la ultima que hay
    en la base antes del hueco.

  * El borde de las 21:03 lo puso el extractor NUEVO. La ventana movil llego
    el 14/08 con 7 dias, asi que su primer piso fue el 07/08 -- el 06/08 ya
    habia quedado afuera. Y el piso se resuelve en UTC: 07/08 00:00 UTC son
    las 21:00 del 06/08 en Argentina. De ahi en adelante si se volvio a
    pedir.

La franja del medio no le toco a ninguno de los dos: demasiado tarde para el
cache congelado, demasiado temprano para la ventana. Nadie volvio a
preguntar por ella.

============================================================================
 QUE HACE ESTE SCRIPT
============================================================================

Le pide a la API el rango que se le indique y guarda SOLO LAS ORDENES QUE NO
ESTAN. No borra nada, nunca. Eso lo hace repetible sin riesgo: correrlo dos
veces no duplica ni pisa nada, y no puede romper datos buenos si el rango
esta de mas.

Es a proposito que NO reutilice `guardar_ventana_en_bd`: esa funcion borra
la ventana antes de insertar, que es lo correcto para la corrida de todos los
dias --una orden cancelada en el origen tiene que desaparecer-- y es
exactamente lo que no se quiere para una carga hacia atras hecha a mano.

    python rellenar_ventas_ml.py --desde 2026-08-06 --hasta 2026-08-06

Despues hay que reconstruir gold para esas fechas, que es lo que el tablero
lee de verdad:

    python modelo.py --dias 50
"""

import argparse
from datetime import date, datetime

import pandas as pd

import mercadolibre as ml


def _pedir_ordenes(access_token, desde, hasta):
    """Todas las ordenes creadas entre `desde` y `hasta` (fechas argentinas).

    El rango se arma con offset -03:00 y no con -00:00 como la ventana movil.
    La ventana pide de mas a proposito --un dia de colchon no le molesta--
    pero aca los bordes se escriben a mano para tapar un hueco conocido, y
    conviene que "06/08" signifique el 06/08 de aca.
    """
    desde_iso = f"{desde.isoformat()}T00:00:00.000-03:00"
    hasta_iso = f"{hasta.isoformat()}T23:59:59.000-03:00"
    print(f"  Pidiendo a ML: {desde_iso}  ->  {hasta_iso}")

    ordenes = []
    esperadas = None
    offset = 0
    limit = 50

    while True:
        datos = ml.llamar_ml(
            "/orders/search",
            access_token,
            params={
                "seller": ml.USER_ID,
                "order.date_created.from": desde_iso,
                "order.date_created.to": hasta_iso,
                "offset": offset,
                "limit": limit,
            },
        )
        total = datos.get("paging", {}).get("total", 0)
        if esperadas is None:
            esperadas = total
            print(f"  ML dice que hay {esperadas} ordenes en el rango")

        resultados = datos.get("results", [])
        if not resultados:
            # UNA PAGINA VACIA ANTES DE TIEMPO NO ES EL FINAL.
            #
            # La API contesta 200 con `results: []` cuando hipa, y cortar ahi
            # deja el tramo a medio bajar. En la ventana movil eso se arregla
            # solo a la hora siguiente; aca no hay hora siguiente, asi que se
            # corta con error y se vuelve a correr.
            if offset < esperadas:
                raise RuntimeError(
                    f"ML devolvio una pagina vacia en el offset {offset} de "
                    f"{esperadas}. El tramo quedaria incompleto: no se guarda nada."
                )
            break

        ordenes.extend(resultados)
        offset += limit
        if offset >= esperadas or offset >= 10000:
            break

    return ordenes, esperadas


def rellenar(desde, hasta):
    print(f"\n=== RELLENO DE VENTAS ML: {desde} a {hasta} ===")

    access_token = ml.token_ml()
    ordenes, esperadas = _pedir_ordenes(access_token, desde, hasta)
    print(f"  {len(ordenes)} ordenes bajadas")

    if not ordenes:
        print("  ML no devolvio ninguna orden en ese rango. No hay nada que hacer.")
        return

    df = pd.json_normalize(ordenes)
    # Misma razon que en la ventana movil: la paginacion por offset puede
    # devolver dos veces la misma orden si ML la reubica mientras se recorre.
    if "last_updated" in df.columns:
        df = df.sort_values("last_updated", na_position="first")
    df = df.drop_duplicates(subset=["id"], keep="last")

    engine = ml._crear_engine()
    ids = [int(x) for x in df["id"].tolist()]
    with engine.begin() as con:
        ya_estan = {
            f[0] for f in con.exec_driver_sql(
                "SELECT id FROM bronze.ml_ventas WHERE id = ANY(%(ids)s::bigint[])",
                {"ids": ids},
            ).fetchall()
        }

    faltan = df[~df["id"].astype("int64").isin(ya_estan)]
    print(f"  Ya estaban en la base: {len(ya_estan)}")
    print(f"  A insertar (faltaban): {len(faltan)}")

    if faltan.empty:
        print("  No falta ninguna. La tabla ya estaba completa para ese rango.")
        return

    faltan = ml._listas_a_texto(faltan)
    with engine.begin() as con:
        ml._sincronizar_columnas(con, "ml_ventas", faltan)
        faltan.to_sql("ml_ventas", con, schema="bronze",
                      if_exists="append", index=False)

    print(f"  Insertadas {len(faltan)} ordenes en bronze.ml_ventas")
    print("\n  FALTA UN PASO: gold todavia no las tiene. Correr")
    print("      python modelo.py --dias N")
    print("  con un N que llegue hasta la fecha mas vieja que se acaba de cargar.")


def _fecha(texto):
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' no es una fecha YYYY-MM-DD")


def main():
    parser = argparse.ArgumentParser(
        description="Rellena las ventas de ML que falten en un rango de fechas.",
    )
    parser.add_argument("--desde", type=_fecha, required=True,
                        help="Primer dia a revisar (YYYY-MM-DD, hora argentina)")
    parser.add_argument("--hasta", type=_fecha, required=True,
                        help="Ultimo dia a revisar, inclusive (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.hasta < args.desde:
        parser.error("--hasta no puede ser anterior a --desde")
    if args.desde < ml.FECHA_CORTE:
        parser.error(f"--desde no puede ser anterior a {ml.FECHA_CORTE}, "
                     f"que es el piso historico del tablero")
    if args.hasta > date.today():
        parser.error("--hasta no puede ser futuro")

    rellenar(args.desde, args.hasta)
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
