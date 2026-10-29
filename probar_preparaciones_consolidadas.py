"""Pruebas del guardado de digip_preparaciones, sin base ni red.

QUE SE PRUEBA Y POR QUE. Este guardado duplico filas SIN QUE NADA FALLE:

  * Una preparacion de Digip puede juntar varios pedidos, y el script guarda
    una fila por cada uno. Pero solo consulta los pedidos de la ventana de 7
    dias, y borraba solo esos. Si la preparacion juntaba uno de adentro con
    uno de afuera, la fila del de afuera se agregaba en cada corrida.

  * Al 29/10/2026 eran 5 preparaciones y 477 filas de mas. La 14092763 (pedidos
    4287 del 28/08 y 4290 del 31/08) tenia el 4287 64 veces, y
    gold.reporte_logistica le sumaba el neto 64 veces: SALVATIERRA VIVIANA con
    $14.079.115 en vez de $390.793 en la planilla de logistica.

Y de paso, la otra perdida silenciosa del mismo guardado: las correcciones de
KG/volumen que se cargan desde la planilla se borraban con el reemplazo de la
ventana, porque Digip no las trae.

La base de mentira de aca guarda las filas de verdad y aplica los DELETE, asi
que lo que se mide es lo que queda en la tabla despues de varias corridas.

    python probar_preparaciones_consolidadas.py
"""

import sys
import types

import pandas as pd

# El script arma su engine al importarse, y aca no hay base ni credenciales.
# Mismo truco que probar_envios_full.py. `errores_bd` es el de verdad: es justo
# lo que decide si un error se tolera.
sys.modules.setdefault("conexion", types.SimpleNamespace(crear_engine=lambda **k: None))

import digip_preparaciones as dp  # noqa: E402

FALLOS = []


def revisar(nombre, ok, detalle=""):
    print(f"OK  {nombre}" if ok else f"MAL {nombre}  {detalle}")
    if not ok:
        FALLOS.append(nombre)


class ErrorFalsoDeBD(Exception):
    """Un error cualquiera de la base. NO es "la tabla no existe"."""


class TablaFalsa:
    """bronze.digip_preparaciones en memoria: una lista de dicts."""

    def __init__(self, filas=()):
        self.filas = [dict(f) for f in filas]
        self.romper_en = None

    def pares(self):
        return sorted((f["preparacion_id"], f["pedido_codigo"]) for f in self.filas)


class ConexionFalsa:
    def __init__(self, tabla):
        self.tabla = tabla

    def exec_driver_sql(self, sql, params=None):
        limpio = " ".join(sql.split())
        if self.tabla.romper_en and limpio.startswith(self.tabla.romper_en):
            raise ErrorFalsoDeBD("se paso del statement_timeout")
        params = params or {}

        def toca(f):
            # Lo mismo que el WHERE: por pedido, y por preparacion si el SQL lo pide.
            por_pedido = f["pedido_codigo"] in params.get("codigos", [])
            por_prep = "preparacion_id = ANY" in limpio and f["preparacion_id"] in params.get("prep_ids", [])
            return por_pedido or por_prep

        class Resultado:
            rowcount = 0
            filas = []

            def fetchall(self):
                return self.filas

        r = Resultado()
        if limpio.startswith("DELETE"):
            antes = len(self.tabla.filas)
            self.tabla.filas = [f for f in self.tabla.filas if not toca(f)]
            r.rowcount = antes - len(self.tabla.filas)
        elif limpio.startswith("SELECT"):
            por_prep = {}
            for f in self.tabla.filas:
                kg, vol = f.get("kg_preparacion_manual"), f.get("volumen_preparacion_manual")
                if toca(f) and (kg is not None or vol is not None):
                    por_prep[f["preparacion_id"]] = (kg, vol)
            r.filas = [(p, kg, vol) for p, (kg, vol) in por_prep.items()]
        return r


class Transaccion:
    """Como engine.begin(): si algo explota adentro, la tabla vuelve a como estaba."""

    def __init__(self, tabla):
        self.tabla = tabla

    def __enter__(self):
        self.copia = [dict(f) for f in self.tabla.filas]
        return ConexionFalsa(self.tabla)

    def __exit__(self, tipo, *a):
        if tipo is not None:
            self.tabla.filas = self.copia
        return False


class EngineFalso:
    def __init__(self, tabla):
        self.tabla = tabla

    def begin(self):
        return Transaccion(self.tabla)


