"""Un solo lugar para decidir si un error de la base se puede ignorar.

POR QUE EXISTE ESTE ARCHIVO

Tres scripts guardaban asi:

    try:
        with engine.begin() as con:
            con.exec_driver_sql('DELETE FROM bronze."tabla";')
            df.to_sql(tabla, con, if_exists="append")
    except Exception:
        df.to_sql(tabla, engine, if_exists="append")   # <-- inserta SIN borrar

El `except` estaba puesto para UN caso benigno: la primera corrida en una base
limpia, cuando la tabla todavia no existe y el DELETE falla. Pero atrapaba
CUALQUIER error, y despues hacia lo unico que corrompe los datos: insertar sin
haber borrado.

Paso el 21/08/2026. El DELETE de la ventana movil de bronze.ml_ventas se paso
del statement_timeout de Supabase (2 minutos; la tabla son 108 MB y todavia no
tenia indices). La transaccion hizo rollback -- o sea que los borrados se
deshicieron -- y el except inserto igual. Quedaron 2.548 ordenes duplicadas,
que en gold.fact_ventas son ventas contadas dos veces.

Lo peor no fue el error: fue que el paso reporto OK. El orquestador no tenia
como enterarse, y el numero equivocado estuvo en el tablero hasta que alguien
lo noto a ojo.

La regla, entonces: se tolera EXACTAMENTE un error, y cualquier otro explota.
Un paso que falla se ve en el log y se reintenta; un paso que corrompe en
silencio no se ve nunca.
"""

from psycopg2 import errors as pg


def es_tabla_inexistente(e) -> bool:
    """True solo si el error es "esta tabla no existe" (primera corrida).

    Mira `e.orig`, que es donde SQLAlchemy guarda la excepcion original de
    psycopg2. Comparar el texto del mensaje no serviria: cambia con el idioma
    del servidor y con la version.
    """
    return isinstance(getattr(e, "orig", None), pg.UndefinedTable)
