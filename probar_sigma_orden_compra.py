"""Sondeo del endpoint ImportOrdenDeCompra de SIGMA.

QUE ES ESTO Y POR QUE EXISTE
----------------------------
El tablero arma la orden de compra y hoy la baja como archivo para importarla
a mano en la grilla de SIGMA. El paso siguiente es que un boton la mande sola,
y para escribir ese boton hace falta saber DOS cosas que la documentacion no
dice: con que metodo HTTP se llama al endpoint, y que forma tiene el cuerpo.

Soporte de SIGMA confirmo el nombre: `importordendecompra`. Este script
averigua el resto preguntandole al servidor, que es la unica fuente confiable.

LO QUE YA SABEMOS
-----------------
La primera corrida contesto esto:

    GET ImportOrdenDeCompra -> 500 "Parametro content requerido"
    OPTIONS                 -> 200

O sea: la ruta se llama `ImportOrdenDeCompra`, existe, y no pide un JSON con
campos sueltos sino UN parametro llamado `content` con el contenido adentro.
Falta saber en que formato viene ese contenido.

NO CREA NINGUNA ORDEN
---------------------
Las dos primeras etapas son inofensivas: preguntan como se llama al endpoint,
no que haga algo. La tercera manda `content` con una cadena que NO puede ser
una orden, para que el servidor conteste que formato esperaba. Por eso:

  * no corre sola: hay que pasarle --sondear-formato;
  * antes de mandar imprime exactamente que va a mandar y pide confirmacion;
  * lo que manda no es una grilla valida, asi que no hay orden posible.

Para MANDAR UNA ORDEN DE VERDAD no alcanza con este script y es a proposito:
eso va en el tablero, con la confirmacion de la persona que compra delante.

USO
    python probar_sigma_orden_compra.py
    python probar_sigma_orden_compra.py --sondear-formato

DESDE GITHUB ACTIONS no hay teclado con quien confirmar, asi que la
confirmacion se escribe en el formulario del workflow y llega por --confirmo.
Es el mismo permiso, pedido en el unico lugar donde se puede pedir.
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

# Mismos timeouts que sigma.py: 10 s para conectar, 120 para leer. Sin esto
# `requests` espera para siempre si el servidor acepta la conexion y despues no
# contesta.
TIMEOUT_HTTP = (10, 120)

URL_BASE = (
    f"https://{os.getenv('SIGMA_URL_CLIENTE')}"
    f"/{os.getenv('SIGMA_BASEALIAS')}"
    f"/{os.getenv('SIGMA_ID_CLIENTE')}"
    f"/sigma/api/v10/"
)
HEADERS = {"X-Auth-Token": os.getenv("SIGMA_TOKEN", "")}

# El nombre que dio soporte, y las variantes de mayusculas que suelen aceptar
# estos servidores. La primera que no conteste 404 es la buena.
# `ImportOrdenDeCompra` es el que contesto en la primera corrida. Las otras
# quedan por si algun dia cambia.
NOMBRES = ["ImportOrdenDeCompra", "importordendecompra", "ImportOrdenCompra"]


def _mostrar(r):
    """Lo unico que importa de la respuesta: el codigo y que dijo el cuerpo.

    El cuerpo se recorta a 1.500 caracteres porque un 500 de estos servidores
    puede venir con una pagina de error entera adentro.
    """
    print(f"    -> {r.status_code} {r.reason}")
    permitidos = r.headers.get("Allow") or r.headers.get("access-control-allow-methods")
    if permitidos:
        print(f"    -> metodos permitidos: {permitidos}")
    cuerpo = (r.text or "").strip()
    if cuerpo:
        print(f"    -> {cuerpo[:1500]}")


def existe(nombre):
    """Etapa 1: GET. Un 404 dice que el endpoint no se llama asi.

    Un 405 (metodo no permitido) es la MEJOR respuesta posible: significa que
    el endpoint existe y que GET no es como se llama, y muchas veces viene con
    la cabecera `Allow` diciendo cual si.
    """
    print(f"\n=== GET {nombre} ===")
    try:
        r = requests.get(URL_BASE + nombre, headers=HEADERS, timeout=TIMEOUT_HTTP)
    except Exception as e:  # noqa: BLE001 - sondeo: cualquier fallo es informacion
        print(f"    -> error de red: {e}")
        return None
    _mostrar(r)
    return r.status_code


def opciones(nombre):
    """Etapa 2: OPTIONS. Cuando esta implementado, lista los metodos."""
    print(f"\n=== OPTIONS {nombre} ===")
    try:
        r = requests.options(URL_BASE + nombre, headers=HEADERS, timeout=TIMEOUT_HTTP)
    except Exception as e:  # noqa: BLE001
        print(f"    -> error de red: {e}")
        return
    _mostrar(r)


def _confirmado(confirmo):
    """El permiso para mandar el POST, del teclado o del formulario.

    SIN TERMINAL NO SE PREGUNTA NADA. En GitHub Actions `input()` no espera:
    lee un stdin vacio y devuelve "", que comparado con "SI" da False. O sea
    que en el mejor caso cancela sola y en el peor -- si alguien invirtiera la
    comparacion -- mandaria sin permiso. Se resuelve mirando si hay terminal:
    si no la hay, el unico permiso valido es el que vino por --confirmo.
    """
    if confirmo is not None:
        return confirmo.strip().upper() == "SI"
    if not sys.stdin.isatty():
        print("    Sin terminal para confirmar. Pasar --confirmo SI.")
        return False
    return input("\n    Escribi SI para mandarla: ").strip().upper() == "SI"


# Lo que se manda en `content` para preguntar el formato. Tiene que ser algo
# que NO pueda confundirse con una orden: si el servidor lo pudiera parsear,
# esto crearia una orden de verdad en vez de contestar un error.
BASURA = "SONDEO_TABLERO_ESTO_NO_ES_UNA_ORDEN"


def sondear_formato(nombre, confirmo=None):
    """Etapa 3: mandar `content` con basura, para que diga que formato espera.

    El GET de la etapa 1 ya contesto "Parametro content requerido", asi que el
    endpoint no pide un JSON con campos sueltos: pide UN parametro con el
    contenido adentro -- muy probablemente la misma grilla que hoy se importa a
    mano. Lo que falta saber es como viene esa grilla y si ademas hay que
    mandar la cabecera (proveedor, fecha) por separado.

    Se prueba por los tres caminos posibles porque no sabemos cual es: query
    string, formulario y JSON.

    ES LA UNICA PARTE QUE PODRIA ESCRIBIR, y por eso pide permiso. El riesgo es
    bajo por construccion: `BASURA` no es una grilla valida, asi que en el peor
    caso el servidor la rechaza. Nunca se manda un renglon de verdad.
    """
    print(f"\n=== content con basura, en {nombre} ===")
    print(f"    URL:      {URL_BASE + nombre}")
    print(f"    content:  {BASURA}")
    print("\n    No es una grilla valida, asi que SIGMA deberia rechazarla")
    print("    diciendo que formato esperaba -- que es lo que se quiere leer.")
    if not _confirmado(confirmo):
        print("    Cancelado, no se mando nada.")
        return

    intentos = [
        ("GET  con content en la query", lambda u: requests.get(
            u, headers=HEADERS, params={"content": BASURA}, timeout=TIMEOUT_HTTP)),
        ("POST con content de formulario", lambda u: requests.post(
            u, headers=HEADERS, data={"content": BASURA}, timeout=TIMEOUT_HTTP)),
        ("POST con content en JSON", lambda u: requests.post(
            u, headers=HEADERS, json={"content": BASURA}, timeout=TIMEOUT_HTTP)),
    ]
    for etiqueta, llamar in intentos:
        print(f"\n  --- {etiqueta} ---")
        try:
            _mostrar(llamar(URL_BASE + nombre))
        except Exception as e:  # noqa: BLE001
            print(f"    -> error de red: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sondear-formato",
        action="store_true",
        help="ademas, mandar `content` con basura para que diga el formato",
    )
    ap.add_argument(
        "--confirmo",
        default=None,
        help='la confirmacion del envio, para cuando no hay teclado. Vale "SI".',
    )
    args = ap.parse_args()

    if not os.getenv("SIGMA_TOKEN"):
        print("Falta SIGMA_TOKEN en el .env. Sin token todo va a dar 401.")
        return

    print(f"Base: {URL_BASE}")

    encontrado = None
    for nombre in NOMBRES:
        codigo = existe(nombre)
        # 404 = no se llama asi. Cualquier otra cosa (401, 405, 400, 500) dice
        # que la ruta existe: el servidor llego a mirarla.
        if codigo is not None and codigo != 404:
            encontrado = nombre
            break

    if not encontrado:
        print("\nNinguna variante del nombre contesto distinto de 404.")
        print("Volver a preguntarle a soporte la ruta EXACTA y el metodo.")
        return

    print(f"\nLa ruta que responde es: {encontrado}")
    opciones(encontrado)
    if args.sondear_formato:
        sondear_formato(encontrado, args.confirmo)
    else:
        print("\nPara que ademas conteste que formato espera, correr:")
        print("    python probar_sigma_orden_compra.py --sondear-formato")


if __name__ == "__main__":
    main()