def correr(tabla, df, codigos):
    """Un guardado contra la tabla de mentira. Devuelve el error o None."""
    to_sql_original = pd.DataFrame.to_sql

    def to_sql_falso(self, *a, **k):
        tabla.filas.extend(self.to_dict("records"))

    pd.DataFrame.to_sql = to_sql_falso
    try:
        dp.guardar_preparaciones(df, codigos, EngineFalso(tabla))
        return None
    except Exception as e:
        return e
    finally:
        pd.DataFrame.to_sql = to_sql_original


def fila(prep, pedido, kg=1.0):
    return {"preparacion_id": prep, "pedido_codigo": pedido, "transporte": "SEBASTIAN BRANDMARK",
            "bultos_preparacion": 12, "volumen_preparacion": 55000000, "kg_preparacion": kg,
            "tipo_preparacion": "Bultos", "cant_pedidos_en_prep": 2}


# Lo que devuelve la API para Preparaciones/4290: la 14092763, con los dos pedidos.
CONSOLIDADA = pd.DataFrame([fila(14092763, "4287"), fila(14092763, "4290")])


def main():
    # --- 1. EL BUG: el 4287 ya salio de la ventana, el 4290 todavia no -------
    tabla = TablaFalsa()
    for _ in range(5):
        error = correr(tabla, CONSOLIDADA, ["4290"])
    revisar("5 corridas seguidas sin errores", error is None, f"-> {error!r}")
    revisar("  dejan UNA fila por (preparacion, pedido)",
            tabla.pares() == [(14092763, "4287"), (14092763, "4290")], f"-> {tabla.pares()}")

    # --- 2. Una tabla que ya tiene el duplicado se arregla sola ---------------
    tabla = TablaFalsa([fila(14092763, "4287")] * 64 + [fila(14092763, "4290")])
    error = correr(tabla, CONSOLIDADA, ["4290"])
    revisar("las 64 copias viejas se reemplazan por una",
            error is None and tabla.pares() == [(14092763, "4287"), (14092763, "4290")],
            f"-> {error or tabla.pares()}")

    # --- 3. No toca lo que no es de esta corrida -----------------------------
    tabla = TablaFalsa([fila(13000000, "4000"), fila(14092763, "4287")])
    correr(tabla, CONSOLIDADA, ["4290"])
    revisar("una preparacion vieja de otro pedido queda como estaba",
            (13000000, "4000") in tabla.pares(), f"-> {tabla.pares()}")

    # --- 4. Las correcciones de la planilla sobreviven al reemplazo ----------
    #
    # La planilla las guarda por preparacion, asi que estan en las dos filas.
    corregida = [dict(fila(14092763, p), kg_preparacion_manual=18.5, volumen_preparacion_manual=60000000)
                 for p in ("4287", "4290")]
    tabla = TablaFalsa(corregida)
    error = correr(tabla, CONSOLIDADA, ["4290"])
    kgs = sorted(f.get("kg_preparacion_manual") for f in tabla.filas)
    vols = sorted(f.get("volumen_preparacion_manual") for f in tabla.filas)
    revisar("el KG corregido a mano sigue en las dos filas", error is None and kgs == [18.5, 18.5],
            f"-> {error or kgs}")
    revisar("  y el volumen corregido tambien", vols == [60000000, 60000000], f"-> {vols}")
    revisar("  y el KG de Digip se actualiza igual",
            all(f["kg_preparacion"] == 1.0 for f in tabla.filas))

    # Una preparacion SIN correccion queda sin correccion (null, no cero).
    tabla = TablaFalsa()
    correr(tabla, CONSOLIDADA, ["4290"])
    revisar("sin correccion, las columnas manuales quedan vacias",
            all(f.get("kg_preparacion_manual") is None for f in tabla.filas))

    # --- 5. Lo que ya cuidaba el guardado se sigue cuidando ------------------
    tabla = TablaFalsa([fila(14092763, "4287"), fila(14092763, "4290")])
    error = correr(tabla, CONSOLIDADA.iloc[0:0], ["4290"])
    revisar("con Digip caido (df vacio) no se borra nada",
            error is None and len(tabla.filas) == 2, f"-> {error or tabla.pares()}")

    tabla = TablaFalsa([fila(14092763, "4287"), fila(14092763, "4290")])
    tabla.romper_en = "DELETE"
    error = correr(tabla, CONSOLIDADA, ["4290"])
    revisar("si el DELETE falla, explota", isinstance(error, ErrorFalsoDeBD), f"-> {error!r}")
    revisar("  y la tabla queda como estaba", len(tabla.filas) == 2, f"-> {tabla.pares()}")

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) MAL")
        raise SystemExit(1)
    print("Todo OK")


if __name__ == "__main__":
    main()
