"""Pruebas de los costos con fecha de vigencia.

POR QUE ESTO TIENE PRUEBAS. Un costo que se aplica un dia antes o un dia
despues no se ve como un error: se ve como un margen raro en una pantalla, y
recien se nota semanas mas tarde. Las tres cuentas que deciden eso --de que dia
rige un archivo, que tramo le toca a una venta, y desde cuando hay que
recalcular gold-- se pueden probar sin base, asi que se prueban.

    python probar_costos_vigencia.py
"""

import sys
import types
from datetime import date

# Los dos modulos abren la base al importarse. Aca solo se prueban cuentas.
sys.modules.setdefault("conexion", types.SimpleNamespace(crear_engine=lambda **k: None))
sys.modules.setdefault("errores_bd", types.SimpleNamespace())
sys.modules.setdefault("estado", types.SimpleNamespace(
    leer=lambda *a, **k: None, guardar=lambda *a, **k: None))

import pandas as pd  # noqa: E402

import calendario  # noqa: E402
import costos  # noqa: E402
import modelo  # noqa: E402

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# --- DE QUE DIA RIGE CADA ARCHIVO ------------------------------------------

revisar("la lista del mes rige desde que arranca el mes",
        costos.mes_y_vigencia("2026-10"), ("2026-10", date(2026, 10, 6)))
revisar("con cierre movido, desde el dia que arranca de verdad",
        costos.mes_y_vigencia("2026-09"), ("2026-09", date(2026, 9, 7)))
revisar("una lista de media de mes rige desde su fecha",
        costos.mes_y_vigencia("2026-09-18"), ("2026-09", date(2026, 9, 18)))
# El dia decide a que MES COMERCIAL pertenece el tramo, y eso no es el mes del
# nombre: una lista del 03/10 todavia es de septiembre.
revisar("una lista de principio de mes cae en el mes comercial anterior",
        costos.mes_y_vigencia("2026-10-03"), ("2026-09", date(2026, 10, 3)))

for malo in ["septiembre", "2026-9", "2026-09-1", "2026-09-18-2", ""]:
    try:
        costos.mes_y_vigencia(malo)
    except ValueError:
        revisar(f"nombre invalido rechazado: {malo!r}", True, True)
    else:
        revisar(f"nombre invalido rechazado: {malo!r}", False, True)


# --- QUE TRAMO LE TOCA A UNA VENTA -----------------------------------------

TRAMOS = [(date(2026, 9, 7), 100.0), (date(2026, 9, 18), 130.0)]

revisar("el dia que arranca el mes, el primer tramo",
        modelo.costo_vigente(TRAMOS, date(2026, 9, 7)), 100.0)
revisar("el dia antes del aumento, todavia el viejo",
        modelo.costo_vigente(TRAMOS, date(2026, 9, 17)), 100.0)
revisar("el dia del aumento ya es el nuevo",
        modelo.costo_vigente(TRAMOS, date(2026, 9, 18)), 130.0)
revisar("despues del aumento sigue el nuevo",
        modelo.costo_vigente(TRAMOS, date(2026, 10, 2)), 130.0)
revisar("antes del primer tramo no hay costo",
        modelo.costo_vigente(TRAMOS, date(2026, 9, 6)), None)
revisar("sin tramos no hay costo", modelo.costo_vigente(None, date(2026, 9, 9)), None)
revisar("sin fecha no hay costo", modelo.costo_vigente(TRAMOS, None), None)

# Un solo tramo es el caso de siempre: el mes entero con la misma lista.
UNICO = [(date(2026, 8, 6), 55.0)]
revisar("con un solo tramo, todo el mes vale lo mismo",
        modelo.costo_vigente(UNICO, date(2026, 8, 31)), 55.0)


# --- DESDE CUANDO HAY QUE RECALCULAR GOLD ----------------------------------
#
# Se compara contra la tabla con un `con` de mentira: lo unico que hace falta
# de la base son las filas que estan hoy.


