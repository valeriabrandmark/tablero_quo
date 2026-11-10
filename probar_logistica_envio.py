"""Pruebas del lector de logistica de los envios de Mercado Libre.

POR QUE ESTO TIENE PRUEBAS. De este dato sale la columna "Logistica" del tablero
y el grafico de como se despacha: Full, Flex, Colecta o punto de despacho. Son
numeros que se usan para decidir --cuanto depende la operacion de Full-- y el
error facil es silencioso: si la funcion devuelve None para una forma del JSON
que ML si manda, el grafico no se rompe, simplemente cuenta esas ventas como
"sin dato" y da una foto equivocada sin avisar.

Los dos errores que cubre:

1. ML MOVIO EL CAMPO. `logistic_type` viaja suelto en la raiz del envio, y en el
   formato nuevo de la API va adentro de `logistic`. Las dos formas conviven, asi
   que leer una sola deja la mitad de los envios sin logistica.

2. UN MODO NUEVO NO SE PUEDE COMER. La funcion devuelve el valor crudo de ML y no
   una etiqueta: si manana aparece un modo que no conocemos tiene que pasar
   derecho, para que se vea como texto raro en el tablero en vez de esconderse
   dentro de un "Otros".

    python probar_logistica_envio.py
"""

import os
import sys
import types

# El modulo abre la base y lee ML_USER_ID al importarse. Aca solo se prueban las
# funciones puras, asi que se le pone lo minimo para que el import pase.
os.environ.setdefault("ML_USER_ID", "0")
sys.modules.setdefault("conexion", types.SimpleNamespace(crear_engine=lambda **k: None))
sys.modules.setdefault("mercadolibre", types.SimpleNamespace(token_ml=lambda **k: "x"))
sys.modules.setdefault(
    "relleno",
    types.SimpleNamespace(
        fuera_de_ventana=lambda *a: False,
        condicion_marca_en_items=lambda *a: "true",
    ),
)

from ml_envios import destino_de_envio, logistica_de_envio, texto_o_nada  # noqa: E402

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# ---------------------------------------------------------------------------
#  LA FORMA VIEJA: los campos sueltos en la raiz
# ---------------------------------------------------------------------------

revisar(
    "Full con los campos en la raiz",
    logistica_de_envio({"logistic_type": "fulfillment", "mode": "me2"}),
    ("fulfillment", "me2"),
)

revisar(
    "Flex",
    logistica_de_envio({"logistic_type": "self_service", "mode": "me2"}),
    ("self_service", "me2"),
)

revisar(
    "Colecta",
    logistica_de_envio({"logistic_type": "cross_docking", "mode": "me2"}),
    ("cross_docking", "me2"),
)

revisar(
    "punto de despacho",
    logistica_de_envio({"logistic_type": "drop_off", "mode": "me2"}),
    ("drop_off", "me2"),
)

# ---------------------------------------------------------------------------
#  LA FORMA NUEVA: todo adentro de `logistic`
# ---------------------------------------------------------------------------

revisar(
    "formato nuevo: logistic.type / logistic.mode",
    logistica_de_envio({"logistic": {"type": "fulfillment", "mode": "me2"}}),
    ("fulfillment", "me2"),
)

revisar(
    "la raiz gana cuando estan las dos",
    logistica_de_envio({
        "logistic_type": "self_service",
        "mode": "me2",
        "logistic": {"type": "fulfillment", "mode": "me1"},
    }),
    ("self_service", "me2"),
)

revisar(
    "mezcla: tipo en la raiz, modo solo en logistic",
    logistica_de_envio({
        "logistic_type": "cross_docking",
        "logistic": {"mode": "me2"},
    }),
    ("cross_docking", "me2"),
)

# ---------------------------------------------------------------------------
#  UN MODO QUE NO CONOCEMOS PASA DERECHO
# ---------------------------------------------------------------------------
#
# Si ML inventa un modo, tiene que llegar al tablero tal como vino. Esta prueba
# es la que se rompe el dia que alguien tenga la idea de mapear a una lista
# cerrada de valores conocidos aca abajo, que es justo lo que no hay que hacer.

revisar(
    "un modo desconocido no se descarta ni se renombra",
    logistica_de_envio({"logistic_type": "modo_nuevo_de_ml", "mode": "me3"}),
    ("modo_nuevo_de_ml", "me3"),
)

# ---------------------------------------------------------------------------
#  LO QUE VIENE MAL O NO VIENE
# ---------------------------------------------------------------------------

revisar("envio sin nada", logistica_de_envio({}), (None, None))
revisar("envio None", logistica_de_envio(None), (None, None))
revisar("respuesta que no es un objeto", logistica_de_envio([1, 2, 3]), (None, None))
revisar("respuesta que es un texto", logistica_de_envio("error"), (None, None))

