"""Prueba de `_sincronizar_columnas`, la red que evita que una columna nueva
del origen tumbe una extraccion entera.

No toca la base: usa una conexion falsa que anota los ALTER que le piden. Es
justo la funcion que NO conviene tener sin prueba -- si falla, falla en silencio
y lo que se pierde es una extraccion completa.

    python probar_columnas_nuevas.py
"""

import pandas as pd

from sigma import _sincronizar_columnas


class Resultado:
    def __init__(self, filas): self.filas = filas
    def fetchall(self): return self.filas


class ConexionFalsa:
    """Se hace pasar por engine y por conexion a la vez, para probar los dos
    caminos con el mismo objeto."""

    def __init__(self, columnas):
        self.columnas = columnas
        self.alters = []

    def exec_driver_sql(self, sql, params=None):
        if "information_schema" in sql:
            return Resultado([(c,) for c in self.columnas])
        self.alters.append(sql)
        return Resultado([])

    # Como engine
    def begin(self): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False


def columnas_alteradas(alters):
    import re
    return sorted(re.search(r'ADD COLUMN IF NOT EXISTS "([^"]+)"', a).group(1) for a in alters)


def check(nombre, ok, detalle=""):
    print(("OK  " if ok else "MAL ") + nombre)
    if not ok and detalle:
        print("     " + detalle)
    return ok


todo = []

# 1. El caso real: SIGMA suma `operacionId` y la tabla no lo tiene.
con = ConexionFalsa(["id", "fecha", "itemArticuloId"])
df = pd.DataFrame(columns=["id", "fecha", "itemArticuloId", "operacionId"])
_sincronizar_columnas(con, "sigma_ventas", df)
todo.append(check("agrega la columna que falta",
                  columnas_alteradas(con.alters) == ["operacionId"],
                  str(con.alters)))

# 2. Sin novedades no toca nada. Un ALTER de mas por corrida seria ruido, y
#    sobre una tabla grande, un lock que nadie pidio.
con = ConexionFalsa(["id", "fecha"])
_sincronizar_columnas(con, "sigma_ventas", pd.DataFrame(columns=["id", "fecha"]))
todo.append(check("no hace nada si no hay columnas nuevas", con.alters == [], str(con.alters)))

# 3. Varias de una.
con = ConexionFalsa(["id"])
_sincronizar_columnas(con, "t", pd.DataFrame(columns=["id", "a", "b", "c"]))
todo.append(check("agrega varias en la misma pasada",
                  columnas_alteradas(con.alters) == ["a", "b", "c"], str(con.alters)))

# 4. Tabla que todavia no existe: no hay nada que alterar, la crea el to_sql.
#    Si intentara el ALTER, la primera corrida en una base limpia explotaria.
con = ConexionFalsa([])
_sincronizar_columnas(con, "no_existe", pd.DataFrame(columns=["id", "x"]))
todo.append(check("tabla inexistente: no intenta alterar nada", con.alters == [], str(con.alters)))

# 5. Que la tabla tenga columnas de mas es normal (las que el origen dejo de
#    mandar) y no tiene que provocar nada.
con = ConexionFalsa(["id", "fecha", "vieja"])
_sincronizar_columnas(con, "t", pd.DataFrame(columns=["id", "fecha"]))
todo.append(check("columnas de mas en la tabla no molestan", con.alters == [], str(con.alters)))

# 6. El nombre va entrecomillado: SIGMA usa camelCase y sin comillas Postgres lo
#    pasaria a minusculas, creando una columna que despues nunca empalma.
con = ConexionFalsa(["id"])
_sincronizar_columnas(con, "t", pd.DataFrame(columns=["id", "operacionId"]))
todo.append(check('el nombre va entre comillas (camelCase)',
                  '"operacionId"' in con.alters[0], str(con.alters)))

print("\n" + ("TODO OK" if all(todo) else "HAY FALLAS"))
