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
    GET ImportOrdenDeCompra              -> 500 "Parametro content requerido"
    OPTIONS                              -> 200
    GET  con content en la query         -> 500 "El formato recibido del JSON es incorrecto"
    POST con content de formulario       -> 500 "El formato recibido del JSON es incorrecto"
    POST con content en JSON             -> 500 "Empresa Obligatoria"

Las tres cosas que eso resuelve:

  1. La ruta se llama `ImportOrdenDeCompra` y existe.
  2. SE LLAMA CON POST Y CUERPO JSON. Los otros dos caminos ni miran el
     contenido: se quejan del sobre.
  3. Con el sobre bien, el servidor pasa a quejarse DE NEGOCIO -- "Empresa
     Obligatoria" -- que es lo que se venia a buscar. Cada campo que se agrega
     destapa el siguiente que falta.

Falta la lista completa de campos, y por eso este script toma `--campos`: se
agregan los que va pidiendo, sin tocar el codigo en cada vuelta.

NO CREA NINGUNA ORDEN
---------------------
Las dos primeras etapas son inofensivas: preguntan como se llama al endpoint,
no que haga algo. La tercera manda `content` con una cadena que NO puede ser
una orden, para que el servidor conteste que formato esperaba. Por eso:

  * no corre sola: hay que pasarle --sondear-formato;
  * antes de mandar imprime exactamente que va a mandar y pide confirmacion;
  * `content` SIEMPRE va con basura y no se puede cambiar desde afuera. Es la
    garantia que no depende de que nadie se acuerde: sin renglones validos no
    hay orden que crear, por mas campos de cabecera que se completen.

Para MANDAR UNA ORDEN DE VERDAD no alcanza con este script y es a proposito:
eso va en el tablero, con la confirmacion de la persona que compra delante.

USO
    python probar_sigma_orden_compra.py
    python probar_sigma_orden_compra.py --sondear-formato
    python probar_sigma_orden_compra.py --sondear-formato --campos "Empresa=ZZZ" 

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


def _parsear_campos(texto):
    """"Empresa=ZZZ;Proveedor=XXX" -> {"Empresa": "ZZZ", "Proveedor": "XXX"}.

    Punto y coma y no coma: los valores de SIGMA tienen comas adentro
    (descripciones, razones sociales) y partir por coma los cortaria al medio.
    """
    campos = {}
    for parte in (texto or "").split(";"):
        parte = parte.strip()
        if not parte:
            continue
        if "=" not in parte:
            print(f"    Ignorado (sin '='): {parte!r}")
            continue
        clave, valor = parte.split("=", 1)
        campos[clave.strip()] = valor.strip()
    return campos


def sondear_formato(nombre, confirmo=None, texto_campos=""):
    """Etapa 3: POST con cuerpo JSON, para que diga que campo le falta ahora.

    SE MANDA SOLO POR JSON porque los otros dos caminos ya contestaron: tanto
    el GET con `content` en la query como el POST de formulario devuelven "El
    formato recibido del JSON es incorrecto", o sea que ni llegan a mirar el
    contenido. El unico sobre que el servidor abre es un POST con cuerpo JSON.

    LA IDEA ES PELAR LA CEBOLLA. Con `{"content": ...}` contesto "Empresa
    Obligatoria". Se agrega Empresa, vuelve a correr, y dice cual falta
    despues. Los campos entran por `--campos` justamente para no tener que
    tocar el codigo en cada vuelta.

    LOS VALORES VAN A PROPOSITO INVENTADOS. No se busca que la llamada salga
    bien: se busca leer el proximo error. Un valor que no existe en SIGMA
    destapa el nombre del campo igual que uno bueno, y no puede crear nada.
    """
    cuerpo = {"content": BASURA}
    cuerpo.update(_parsear_campos(texto_campos))

    print(f"\n=== POST JSON a {nombre} ===")
    print(f"    URL:    {URL_BASE + nombre}")
    print(f"    Cuerpo: {json.dumps(cuerpo, ensure_ascii=False)}")
    print("\n    `content` no es una orden valida, asi que SIGMA no puede crear")
    print("    nada: lo unico que puede hacer es decir que le falta.")
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
    print("\n    Si dice que falta otro campo, agregalo y volve a correr:")
    print('      --campos "Empresa=ZZZ;ElQuePidio=ZZZ"')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sondear-formato",
        action="store_true",
        help="ademas, mandar `content` con basura para que diga el formato",
    )
    ap.add_argument(
        "--campos",
        default="",
        help='campos extra del cuerpo, separados por ";". Ej: "Empresa=ZZZ"',
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
        sondear_formato(encontrado, args.confirmo, args.campos)
    else:
        print("\nPara que ademas conteste que formato espera, correr:")
        print("    python probar_sigma_orden_compra.py --sondear-formato")


if __name__ == "__main__":
    main()
