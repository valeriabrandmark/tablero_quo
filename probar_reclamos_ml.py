"""Sondeo: que nos deja ver Mercado Libre de los reclamos y mediaciones.

============================================================================
 POR QUE UN SONDEO Y NO EL EXTRACTOR DERECHO
============================================================================

Porque hay tres cosas que NO se pueden dar por sabidas, y las tres cambian
como hay que armar el tablero:

  1. SI EL TOKEN TIENE ACCESO. La documentacion dice que los vendedores
     clasificados como "Modelo 6" reciben 403 en los endpoints de reclamos y
     devoluciones. No es un error de codigo: es un permiso de la cuenta. Si
     pasa eso, no hay tablero posible por esta via y hay que pedirle el
     permiso a Mercado Libre antes de escribir una linea mas.

  2. SI VIENE QUIEN GANO EL RECLAMO. Es lo que se quiere para el listado de
     cerrados: "a favor del cliente o nuestro". Segun la documentacion el
     campo se llama `resolution.benefited` y vale `complainant` (el que
     reclama, o sea el comprador) o `respondent` (el reclamado, o sea
     nosotros). Pero la documentacion de Mercado Libre cambia por pais y por
     version, asi que hasta no verlo en una respuesta real no esta.

  3. HASTA DONDE LLEGA EL HISTORICO. El listado de cerrados se puede llenar
     hacia atras solo hasta donde la API deje buscar.

El sondeo no escribe NADA en la base. Solo mira y cuenta.

============================================================================
 LO QUE YA SE SABE Y NO HACE FALTA PREGUNTAR
============================================================================

LAS MEDIACIONES NO SON OTRA COSA: son el mismo reclamo en otra etapa. El
campo `stage` vale `claim` (recien abierto, entre las partes), `dispute` (ahi
entro Mercado Libre a mediar) o `recontact` (alguien volvio a escribir
despues de cerrado). O sea que un solo listado con una columna de etapa
cubre reclamos Y mediaciones, sin dos extracciones separadas.

LA RUTA VIEJA ESTA MUERTA. `/v1/claims` quedo deprecada en mayo de 2024; la
que vive es `/post-purchase/v1/claims/`. Si alguien googlea esto y encuentra
ejemplos con la ruta vieja, son de antes.

============================================================================
 COMO SE CORRE
============================================================================

    python probar_reclamos_ml.py              # el sondeo completo
    python probar_reclamos_ml.py --detalle 5  # solo 5 reclamos al detalle
    python probar_reclamos_ml.py --crudo      # ademas, un reclamo entero

Necesita las mismas variables que el resto: ML_CLIENT_ID, ML_CLIENT_SECRET,
ML_REDIRECT_URI, ML_USER_ID y la base para leer el token guardado.
"""

import argparse
import json
import os
from collections import Counter

import requests

import mercadolibre as ml
from mercadolibre import token_ml

BASE = "/post-purchase/v1/claims"

# Los filtros que la documentacion dice que acepta la busqueda. Se prueban de
# a uno y se reporta cual anduvo: es la mitad del sondeo. Pedir con un filtro
# que no existe no siempre da error -- a veces lo ignora y devuelve todo, que
# es peor, porque uno cree que filtro.
ETAPAS = ("claim", "dispute", "recontact")
ESTADOS = ("opened", "closed")

# Lo que se espera encontrar en la resolucion. Si alguno no aparece en ninguna
# respuesta real, el tablero no lo puede mostrar y hay que decirlo.
CAMPOS_RESOLUCION = ("reason", "benefited", "closed_by", "date_created")

# `benefited` viene en el idioma de la API. Nosotros somos SIEMPRE el
# reclamado: los reclamos los abre el comprador.
QUIEN_GANO = {
    "complainant": "el comprador",
    "respondent": "nosotros",
}


def llamar(ruta, token, params=None):
    """Como `ml.llamar_ml` pero devolviendo el error en vez de explotar.

    El sondeo tiene que poder DECIR "esto da 403" en vez de cortarse con un
    traceback: justamente lo que se esta averiguando es que anda y que no.
    """
    try:
        return ml.llamar_ml(ruta, token, params, pausa=True), None
    except requests.exceptions.HTTPError as e:
        codigo = e.response.status_code if e.response is not None else "?"
        cuerpo = ""
        if e.response is not None:
            cuerpo = (e.response.text or "")[:300]
        return None, f"HTTP {codigo} — {cuerpo}"
    except Exception as e:                      # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def _filas(datos):
    """Las filas de una respuesta de busqueda, venga como venga.

    No se asume la forma: distintas rutas de Mercado Libre devuelven
    `results`, `data` o una lista pelada. Si no se reconoce, se avisa en vez
    de devolver vacio en silencio -- que es como un sondeo termina diciendo
    "no hay reclamos" cuando en realidad no supo leer la respuesta.
    """
    if isinstance(datos, list):
        return datos, "lista pelada"
    if isinstance(datos, dict):
        for clave in ("results", "data", "claims"):
            if isinstance(datos.get(clave), list):
                return datos[clave], clave
    return [], None


