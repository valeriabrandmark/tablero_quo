"""Los limites de una reconstruccion de gold.fact_ventas.

============================================================================
 POR QUE ESTO VIVE SOLO, Y NO ADENTRO DE modelo.py
============================================================================

Por lo mismo que `calendario.py`: `modelo.py` abre la base al importarse, asi
que nada que solo quiera hacer cuentas lo puede importar -- y eso incluye a
las pruebas.

Y ACA HAY ALGO QUE HAY QUE PODER PROBAR. La corrida de todos los dias borra
"de CUTOFF en adelante" y vuelve a insertar, sin techo, porque su ventana
termina en hoy. El modo relleno usa la misma maquinaria para traer un rango
VIEJO, y ahi ese borrado sin techo es una bomba:

    DELETE FROM gold.fact_ventas WHERE fecha >= '2026-02-01'

son cuatro meses de datos buenos borrados para insertar cinco lineas de
febrero. No tira ningun error: la tabla queda con cinco filas.

El dia que alguien simplifique esta funcion "porque el techo casi nunca se
usa", la prueba de `probar_relleno.py` es lo unico que lo va a frenar.
"""


def fuera_de_ventana(fecha, cutoff, hasta=None):
    """Si esa fecha queda afuera de lo que hay que reconstruir.

    El piso es `cutoff` y siempre esta. El techo `hasta` solo existe en el
    modo relleno; en la corrida de todos los dias es None, porque la ventana
    llega hasta hoy y no hay nada mas arriba que proteger.

    Sin fecha se considera afuera: una linea sin fecha no se puede ubicar en
    ninguna ventana, y meterla "por las dudas" la dejaria fuera del alcance de
    cualquier borrado futuro.
    """
    if fecha is None or fecha < cutoff:
        return True
    return hasta is not None and fecha > hasta


def condicion_borrado(cutoff, hasta=None, marca=None):
    """El `WHERE` del DELETE previo a insertar, como (sql, valores).

    LLEVA EXACTAMENTE LOS MISMOS LIMITES con los que se armaron las filas que
    se van a insertar. Esa es toda la regla, y es lo que hace que el relleno
    sea seguro y repetible: se borra lo que se va a reemplazar, ni un dia ni
    una marca de mas.

    Con `hasta` y `marca` en None --la corrida normal-- devuelve el borrado de
    siempre, palabra por palabra.
    """
    condiciones = ["fecha >= %(cutoff)s"]
    valores = {"cutoff": cutoff}

    if hasta is not None:
        condiciones.append("fecha <= %(hasta)s")
        valores["hasta"] = hasta

    if marca is not None:
        # La marca del maestro viene como la escribio quien la cargo, asi que
        # se compara normalizada de los dos lados.
        condiciones.append("upper(trim(coalesce(marca, ''))) = %(marca)s")
        valores["marca"] = marca

    return " AND ".join(condiciones), valores
