"""Pruebas de la herencia de costos al cambiar de mes comercial.

POR QUE ESTO TIENE PRUEBAS, Y POR QUE SON ESTAS

El mes comercial cierra el 5. El 06/11/2026, primer dia del mes nuevo, las 135
ventas del dia quedaron SIN COSTO: la lista de noviembre no estaba cargada y
`costo_de` no se salia del mes. Y un costo que falta no se ve como un hueco en
el tablero -- `COSTO` es `coalesce(costo_unitario, 0)` -- asi que la
rentabilidad del dia dio ~60% en vez de ~13%. Un costo faltante que se lee como
una fiesta es la peor forma de fallar que tiene este tablero.

La herencia arregla eso, pero puede romper algo peor si se hace de mas. Lo que
estas pruebas fijan son los tres limites:

1. QUE NO RESUCITE ARTICULOS. Si el mes TIENE lista y el articulo no esta en
   ella, es porque ese mes no se compraba. Ahi NO se hereda: sin costo, margen
   en null, como siempre. Esto es lo que protegia el comentario original de
   `costo_de` y es lo que mas facil se pierde al tocar esto.

2. QUE NO HEREDE PARA SIEMPRE. Un mes de gracia y se corta. Si nadie carga una
   lista por dos meses, los costos se caen y eso grita; heredar sin limite
   seria valuar diciembre con los costos de septiembre y no enterarse nunca.

3. QUE HEREDE EL CIERRE Y NO EL ARRANQUE. Un mes puede tener varios tramos --el
   proveedor manda lista a mitad de mes-- y lo que se hereda es el ULTIMO, que
   es el que regia al cerrar.

    python probar_herencia_costos.py
"""

import os
import sys
import types
from datetime import date

# El modulo abre la base al importarse. Aca solo se prueban funciones puras.
os.environ.setdefault("ML_USER_ID", "0")
sys.modules.setdefault("conexion", types.SimpleNamespace(crear_engine=lambda **k: None))
sys.modules.setdefault("errores_bd", types.SimpleNamespace(
    es_tabla_inexistente=lambda e: False))

from modelo import costo_del_mes, costo_vigente  # noqa: E402

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# ---------------------------------------------------------------------------
#  EL ESCENARIO DEL 06/11/2026
# ---------------------------------------------------------------------------
#
# Octubre tiene lista con tres tramos (el del Excel y los dos del boton);
# noviembre no tiene nada todavia. GL39002 estaba en octubre; GL99999 no.

IDX = {
    ("GL39002", "2026-10"): [
        (date(2026, 10, 6), 1000.0),
        (date(2026, 10, 29), 900.0),
        (date(2026, 11, 1), 800.0),     # el cierre de octubre
    ],
    ("GL05031", "2026-10"): [
        (date(2026, 10, 6), 500.0),
    ],
    ("GL11111", "2026-09"): [
        (date(2026, 9, 7), 300.0),
    ],
}
MESES = {"2026-09", "2026-10"}

# Una venta del 6 de noviembre: mes comercial 2026-11, que no tiene lista.
revisar(
    "el dia despues del cierre hereda el cierre",
    costo_del_mes(IDX, MESES, "GL39002", "2026-11", date(2026, 11, 6)),
    (800.0, "2026-10"),
)

# LA PRUEBA DEL PUNTO 3. Hereda 800 --el tramo del 01/11-- y no 1000, que era el
# del arranque de octubre.
revisar(
    "hereda el ultimo tramo, no el primero",
    costo_del_mes(IDX, MESES, "GL39002", "2026-11", date(2026, 11, 20))[0],
    800.0,
)

# Un articulo con un solo tramo en el mes anterior.
revisar(
    "con un tramo solo tambien",
    costo_del_mes(IDX, MESES, "GL05031", "2026-11", date(2026, 11, 6)),
    (500.0, "2026-10"),
)

# ---------------------------------------------------------------------------
#  LA PRUEBA DEL PUNTO 1: NO RESUCITAR ARTICULOS
# ---------------------------------------------------------------------------
#
# Si alguien reescribe esto heredando "cuando no encuentro el costo", esta es la
# prueba que se rompe. GL11111 estaba en septiembre y NO en octubre: octubre
# tiene lista, asi que ese articulo ese mes no se compraba.

