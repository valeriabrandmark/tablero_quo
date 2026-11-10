"""Los dias con stock por deposito. Sin base.

El caso que lo origino, tal como lo conto compras: entran 30 unidades el 1 de
enero, se venden 10 por dia el 1, el 2 y el 3, y despues no hay mas. A fin de
mes el ritmo tiene que salir de 3 dias con stock, no de 30.
"""

import datetime as dt

from stock_diario import calcular, dias_con_stock, resumir

FALLOS = []


def revisar(nombre, obtenido, esperado):
    ok = obtenido == esperado
    print(f"OK  {nombre}" if ok else
          f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
    if not ok:
        FALLOS.append(nombre)


def d(n):
    return dt.date(2026, 1, n)


HOY = d(30)

# --- El caso de compras, sin ninguna foto vieja: todo reconstruido ---------
dias = dias_con_stock(
    HOY, 30,
    fotos={HOY: 0},
    entradas={d(1): 30},
    salidas={d(1): 10, d(2): 10, d(3): 10},
    ventas={d(1): 10, d(2): 10, d(3): 10},
)
con = sorted(x[0].day for x in dias if x[1])
revisar("30 entran, 10/dia por 3 dias: tuvo stock el 1, 2 y 3", con, [1, 2, 3])
revisar("  solo hoy sale de foto, el resto es reconstruido",
        [x[0].day for x in dias if x[2]], [30])

# --- Una foto real le gana a la reconstruccion -----------------------------
# La cuenta diria 0 el 15 (no hubo movimientos), pero la foto dice que habia 5:
# un ajuste de inventario que no vemos.
dias = dias_con_stock(HOY, 30, fotos={HOY: 0, d(15): 5},
                      entradas={}, salidas={}, ventas={})
con = sorted(x[0].day for x in dias if x[1])
revisar("la foto re-ancla: el 15 y para atras tenia stock",
        con, list(range(1, 16)))

# --- Nunca negativo: falta una compra vieja que no tenemos -----------------
# Hoy hay 10 y el 28 entraron 50: para atras daria -40. Sin el corte en 0, la
# venta de 20 del 26 dejaria -20 al abrir ese dia y el 25 "sin stock"; con el
# corte el 26 abre en 20 (lo minimo para poder vender), el 27 abre en 0 porque
# ya se vendio todo, y el 25 sigue con esos 20.
dias = dias_con_stock(HOY, 6, fotos={HOY: 10}, entradas={d(28): 50},
                      salidas={d(26): 20}, ventas={d(26): 20})
revisar("el corte en 0 no arrastra un negativo hacia atras",
        [x[1] for x in dias], [True, True, True, False, True, True])

# --- Una venta prueba que habia stock aunque la cuenta diga 0 --------------
dias = dias_con_stock(HOY, 3, fotos={HOY: 0}, entradas={},
                      salidas={}, ventas={d(29): 2})
revisar("vender prueba stock aunque la reconstruccion no lo vea",
        dias[1][1], True)

# --- Resumen por ventana ----------------------------------------------------
hoy = dt.date(2026, 11, 10)
serie = [(hoy - dt.timedelta(days=i), i < 10, i < 3) for i in range(120)]
r = resumir(serie, hoy)
revisar("ventana 30: 10 dias con stock, 3 con foto", r[30], (10, 3))
revisar("ventana 120: los mismos 10", r[120], (10, 3))

# --- Los depositos van separados --------------------------------------------
# 40 en Tucuman todo el tiempo; en Full entraron 12 hace 3 dias y se vendieron.
hoy = dt.date(2026, 11, 10)
hace = lambda n: hoy - dt.timedelta(days=n)  # noqa: E731
df = calcular(
    hoy,
    foto_tuc={"AL01081": {hoy: 40}},
    foto_full={"AL01081": {hoy: 0}},
    mov={"AL01081": {"envios": {hace(3): 12},
                     "ventas_full": {hace(3): 6, hace(2): 6}}},
)
fila = lambda dep, v: df[(df.deposito == dep) & (df.ventana == v)].iloc[0]  # noqa: E731
# Tucuman: 40 hoy, y hacia atras el envio a Full suma 12 -> siempre con stock.
revisar("Tucuman: con stock los 30 dias", int(fila("tuc", 30).dias_con_stock), 30)
revisar("Full: solo los 2 dias que tuvo (hace 3 entra y vende, hace 2 vende)",
        int(fila("full", 30).dias_con_stock), 2)

print(f"\n{len(FALLOS)} pruebas fallaron" if FALLOS else "\nTodo OK")
raise SystemExit(1 if FALLOS else 0)
