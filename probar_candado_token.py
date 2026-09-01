"""Pruebas del candado y del reuso del token de Mercado Libre.

No tocan la red ni la base: lo que se prueba es la DECISION de renovar o no, y
que el numero del candado sea el mismo en cualquier maquina. Las dos cosas son
las que, si se rompen, se rompen en silencio: el sintoma no aparece en la
corrida que las rompio sino en la siguiente, con un `invalid_grant` que hay que
arreglar reautorizando a mano.

    python probar_candado_token.py
"""

import datetime

import estado
from mercadolibre import MARGEN_TOKEN, sellar_vencimiento, vigente

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


AHORA = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=datetime.timezone.utc)


def con_vencimiento(delta, access="abc"):
    return {"access_token": access, "vence_en": (AHORA + delta).isoformat()}


# --- vigente: cuando el token guardado todavia sirve -----------------------

revisar("sin token guardado", vigente(None, AHORA), False)
revisar("token vacio", vigente({}, AHORA), False)

# El token de antes de este cambio no trae `vence_en`. Se toma por vencido a
# proposito: no se sabe cuando se pidio, y darlo por bueno seria adivinar.
revisar("sin vence_en", vigente({"access_token": "abc"}, AHORA), False)

revisar("vencido hace una hora",
        vigente(con_vencimiento(-datetime.timedelta(hours=1)), AHORA), False)
revisar("vence justo ahora",
        vigente(con_vencimiento(datetime.timedelta(0)), AHORA), False)

# El margen es lo que evita quedarse sin token en el medio de una corrida de 35
# minutos: adentro del margen se renueva aunque tecnicamente todavia valga.
revisar("dentro del margen (5 min)",
        vigente(con_vencimiento(datetime.timedelta(minutes=5)), AHORA), False)
revisar("justo en el borde del margen",
        vigente(con_vencimiento(MARGEN_TOKEN), AHORA), False)
revisar("un minuto despues del margen",
        vigente(con_vencimiento(MARGEN_TOKEN + datetime.timedelta(minutes=1)), AHORA),
        True)
revisar("recien renovado (6 horas)",
        vigente(con_vencimiento(datetime.timedelta(hours=6)), AHORA), True)

# Una fecha sin zona se lee como UTC en vez de explotar: si `vence_en` viniera
# raro, lo peor que puede pasar es una renovacion de mas, nunca una corrida
# muerta.
revisar("vence_en sin zona horaria",
        vigente({"access_token": "abc", "vence_en": "2026-09-01T20:00:00"}, AHORA),
        True)
revisar("vence_en ilegible",
        vigente({"access_token": "abc", "vence_en": "el martes"}, AHORA), False)
revisar("vence_en en None",
        vigente({"access_token": "abc", "vence_en": None}, AHORA), False)


# --- sellar_vencimiento: de "cuanto dura" a "hasta cuando" -----------------

sellado = sellar_vencimiento({"access_token": "abc", "expires_in": 21600}, AHORA)
revisar("sella 6 horas",
        sellado["vence_en"], (AHORA + datetime.timedelta(hours=6)).isoformat())
revisar("lo sellado queda vigente", vigente(sellado, AHORA), True)
revisar("no pierde el resto del token", sellado["access_token"], "abc")

# ML manda `expires_in` siempre, pero si un dia no lo mandara, guardar el token
# igual es mejor que perderlo: queda sin sellar y se renueva la proxima.
revisar("sin expires_in no inventa fecha",
        "vence_en" in sellar_vencimiento({"access_token": "abc"}, AHORA), False)

# Que sea una copia importa: `guardar_tokens` recibe lo que devolvio ML y no
# tiene por que modificarselo al que llamo.
original = {"access_token": "abc", "expires_in": 21600}
sellar_vencimiento(original, AHORA)
revisar("no modifica el diccionario original", "vence_en" in original, False)


# --- el numero del candado -------------------------------------------------

# ESTE NUMERO ESTA ESCRITO A MANO A PROPOSITO. Es el que Postgres usa para
# identificar el candado, y tiene que dar IGUAL en todas las maquinas. Con
# `hash()` de Python daria distinto en cada proceso --lleva una semilla
# aleatoria-- y cada runner tomaria un candado distinto: o sea, ninguno. Si este
# numero cambia, alguien toco la forma de calcularlo y hay que entender por que.
revisar("el numero del candado no cambia",
        estado._numero_de_candado("ml_tokens"), 5784918749378309150)
revisar("entra en un bigint de Postgres",
        -(2 ** 63) <= estado._numero_de_candado("ml_tokens") < 2 ** 63, True)
revisar("claves distintas, candados distintos",
        estado._numero_de_candado("ml_tokens") != estado._numero_de_candado("pasos"),
        True)


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
