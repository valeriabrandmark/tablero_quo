"""Rellena los tramos de bronze.ml_ventas que hayan quedado sin cargar.

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

Es a proposito que NO reutilice `guardar_ventana`: esa funcion borra
la ventana antes de insertar, que es lo correcto para la corrida de todos los
dias --una orden cancelada en el origen tiene que desaparecer-- y es
exactamente lo que no se quiere para una carga hacia atras hecha a mano.

    # tapar el hueco conocido
    python rellenar_ventas_ml.py --desde 2026-08-06 --hasta 2026-08-06

    # revisar TODA la historia, por las dudas
    python rellenar_ventas_ml.py --desde 2026-05-06 --hasta 2026-09-21

    # traer ventas ANTERIORES al piso del tablero (ver motivo_para_no_correr)
    python rellenar_ventas_ml.py --desde 2026-02-01 --hasta 2026-05-05 --antes-del-piso

Despues hay que reconstruir gold para esas fechas, que es lo que el tablero
lee de verdad:

    python modelo.py --dias 50
"""

import argparse
from datetime import date, datetime, timedelta

import pandas as pd

import mercadolibre as ml


# EL TOPE DE LA API, Y POR QUE EL RANGO SE PARTE EN TRAMOS
#
# `/orders/search` se pagina con offset y NO DEVUELVE NADA PASADO EL 10.000.
# Un rango que tenga mas ordenes que eso no da error: simplemente se corta, y
# el resto no existe para quien pregunta.
#
# Eso importa justo cuando mas se usa este script. Toda la historia --del
# 06/05 a hoy-- son ~56.000 ordenes: pedidas de una, la API contestaria las
# primeras 10.000 y el relleno diria que termino bien habiendo mirado menos de
# un quinto. Partido en tramos, cada pedido entra holgado bajo el tope.
#
# 15 dias son ~6.000 ordenes al ritmo de hoy (~400 por dia). Si algun dia el
# volumen sube, `_pedir_tramo` parte el tramo al medio solo: el numero de aca
# es el punto de partida, no un limite que haya que mantener a mano.
TOPE_OFFSET = 10000

# A partir de cuantas ordenes se parte el tramo. No es 10.000 a proposito: las
# ordenes siguen entrando mientras se pagina, asi que un tramo medido en 9.900
# puede ser de 10.050 para cuando se lo termina de leer.
TOPE_SEGURO = 8000

DIAS_POR_TRAMO = 15


def _rango_iso(desde, hasta):
    """Los bordes del rango como los quiere la API, en hora argentina.

    Con offset -03:00 y no -00:00 como la ventana movil. La ventana pide de
    mas a proposito --un dia de colchon no le molesta-- pero aca los bordes
    se escriben a mano para tapar un hueco conocido, y conviene que "06/08"
    signifique el 06/08 de aca.
    """
    return (f"{desde.isoformat()}T00:00:00.000-03:00",
            f"{hasta.isoformat()}T23:59:59.000-03:00")


def _cuantas_hay(access_token, desde, hasta):
    """Cuantas ordenes dice la API que hay en el rango, sin bajarlas."""
    desde_iso, hasta_iso = _rango_iso(desde, hasta)
    datos = ml.llamar_ml(
        "/orders/search",
        access_token,
        params={
            "seller": ml.USER_ID,
            "order.date_created.from": desde_iso,
            "order.date_created.to": hasta_iso,
            "offset": 0,
            "limit": 1,
        },
    )
    return datos.get("paging", {}).get("total", 0)


def _paginar(access_token, desde, hasta, esperadas):
    """Baja las `esperadas` ordenes del rango, de a 50."""
    desde_iso, hasta_iso = _rango_iso(desde, hasta)
    ordenes = []
    offset = 0
    limit = 50

    while offset < esperadas:
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
        resultados = datos.get("results", [])
        if not resultados:
            # UNA PAGINA VACIA ANTES DE TIEMPO NO ES EL FINAL.
            #
            # La API contesta 200 con `results: []` cuando hipa, y cortar ahi
            # deja el tramo a medio bajar. En la ventana movil eso se arregla
            # solo a la hora siguiente; aca no hay hora siguiente, asi que se
            # corta con error y se vuelve a correr.
            raise RuntimeError(
                f"ML devolvio una pagina vacia en el offset {offset} de "
                f"{esperadas} ({desde} a {hasta}). El tramo quedaria "
                f"incompleto: no se guarda nada."
            )
        ordenes.extend(resultados)
        offset += limit

    return ordenes


