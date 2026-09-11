"""El mes comercial: donde empieza, donde termina y a cual pertenece una fecha.

POR QUE ES UN MODULO APARTE

Esto vivia adentro de modelo.py, que abre la base al importarse. costos.py
tambien necesita las mismas reglas --tiene que saber en que dia arranca un mes
comercial-- y no puede importar modelo.py sin arrastrar la conexion. La
alternativa era copiar la regla en los dos lados, que es la forma segura de que
un dia digan cosas distintas.

Este modulo no importa nada del proyecto ni toca la red: solo fechas.
"""

from datetime import date, timedelta

# Primer dia del mes comercial. Del 6 de un mes al 5 del siguiente.
DIA_INICIO_MES_COMERCIAL = 6

# ============================================================================
#  MESES QUE NO CERRARON EL DIA 5
# ============================================================================
#
# La regla del 6 al 5 vale siempre, MENOS cuando la lista de costos nueva llega
# tarde o se decide estirar el mes. Ahi el cierre se corre unos dias, y las
# ventas de esos dias tienen que seguir costeandose con la lista vieja.
#
# ESTO NO ES UN AJUSTE COSMETICO. El mes comercial es lo que decide QUE LISTA DE
# COSTOS se le aplica a cada venta. Si una venta del 06/09 queda etiquetada
# 2026-09 y la lista de septiembre todavia no se cargo, esa venta se queda SIN
# COSTO y su margen aparece inflado -- o directamente en null.
#
# El valor es el ULTIMO DIA que pertenece a ese mes comercial, inclusive.
#
#   "2026-08": date(2026, 9, 6)   agosto cerro el 06/09 y no el 05/09, asi que
#                                 las ventas del 06/09 van con costos de agosto.
#                                 Septiembre arranca el 07/09.
#
# Sirve para los dos lados: un mes que se estira se queda con dias del
# siguiente, y uno que se acorta se los cede.
#
# OJO: el tablero tiene esta misma tabla en lib/constantes.ts. Las dos tienen
# que decir lo mismo, o el filtro "Mes comercial" de la pantalla va a mostrar un
# rango distinto del que tienen etiquetados los datos.
CIERRES_EXCEPCION = {
    "2026-08": date(2026, 9, 6),
}


def _mes_estandar(fecha):
    """La regla del 6 al 5, sin excepciones."""
    if fecha.day >= DIA_INICIO_MES_COMERCIAL:
        anio, mes = fecha.year, fecha.month
    else:
        if fecha.month == 1:
            anio, mes = fecha.year - 1, 12
        else:
            anio, mes = fecha.year, fecha.month - 1
    return f"{anio:04d}-{mes:02d}"


def correr_mes(mes, pasos):
    """'2026-08' mas o menos N meses."""
    anio, m = (int(x) for x in mes.split("-"))
    total = anio * 12 + (m - 1) + pasos
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def mes_comercial(fecha):
    """El mes comercial 'AAAA-MM' de una fecha, respetando los cierres movidos.

    Del 6 al 5, salvo que ese mes --o el anterior-- tenga un cierre distinto
    cargado en CIERRES_EXCEPCION.
    """
    if fecha is None:
        return None

    mes = _mes_estandar(fecha)

    # El mes ANTERIOR se estiro y esta fecha todavia le pertenece.
    anterior = correr_mes(mes, -1)
    fin_anterior = CIERRES_EXCEPCION.get(anterior)
    if fin_anterior is not None and fecha <= fin_anterior:
        return anterior

    # ESTE mes se acorto y la fecha ya quedo afuera: es del siguiente.
    fin = CIERRES_EXCEPCION.get(mes)
    if fin is not None and fecha > fin:
        return correr_mes(mes, 1)

    return mes


# Cuantos dias alrededor del 6 se buscan para encontrar el arranque de un mes.
#
# Un cierre movido corre el arranque unos pocos dias --el mas grande hasta hoy
# fue uno-- y un corrimiento de mas de veinte dias ya no seria "el mes cerro
# tarde", seria otro mes. Si algun dia hiciera falta mas, el que falla es este
# numero y avisa con un error, que es mejor que devolver una fecha de mentira.
DIAS_DE_BUSQUEDA = 20


def inicio_del_mes_comercial(mes):
    """El PRIMER dia que pertenece al mes comercial 'AAAA-MM'.

    Normalmente el 6. Con un cierre movido, el dia siguiente al cierre del mes
    anterior: 2026-09 arranca el 07/09 porque agosto se estiro hasta el 06/09.

    SE CALCULA PREGUNTANDOLE A `mes_comercial`, A PROPOSITO. Escribir la regla
    de nuevo aca --"el 6, salvo que el mes anterior tenga excepcion, y ahi el
    dia siguiente"-- serian dos versiones de la misma cuenta que hay que
    acordarse de cambiar juntas. Asi hay una sola: si `mes_comercial` cambia,
    esto cambia con ella.
    """
    anio, m = (int(x) for x in mes.split("-"))
    base = date(anio, m, DIA_INICIO_MES_COMERCIAL)
    for delta in range(-DIAS_DE_BUSQUEDA, DIAS_DE_BUSQUEDA + 1):
        dia = base + timedelta(days=delta)
        if mes_comercial(dia) == mes:
            return dia
    raise ValueError(
        f"No se encontro el arranque del mes comercial {mes} a menos de "
        f"{DIAS_DE_BUSQUEDA} dias del {base}. Revisar CIERRES_EXCEPCION."
    )
