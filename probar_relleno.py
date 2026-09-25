"""Pruebas de los limites de una reconstruccion. Sin base, sin red.

LO QUE SE CUIDA ACA ES UN BORRADO. La corrida de todos los dias hace

    DELETE FROM gold.fact_ventas WHERE fecha >= CUTOFF

y esta bien, porque su ventana termina en hoy. El modo relleno usa la misma
maquinaria para traer un rango VIEJO, y ahi ese mismo borrado sin techo se
lleva puestos los cuatro meses buenos que vienen despues, para insertar cinco
lineas de febrero. No tira ningun error: la tabla queda con cinco filas.

Por eso las pruebas de abajo miran sobre todo que el techo ESTE, y que los
limites del borrado sean los mismos con los que se armaron las filas que se
van a insertar. Si algun dia alguien simplifica `condicion_borrado` porque "el
techo casi nunca se usa", esto es lo unico que lo va a frenar.

    python probar_relleno.py
"""

from datetime import date, timedelta

from relleno import condicion_borrado, fuera_de_ventana

FALLOS = []


def revisar(nombre, ok, detalle=""):
    print(f"OK  {nombre}" if ok else f"MAL {nombre}" + (f"\n     {detalle}" if detalle else ""))
    if not ok:
        FALLOS.append(nombre)


CUTOFF = date(2026, 2, 1)
HASTA = date(2026, 5, 5)
HOY = date(2026, 9, 25)

# --- La ventana de la corrida normal: piso y nada mas ----------------------

revisar("antes del piso, afuera",
        fuera_de_ventana(date(2026, 1, 31), CUTOFF) is True)
revisar("justo en el piso, adentro",
        fuera_de_ventana(CUTOFF, CUTOFF) is False)
# Sin techo, cualquier cosa posterior entra: la ventana de todos los dias
# termina en hoy y no hay nada mas arriba que proteger.
revisar("sin techo, lo de hoy entra",
        fuera_de_ventana(HOY, CUTOFF) is False)
revisar("una linea sin fecha queda afuera",
        fuera_de_ventana(None, CUTOFF) is True)

# --- Con techo: el rango es cerrado de los dos lados -----------------------

revisar("justo en el techo, adentro",
        fuera_de_ventana(HASTA, CUTOFF, HASTA) is False)
revisar("un dia despues del techo, afuera",
        fuera_de_ventana(date(2026, 5, 6), CUTOFF, HASTA) is True)
# EL CASO QUE IMPORTA: sin el techo esta fecha entraria, y su linea se
# insertaria pisando lo que ya estaba bien.
revisar("lo de cuatro meses despues, afuera",
        fuera_de_ventana(HOY, CUTOFF, HASTA) is True)

# --- El borrado de la corrida de todos los dias, palabra por palabra -------
#
# Tiene que quedar EXACTAMENTE como estaba antes de que existiera el relleno:
# ese camino corre cada hora y no es el que se esta cambiando.

donde, valores = condicion_borrado(CUTOFF)
revisar("la corrida normal borra de CUTOFF en adelante",
        donde == "fecha >= %(cutoff)s", donde)
revisar("y no manda ningun valor de mas",
        set(valores) == {"cutoff"}, str(valores))

# --- El borrado del relleno lleva los dos limites --------------------------

donde, valores = condicion_borrado(CUTOFF, HASTA, "DIAPER GENIE")
revisar("el relleno acota por arriba", "fecha <= %(hasta)s" in donde, donde)
revisar("y por marca", "%(marca)s" in donde, donde)
revisar("con los tres valores", set(valores) == {"cutoff", "hasta", "marca"}, str(valores))
revisar("y los valores son los que se pidieron",
        valores["cutoff"] == CUTOFF
        and valores["hasta"] == HASTA
        and valores["marca"] == "DIAPER GENIE", str(valores))

# Las tres condiciones van en AND: con un OR colado, el borrado se comeria
# todo lo de la marca en cualquier fecha, o todas las marcas del rango.
revisar("las condiciones van en AND", " OR " not in donde.upper(), donde)
revisar("son tres", donde.count("AND") == 2, donde)

# --- Cada limite se agrega solo si se pidio --------------------------------

donde, valores = condicion_borrado(CUTOFF, HASTA)
revisar("sin marca, no filtra por marca", "%(marca)s" not in donde, donde)
revisar("pero el techo sigue", "fecha <= %(hasta)s" in donde, donde)

donde, valores = condicion_borrado(CUTOFF, None, "DIAPER GENIE")
revisar("sin techo, no inventa uno", "%(hasta)s" not in donde, donde)
revisar("y filtra por marca igual", "%(marca)s" in donde, donde)

# --- La marca se compara normalizada --------------------------------------
#
# El maestro de Sigma la trae como la escribio quien la cargo, asi que la
# comparacion no puede ser sensible a mayusculas ni a espacios de mas.

