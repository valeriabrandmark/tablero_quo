"""Pruebas del mes comercial: a que mes cae una fecha y donde arranca cada mes.

POR QUE ESTO TIENE PRUEBAS. El mes comercial decide QUE LISTA DE COSTOS se le
aplica a cada venta: costos_historicos esta indexada por (sku, mes_comercial).
Una fecha en el mes equivocado no se ve como un error, se ve como un margen
raro -- o como una venta sin costo, con el margen inflado.

Y ahora la regla tiene excepciones, que es justo el tipo de cosa que se rompe
callada cuando alguien la toca seis meses despues.

    python probar_mes_comercial.py
"""

import sys
import types
from datetime import date, timedelta

import calendario
from calendario import inicio_del_mes_comercial, mes_comercial

# LAS EXCEPCIONES SE PARCHEAN EN `calendario`, QUE ES DONDE VIVEN. Parchearlas
# en `modelo` --que es de donde salieron-- no cambiaria nada: la funcion lee su
# propio modulo, y las pruebas pasarian sin estar probando el caso.
original = calendario.CIERRES_EXCEPCION

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# --- La regla del 6 al 5, sin excepciones ---------------------------------

SIN_EXCEPCIONES = {}
calendario.CIERRES_EXCEPCION = SIN_EXCEPCIONES

revisar("el 6 abre el mes", mes_comercial(date(2026, 8, 6)), "2026-08")
revisar("el 5 lo cierra", mes_comercial(date(2026, 9, 5)), "2026-08")
revisar("el 6 del mes siguiente ya es otro", mes_comercial(date(2026, 9, 6)), "2026-09")
revisar("fin de mes calendario no corta nada", mes_comercial(date(2026, 8, 31)), "2026-08")
revisar("el 1 pertenece al mes anterior", mes_comercial(date(2026, 9, 1)), "2026-08")
revisar("sin fecha, sin mes", mes_comercial(None), None)

# El cambio de año, que es donde se rompen las cuentas de meses.
revisar("5 de enero es diciembre", mes_comercial(date(2027, 1, 5)), "2026-12")
revisar("6 de enero ya es enero", mes_comercial(date(2027, 1, 6)), "2027-01")
revisar("6 de diciembre", mes_comercial(date(2026, 12, 6)), "2026-12")


# --- Un mes que se ESTIRA: se queda con dias del siguiente -----------------

calendario.CIERRES_EXCEPCION = {"2026-08": date(2026, 9, 6)}

revisar("estirado: el 5 sigue siendo del mes", mes_comercial(date(2026, 9, 5)), "2026-08")
revisar("estirado: el 6 TAMBIEN es del mes", mes_comercial(date(2026, 9, 6)), "2026-08")
revisar("estirado: el 7 ya es del siguiente", mes_comercial(date(2026, 9, 7)), "2026-09")
# Lo de antes no se mueve: una excepcion al final del mes no puede cambiar
# donde empezo.
revisar("estirado: el arranque no se mueve", mes_comercial(date(2026, 8, 6)), "2026-08")
revisar("estirado: el mes anterior queda igual", mes_comercial(date(2026, 8, 5)), "2026-07")
revisar("estirado: dos meses despues, normal", mes_comercial(date(2026, 10, 6)), "2026-10")


# --- Un mes que se ACORTA: cede dias al siguiente --------------------------

calendario.CIERRES_EXCEPCION = {"2026-08": date(2026, 9, 2)}

revisar("acortado: el 2 todavia es del mes", mes_comercial(date(2026, 9, 2)), "2026-08")
revisar("acortado: el 3 ya es del siguiente", mes_comercial(date(2026, 9, 3)), "2026-09")
revisar("acortado: el 6 sigue siendo del siguiente", mes_comercial(date(2026, 9, 6)), "2026-09")


# --- Una excepcion que cruza el año ---------------------------------------

