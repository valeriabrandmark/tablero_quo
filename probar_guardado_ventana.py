"""Pruebas del guardado por ventana movil, sin base ni red.

QUE SE PRUEBA Y POR QUE. Esta funcion es la que duplico datos DOS VECES:

  * 21/08/2026, Mercado Libre: 2.548 ordenes. El DELETE se paso del
    statement_timeout, la transaccion hizo rollback, y un `except Exception`
    dejo que la insercion siguiera igual. El paso reporto OK.

  * 14/08/2026, SIGMA: 31 lineas (2.046 unidades de mas en agosto),
    descubiertas recien el 22/09 porque un sell out a mano no cerraba.

Las dos veces el sintoma fue el mismo --numeros inflados-- y la causa
tambien: guardar de menos, o guardar dos veces, SIN QUE NADA FALLE. Por eso
lo que se fija aca no es que guarde bien cuando todo anda, sino que EXPLOTE
cuando no puede garantizarlo.

    python probar_guardado_ventana.py
"""

from datetime import date

import pandas as pd

import guardado

FALLOS = []


def revisar(nombre, ok, detalle=""):
    print(f"OK  {nombre}" if ok else f"MAL {nombre}  {detalle}")
    if not ok:
        FALLOS.append(nombre)


# --- Una base de mentira, que anota lo que le piden -------------------------


class ErrorFalsoDeBD(Exception):
    """Un error cualquiera de la base. NO es "la tabla no existe"."""


class ConexionFalsa:
    def __init__(self, diario, romper_en=None):
        self.diario = diario
        self.romper_en = romper_en

    def begin(self):
        # `sincronizar_columnas` decide si abrir transaccion propia mirando si
        # lo que recibe es una Connection de SQLAlchemy. Esta no lo es, asi que
        # toma el camino de abrirla -- y tiene que encontrar por donde.
        return TransaccionFalsa(self)

    def exec_driver_sql(self, sql, params=None):
        limpio = " ".join(sql.split())
        self.diario.append(limpio)
        if self.romper_en and self.romper_en in limpio:
            raise ErrorFalsoDeBD("se paso del statement_timeout")

        class Resultado:
            rowcount = 0

            @staticmethod
            def fetchall():
                # Las columnas que la tabla ya tiene: asi `sincronizar_columnas`
                # no intenta agregar ninguna.
                return [("id",), ("item",), ("fecha",)]

            @staticmethod
            def scalar():
                return True

        return Resultado


class TransaccionFalsa:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self.con

    def __exit__(self, *a):
        return False


class EngineFalso:
    def __init__(self, diario, romper_en=None):
        self.con = ConexionFalsa(diario, romper_en)

    def begin(self):
        return TransaccionFalsa(self.con)


def correr(df, romper_en=None, tabla="sigma_ventas", clave=("id", "item")):
    """Devuelve (diario_de_sql, insertos, error_o_None)."""
    diario, insertos = [], []
    engine = EngineFalso(diario, romper_en)

    # `guardar_ventana` importa `crear_engine` de `conexion` adentro de la
    # funcion, asi que se reemplaza ahi y no en `guardado`.
    import conexion

    conexion_original = conexion.crear_engine
    conexion.crear_engine = lambda *a, **k: engine

    to_sql_original = pd.DataFrame.to_sql
    pd.DataFrame.to_sql = lambda self, *a, **k: insertos.append(len(self))

    try:
        guardado.guardar_ventana(df, tabla, "fecha", date(2026, 8, 14), clave)
        return diario, insertos, None
    except Exception as e:
        return diario, insertos, e
    finally:
        conexion.crear_engine = conexion_original
        pd.DataFrame.to_sql = to_sql_original


VENTAS = pd.DataFrame(
    [
        {"id": 4343, "item": 1, "fecha": "2026-08-14"},
        {"id": 4344, "item": 1, "fecha": "2026-08-14"},
        {"id": 4345, "item": 1, "fecha": "2026-08-15"},
    ]
)