def _total(datos):
    """El total que declara la respuesta, si lo declara."""
    if isinstance(datos, dict):
        for clave in ("total", "paging"):
            valor = datos.get(clave)
            if isinstance(valor, int):
                return valor
            if isinstance(valor, dict) and isinstance(valor.get("total"), int):
                return valor["total"]
    return None


def probar_acceso(token):
    """Lo primero: ¿la cuenta puede leer reclamos? Devuelve (puede, datos)."""
    print("\n" + "=" * 74)
    print(" 1. ¿TENEMOS ACCESO?")
    print("=" * 74)

    datos, error = llamar(f"{BASE}/search", token, {"limit": 1})
    if error:
        print(f"  NO: {error}")
        if "403" in error:
            print()
            print("  Un 403 aca NO es un bug del script: es la cuenta. Mercado")
            print("  Libre bloquea estos endpoints para los vendedores que")
            print("  clasifica como 'Modelo 6'. Hay que pedirles el permiso.")
        elif "401" in error:
            print()
            print("  El token no sirve para esta ruta. Puede ser que falte el")
            print("  scope de post-venta en la aplicacion de Mercado Libre.")
        return False, None

    filas, forma = _filas(datos)
    total = _total(datos)
    print(f"  SI. La busqueda contesta.")
    print(f"  Las filas vienen en: {forma or 'NO SE RECONOCIO LA FORMA'}")
    if forma is None:
        print(f"  Claves de la respuesta: {sorted(datos)[:15]}")
    if total is not None:
        print(f"  Total que declara: {total}")
    return True, datos


def contar(token):
    """Cuantos hay, por estado y por etapa. Y si los filtros filtran."""
    print("\n" + "=" * 74)
    print(" 2. CUANTOS HAY Y SI LOS FILTROS ANDAN")
    print("=" * 74)

    base_total = _total(llamar(f"{BASE}/search", token, {"limit": 1})[0] or {})
    print(f"  Sin filtro: {base_total if base_total is not None else 'no lo dice'}")

    for campo, valores in (("status", ESTADOS), ("stage", ETAPAS)):
        print(f"\n  por {campo}:")
        for valor in valores:
            datos, error = llamar(f"{BASE}/search", token, {campo: valor, "limit": 1})
            if error:
                print(f"    {valor:<12} ERROR: {error[:60]}")
                continue
            total = _total(datos)
            # Si el filtro devuelve exactamente lo mismo que sin filtro, lo
            # mas probable es que lo haya IGNORADO. Vale la pena decirlo.
            sospecha = ""
            if total is not None and base_total is not None and total == base_total:
                sospecha = "  <-- igual al total: ¿lo ignoro?"
            print(f"    {valor:<12} {total if total is not None else '?'}{sospecha}")


def ver_detalle(token, cuantos, crudo):
    """Abre unos reclamos y dice que campos traen de verdad."""
    print("\n" + "=" * 74)
    print(f" 3. QUE TRAE UN RECLAMO (mirando {cuantos})")
    print("=" * 74)

    datos, error = llamar(f"{BASE}/search", token, {"limit": cuantos})
    if error:
        print(f"  No se pudo listar: {error}")
        return
    filas, _ = _filas(datos)
    if not filas:
        print("  La busqueda no devolvio ningun reclamo.")
        print("  Puede ser que de verdad no haya, o que haga falta un filtro.")
        return

    claves = Counter()
    con_resolucion = 0
    resolucion_claves = Counter()
    ganadores = Counter()
    etapas = Counter()
    tipos = Counter()
    estados = Counter()

    for i, fila in enumerate(filas):
        if not isinstance(fila, dict):
            continue
        claves.update(fila.keys())
        etapas[fila.get("stage")] += 1
        tipos[fila.get("type")] += 1
        estados[fila.get("status")] += 1

        resolucion = fila.get("resolution")
        if isinstance(resolucion, dict):
            con_resolucion += 1
            resolucion_claves.update(resolucion.keys())
            ganadores[resolucion.get("benefited")] += 1

        if crudo and i == 0:
            print("\n  --- un reclamo entero, como viene ---")
            print(json.dumps(fila, indent=2, ensure_ascii=False)[:3000])
            print("  --- fin ---\n")

    print(f"\n  Campos que trae la busqueda (de {len(filas)} reclamos):")
    for clave, veces in claves.most_common():
        print(f"    {clave:<24} en {veces}/{len(filas)}")

    print(f"\n  status: {dict(estados)}")
    print(f"  stage:  {dict(etapas)}")
    print(f"  type:   {dict(tipos)}")

    print(f"\n  --- LA PREGUNTA QUE IMPORTA: ¿viene quien gano? ---")
    if not con_resolucion:
        print("    NINGUNO de estos trae `resolution`.")
        print("    Puede ser que solo la traigan los cerrados, o que haya que")
        print("    pedir el detalle de a uno. Mira el punto 4.")
    else:
        print(f"    {con_resolucion}/{len(filas)} traen `resolution`.")
        print(f"    Campos adentro: {sorted(resolucion_claves)}")
        faltan = [c for c in CAMPOS_RESOLUCION if c not in resolucion_claves]
        if faltan:
            print(f"    NO aparecieron: {faltan}")
        if ganadores:
            print("    benefited:")
            for valor, veces in ganadores.most_common():
                print(f"      {str(valor):<14} x{veces}   ({QUIEN_GANO.get(valor, '?')})")

    return filas


