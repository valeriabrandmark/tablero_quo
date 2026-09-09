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
from datetime import date

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


# ---------------------------------------------------------------------------
# LA ORDEN DE PRUEBA
# ---------------------------------------------------------------------------

# La cabecera que hoy manda el tablero, con los codigos de la OC 00000371.
# Cada corrida puede pisar cualquiera de estos con --campos, o SACAR uno
# poniendolo vacio (`frecdia=`), que es lo que hace falta para probar hipotesis
# sin tocar el codigo del tablero.
CABECERA_BASE = {
    "proveedorId": "00239",
    "fechaCarga": "",           # se completa con la fecha de hoy
    "fechaPedido": "",          # idem
    "fusuari": 0,
    "usuario": 3,
    "observaciones": "PRUEBA TABLERO - ANULAR",
    "estado": "P",
    "tipoOrden": "01",
    "depositoRecepcion": "13",
    "condicionPago": "04",
    "vencimiento": "",          # idem
    "observacionInterna": "",
    "codigoSucursal": "0002",
    "moneda": "1",
    "empresa": "0001",
    "cotizacion": 1,
    "frecdia": "",              # idem
}

# UN renglon, con los cinco descuentos obligatorios en cero. Sin valores por
# defecto de articulo ni precio A PROPOSITO: ver `armar_orden`.
ITEM_BASE = {
    "articuloId": "",           # sin valor por defecto: ver `armar_orden`
    "cantidad": 1,
    "precio": "",               # idem
    "descuento1": 0,
    "descuento2": 0,
    "descuento3": 0,
    "descuento4": 0,
    "descuento5": 0,
    "descuento6": 0,
    "unidadDeCompra": "U",
}


# Los UNICOS campos que la documentacion declara `numeric`. Todo lo demas es
# TEXTO, y la diferencia no es cosmetica: los codigos de SIGMA llevan ceros
# adelante ("0001", "01") y adivinar por la pinta del valor convertiria
# `depositoRecepcion: "13"` en `13` y `moneda: "1"` en `1` -- que es mandar algo
# distinto de lo que manda el tablero, justo en un script cuyo trabajo es
# reproducirlo exactamente.
NUMERICOS = {
    "cotizacion", "fusuari", "usuario",
    "precio", "cantidad",
    "descuento1", "descuento2", "descuento3",
    "descuento4", "descuento5", "descuento6",
}


def _tipar(clave, v):
    """Convierte a numero solo lo que la documentacion dice que es numero."""
    if clave not in NUMERICOS or not isinstance(v, str) or v == "":
        return v
    try:
        return int(v) if v.lstrip("-").isdigit() else float(v)
    except ValueError:
        return v


def armar_orden(texto_campos, texto_item):
    """La orden completa, con lo que la corrida haya pisado.

    LAS CLAVES VAN EN EL ORDEN DEL EJEMPLO DE LA DOCUMENTACION. En JSON el
    orden no significa nada, pero lo pidio soporte y este endpoint ya
    contradijo su propia documentacion tres veces, asi que sale gratis
    descartarlo bien en vez de por deduccion. El tablero manda el mismo orden.

    "VACIO" QUIERE DECIR DOS COSAS DISTINTAS, y hay que separarlas. Escribir
    `--campos "frecdia="` es pedir que ese campo NO SE MANDE: es la unica forma
    de probar "y si lo sacamos" sin editar codigo. Pero `observacionInterna` va
    vacia de verdad --el tablero manda ""-- y tratarla igual la borraria del
    cuerpo, o sea que este script mandaria algo distinto del tablero justo
    cuando su trabajo es reproducirlo.

    Asi que se omite SOLO lo que vino vacio POR --campos, no lo que ya estaba
    vacio en la base.

    EL ARTICULO Y EL PRECIO NO TIENEN VALOR POR DEFECTO. La documentacion avisa
    que una orden sin items se registra igual, vacia y sin error; y un articulo
    puesto "de ejemplo" en un script es la forma mas facil de cargarle a un
    proveedor algo que nadie pidio. Si no vienen, no se manda nada.
    """
    hoy = date.today().isoformat()

    pisados = _parsear_campos(texto_campos)
    a_omitir = {k for k, v in pisados.items() if v == ""}

    # Pisar una clave que YA EXISTE no cambia su posicion en un dict de Python,
    # asi que completar las fechas y aplicar --campos mantiene el orden de
    # CABECERA_BASE. Un campo nuevo que venga por --campos si va al final, y
    # esta bien: no esta en el ejemplo.
    cabecera = dict(CABECERA_BASE)
    for clave in ("fechaCarga", "fechaPedido", "vencimiento", "frecdia"):
        cabecera[clave] = hoy
    cabecera.update(pisados)

    pisados_item = _parsear_campos(texto_item)
    a_omitir |= {k for k, v in pisados_item.items() if v == ""}
    item = dict(ITEM_BASE)
    item.update(pisados_item)

    faltan = [c for c in ("articuloId", "precio") if not item.get(c)]
    if faltan:
        print(f"    Falta {' y '.join(faltan)} en --item. No se manda nada.")
        return None

    cuerpo = {k: _tipar(k, v) for k, v in cabecera.items() if k not in a_omitir}
    cuerpo["items"] = [{k: _tipar(k, v) for k, v in item.items() if k not in a_omitir}]
    return cuerpo


def mandar_orden(nombre, confirmo, texto_campos, texto_item):
    """Manda UNA orden de compra de verdad.

    Es lo unico de este archivo que crea algo en el ERP, y por eso pide permiso
    igual que el sondeo. Si sale bien hay que ANULAR LA ORDEN EN SIGMA: no hay
    forma de borrarla desde acá.
    """
    cuerpo = armar_orden(texto_campos, texto_item)
    if cuerpo is None:
        return

    print(f"\n=== POST {nombre} · ORDEN DE VERDAD ===")
    print(f"    URL: {URL_BASE + nombre}")
    print(json.dumps(cuerpo, indent=2, ensure_ascii=False))
    print("\n    ESTO CREA UNA ORDEN DE COMPRA REAL. Si entra, hay que anularla")
    print("    en Sigma: desde acá no se puede borrar.")
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
    if r.status_code == 200:
        print("\n    ENTRO. Buscala en Sigma, verificá el renglón y ANULALA.")


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
        "--mandar-orden",
        action="store_true",
        help="mandar una orden de compra DE VERDAD (pide confirmacion)",
    )
    ap.add_argument(
        "--item",
        default="",
        help='el renglon. Ej: "articuloId=AL26011;precio=14968.8;cantidad=1"',
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
    if args.mandar_orden:
        mandar_orden(encontrado, args.confirmo, args.campos, args.item)
    elif args.sondear_formato:
        sondear_formato(encontrado, args.confirmo, args.campos)
    else:
        print("\nPara que ademas conteste que formato espera, correr:")
        print("    python probar_sigma_orden_compra.py --sondear-formato")


if __name__ == "__main__":
    main()
