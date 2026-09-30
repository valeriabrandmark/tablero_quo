"""Pruebas de la lectura de respuestas de ml_competencia.py, sin red ni base.

QUE SE PRUEBA Y POR QUE. price_to_win omite campos segun el estado de la
publicacion. Un campo que falta tiene que quedar en None ("no se sabe") y no en
cero: un precio para ganar en 0 se leeria en el tablero como "hay que regalarlo".

    python probar_competencia.py
"""

import json
import sys

from ml_competencia import (
    leer_comision,
    leer_items_de_producto,
    leer_price_to_win,
    leer_vendedor,
)

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# Perdiendo: trae ganador, boosts y motivos.
perdiendo = leer_price_to_win("MLA1", {
    "status": "competing", "current_price": 15000, "price_to_win": 13999.5,
    "visit_share": "minimum", "competitors_sharing_first_place": 0,
    "consistent": True,
    "boosts": [{"id": "fulfillment", "status": "boosted", "description": "x"},
               {"id": "free_installments", "status": "opportunity"}],
    "winner": {"item_id": "MLA9", "price": 13999.5,
               "boosts": [{"id": "free_installments", "status": "boosted"}]},
    "reason": ["better_price"],
})
revisar("perdiendo: estado", perdiendo["estado"], "competing")
revisar("perdiendo: no gana", perdiendo["ganando"], False)
revisar("perdiendo: precio para ganar", perdiendo["precio_para_ganar"], 13999.5)
revisar("perdiendo: ganador", (perdiendo["ganador_item_id"], perdiendo["ganador_precio"]),
        ("MLA9", 13999.5))
revisar("perdiendo: boosts sin descripcion", json.loads(perdiendo["boosts"]),
        [{"id": "fulfillment", "status": "boosted"},
         {"id": "free_installments", "status": "opportunity"}])
revisar("perdiendo: motivos", json.loads(perdiendo["motivos"]), ["better_price"])

# Listada sin competencia: no trae precio para ganar ni ganador.
sola = leer_price_to_win("MLA2", {"status": "listed", "current_price": 5000})
revisar("sin precio para ganar queda None", sola["precio_para_ganar"], None)
revisar("sin ganador queda None", sola["ganador_item_id"], None)
revisar("sin boosts es lista vacia", sola["boosts"], "[]")

# El motivo a veces viene como texto suelto.
revisar("motivo suelto", json.loads(leer_price_to_win("X", {"reason": "a"})["motivos"]), ["a"])

# Competidores: la nuestra se marca comparando como texto.
filas = leer_items_de_producto("MLA-P", {"results": [
    {"item_id": "A", "seller_id": 111, "price": 10,
     "shipping": {"free_shipping": True, "logistic_type": "fulfillment"}},
    {"item_id": "B", "seller_id": 222, "price": 9},
]}, "111")
revisar("competidores: dos filas", len(filas), 2)
revisar("competidores: la propia", [f["propia"] for f in filas], [True, False])
revisar("competidores: seller como texto", filas[1]["seller_id"], "222")
revisar("competidores: sin shipping queda None", filas[1]["logistica"], None)
revisar("producto sin resultados", leer_items_de_producto("P", {}, "1"), [])

# Comision: objeto o lista.
cuerpo = {"sale_fee_amount": 1800.5,
          "sale_fee_details": {"percentage_fee": 14.5, "fixed_fee": 350}}
revisar("comision objeto", leer_comision(cuerpo), (1800.5, 14.5, 350))
revisar("comision lista", leer_comision([cuerpo]), (1800.5, 14.5, 350))
revisar("comision vacia", leer_comision([]), (None, None, None))

v = leer_vendedor({"id": 5, "nickname": "RIVAL",
                   "seller_reputation": {"level_id": "5_green",
                                         "power_seller_status": "platinum",
                                         "transactions": {"total": 900}}})
revisar("vendedor", (v["seller_id"], v["apodo"], v["reputacion"], v["medalla"], v["ventas_totales"]),
        ("5", "RIVAL", "5_green", "platinum", 900))

if FALLOS:
    print(f"\n{len(FALLOS)} pruebas fallaron")
    sys.exit(1)
print("\nTodo OK")