def main():
    # --- 1. El caso normal: borra por ventana Y por clave, y despues inserta -
    diario, insertos, error = correr(VENTAS)
    borrados = [s for s in diario if s.startswith("DELETE")]
    revisar("sin errores: inserta las 3 filas", error is None and insertos == [3],
            f"-> {error or insertos}")
    revisar("  borra por ventana", any('"fecha"::date >= %(cutoff)s' in s for s in borrados),
            f"-> {borrados}")
    revisar("  y tambien por clave natural",
            any('("id", "item") IN' in s for s in borrados), f"-> {borrados}")

    # El pre-filtro de texto es lo unico que deja usar el indice: sin el, el
    # DELETE recorre la tabla entera y es lo que se paso del timeout el 21/08.
    revisar("  con el pre-filtro que deja usar el indice",
            any('"fecha" >= %(piso)s' in s for s in borrados), f"-> {borrados}")

    # --- 2. EL BUG DEL 21/08: si el borrado falla, NO se puede insertar ------
    diario, insertos, error = correr(VENTAS, romper_en="DELETE")
    revisar("si el DELETE de la ventana falla, explota", isinstance(error, ErrorFalsoDeBD),
            f"-> {error!r}")
    revisar("  y NO inserto nada", insertos == [], f"-> {insertos}")

    # Lo mismo si el que falla es el borrado por clave: los dos estan en la
    # misma transaccion y ninguno puede quedar a medias.
    diario, insertos, error = correr(VENTAS, romper_en='("id", "item") IN')
    revisar("si el DELETE por clave falla, tambien explota",
            isinstance(error, ErrorFalsoDeBD), f"-> {error!r}")
    revisar("  y tampoco inserto nada", insertos == [], f"-> {insertos}")

    # --- 3. Una tanda con la fila repetida no tumba la corrida --------------
    #
    # Paso de verdad: el 26/08 la orden 2000018121647354 vino repetida en la
    # paginacion de ML y tumbo el orquestador dos corridas seguidas.
    repetida = pd.concat([VENTAS, VENTAS.iloc[[0]]], ignore_index=True)
    diario, insertos, error = correr(repetida)
    revisar("una fila repetida en la tanda se deduplica",
            error is None and insertos == [3], f"-> {error or insertos}")

    # --- 4. Una respuesta VACIA no borra la ventana -------------------------
    #
    # Es la otra forma de perder datos, y la que tenia sigma.py: borraba
    # primero y recien despues miraba si habia algo con que reemplazar. Una API
    # que contesta una lista vacia porque se cayo no es "no hubo ventas".
    diario, insertos, error = correr(VENTAS.iloc[0:0])
    revisar("con el origen vacio no se borra nada",
            error is None and not any(s.startswith("DELETE") for s in diario),
            f"-> {[s for s in diario if s.startswith('DELETE')]}")
    revisar("  ni se inserta nada", insertos == [], f"-> {insertos}")

    # --- 5. La clave simple usa `= ANY`, que tambien entra por indice -------
    compras = pd.DataFrame([{"id": 7, "fecha": "2026-08-14"}])
    diario, insertos, error = correr(compras, tabla="sigma_compras", clave=("id",))
    revisar("clave de una sola columna: borra con = ANY",
            any('"id" = ANY(%(k0)s::bigint[])' in s for s in diario if s.startswith("DELETE")),
            f"-> {[s for s in diario if s.startswith('DELETE')]}")

    # --- 6. El engine del llamador se respeta -------------------------------
    #
    # `sigma.py` arma el suyo con `client_encoding=utf8`. Si esta funcion lo
    # ignorara y armara uno propio, esa conexion cambiaria sin que nadie lo
    # decida -- y seria invisible hasta que apareciera un acento roto.
    diario, insertos = [], []
    mio = EngineFalso(diario)
    import conexion

    conexion_original = conexion.crear_engine
    conexion.crear_engine = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no tenia que armar un engine propio")
    )
    to_sql_original = pd.DataFrame.to_sql
    pd.DataFrame.to_sql = lambda self, *a, **k: insertos.append(len(self))
    try:
        guardado.guardar_ventana(
            VENTAS, "sigma_ventas", "fecha", date(2026, 8, 14), ("id", "item"), engine=mio
        )
        revisar("usa el engine que le pasan", insertos == [3], f"-> {insertos}")
    except AssertionError as e:
        revisar("usa el engine que le pasan", False, f"-> {e}")
    finally:
        conexion.crear_engine = conexion_original
        pd.DataFrame.to_sql = to_sql_original

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) MAL: {', '.join(FALLOS)}")
        raise SystemExit(1)
    print("Todas las pruebas pasaron.")


if __name__ == "__main__":
    main()
