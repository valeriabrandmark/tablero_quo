"""Sondeo del endpoint ImportOrdenDeCompra de SIGMA.

QUE ES ESTO Y POR QUE EXISTE
----------------------------
El tablero arma la orden de compra y hoy la baja como archivo para importarla
a mano en la grilla de SIGMA. El paso siguiente es que un boton la mande sola,
y para escribir ese boton hace falta saber DOS cosas que la documentacion no
dice: con que metodo HTTP se llama al endpoint, y que forma tiene el cuerpo.

Soporte de SIGMA confirmo el nombre: `importordendecompra`. Este script
averigua el resto preguntandole al servidor, que es la unica fuente confiable.

NO CREA NINGUNA ORDEN
---------------------
Las tres primeras etapas son inofensivas: preguntan como se llama al endpoint,
no que haga algo. La cuarta manda un cuerpo VACIO, que es la que suele
contestar con la lista de campos obligatorios -- y la unica que, en teoria,
podria escribir algo. Por eso:

  * no corre sola: hay que pasarle --sondear-vacio;
  * antes de mandar imprime exactamente que va a mandar y pide confirmacion;
  * nunca manda renglones, asi que en el peor caso seria una orden sin items.

Para MANDAR UNA ORDEN DE VERDAD no alcanza con este script y es a proposito:
eso va en el tablero, con la confirmacion de la persona que compra delante.

USO
    python probar_sigma_orden_compra.py
    python probar_sigma_orden_compra.py --sondear-vacio

DESDE GITHUB ACTIONS no hay teclado con quien confirmar, asi que la
confirmacion se escribe en el formulario del workflow y llega por --confirmo.
Es el mismo permiso, pedido en el unico lugar donde se puede pedir.
"""

import argparse
import json
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


def sondear_vacio(nombre, confirmo=None):
    """Etapa 3: POST con el cuerpo vacio, para que el servidor diga que falta.

    Es la unica llamada de este script que escribe, y por eso pide permiso.
    """
    cuerpo = {}
    print(f"\n=== POST {nombre} ===")
    print(f"    URL:    {URL_BASE + nombre}")
    print(f"    Cuerpo: {json.dumps(cuerpo)}")
    print("\n    Esto le pide a SIGMA que cree una orden SIN datos. Lo normal es")
    print("    que la rechace y diga que campos faltan, que es justo lo que se")
    print("    quiere averiguar. Si en cambio la aceptara, quedaria una orden")
    print("    vacia para borrar a mano.")
    if not _confirmado(confirmo):
        print("    Cancelado, no se mando nada.")
        return
    try:
        r = requests.post(
            URL_BASE + nombre, headers=HEADERS, json=cuerpo, timeout=TIMEOUT_HTTP
        )
    except Exception as e:  # noqa: BLE001
        print(f"    -> error de red: {e}")
        return
    _mostrar(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sondear-vacio",
        action="store_true",
        help="ademas, mandar un POST con cuerpo vacio (pide confirmacion)",
    )
    ap.add_argument(
        "--confirmo",
        default=None,
        help='la confirmacion del POST, para cuando no hay teclado. Vale "SI".',
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
    if args.sondear_vacio:
        sondear_vacio(encontrado, args.confirmo)
    else:
        print("\nPara que ademas conteste que campos pide, correr:")
        print("    python probar_sigma_orden_compra.py --sondear-vacio")


if __name__ == "__main__":
    main()
