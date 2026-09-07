"""Pruebas del mes comercial, incluidos los cierres movidos.

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
from datetime import date

# El modulo abre la base al importarse. Como acá sólo se prueba una función de
# fechas, se reemplazan las dos dependencias que tocan afuera.
sys.modules.setdefault("conexion", types.SimpleNamespace(crear_engine=lambda **k: None))
sys.modules.setdefault("errores_bd", types.SimpleNamespace())

import modelo  # noqa: E402
from modelo import mes_comercial  # noqa: E402

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# --- La regla del 6 al 5, sin excepciones ---------------------------------

SIN_EXCEPCIONES = {}
original = modelo.CIERRES_EXCEPCION
modelo.CIERRES_EXCEPCION = SIN_EXCEPCIONES

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

modelo.CIERRES_EXCEPCION = {"2026-08": date(2026, 9, 6)}

revisar("estirado: el 5 sigue siendo del mes", mes_comercial(date(2026, 9, 5)), "2026-08")
revisar("estirado: el 6 TAMBIEN es del mes", mes_comercial(date(2026, 9, 6)), "2026-08")
revisar("estirado: el 7 ya es del siguiente", mes_comercial(date(2026, 9, 7)), "2026-09")
# Lo de antes no se mueve: una excepcion al final del mes no puede cambiar
# donde empezo.
revisar("estirado: el arranque no se mueve", mes_comercial(date(2026, 8, 6)), "2026-08")
revisar("estirado: el mes anterior queda igual", mes_comercial(date(2026, 8, 5)), "2026-07")
revisar("estirado: dos meses despues, normal", mes_comercial(date(2026, 10, 6)), "2026-10")


# --- Un mes que se ACORTA: cede dias al siguiente --------------------------

modelo.CIERRES_EXCEPCION = {"2026-08": date(2026, 9, 2)}

revisar("acortado: el 2 todavia es del mes", mes_comercial(date(2026, 9, 2)), "2026-08")
revisar("acortado: el 3 ya es del siguiente", mes_comercial(date(2026, 9, 3)), "2026-09")
revisar("acortado: el 6 sigue siendo del siguiente", mes_comercial(date(2026, 9, 6)), "2026-09")


# --- Una excepcion que cruza el año ---------------------------------------

modelo.CIERRES_EXCEPCION = {"2026-12": date(2027, 1, 8)}

revisar("fin de año estirado: 8 de enero es diciembre",
        mes_comercial(date(2027, 1, 8)), "2026-12")
revisar("fin de año estirado: 9 de enero ya es enero",
        mes_comercial(date(2027, 1, 9)), "2027-01")


# --- La tabla que esta cargada de verdad ----------------------------------

modelo.CIERRES_EXCEPCION = original

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


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