class ConFalso:
    def __init__(self, filas):
        self.filas = filas

    def exec_driver_sql(self, sql, params=None):
        return types.SimpleNamespace(fetchall=lambda: self.filas)


def df(filas):
    return pd.DataFrame(
        filas, columns=["sku", "mes_comercial", "vigente_desde", "costo_real"])


VIEJAS = [
    ("A", "2026-08", date(2026, 8, 6), 10.0),
    ("A", "2026-09", date(2026, 9, 7), 20.0),
    ("B", "2026-09", date(2026, 9, 7), 30.0),
]

revisar("si no cambio ningun costo, no se recalcula nada",
        costos.vigencia_mas_vieja_que_cambia(ConFalso(VIEJAS), df(VIEJAS), "", {}),
        None)

cambiado = [
    ("A", "2026-08", date(2026, 8, 6), 10.0),
    ("A", "2026-09", date(2026, 9, 7), 25.0),      # cambio este
    ("B", "2026-09", date(2026, 9, 7), 30.0),
]
revisar("se recalcula desde la vigencia del costo que cambio",
        costos.vigencia_mas_vieja_que_cambia(ConFalso(VIEJAS), df(cambiado), "", {}),
        date(2026, 9, 7))

dos_cambios = [
    ("A", "2026-08", date(2026, 8, 6), 11.0),      # y este, mas viejo
    ("A", "2026-09", date(2026, 9, 7), 25.0),
    ("B", "2026-09", date(2026, 9, 7), 30.0),
]
revisar("con dos cambios gana el mas viejo",
        costos.vigencia_mas_vieja_que_cambia(ConFalso(VIEJAS), df(dos_cambios), "", {}),
        date(2026, 8, 6))

# Un articulo que deja de estar en el archivo tambien cambia: las lineas que lo
# tenian costeado se quedan sin costo, y eso hay que recalcularlo igual.
sin_b = [f for f in VIEJAS if f[0] != "B"]
revisar("un articulo que desaparece tambien manda a recalcular",
        costos.vigencia_mas_vieja_que_cambia(ConFalso(VIEJAS), df(sin_b), "", {}),
        date(2026, 9, 7))

# Y uno nuevo, tambien: antes no tenia costo y ahora si.
con_c = VIEJAS + [("C", "2026-09", date(2026, 9, 18), 40.0)]
revisar("un articulo nuevo tambien manda a recalcular",
        costos.vigencia_mas_vieja_que_cambia(ConFalso(VIEJAS), df(con_c), "", {}),
        date(2026, 9, 18))

# Un costo vacio no puede figurar como cambio en cada carga: NaN nunca es igual
# a NaN, y sin cuidado eso mandaria a reconstruir gold todas las corridas.
NAN = float("nan")
con_nan_viejo = [("A", "2026-09", date(2026, 9, 7), NAN)]
revisar("un costo vacio que sigue vacio no es un cambio",
        costos.vigencia_mas_vieja_que_cambia(
            ConFalso(con_nan_viejo), df(con_nan_viejo), "", {}),
        None)


# --- LOS DOS MODULOS USAN EL MISMO CALENDARIO -------------------------------
#
# Si costos.py calculara el arranque del mes por su cuenta, un cierre movido
# haria que la lista entre con una fecha y las ventas se etiqueten con otra.
revisar("costos.py usa el calendario de modelo.py",
        costos.inicio_del_mes_comercial is calendario.inicio_del_mes_comercial, True)
revisar("modelo.py usa el mismo mes_comercial",
        modelo.mes_comercial is calendario.mes_comercial, True)

# Y los dos lados de la anotacion tienen que nombrar la misma clave.
revisar("la clave del aviso es la misma en los dos",
        costos.CLAVE_RECONSTRUIR, modelo.CLAVE_COSTOS_PENDIENTES)


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