def _pedir_tramo(access_token, desde, hasta):
    """Las ordenes del tramo, partiendolo al medio si no entra bajo el tope."""
    esperadas = _cuantas_hay(access_token, desde, hasta)

    if esperadas > TOPE_SEGURO:
        if desde == hasta:
            # Un solo dia con mas ordenes que el tope no se puede partir mas
            # por fecha. No pasa hoy --el dia mas cargado no llega a 600-- y
            # si algun dia pasa, hay que partir por hora y no adivinarlo aca.
            raise RuntimeError(
                f"El {desde} tiene {esperadas} ordenes y no entra bajo el tope "
                f"de offset {TOPE_OFFSET} de la API. Hay que partir por hora."
            )
        medio = desde + (hasta - desde) // 2
        print(f"  {desde} a {hasta}: {esperadas} ordenes, se parte al medio")
        return (_pedir_tramo(access_token, desde, medio)
                + _pedir_tramo(access_token, medio + timedelta(days=1), hasta))

    print(f"  {desde} a {hasta}: {esperadas} ordenes segun ML", end="", flush=True)
    ordenes = _paginar(access_token, desde, hasta, esperadas)
    print(f" -> {len(ordenes)} bajadas")
    return ordenes


def _insertar_las_que_falten(engine, ordenes):
    """Guarda las ordenes que no esten todavia. Devuelve cuantas entraron."""
    if not ordenes:
        return 0

    df = pd.json_normalize(ordenes)
    # Misma razon que en la ventana movil: la paginacion por offset puede
    # devolver dos veces la misma orden si ML la reubica mientras se recorre.
    if "last_updated" in df.columns:
        df = df.sort_values("last_updated", na_position="first")
    df = df.drop_duplicates(subset=["id"], keep="last")

    ids = [int(x) for x in df["id"].tolist()]
    with engine.begin() as con:
        ya_estan = {
            f[0] for f in con.exec_driver_sql(
                "SELECT id FROM bronze.ml_ventas WHERE id = ANY(%(ids)s::bigint[])",
                {"ids": ids},
            ).fetchall()
        }

    faltan = df[~df["id"].astype("int64").isin(ya_estan)]
    if faltan.empty:
        return 0

    faltan = ml._listas_a_texto(faltan)
    with engine.begin() as con:
        ml._sincronizar_columnas(con, "ml_ventas", faltan)
        faltan.to_sql("ml_ventas", con, schema="bronze",
                      if_exists="append", index=False)
    return len(faltan)


def _tramos(desde, hasta, dias):
    """Parte el rango en pedazos de `dias`, con los dos bordes incluidos."""
    inicio = desde
    while inicio <= hasta:
        fin = min(inicio + timedelta(days=dias - 1), hasta)
        yield inicio, fin
        inicio = fin + timedelta(days=1)


def rellenar(desde, hasta, dias_por_tramo=DIAS_POR_TRAMO):
    print(f"\n=== RELLENO DE VENTAS ML: {desde} a {hasta} ===")

    access_token = ml.token_ml()
    engine = ml._crear_engine()

    # SE INSERTA TRAMO POR TRAMO Y NO TODO JUNTO AL FINAL.
    #
    # Por memoria --toda la historia son ~130 MB de JSON, y normalizarla de una
    # sola vez en un DataFrame no hace falta-- y sobre todo porque lo que ya
    # entro queda entrado: si el tramo 8 se cae, los 7 anteriores estan
    # guardados y volver a correr el script los saltea solos (ya no faltan).
    total_bajadas = 0
    total_insertadas = 0

    for tramo_desde, tramo_hasta in _tramos(desde, hasta, dias_por_tramo):
        ordenes = _pedir_tramo(access_token, tramo_desde, tramo_hasta)
        insertadas = _insertar_las_que_falten(engine, ordenes)
        total_bajadas += len(ordenes)
        total_insertadas += insertadas
        if insertadas:
            print(f"    FALTABAN {insertadas} -> insertadas")

    print(f"\n  Revisadas: {total_bajadas} ordenes")
    print(f"  Insertadas (faltaban): {total_insertadas}")

    if not total_insertadas:
        print("  No faltaba ninguna. La tabla ya estaba completa para ese rango.")
        return

    print("\n  FALTA UN PASO: gold todavia no las tiene. Correr")
    print("      python modelo.py --dias N")
    print("  con un N que llegue hasta la fecha mas vieja que se acaba de cargar.")


