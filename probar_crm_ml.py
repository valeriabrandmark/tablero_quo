"""Sondeo: que nos deja ver Mercado Libre para el tablero de CRM.

Mira las TRES fuentes que va a juntar el tablero:

  RECLAMOS Y MEDIACIONES   `/post-purchase/v1/claims`  -- y quien gano cada uno
  PREGUNTAS DE PUBLICACION `/questions/search`         -- la pre-venta
  MENSAJES POST-VENTA      `/messages/packs/...`       -- la charla de la orden

============================================================================
 POR QUE UN SONDEO Y NO EL EXTRACTOR DERECHO
============================================================================

Porque hay cosas que NO se pueden dar por sabidas, y todas cambian como hay
que armar el tablero. Tres son de los reclamos:

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

Y la cuarta es la que decide si el tablero entra en la corrida:

  4. CUANTAS LLAMADAS CUESTA CADA FUENTE. Los reclamos y las preguntas se
     piden de a muchos por llamada. Los mensajes post-venta NO: van de a UNA
     ORDEN POR VEZ. Con miles de ordenes en la ventana eso son miles de
     llamadas, y la corrida entera tiene 50 minutos de presupuesto. Si el
     numero no entra, la fuente no va en cada corrida -- o va filtrada por
     las que tienen mensajes sin leer, que es lo que el punto 7 averigua.

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

    python probar_crm_ml.py              # el sondeo completo
    python probar_crm_ml.py --detalle 5  # solo 5 reclamos al detalle
    python probar_crm_ml.py --crudo      # ademas, un reclamo entero

Necesita las mismas variables que el resto: ML_CLIENT_ID, ML_CLIENT_SECRET,
ML_REDIRECT_URI, ML_USER_ID y la base para leer el token guardado.
"""

