"""Pruebas de la lectura de la reposicion de Full, sin red ni base.

Los casos son las respuestas REALES del sondeo del 22/10/2026 (recortadas):
un producto restringido con historia de ventas, y uno restringido sin ventas
ni sugerencia. Que falte la sugerencia tiene que quedar en None, no en 0.

    python probar_reposicion.py
"""

import sys

from ml_reposicion_full import leer_reposicion

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


semana = {"units_sold": 0, "days_out_of_stock": 2, "campaigns": []}
algodon = leer_reposicion("MLAU3294611201", {
    "identifiers": {"user_product_id": "MLAU3294611201", "inventory_id": "ZTHJ78898",
                    "seller_sku": "AL09018"},
    "product": {"tags": ["is_restricted"], "packages_master_case": []},
    "stock": {"total_stock": 0, "shipping_urgency": "URGENT", "minimum_distributable_stock": 0},
    "sales": {"sales_totals": {"period": "30DAYS", "gmv": [{"full": 0.0}],
                               "units_sold": [{"full": 0}]},
              "sales_history": [semana] * 6},
    "recommendation": {"recommendation_type": "NO_REPLENISHMENT_BY_RESTRICTION",
                       "suggested_quantity": None, "replenishment_deadline": "THIS_WEEK",
                       "replenishment_frequency": 4},
    "eligibility_benefits": [],
})
revisar("restringido", algodon["restringido"], True)
revisar("recomendacion", algodon["recomendacion"], "NO_REPLENISHMENT_BY_RESTRICTION")
revisar("ids", (algodon["inventory_id"], algodon["sku_ml"]), ("ZTHJ78898", "AL09018"))
revisar("urgencia y plazo", (algodon["urgencia"], algodon["plazo"], algodon["frecuencia_dias"]),
        ("URGENT", "THIS_WEEK", 4))
revisar("ventas 30 dias en cero, no en None", (algodon["unidades_30d"], algodon["facturacion_30d"]),
        (0, 0.0))
revisar("sugerida en None", algodon["cantidad_sugerida"], None)
revisar("dias sin stock sumados", (algodon["semanas_historia"], algodon["dias_sin_stock"]), (6, 12))

perfumante = leer_reposicion("MLAU3950365878", {
    "identifiers": {"user_product_id": "MLAU3950365878", "inventory_id": "ZQBH00926"},
    "product": {"tags": ["is_restricted"]},
    "stock": {"total_stock": 0, "shipping_urgency": None},
    "sales": {"sales_totals": {}, "sales_history": []},
    "recommendation": {"recommendation_type": "NO_REPLENISHMENT_BY_RESTRICTION"},
})
revisar("sin ventas: unidades None", perfumante["unidades_30d"], None)
revisar("sin historia: dias sin stock None", perfumante["dias_sin_stock"], None)

# ===========================================================================
#  LA CANTIDAD SUGERIDA VIENE ADENTRO DE UN OBJETO
#
#  Esta es la respuesta REAL de LF02040 el 26/10/2026 (recortada). El caso que
#  habia antes aca tenia "suggested_quantity": 24 -- un numero suelto, escrito
#  a mano, porque el sondeo del 22/10 solo habia encontrado productos
#  restringidos, donde la cantidad es null de verdad y la forma no se ve.
#
#  Con la forma equivocada, la prueba pasaba y la produccion guardaba vacio:
#  pd.to_numeric convierte el diccionario en NaN sin quejarse. Mercado Libre
#  venia diciendo cuanto mandar de 1.192 productos y se tiraba.
# ===========================================================================
libre = leer_reposicion("MLAU1", {
    "product": {"tags": ["is_fulfillment"]},
    "stock": {"total_stock": 0, "shipping_urgency": "URGENT",
              "minimum_distributable_stock": 0},
    "recommendation": {"recommendation_type": "REPLENISH",
                       "suggested_quantity": {"type": "exact", "value": 140},
                       "replenishment_deadline": "THIS_WEEK",
                       "replenishment_frequency": 4},
})
revisar("no restringido", libre["restringido"], False)
revisar("sugerida sale del objeto", libre["cantidad_sugerida"], 140)
revisar("y se guarda el tipo", libre["tipo_cantidad"], "exact")
revisar("id del pedido si la API no lo trae", libre["user_product_id"], "MLAU1")

# UN TIPO QUE NO CONOCEMOS SE GUARDA IGUAL, con su valor. Hoy todos vienen
# "exact", pero que el campo exista quiere decir que hay otros; si aparece uno,
# tiene que poder verse en la base en vez de desaparecer.
raro = leer_reposicion("MLAU4", {
    "product": {"tags": []},
    "recommendation": {"recommendation_type": "REPLENISH",
                       "suggested_quantity": {"type": "range", "value": 50}},
})
revisar("un tipo nuevo no se pierde", (raro["tipo_cantidad"], raro["cantidad_sugerida"]),
        ("range", 50))

# SI ALGUN DIA VUELVE A SER UN NUMERO SUELTO tampoco se rompe: este arreglo no
# tiene por que convertirse en el error de maniana.
suelto = leer_reposicion("MLAU5", {
    "product": {"tags": []},
    "recommendation": {"recommendation_type": "REPLENISH", "suggested_quantity": 24},
})
revisar("un numero suelto se sigue leyendo",
        (suelto["tipo_cantidad"], suelto["cantidad_sugerida"]), ("exact", 24))

# Y LOS RESTRINGIDOS NO TIENEN CANTIDAD NI TIPO: ahi el null es de verdad.
revisar("el restringido no tiene tipo", algodon["tipo_cantidad"], None)

vacio = leer_reposicion("MLAU2", {})
revisar("respuesta vacia no rompe", (vacio["restringido"], vacio["recomendacion"]), (False, None))

if FALLOS:
    print(f"\n{len(FALLOS)} pruebas fallaron")
    sys.exit(1)
print("\nTodo OK")
