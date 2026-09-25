"""Traer ventas de SIGMA de un rango viejo, sin tocar la ventana movil.

============================================================================
 PARA QUE EXISTE
============================================================================

`sigma.py --ventas` trae los ultimos WINDOW_DAYS dias y los guarda con
`guardar_ventana`, que BORRA la ventana antes de insertar. Eso es lo correcto
todos los dias --una linea corregida en Sigma pisa a la vieja-- y es
exactamente lo que no se puede hacer para rellenar un rango viejo: con un
cutoff en febrero, ese borrado se llevaria puesto todo lo que hay de mayo en
adelante.

Asi que esto es el hermano de `rellenar_ventas_ml.py` para el otro canal:
mismo trabajo, misma regla de oro. SOLO INSERTA LO QUE NO ESTA. Nunca borra,
nunca actualiza. Si una linea ya esta en bronze, se deja como esta: la que
manda sobre el presente es la corrida de todos los dias, no esta.

Correrlo dos veces con el mismo rango es seguro: la segunda no inserta nada.

============================================================================
 POR QUE SE PARTE EN TRAMOS
============================================================================

`ExportArticulosVendidos` corta en 28.000 registros y NO avisa: devuelve las
primeras 28.000 y se queda callado, igual que el `/orders/search` de Mercado
Libre con su tope de offset. Un trimestre entero puede pasarlo, y el agujero
no daria ningun error -- aparecerian menos ventas y nadie se enteraria.

Por eso se pide de a un mes y se avisa fuerte si algun tramo se acerca al
tope. Un mes de ventas anda por las 4.000 lineas, asi que hay margen de
sobra; el aviso es por si el negocio crece o alguien agranda el tramo.

    python rellenar_ventas_sigma.py --desde 2026-02-01 --hasta 2026-05-05
"""

import argparse
from datetime import date, timedelta

import pandas as pd

import sigma
from guardado import listas_a_texto, sincronizar_columnas

# Cuantos dias pide cada llamada. Un mes: ver el porque arriba.
DIAS_POR_TRAMO = 30

# A partir de aca se avisa que el tramo puede estar recortado. Es el mismo
# numero que vigila `sigma.extraer_ventas`.
TOPE_REGISTROS = 28000


def _fecha(texto):
    return date.fromisoformat(texto)


def _tramos(desde, hasta, dias):
    """Parte el rango en pedazos de `dias`, con los dos bordes incluidos."""
    inicio = desde
    while inicio <= hasta:
        fin = min(inicio + timedelta(days=dias - 1), hasta)
        yield inicio, fin
        inicio = fin + timedelta(days=1)


def _pedir_tramo(desde, hasta):
    """Las lineas de venta de ese rango, tal como las devuelve Sigma."""
    datos = sigma.llamar_sigma(
        "ExportArticulosVendidos",
        {"dde": desde.isoformat(), "hta": hasta.isoformat()},
    )
    print(f"  {desde} a {hasta}: {len(datos)} lineas", end="")
    if len(datos) >= TOPE_REGISTROS:
        print(f"\n  ATENCION: el tramo llego al tope de {TOPE_REGISTROS}"
              " registros y puede estar recortado."
              " Volve a correrlo con --dias-por-tramo mas chico.")
    else:
        print()
    return datos


def _insertar_las_que_falten(engine, datos):
    """Guarda las lineas que no esten todavia. Devuelve cuantas entraron.

    LA CLAVE ES (id, item), igual que en la ventana movil: un comprobante
    tiene un renglon por articulo facturado, asi que el `id` solo no alcanza
    --se insertaria una linea por comprobante y se perderian las demas.
    """
    if not datos:
        return 0

    df = pd.json_normalize(datos)
    if df.empty:
        return 0

    # Sigma puede devolver la misma linea dos veces si se pide un rango que
    # se solapa con otro. Con la clave completa, quedarse con una es correcto.
    df = df.drop_duplicates(subset=["id", "item"], keep="last")

    claves = list(zip(df["id"].astype(str), df["item"].astype(str)))
    with engine.begin() as con:
        ya_estan = {
            (str(f[0]), str(f[1]))
            for f in con.exec_driver_sql(
                """
                SELECT v.id, v.item
                FROM bronze.sigma_ventas v
                JOIN unnest(%(ids)s::text[], %(items)s::text[]) AS p(id, item)
                  ON v.id::text = p.id AND v.item::text = p.item
                """,
                {"ids": [c[0] for c in claves], "items": [c[1] for c in claves]},
            ).fetchall()
        }

    faltan = df[
        ~pd.Series(
            [c in ya_estan for c in claves], index=df.index
        )
    ]
    if faltan.empty:
        return 0

    faltan = listas_a_texto(faltan)
    with engine.begin() as con:
        sincronizar_columnas(con, "sigma_ventas", faltan)
        faltan.to_sql("sigma_ventas", con, schema="bronze",
                      if_exists="append", index=False)
    return len(faltan)


def main():
    parser = argparse.ArgumentParser(
        description="Rellena bronze.sigma_ventas de un rango viejo, sin borrar nada."
    )
    parser.add_argument("--desde", type=_fecha, required=True,
                        help="Primer dia a pedir (YYYY-MM-DD)")
    parser.add_argument("--hasta", type=_fecha, required=True,
                        help="Ultimo dia a pedir, incluido (YYYY-MM-DD)")
    parser.add_argument("--dias-por-tramo", type=int, default=DIAS_POR_TRAMO,
                        help=f"Tamano de cada llamada (por defecto {DIAS_POR_TRAMO})")
    args = parser.parse_args()

    if args.hasta < args.desde:
        parser.error("--hasta no puede ser anterior a --desde")

    engine = sigma._crear_engine()

    print(f"=== Rellenando ventas de Sigma: {args.desde} a {args.hasta} ===")
    print("    Solo se insertan las lineas que no esten. No se borra nada.\n")

    total = 0
    for desde, hasta in _tramos(args.desde, args.hasta, args.dias_por_tramo):
        datos = _pedir_tramo(desde, hasta)
        nuevas = _insertar_las_que_falten(engine, datos)
        total += nuevas
        print(f"    insertadas: {nuevas}")

    print(f"\n=== LISTO: {total} lineas nuevas en bronze.sigma_ventas ===")
    if total:
        print("    Para que entren a gold hay que correr modelo.py --relleno.")


if __name__ == "__main__":
    main()