donde, _ = condicion_borrado(CUTOFF, HASTA, "DIAPER GENIE")
revisar("la marca se compara en mayusculas", "upper(" in donde, donde)
revisar("y sin espacios alrededor", "trim(" in donde, donde)
# Un NULL en marca no puede hacer que la fila se escape del borrado.
revisar("una marca nula cuenta como vacia", "coalesce(marca" in donde, donde)

# --- El piso del relleno de Mercado Libre ----------------------------------
#
# `rellenar_ventas_ml.py` copiaba `ml.FECHA_CORTE` como limite duro, y por eso
# freno el pedido de las ventas de febrero: un rango perfectamente valido, que
# la API de ML sirve sin problema. Ahora el piso es el default y se abre con
# --antes-del-piso.
#
# Se prueba la funcion y no el parser porque es la unica parte del script que
# decide algo; lo demas es red.

import mercadolibre as ml
from rellenar_ventas_ml import motivo_para_no_correr

ANTES = date(2026, 2, 1)
DESPUES = date(2026, 5, 6)

revisar("un rango normal se corre",
        motivo_para_no_correr(DESPUES, date(2026, 9, 21), 15, False, HOY) is None)

# LO QUE ROMPIO: sin el flag, el piso frena algo que la API si puede dar.
freno = motivo_para_no_correr(ANTES, date(2026, 5, 5), 15, False, HOY)
revisar("antes del piso, sin permiso, no corre", freno is not None)
revisar("y el error dice como habilitarlo",
        freno is not None and "--antes-del-piso" in freno, str(freno))

revisar("antes del piso, con permiso, corre",
        motivo_para_no_correr(ANTES, date(2026, 5, 5), 15, True, HOY) is None)

# El permiso abre el piso y NADA MAS: los otros tres frenos siguen.
revisar("con permiso, un rango al reves sigue frenado",
        motivo_para_no_correr(date(2026, 5, 5), ANTES, 15, True, HOY) is not None)
revisar("con permiso, el futuro sigue frenado",
        motivo_para_no_correr(ANTES, date(2026, 12, 1), 15, True, HOY) is not None)
revisar("con permiso, un tramo de cero dias sigue frenado",
        motivo_para_no_correr(ANTES, date(2026, 5, 5), 0, True, HOY) is not None)

# El piso es el de mercadolibre.py, no una copia que pueda quedar desfasada.
revisar("el piso sale de mercadolibre.py",
        motivo_para_no_correr(ml.FECHA_CORTE, ml.FECHA_CORTE, 15, False, HOY) is None
        and motivo_para_no_correr(
            ml.FECHA_CORTE - timedelta(days=1), ml.FECHA_CORTE, 15, False, HOY,
        ) is not None)

# --- El filtro de marca sobre el JSON de una orden de ML -------------------
#
# Es el que hace que rellenar los envios de una marca sean 14 llamadas a la API
# y no 28.000. Se prueba contra el MISMO serializador con el que bronze guarda
# `order_items`: si algun dia json.dumps cambia de formato --o alguien le pone
# separators=(',',':')-- el pedazo buscado deja de aparecer y el filtro no
# encontraria una sola orden, en silencio.

import json

from relleno import MOLDE_SKU, condicion_marca_en_items

def _como_lo_guarda_bronze(sku):
    """Un order_items de una sola linea, serializado como en guardado.py."""
    return json.dumps([{"item": {"seller_sku": sku, "id": "MLA1"}, "quantity": 1}],
                      ensure_ascii=False)

guardado = _como_lo_guarda_bronze("AC01001")
revisar("el molde es el que json.dumps escribe de verdad",
        MOLDE_SKU.format(sku="AC01001") in guardado, guardado)

# LA COMILLA DEL FINAL NO ES DECORACION: sin ella, pedir los envios de un SKU
# se llevaria puestos los de todos los que empiezan igual.
revisar("un SKU que es prefijo de otro no coincide",
        MOLDE_SKU.format(sku="AC0100") not in guardado)
revisar("y uno que no esta, tampoco",
        MOLDE_SKU.format(sku="AC01002") not in guardado)

clausula = condicion_marca_en_items("v.order_items")
revisar("la clausula mira la columna que se le pasa",
        "strpos(v.order_items," in clausula, clausula)
revisar("y recibe los SKU como parametro", "%(skus)s::text[]" in clausula, clausula)

# Sin comodines: psycopg2 lee cualquier otro % de la consulta como parametro
# suyo, asi que un LIKE '%...%' aca rompe la consulta entera.
revisar("no usa comodines", clausula.replace("%(skus)s", "") .count("%") == 0, clausula)

print(f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}" if FALLOS else "\nTODO OK")
raise SystemExit(1 if FALLOS else 0)