calendario.CIERRES_EXCEPCION = {"2026-12": date(2027, 1, 8)}

revisar("fin de año estirado: 8 de enero es diciembre",
        mes_comercial(date(2027, 1, 8)), "2026-12")
revisar("fin de año estirado: 9 de enero ya es enero",
        mes_comercial(date(2027, 1, 9)), "2027-01")


# --- La tabla que esta cargada de verdad ----------------------------------

calendario.CIERRES_EXCEPCION = original

# Esta es la excepcion real del cierre de agosto 2026. Cuando deje de hacer
# falta se saca de modelo.py y este bloque se borra con ella.
if "2026-08" in original:
    revisar("real: el 06/09 va con costos de agosto",
            mes_comercial(date(2026, 9, 6)), "2026-08")
    revisar("real: el 07/09 ya toma los de septiembre",
            mes_comercial(date(2026, 9, 7)), "2026-09")

# Los cierres tienen que ser fechas, no textos: comparar un date con un str
# explota, y explotaria adentro de la corrida y no aca.
for mes, fin in original.items():
    revisar(f"real: {mes} cierra con una fecha", isinstance(fin, date), True)


# --- DONDE ARRANCA EL MES ---------------------------------------------------
#
# De aca sale la vigencia con la que entra la lista de costos de cada mes
# (costos.py). Si dijera un dia de mas, las ventas de ese dia se quedarian sin
# costo; uno de menos, se costearian con la lista que todavia no regia.

calendario.CIERRES_EXCEPCION = SIN_EXCEPCIONES
revisar("arranque: normalmente el 6", inicio_del_mes_comercial("2026-10"),
        date(2026, 10, 6))
revisar("arranque: enero tambien", inicio_del_mes_comercial("2027-01"),
        date(2027, 1, 6))

calendario.CIERRES_EXCEPCION = {"2026-08": date(2026, 9, 6)}
revisar("arranque: el mes de la excepcion no se mueve",
        inicio_del_mes_comercial("2026-08"), date(2026, 8, 6))
revisar("arranque: el siguiente arranca el dia despues del cierre",
        inicio_del_mes_comercial("2026-09"), date(2026, 9, 7))

calendario.CIERRES_EXCEPCION = {"2026-08": date(2026, 9, 2)}
revisar("arranque: si el mes se acorto, el siguiente empieza antes",
        inicio_del_mes_comercial("2026-09"), date(2026, 9, 3))

calendario.CIERRES_EXCEPCION = {"2026-12": date(2027, 1, 8)}
revisar("arranque: cruzando el año", inicio_del_mes_comercial("2027-01"),
        date(2027, 1, 9))

calendario.CIERRES_EXCEPCION = original

# EL ARRANQUE Y EL MES TIENEN QUE DECIR LO MISMO. El dia anterior al arranque
# pertenece a otro mes: si no, las dos funciones estarian contando distinto.
for mes in ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]:
    arranque = inicio_del_mes_comercial(mes)
    revisar(f"coherencia: {mes} arranca donde dice mes_comercial",
            mes_comercial(arranque), mes)
    revisar(f"coherencia: el dia antes de {mes} no es de {mes}",
            mes_comercial(arranque - timedelta(days=1)) != mes,
            True)


# --- modelo.py sigue ofreciendo la funcion ----------------------------------
#
# Medio modelo.py la usa sin calificar y este mismo archivo la importaba de
# ahi. El modulo abre la base al importarse, asi que se reemplazan las dos
# dependencias que tocan afuera.
sys.modules.setdefault("conexion", types.SimpleNamespace(crear_engine=lambda **k: None))
sys.modules.setdefault("errores_bd", types.SimpleNamespace())
sys.modules.setdefault("estado", types.SimpleNamespace(
    leer=lambda *a, **k: None, guardar=lambda *a, **k: None))
import modelo  # noqa: E402

revisar("modelo importa la misma funcion", modelo.mes_comercial is mes_comercial, True)


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
