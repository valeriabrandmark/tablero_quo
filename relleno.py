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


# Como se ve un SKU adentro del JSON de una orden de Mercado Libre.
#
# `order_items` se guarda con json.dumps (ver listas_a_texto en guardado.py),
# que separa clave y valor con dos puntos Y UN ESPACIO. Por eso el pedazo que
# se busca es exactamente  "seller_sku": "AC01001"  --con ese espacio y con la
# comilla del final, que es la que hace que un SKU que sea prefijo de otro no
# se pueda colar.
MOLDE_SKU = '"seller_sku": "{sku}"'


def condicion_marca_en_items(columna, parametro="skus"):
    """El `AND` que deja solo las ordenes que llevan alguno de esos SKU.

    SE USA strpos Y NO LIKE. LIKE necesitaria comodines '%', y psycopg2 lee
    cualquier % de la consulta como un parametro suyo: un LIKE aca reventaria
    la consulta entera o, peor, la dejaria pasar mal armada.
    """
    molde = MOLDE_SKU.replace("{sku}", "' || s || '")
    return (f"EXISTS (SELECT 1 FROM unnest(%({parametro})s::text[]) s "
            f"WHERE strpos({columna}, '{molde}') > 0)")


# Las dos tablas de gold, y cual le toca a cada corrida.
#
# EL TABLERO CUENTA CON QUE fact_ventas ARRANCA EN LA FECHA DE CORTE. Todos
# sus paneles comparan meses, sacan promedios y arman ritmos sobre lo que
# encuentran ahi. Un relleno de una marca sola mete un abril de catorce
# lineas: correcto como dato, y un numero equivocado para cualquiera que mire
# abril, porque no tiene como saber que eso no es abril entero.
#
# Por eso lo que se rellena hacia atras va a otra tabla, con la misma forma.
# Quien lo necesita lo lee de ahi --o de una vista que une las dos-- y el
# tablero sigue viendo exactamente lo que veia.
TABLA_NORMAL = "fact_ventas"
TABLA_PREVIO = "fact_ventas_previo"


def tabla_destino(es_relleno):
    """En que tabla de gold escribe esta corrida."""
    return TABLA_PREVIO if es_relleno else TABLA_NORMAL