def detalle_de_a_uno(token, filas):
    """El detalle individual, que suele traer mas que la busqueda."""
    print("\n" + "=" * 74)
    print(" 4. EL DETALLE DE A UNO (¿trae mas que la busqueda?)")
    print("=" * 74)

    if not filas:
        print("  No hay reclamos para probar.")
        return

    ident = (filas[0] or {}).get("id")
    if ident is None:
        print("  El primer reclamo no trae `id`, no se puede pedir el detalle.")
        return

    datos, error = llamar(f"{BASE}/{ident}", token)
    if error:
        print(f"  No se pudo: {error}")
        return

    de_la_busqueda = set((filas[0] or {}).keys())
    del_detalle = set(datos) if isinstance(datos, dict) else set()
    de_mas = sorted(del_detalle - de_la_busqueda)

    print(f"  Reclamo {ident}: el detalle trae {len(del_detalle)} campos.")
    if de_mas:
        print(f"  Lo que NO estaba en la busqueda: {de_mas}")
    else:
        print("  No trae nada que la busqueda no tuviera: alcanza con buscar.")

    resolucion = datos.get("resolution") if isinstance(datos, dict) else None
    if isinstance(resolucion, dict):
        print(f"  resolution: {json.dumps(resolucion, ensure_ascii=False)[:400]}")


def probar_mensajes(token, filas):
    """Los mensajes del reclamo, que es la mitad de lo que se quiere ver."""
    print("\n" + "=" * 74)
    print(" 5. LOS MENSAJES")
    print("=" * 74)

    if not filas:
        print("  No hay reclamos para probar.")
        return

    ident = (filas[0] or {}).get("id")
    if ident is None:
        print("  Sin id no se pueden pedir.")
        return

    # Se prueban las dos rutas que aparecen en la documentacion segun la
    # version. La que conteste es la que vale.
    rutas = [
        f"{BASE}/{ident}/messages",
        f"/post-purchase/v2/claims/{ident}/messages",
    ]
    for ruta in rutas:
        datos, error = llamar(ruta, token)
        if error:
            print(f"  {ruta}\n    no: {error[:80]}")
            continue
        mensajes, forma = _filas(datos)
        print(f"  {ruta}\n    SI — {len(mensajes)} mensajes (en '{forma}')")
        if mensajes and isinstance(mensajes[0], dict):
            print(f"    campos: {sorted(mensajes[0])}")
        return
    print("\n  Ninguna de las dos rutas contesto. Hay que buscar cual es.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--detalle", type=int, default=20, metavar="N",
                        help="cuantos reclamos mirar al detalle (por defecto 20)")
    parser.add_argument("--crudo", action="store_true",
                        help="ademas, imprime un reclamo entero como viene")
    args = parser.parse_args()

    if not os.getenv("ML_USER_ID"):
        print("Falta ML_USER_ID. Es la misma variable que usa el resto.")
        raise SystemExit(2)

    token = token_ml()
    print("Token conseguido. Sondeando reclamos...")

    puede, _ = probar_acceso(token)
    if not puede:
        print("\nSin acceso no tiene sentido seguir. Nada que hacer en el codigo.")
        raise SystemExit(1)

    contar(token)
    filas = ver_detalle(token, args.detalle, args.crudo) or []
    detalle_de_a_uno(token, filas)
    probar_mensajes(token, filas)

    print("\n" + "=" * 74)
    print(" Pegale la salida al chat y con eso se arma el extractor.")
    print("=" * 74 + "\n")


if __name__ == "__main__":
    main()
