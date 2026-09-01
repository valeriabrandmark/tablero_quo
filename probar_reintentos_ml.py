"""Pruebas de los reintentos de `llamar_ml`, sin tocar la red.

QUE SE PRUEBA Y POR QUE. Un 429 y un 500 se parecen --los dos son "no te
contesto ahora"-- y se tratan distinto: el 429 lo pide la API y hasta dice
cuanto esperar; el 5xx es una caida de ellos y no avisa nada. Que los dos
contadores sean independientes es lo que evita que una racha de 429 deje sin
intentos al primer 500 que llegue despues.

Y sobre todo: que un 5xx NO sea definitivo. El 01/09/2026 la corrida de
antiguedad perdio 295 inventarios de 1.792 por 500 de Mercado Libre que se
tomaron como error final.

    python probar_reintentos_ml.py
"""

import requests

import mercadolibre

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


class RespuestaFalsa:
    def __init__(self, status, cuerpo=None, headers=None):
        self.status_code = status
        self._cuerpo = cuerpo if cuerpo is not None else {"ok": True}
        self.headers = headers or {}
        self.text = str(self._cuerpo)

    def json(self):
        return self._cuerpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


def correr(respuestas, **kwargs):
    """Corre `llamar_ml` contra una lista de respuestas preparadas.

    Devuelve (resultado_o_error, cuantas_llamadas, cuanto_durmio).
    """
    pendientes = list(respuestas)
    llamadas = []
    dormido = []

    def get_falso(url, headers=None, params=None, timeout=None):
        llamadas.append(url)
        return pendientes.pop(0)

    original_get, original_sleep = requests.get, mercadolibre.time.sleep
    requests.get = get_falso
    mercadolibre.time.sleep = dormido.append
    try:
        try:
            salida = mercadolibre.llamar_ml("/x", "token", pausa=False, **kwargs)
        except requests.HTTPError as e:
            salida = f"HTTPError {e.response.status_code}"
    finally:
        requests.get = original_get
        mercadolibre.time.sleep = original_sleep

    return salida, len(llamadas), dormido


# --- el camino feliz -------------------------------------------------------

salida, llamadas, dormido = correr([RespuestaFalsa(200, {"dato": 1})])
revisar("200 a la primera: devuelve el json", salida, {"dato": 1})
revisar("200 a la primera: una sola llamada", llamadas, 1)
revisar("200 a la primera: no duerme", dormido, [])


# --- 5xx: se reintenta, no es definitivo -----------------------------------

salida, llamadas, dormido = correr([RespuestaFalsa(500), RespuestaFalsa(200, {"dato": 2})])
revisar("500 y despues 200: se recupera", salida, {"dato": 2})
revisar("500 y despues 200: dos llamadas", llamadas, 2)
revisar("500 y despues 200: espera 2s", dormido, [2])

# La espera se duplica: machacar una API caida no la levanta antes.
salida, llamadas, dormido = correr(
    [RespuestaFalsa(500), RespuestaFalsa(502), RespuestaFalsa(503), RespuestaFalsa(200)]
)
revisar("tres 5xx seguidos: se recupera", salida, {"ok": True})
revisar("tres 5xx seguidos: la espera se duplica", dormido, [2, 4, 8])

# Y en algun momento se rinde: mejor un error a la vista que una corrida eterna.
salida, llamadas, dormido = correr([RespuestaFalsa(500)] * 5)
revisar("5xx que no para: se rinde", salida, "HTTPError 500")
revisar("5xx que no para: 4 llamadas (1 + 3 reintentos)", llamadas, 4)

# 503 y 504 son lo mismo que un 500: la API no esta, y vuelve.
salida, _, _ = correr([RespuestaFalsa(504), RespuestaFalsa(200, {"dato": 3})])
revisar("504 tambien se reintenta", salida, {"dato": 3})


# --- 4xx: no se reintenta, porque no se arregla solo -----------------------

salida, llamadas, dormido = correr([RespuestaFalsa(400)])
revisar("400: no se reintenta", llamadas, 1)
revisar("400: explota", salida, "HTTPError 400")

salida, llamadas, _ = correr([RespuestaFalsa(404)])
revisar("404: no se reintenta", llamadas, 1)


# --- 429: su propio contador y su propia espera ----------------------------

salida, llamadas, dormido = correr(
    [RespuestaFalsa(429, headers={"Retry-After": "7"}), RespuestaFalsa(200)]
)
revisar("429: respeta el Retry-After", dormido, [7])
revisar("429: se recupera", salida, {"ok": True})

# Sin Retry-After, la espera propia (5, 10, 15...).
salida, _, dormido = correr([RespuestaFalsa(429), RespuestaFalsa(429), RespuestaFalsa(200)])
revisar("429 sin Retry-After: espera propia", dormido, [5, 10])

# LO QUE IMPORTA: los contadores son independientes. Tres 429 seguidos no dejan
# sin intentos al 500 que llega despues.
salida, llamadas, dormido = correr(
    [RespuestaFalsa(429), RespuestaFalsa(429), RespuestaFalsa(429),
     RespuestaFalsa(500), RespuestaFalsa(200, {"dato": 4})]
)
revisar("429 y 5xx no comparten contador", salida, {"dato": 4})
revisar("429 y 5xx: cada uno con su espera", dormido, [5, 10, 15, 2])


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