import argparse
import datetime
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

    La lista salio corta en la primera corrida: las preguntas vienen en
    `questions` y la funcion no la tenia, asi que conto 2.004 preguntas y
    despues dijo "no se reconocio la forma". Avisar en vez de callar es
    justamente lo que permitio verlo.
    """
    if isinstance(datos, list):
        return datos, "lista pelada"
    if isinstance(datos, dict):
        for clave in ("results", "data", "claims", "questions", "messages",
                      "conversations", "orders"):
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
    """Lo primero: ¿la cuenta puede leer reclamos? Devuelve (puede, datos).

    LA BUSQUEDA EXIGE AL MENOS UN FILTRO. Pedirla pelada devuelve

        400 atLeastOneFilterProvided: at least one filter parameter must be
        provided

    que en la primera corrida se leyo como "no tenemos acceso" cuando en
    realidad la llamada ni se ejecuto. Por eso va con `status=opened`: es el
    filtro mas barato que ademas contesta la pregunta que importa -- cuantos
    reclamos hay abiertos ahora.
    """
    print("\n" + "=" * 74)
    print(" 1. ¿TENEMOS ACCESO?")
    print("=" * 74)

    datos, error = llamar(f"{BASE}/search", token, {"status": "opened", "limit": 1})
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

    # NO HAY "SIN FILTRO" CONTRA QUE COMPARAR: la busqueda lo rechaza con un
    # 400. Asi que la referencia es la suma de abierto + cerrado, que por
    # definicion es el total. Si una etapa diera mas que eso, algo no cierra.
    totales = {}
    for valor in ESTADOS:
        datos, error = llamar(f"{BASE}/search", token, {"status": valor, "limit": 1})
        if error:
            print(f"  status {valor:<10} ERROR: {error}")
            continue
        totales[valor] = _total(datos)
        print(f"  status {valor:<10} {totales[valor] if totales[valor] is not None else '?'}")

    base_total = None
    if len(totales) == len(ESTADOS) and all(v is not None for v in totales.values()):
        base_total = sum(totales.values())
        print(f"  {'TOTAL':<17} {base_total}")

    for campo, valores in (("stage", ETAPAS),):
        print(f"\n  por {campo}:")
        for valor in valores:
            datos, error = llamar(f"{BASE}/search", token, {campo: valor, "limit": 1})
            if error:
                print(f"    {valor:<12} ERROR: {error}")
                continue
            total = _total(datos)
            # Si el filtro devuelve exactamente lo mismo que sin filtro, lo
            # mas probable es que lo haya IGNORADO. Vale la pena decirlo.
            sospecha = ""
            if total is not None and base_total is not None and total == base_total:
                sospecha = "  <-- igual al total: ¿lo ignoro?"
            print(f"    {valor:<12} {total if total is not None else '?'}{sospecha}")


def ver_detalle(token, cuantos, crudo):
    """Abre unos reclamos y dice que campos traen de verdad.

    Se piden CERRADOS a proposito. Lo que hay que confirmar es
    `resolution.benefited` --quien gano--, y eso solo existe una vez que el
    reclamo se resolvio. Pidiendo los abiertos se veria `resolution` en null
    en todos y no se probaria nada.
    """
    print("\n" + "=" * 74)
    print(f" 3. QUE TRAE UN RECLAMO CERRADO (mirando {cuantos})")
    print("=" * 74)

    datos, error = llamar(f"{BASE}/search", token,
                          {"status": "closed", "limit": cuantos})
    if error:
        print(f"  No se pudo listar: {error}")
        return
    filas, _ = _filas(datos)
    if not filas:
        print("  La busqueda no devolvio ningun reclamo cerrado.")
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
            print(f"  {ruta}\n    no: {error}")
            continue
        mensajes, forma = _filas(datos)
        print(f"  {ruta}\n    SI — {len(mensajes)} mensajes (en '{forma}')")
        if mensajes and isinstance(mensajes[0], dict):
            print(f"    campos: {sorted(mensajes[0])}")
        return
    print("\n  Ninguna de las dos rutas contesto. Hay que buscar cual es.")


def probar_preguntas(token, user_id):
    """Las preguntas de las publicaciones: la pata de pre-venta.

    Devuelve cuantas hay en total, para la cuenta de llamadas del final.
    """
    print("\n" + "=" * 74)
    print(" 6. PREGUNTAS DE LAS PUBLICACIONES (pre-venta)")
    print("=" * 74)

    # `api_version=4` no es decoracion: sin eso la ruta contesta con el
    # formato viejo, que agrupa por publicacion en vez de devolver una
    # pregunta por fila. Con 4 se pagina como cualquier otra busqueda.
    params = {"seller_id": user_id, "api_version": 4, "limit": 1}
    datos, error = llamar("/questions/search", token, params)
    if error:
        print(f"  No se pudo: {error}")
        return None

    total = _total(datos)
    print(f"  Total historico: {total if total is not None else 'no lo dice'}")

    # Lo que importa para el tablero es cuantas estan SIN CONTESTAR: son las
    # que alguien tiene que mirar hoy.
    sin_contestar = None
    for estado in ("UNANSWERED", "ANSWERED"):
        d, e = llamar("/questions/search", token,
                      dict(params, status=estado))
        if e:
            print(f"  {estado:<12} ERROR: {e}")
            continue
        t = _total(d)
        if estado == "UNANSWERED":
            sin_contestar = t
        print(f"  {estado:<12} {t if t is not None else '?'}")

    filas, forma = _filas(datos)
    if filas and isinstance(filas[0], dict):
        print(f"\n  Las filas vienen en '{forma}'. Campos de una pregunta:")
        print(f"    {sorted(filas[0])}")
        respuesta = filas[0].get("answer")
        if isinstance(respuesta, dict):
            print(f"    answer: {sorted(respuesta)}")
        elif respuesta is None:
            print("    answer: viene en null cuando todavia no se contesto")
    else:
        print(f"\n  No se reconocio la forma de la respuesta: {forma}")

    if sin_contestar:
        print(f"\n  >>> {sin_contestar} preguntas sin contestar ahora mismo.")
    return total


def probar_mensajes_postventa(token, user_id):
    """Los mensajes de las ordenes. La fuente cara, y hay que medir cuanto.

    Devuelve (ordenes_en_ventana, hay_atajo_de_no_leidos).
    """
    print("\n" + "=" * 74)
    print(" 7. MENSAJES POST-VENTA (la fuente cara)")
    print("=" * 74)

    # PRIMERO EL ATAJO, QUE ES LO QUE DECIDE EL DISENO.
    #
    # Si existe una ruta que diga "estas son las conversaciones con mensajes
    # sin leer", se piden solo esas y la fuente sale casi gratis. Si no
    # existe, hay que recorrer orden por orden y ahi la cuenta se dispara.
    print("\n  a) ¿Hay un atajo para pedir solo lo que tiene mensajes nuevos?")
    atajo = False
    for ruta, params in (
        ("/messages/unread", {"role": "seller"}),
        ("/messages/pending", {"role": "seller"}),
        (f"/messages/packs/sellers/{user_id}/unread", None),
    ):
        datos, error = llamar(ruta, token, params)
        if error:
            # EL ERROR VA ENTERO. En la primera corrida se imprimia cortado a
            # 70 caracteres y se perdia justo la parte donde Mercado Libre
            # explica que falta -- que es todo lo que un sondeo tiene para dar.
            print(f"     {ruta}\n       no: {error}")
            continue
        atajo = True
        print(f"     {ruta}\n       SI — {json.dumps(datos, ensure_ascii=False)[:300]}")
        break

    # CUANTAS ORDENES HABRIA QUE RECORRER, Y ESTA ES LA CUENTA QUE IMPORTA.
    #
    # En la primera corrida se pidio el total pelado y dio 148.700: TODAS las
    # ordenes de la historia de la cuenta. Ese numero no sirve para decidir
    # nada, porque nadie escribe por una compra de hace dos anios. Lo que hay
    # que medir es cuantas ordenes entran en una ventana corta, que son las
    # unicas que pueden tener conversacion viva.
    print("\n  b) ¿Cuantas ordenes habria que recorrer?")
    ordenes = None
    cuantas = None
    for dias in (7, 30, None):
        params = {"seller": user_id, "sort": "date_desc", "limit": 1}
        if dias is not None:
            desde = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=dias)
            # El formato que pide la API: ISO con milisegundos y zona.
            params["order.date_created.from"] = (
                desde.strftime("%Y-%m-%dT%H:%M:%S.000") + "-00:00"
            )
        datos, error = llamar("/orders/search", token, params)
        etiqueta = f"ultimos {dias} dias" if dias else "toda la historia"
        if error:
            print(f"     {etiqueta:<18} ERROR: {error}")
            continue
        total = _total(datos)
        print(f"     {etiqueta:<18} {total if total is not None else '?'}")
        # La ventana de 7 dias es la que manda para la cuenta del final, y la
        # respuesta mas reciente es la que deja una orden para probar el pack.
        if cuantas is None and total is not None:
            cuantas = total
        if ordenes is None:
            ordenes = datos

    # Y por ultimo: ¿se puede leer la conversacion de una orden concreta?
    print("\n  c) ¿Se lee la conversacion de una orden?")
    filas, _ = _filas(ordenes or {})
    if not filas:
        print("     Sin ordenes no se puede probar.")
        return cuantas, atajo

    orden = filas[0] if isinstance(filas[0], dict) else {}
    # El `pack_id` agrupa las ordenes de un mismo carrito. Cuando la compra
    # fue de un solo articulo viene en null y el pack ES la orden.
    pack = orden.get("pack_id") or orden.get("id")
    if pack is None:
        print("     La orden no trae ni pack_id ni id.")
        return cuantas, atajo

    print(f"     (orden {orden.get('id')}, pack_id {orden.get('pack_id')})")

    # `tag=post_sale` NO ES OPCIONAL, y su falta fue lo que dio 404 en la
    # primera corrida. Se prueban igual las dos variantes y la ruta de
    # marketplace, porque la documentacion difiere entre sitios.
    intentos = [
        (f"/messages/packs/{pack}/sellers/{user_id}", {"tag": "post_sale"}),
        (f"/messages/packs/{pack}/sellers/{user_id}", None),
        (f"/marketplace/messages/packs/{pack}", {"tag": "post_sale"}),
    ]
    for ruta, params in intentos:
        datos, error = llamar(ruta, token, params)
        cola = f"?tag=post_sale" if params else ""
        if error:
            print(f"     {ruta}{cola}\n       no: {error}")
            continue
        mensajes, forma = _filas(datos)
        print(f"     {ruta}{cola}\n       SI — {len(mensajes)} mensajes (en '{forma}')")
        if mensajes and isinstance(mensajes[0], dict):
            print(f"       campos: {sorted(mensajes[0])}")
        else:
            print(f"       claves de la respuesta: {sorted(datos)[:15] if isinstance(datos, dict) else type(datos)}")
        break

    return cuantas, atajo


def resumen_de_costo(ordenes, hay_atajo, preguntas):
    """Lo unico que decide si esto entra en la corrida o no."""
    print("\n" + "=" * 74)
    print(" 8. ¿ENTRA EN LA CORRIDA?")
    print("=" * 74)
    print("  La corrida entera tiene 50 minutos de presupuesto, y a este paso")
    print("  le tocarian 10 o 15. A ~3 llamadas por segundo con los hilos que")
    print("  ya usa el stock Full, eso son unas 2.000 a 2.700 llamadas.")
    print()
    print("  RECLAMOS   se piden de a muchos: son decenas de llamadas. Entra.")

    if preguntas:
        paginas = -(-preguntas // 50)        # division para arriba
        print(f"  PREGUNTAS  {preguntas} historicas = ~{paginas} paginas de 50.")
        print("             En cada corrida solo haria falta lo nuevo. Entra.")
    else:
        print("  PREGUNTAS  no se pudo contar.")

    if hay_atajo:
        print("  MENSAJES   HAY ATAJO: se piden solo las conversaciones con")
        print("             mensajes sin leer. Barato. Entra en cada corrida.")
    elif ordenes:
        # `ordenes` es la ventana de 7 dias, no el total historico. El total
        # de la cuenta son casi 150.000 ordenes y no sirve para decidir:
        # nadie escribe por una compra de hace dos anios.
        print(f"  MENSAJES   SIN ATAJO: una llamada por orden, y en los ultimos")
        print(f"             7 dias hay {ordenes} ordenes.")
        if ordenes > 2000:
            print("             >>> NO ENTRA. Ni siquiera acotado a una semana.")
            print("                 Queda colgarlo de los reclamos: pedir la")
            print("                 conversacion solo de las ordenes que YA")
            print("                 tienen un reclamo abierto, que son decenas.")
        else:
            print("             Entra, acotado a esa ventana.")
    else:
        print("  MENSAJES   no se pudo contar. Es la que hay que mirar.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--detalle", type=int, default=20, metavar="N",
                        help="cuantos reclamos mirar al detalle (por defecto 20)")
    parser.add_argument("--crudo", action="store_true",
                        help="ademas, imprime un reclamo entero como viene")
    args = parser.parse_args()

    user_id = os.getenv("ML_USER_ID")
    if not user_id:
        print("Falta ML_USER_ID. Es la misma variable que usa el resto.")
        raise SystemExit(2)

    token = token_ml()
    print("Token conseguido. Sondeando las tres fuentes del CRM...")

    # LOS RECLAMOS SON LO UNICO QUE PUEDE CORTAR EL SONDEO.
    #
    # Si dan 403 no hay tablero de reclamos, pero las preguntas y los mensajes
    # son otros endpoints y pueden andar igual. Asi que se avisa y se sigue:
    # cortar aca dejaria sin medir las otras dos y haria falta otra corrida.
    puede, _ = probar_acceso(token)
    if puede:
        contar(token)
        filas = ver_detalle(token, args.detalle, args.crudo) or []
        detalle_de_a_uno(token, filas)
        probar_mensajes(token, filas)
    else:
        print("\n  Se saltean los puntos 2 a 5 y se sigue con las otras dos")
        print("  fuentes, que son endpoints distintos y pueden andar igual.")

    preguntas = probar_preguntas(token, user_id)
    ordenes, hay_atajo = probar_mensajes_postventa(token, user_id)
    resumen_de_costo(ordenes, hay_atajo, preguntas)

    print("\n" + "=" * 74)
    print(" Pegale la salida al chat y con eso se arma el extractor.")
    print("=" * 74 + "\n")

    if not puede:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
