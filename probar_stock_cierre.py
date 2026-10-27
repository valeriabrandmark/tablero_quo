"""La foto del stock al cierre de mes. Sin base.

Lo que se prueba es que NO SE PIERDA NADA al juntar las dos fuentes. Digip y
Sigma no coinciden --GL05031 el 27/10/2026: 27 contra 84-- y toda la razon de
guardar las dos es poder ver esa diferencia despues. Un SKU que quede afuera
porque esta en una sola, o una suma que se pise en vez de acumularse, borra
justo lo que la tabla existe para registrar.
"""

from stock_cierre_mes import armar_foto

FALLOS = []


def revisar(nombre, obtenido, esperado):
    ok = obtenido == esperado
    print(f"OK  {nombre}" if ok else
          f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
    if not ok:
        FALLOS.append(nombre)


MES = "2026-10"

df = armar_foto(
    digip=[("A", 27), ("B", 10), ("C", 5)],
    sigma=[("A", 84), ("B", 10), ("D", 7)],
    mes=MES,
)
fila = {r.sku: (r.stock_digip, r.stock_sigma) for r in df.itertuples()}

revisar("estan los SKU de las dos fuentes", sorted(fila), ["A", "B", "C", "D"])
revisar("cada fuente guarda lo suyo", fila["A"], (27.0, 84.0))
revisar("cuando coinciden, coinciden", fila["B"], (10.0, 10.0))

# EL QUE ESTA EN UNA SOLA NO SE DESCARTA: queda en 0 del otro lado. Que Sigma
# tenga unidades donde Digip no tiene es un dato, no un error a esconder.
revisar("el que solo esta en Digip queda con sigma en 0", fila["C"], (5.0, 0.0))
revisar("y al reves", fila["D"], (0.0, 7.0))

revisar("todas las filas llevan el mes", set(df["mes_comercial"]), {MES})

# UN SKU REPARTIDO EN VARIOS DEPOSITOS O LOTES LLEGA EN VARIAS FILAS: se SUMAN.
# Pisando en vez de acumular, el stock de un articulo en tres depositos
# quedaria en el del ultimo.
sumado = armar_foto(digip=[("A", 10), ("A", 5), ("A", 2)], sigma=[], mes=MES)
revisar("las filas repetidas se suman", float(sumado.iloc[0]["stock_digip"]), 17.0)

# Nulos y vacios: un codigo vacio no es un articulo.
limpio = armar_foto(digip=[("", 9), (None, 4), ("A", None)], sigma=[], mes=MES)
revisar("sin codigo no hay fila", list(limpio["sku"]), ["A"])
revisar("un stock nulo cuenta como cero", float(limpio.iloc[0]["stock_digip"]), 0.0)

revisar("dos fuentes vacias dan una foto vacia", armar_foto([], [], MES).empty, True)

print(f"\n{len(FALLOS)} pruebas fallaron" if FALLOS else "\nTodo OK")
raise SystemExit(1 if FALLOS else 0)
