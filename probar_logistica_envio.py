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

from ml_envios import logistica_de_envio, texto_o_nada  # noqa: E402

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


print()
if FALLOS:
    print(f"{len(FALLOS)} prueba(s) MAL: {', '.join(FALLOS)}")
    raise SystemExit(1)
print("Todo bien.")