revisar(
    "un articulo que salio del catalogo NO hereda",
    costo_del_mes(IDX, MESES, "GL11111", "2026-10", date(2026, 10, 20)),
    (None, None),
)

revisar(
    "un sku que no existe en ningun mes tampoco",
    costo_del_mes(IDX, MESES, "NO_EXISTE", "2026-10", date(2026, 10, 20)),
    (None, None),
)

# Y cuando el mes SI tiene lista, el costo sale de ahi sin marcar herencia.
revisar(
    "con lista del mes no hay herencia",
    costo_del_mes(IDX, MESES, "GL39002", "2026-10", date(2026, 10, 30)),
    (900.0, None),
)

# ---------------------------------------------------------------------------
#  LA PRUEBA DEL PUNTO 2: UN MES DE GRACIA Y SE CORTA
# ---------------------------------------------------------------------------

# Diciembre sin lista y noviembre tampoco: no se hereda de octubre.
revisar(
    "dos meses sin lista cortan la herencia",
    costo_del_mes(IDX, MESES, "GL39002", "2026-12", date(2026, 12, 10)),
    (None, None),
)

# Pero si noviembre se carga, diciembre hereda de noviembre.
IDX_CON_NOV = dict(IDX)
IDX_CON_NOV[("GL39002", "2026-11")] = [(date(2026, 11, 6), 700.0)]
revisar(
    "con noviembre cargado, diciembre hereda de noviembre",
    costo_del_mes(IDX_CON_NOV, MESES | {"2026-11"}, "GL39002", "2026-12",
                  date(2026, 12, 10)),
    (700.0, "2026-11"),
)

# El salto de ano no se rompe: enero hereda de diciembre.
IDX_DIC = {("GL39002", "2026-12"): [(date(2026, 12, 6), 600.0)]}
revisar(
    "enero hereda de diciembre del ano anterior",
    costo_del_mes(IDX_DIC, {"2026-12"}, "GL39002", "2027-01", date(2027, 1, 10)),
    (600.0, "2026-12"),
)

# Un articulo nuevo que aparecio recien en el mes sin lista: no hay de donde
# heredar, y eso esta bien. Queda sin costo hasta que se cargue la lista.
revisar(
    "un articulo nuevo no tiene de donde heredar",
    costo_del_mes(IDX, MESES, "GL77777", "2026-11", date(2026, 11, 6)),
    (None, None),
)

# ---------------------------------------------------------------------------
#  LO QUE YA ANDABA SIGUE ANDANDO
# ---------------------------------------------------------------------------

# Una venta ANTES del primer tramo del mes sigue sin costo. Pasa cuando se carga
# una lista de mitad de mes sin la del mes, y heredar ahi taparia ese error de
# carga.
IDX_TARDE = {("GL39002", "2026-10"): [(date(2026, 10, 16), 1000.0)]}
revisar(
    "una venta anterior al primer tramo sigue sin costo",
    costo_del_mes(IDX_TARDE, {"2026-10"}, "GL39002", "2026-10", date(2026, 10, 8)),
    (None, None),
)

# costo_vigente no se toco: estas dos son para que si alguien lo cambia, se
# entere de que la herencia se apoya en el.
revisar("costo_vigente elige el tramo del dia",
        costo_vigente(IDX[("GL39002", "2026-10")], date(2026, 10, 30)), 900.0)
revisar("costo_vigente sin tramos es None",
        costo_vigente([], date(2026, 10, 30)), None)
revisar("costo_vigente sin fecha es None",
        costo_vigente(IDX[("GL39002", "2026-10")], None), None)

# Un mes sin lista y SIN mes anterior cargado tampoco explota.
revisar(
    "indice vacio no explota",
    costo_del_mes({}, set(), "GL39002", "2026-11", date(2026, 11, 6)),
    (None, None),
)


print()
if FALLOS:
    print(f"{len(FALLOS)} prueba(s) MAL: {', '.join(FALLOS)}")
    raise SystemExit(1)
print("Todo bien.")