def _fecha(texto):
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' no es una fecha YYYY-MM-DD")


# EL PISO DE 2026-05-06 ES NUESTRO, NO DE MERCADO LIBRE
#
# `ml.FECHA_CORTE` es desde cuando el tablero guarda ventas, y por eso la
# corrida de todos los dias nunca pide nada anterior. Este script nacio para
# tapar agujeros DENTRO de esa historia, asi que copiaba ese piso como un
# limite duro.
#
# Pero un dia hubo que traer un trimestre anterior --las ventas de una marca de
# febrero a mayo-- y el piso freno un pedido perfectamente valido: la API de ML
# llega muchisimo mas atras, el freno era nuestro.
#
# Asi que el piso sigue siendo el default, porque de verdad atrapa algo: un ano
# mal tipeado (2025 en vez de 2026) pediria meses de ordenes de a 15 dias sin
# que nadie lo note hasta ver la factura de tiempo. Pero ahora se puede abrir a
# proposito, y hay que decirlo con todas las letras.
def motivo_para_no_correr(desde, hasta, dias_por_tramo, antes_del_piso, hoy=None):
    """Por que este rango no se puede pedir, o None si esta bien.

    Vive afuera de `main()` para que se pueda probar sin red y sin base: es la
    unica parte del script que decide algo.
    """
    hoy = hoy or date.today()

    if hasta < desde:
        return "--hasta no puede ser anterior a --desde"
    if hasta > hoy:
        return "--hasta no puede ser futuro"
    if dias_por_tramo < 1:
        return "--dias-por-tramo tiene que ser al menos 1"
    if desde < ml.FECHA_CORTE and not antes_del_piso:
        return (f"--desde ({desde}) es anterior a {ml.FECHA_CORTE}, el piso "
                "historico del tablero. Si es a proposito --un relleno de "
                "ventas viejas-- agregale --antes-del-piso.")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Rellena las ventas de ML que falten en un rango de fechas.",
    )
    parser.add_argument("--desde", type=_fecha, required=True,
                        help="Primer dia a revisar (YYYY-MM-DD, hora argentina)")
    parser.add_argument("--hasta", type=_fecha, required=True,
                        help="Ultimo dia a revisar, inclusive (YYYY-MM-DD)")
    parser.add_argument("--dias-por-tramo", type=int, default=DIAS_POR_TRAMO,
                        help=f"De a cuantos dias se le pide a la API "
                             f"(por defecto {DIAS_POR_TRAMO})")
    parser.add_argument(
        "--antes-del-piso", action="store_true",
        help=f"Habilita pedir fechas anteriores a {ml.FECHA_CORTE}. Ver arriba.",
    )
    args = parser.parse_args()

    problema = motivo_para_no_correr(
        args.desde, args.hasta, args.dias_por_tramo, args.antes_del_piso,
    )
    if problema:
        parser.error(problema)

    if args.desde < ml.FECHA_CORTE:
        print(f"OJO: se piden ventas anteriores a {ml.FECHA_CORTE}.\n"
              "     A esas fechas no las mira ninguna corrida automatica, asi\n"
              "     que lo que entre a bronze ahora queda invisible hasta que\n"
              "     se corra modelo.py --relleno para ese mismo rango.\n")

    rellenar(args.desde, args.hasta, args.dias_por_tramo)
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