revisar(
    "logistic que no es un objeto no explota",
    logistica_de_envio({"logistic": "fulfillment"}),
    (None, None),
)

revisar(
    "string vacio es None, no cadena vacia",
    logistica_de_envio({"logistic_type": "", "mode": ""}),
    (None, None),
)

revisar(
    "solo espacios tambien es None",
    logistica_de_envio({"logistic_type": "   ", "mode": "me2"}),
    (None, "me2"),
)

revisar(
    "tipo sin modo",
    logistica_de_envio({"logistic_type": "fulfillment"}),
    ("fulfillment", None),
)

revisar(
    "modo sin tipo: se guarda el modo igual",
    logistica_de_envio({"mode": "custom"}),
    (None, "custom"),
)

# `not_specified` es un valor REAL de ML --el envio que el comprador arregla por
# fuera-- y no un hueco. Tiene que llegar como texto y no convertirse en None,
# porque "no especificado" y "no lo bajamos todavia" son dos cosas distintas en
# el grafico.
revisar(
    "not_specified es un valor, no un hueco",
    logistica_de_envio({"logistic_type": "not_specified", "mode": "not_specified"}),
    ("not_specified", "not_specified"),
)

# ---------------------------------------------------------------------------
#  texto_o_nada
# ---------------------------------------------------------------------------

revisar("texto_o_nada recorta", texto_o_nada("  me2  "), "me2")
revisar("texto_o_nada con None", texto_o_nada(None), None)
revisar("texto_o_nada con vacio", texto_o_nada(""), None)
revisar("texto_o_nada con un numero", texto_o_nada(3), "3")


# ---------------------------------------------------------------------------
#  destino_de_envio: a donde fue la venta
# ---------------------------------------------------------------------------
#
# De aca sale el mapa de concentracion de ventas por zona. Lo que cubre:
# las dos formas del JSON, que no se cuele la direccion exacta, y que un envio
# sin direccion quede en None y no explote.

VACIO = {
    "destino_provincia_id": None, "destino_provincia": None,
    "destino_ciudad": None, "destino_barrio": None, "destino_cp": None,
}

DIRECCION = {
    "address_line": "Av. Siempreviva 742",
    "street_name": "Av. Siempreviva",
    "street_number": "742",
    "receiver_name": "Fulano",
    "receiver_phone": "1100000000",
    "zip_code": " 1642 ",
    "city": {"id": "TUxBQ1NBTjk4M2M", "name": "San Isidro"},
    "state": {"id": "AR-B", "name": "Buenos Aires"},
    "neighborhood": {"id": None, "name": "Beccar"},
    "country": {"id": "AR", "name": "Argentina"},
}

ESPERADO = {
    "destino_provincia_id": "AR-B",
    "destino_provincia": "Buenos Aires",
    "destino_ciudad": "San Isidro",
    "destino_barrio": "Beccar",
    "destino_cp": "1642",
}

revisar("destino: formato viejo, receiver_address",
        destino_de_envio({"receiver_address": DIRECCION}), ESPERADO)

revisar("destino: formato nuevo, destination.shipping_address",
        destino_de_envio({"destination": {"shipping_address": DIRECCION}}),
        ESPERADO)

revisar("destino: receiver_address vacio cae al formato nuevo",
        destino_de_envio({"receiver_address": {},
                          "destination": {"shipping_address": DIRECCION}}),
        ESPERADO)

# La calle, el nombre y el telefono vienen en la respuesta y NO se guardan. Si
# alguien agrega una columna con la direccion, esta prueba lo frena.
revisar("destino: solo devuelve la zona, nunca la direccion ni el nombre",
        sorted(destino_de_envio({"receiver_address": DIRECCION})),
        sorted(VACIO))

revisar("destino: CABA se distingue por el id",
        destino_de_envio({"receiver_address": {
            "state": {"id": "AR-C", "name": "Capital Federal"},
            "city": {"name": "Palermo"}, "zip_code": "1425"}})["destino_provincia_id"],
        "AR-C")

revisar("destino: envio sin direccion", destino_de_envio({"id": 1}), VACIO)
revisar("destino: None", destino_de_envio(None), VACIO)
revisar("destino: respuesta que no es objeto", destino_de_envio([1]), VACIO)
revisar("destino: campos que no son objetos no explotan",
        destino_de_envio({"receiver_address": {"state": "AR-B", "city": None,
                                               "zip_code": ""}}),
        VACIO)
revisar("destino: CP numerico pasa a texto",
        destino_de_envio({"receiver_address": {"zip_code": 1900}})["destino_cp"],
        "1900")


print()
if FALLOS:
    print(f"{len(FALLOS)} prueba(s) MAL: {', '.join(FALLOS)}")
    raise SystemExit(1)
print("Todo bien.")
