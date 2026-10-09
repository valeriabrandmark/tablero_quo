"""CRM de Mercado Libre: reclamos, mediaciones, mensajes y preguntas.

QUE PREGUNTA CONTESTA

Que esta pasando hoy con los compradores, en un solo lugar: que reclamos hay
abiertos, en que etapa, quien escribio ultimo, que preguntas de publicacion
estan sin contestar. Y, para los que ya se cerraron, a favor de quien se
resolvio.

QUE ESCRIBE

  bronze.ml_reclamos           uno por reclamo, abierto o cerrado. Trae la
                               etapa, nuestro rol y --si se resolvio-- quien
                               gano.
  bronze.ml_reclamos_mensajes  la conversacion de cada reclamo.
  bronze.ml_preguntas          las preguntas de las publicaciones.
  bronze.ml_mensajes_orden     la charla post-venta de las ordenes que tienen
                               un reclamo.

============================================================================
 LOS DOS NUMEROS QUE DECIDIERON EL DISENO (sondeo del 08/10/2026)
============================================================================

LA BUSQUEDA DE RECLAMOS DEVUELVE DE LA MAS VIEJA A LA MAS NUEVA. Hay 8.431
cerrados y los 20 primeros son de 2019 a 2022. Llegar a los de esta semana
por esa via serian 169 paginas EN CADA CORRIDA.

No hace falta. Un reclamo no aparece cerrado de la nada: antes estuvo
abierto, y los abiertos son 24. Entonces:

  - en cada corrida se piden los 24 abiertos enteros (una pagina);
  - los que ya no estan en esa lista son los que se acaban de cerrar, y se
    les pide el detalle de a uno (`crm.cerrados_nuevos`);
  - el historico viejo se llena UNA VEZ con `--historico` y no se vuelve a
    tocar, porque un reclamo cerrado ya no cambia.

LOS MENSAJES POST-VENTA NO SE PUEDEN PEDIR DE TODAS LAS ORDENES. Van de a
una orden por vez, no hay ruta de "no leidos" --se probaron tres y las tres
dan 404-- y hay 2.790 ordenes por semana. Asi que se piden solo de las
ordenes QUE TIENEN UN RECLAMO, que son decenas. Es ademas donde estan los
mensajes que importan: la charla de una venta que salio bien no cambia
ninguna decision.

    python ml_crm.py                 # lo de cada corrida
    python ml_crm.py --historico     # ademas, los 8.431 cerrados viejos
    python ml_crm.py --probar        # no escribe nada, solo cuenta
"""

import argparse
import os

import pandas as pd

import crm
import estado
import mercadolibre as ml
from mercadolibre import token_ml

BASE = "/post-purchase/v1/claims"

# La clave donde se recuerda que reclamos estaban abiertos la corrida
# anterior. Es lo unico que `cerrados_nuevos` necesita para no paginar.
CLAVE_ABIERTOS = "crm_reclamos_abiertos"

# De a cuantos pide cada ruta. 50 es lo que la API acepta sin protestar en
# las dos busquedas.
PAGINA = 50

# Tope de paginas del historico. 8.431 cerrados / 50 = 169, y se deja aire.
# Esta para que un cambio en la API no deje el script dando vueltas para
# siempre, no para cortar la carga.
PAGINAS_MAX = 400


def _detalle_error(e):
    """El codigo y el cuerpo de un error de la API, no solo su clase.

    Se imprimia `type(e).__name__`, o sea "HTTPError" a secas, y con eso la
    corrida del 08/10 informo 24 ordenes fallando sin decir POR QUE. Es el
    mismo error que ya se habia cometido truncando los mensajes del sondeo a
    70 caracteres: lo unico util de un fallo es lo que la API explica.
    """
    respuesta = getattr(e, "response", None)
    if respuesta is None:
        return f"{type(e).__name__}: {str(e)[:200]}"
    cuerpo = (getattr(respuesta, "text", "") or "")[:300]
    return f"HTTP {respuesta.status_code} — {cuerpo}"


def _filas(datos, *claves):
    """Las filas de una respuesta, probando las claves que puede traer."""
    if isinstance(datos, list):
        return datos
    if isinstance(datos, dict):
        for clave in claves:
            if isinstance(datos.get(clave), list):
                return datos[clave]
    return []


def _paginar(token, ruta, params, claves, tope=PAGINAS_MAX):
    """Recorre una busqueda paginada y devuelve todas las filas.

    Corta cuando una pagina vuelve vacia. El tope es una red de seguridad
    contra un cambio de la API, no un limite de negocio.
    """
    todas = []
    for pagina in range(tope):
        datos = ml.llamar_ml(ruta, token,
                             dict(params, limit=PAGINA, offset=pagina * PAGINA))
        filas = _filas(datos, *claves)
        if not filas:
            break
        todas.extend(filas)
        if len(filas) < PAGINA:
            break
    return todas


def existe_tabla(con, tabla, esquema="bronze"):
    """Si la tabla ya esta creada. `to_regclass` devuelve NULL si no existe.

    SE PREGUNTA ANTES EN VEZ DE INTENTAR Y ATAJAR EL ERROR, y la diferencia
    no es de estilo: es lo que rompio las cuatro corridas del 09/10.

    El codigo anterior hacia el DELETE dentro de un `try` y, si la tabla no
    estaba, se daba por enterado y seguia. En Python eso parece razonable. En
    Postgres NO: un error adentro de una transaccion LA DEJA ABORTADA, y desde
    ahi todo lo que se mande contesta

        InFailedSqlTransaction: current transaction is aborted, commands
        ignored until end of transaction block

    Atajar la excepcion en Python no deshace eso. Asi que la primera corrida
    --justamente la unica en la que la tabla no existe-- fallaba siempre, y el
    paso nunca llego a crear ninguna de las cuatro tablas: `ok` quedo en null
    con cuatro fallos seguidos.

    `to_regclass` no lanza nada: contesta NULL y la transaccion sigue limpia.
    """
    return con.exec_driver_sql(
        "SELECT to_regclass(%(nombre)s)", {"nombre": f'{esquema}."{tabla}"'}
    ).scalar() is not None


def _escribir(con, df, tabla, clave):
    """El reemplazo por clave, sobre una conexion ya abierta.

    Vive aparte de `_guardar` para poder probarlo sin base: lo que hay que
    verificar es QUE SE MANDA Y EN QUE ORDEN, no que Postgres conteste.
    """
    con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")
    # En la primera corrida no hay nada que borrar, y preguntar cuesta una
    # consulta al catalogo: muchisimo menos que una transaccion abortada.
    if existe_tabla(con, tabla):
        ids = [str(v) for v in df[clave].tolist()]
        con.exec_driver_sql(
            f'DELETE FROM bronze."{tabla}" WHERE "{clave}"::text = ANY(%(ids)s)',
            {"ids": ids},
        )
    df.to_sql(tabla, con, schema="bronze", if_exists="append", index=False)


def _guardar(filas, tabla, clave="id"):
    """Reemplaza por clave: borra las filas que se vuelven a escribir e inserta.

    No sirve el `replace` de `mercadolibre.guardar_en_bd`, que vacia la tabla
    entera: aca cada corrida trae SOLO los reclamos de hoy y borrar el resto
    perderia los 8.431 cerrados del historico. Tampoco sirve `append` pelado,
    que duplicaria un reclamo abierto en cada corrida.

    Las dos operaciones van en UNA transaccion. Partidas, una corrida cortada
    en el medio deja la tabla sin esas filas.
    """
    if not filas:
        print(f"  (sin datos para {tabla})")
        return
    df = pd.DataFrame(filas)
    engine = ml._crear_engine()
    with engine.begin() as con:
        _escribir(con, df, tabla, clave)
    print(f"  Guardado: bronze.{tabla} ({len(df)} filas)")


# ============================================================
#  RECLAMOS
# ============================================================

def pedir_abiertos(token, user_id):
    """Los reclamos abiertos, enteros. Son 24: entra en una o dos paginas."""
    crudos = _paginar(token, f"{BASE}/search", {"status": "opened"},
                      ("data", "results"))
    filas = [f for f in (crm.fila_reclamo(c, user_id) for c in crudos) if f]
    print(f"  Abiertos: {len(filas)}")
    return filas


def pedir_cerrados_nuevos(token, user_id, ids):
    """El detalle de los que se cerraron desde la corrida anterior.

    De a uno, porque son pocos --los que se cayeron de la lista de abiertos--
    y porque el detalle trae `resolution`, que es lo unico que interesa de un
    reclamo cerrado.
    """
    filas = []
    for ident in ids:
        try:
            datos = ml.llamar_ml(f"{BASE}/{ident}", token)
        except Exception as e:                       # noqa: BLE001
            # Que uno no se pueda leer no puede costar los demas: el resto de
            # la corrida sigue y ese reclamo se reintenta en la proxima,
            # porque sin fila nueva se queda como estaba.
            print(f"  No se pudo leer el reclamo {ident}: {_detalle_error(e)}")
            continue
        fila = crm.fila_reclamo(datos, user_id)
        if fila:
            filas.append(fila)
    if ids:
        print(f"  Se cerraron {len(ids)}, se leyeron {len(filas)}")
    return filas


def pedir_historico(token, user_id):
    """Los 8.431 cerrados viejos. Se corre UNA VEZ, a mano."""
    print("  Paginando los cerrados (esto tarda)...")
    crudos = _paginar(token, f"{BASE}/search", {"status": "closed"},
                      ("data", "results"))
    filas = [f for f in (crm.fila_reclamo(c, user_id) for c in crudos) if f]
    print(f"  Historico: {len(filas)} reclamos cerrados")
    return filas


def pedir_mensajes(token, ids):
    """La conversacion de cada reclamo."""
    filas = []
    for ident in ids:
        try:
            datos = ml.llamar_ml(f"{BASE}/{ident}/messages", token)
        except Exception as e:                       # noqa: BLE001
            print(f"  Sin mensajes del reclamo {ident}: {_detalle_error(e)}")
            continue
        for i, mensaje in enumerate(_filas(datos, "messages", "data")):
            if not isinstance(mensaje, dict):
                continue
            remitente = mensaje.get("sender_role") or mensaje.get("from")
            if isinstance(remitente, dict):
                remitente = remitente.get("role") or remitente.get("user_id")
            filas.append({
                # La API no siempre le pone id a cada mensaje, asi que la
                # clave se arma con el reclamo y la posicion. Es estable
                # mientras la conversacion solo crezca, que es lo que hace.
                "id": f"{ident}-{i}",
                "reclamo": str(ident),
                "de": crm._texto(remitente),
                "texto": crm._texto(mensaje.get("message") or mensaje.get("text")),
                "fecha": crm._texto(mensaje.get("date_created")
                                    or mensaje.get("date")),
            })
    print(f"  Mensajes de reclamos: {len(filas)}")
    return filas


# ============================================================
#  PREGUNTAS DE PUBLICACION
# ============================================================

def pedir_preguntas(token, user_id, todas=False):
    """Las preguntas. Sin contestar siempre; el resto solo con --historico.

    Son 2.004 y cambian poco: repaginarlas enteras en cada corrida serian 41
    llamadas para traer lo mismo. Lo que SI cambia todo el tiempo --y es lo
    unico que obliga a hacer algo hoy-- son las 5 sin contestar.
    """
    params = {"seller_id": user_id, "api_version": 4}
    if todas:
        crudas = _paginar(token, "/questions/search", params, ("questions",))
    else:
        crudas = _paginar(token, "/questions/search",
                          dict(params, status="UNANSWERED"), ("questions",))
    filas = [f for f in (crm.fila_pregunta(p) for p in crudas) if f]
    print(f"  Preguntas: {len(filas)}" + ("" if todas else " (sin contestar)"))
    return filas


# ============================================================
#  MENSAJES POST-VENTA
# ============================================================

def pack_de_la_orden(token, orden):
    """El pack al que pertenece una orden. Si no tiene, la orden misma.

    LA RUTA DE MENSAJES PIDE UN PACK, NO UNA ORDEN, y confundirlos fue lo que
    dejo las 24 conversaciones en 404 el 08/10. El `pack_id` agrupa las
    ordenes de un mismo carrito: cuando la compra fue de un solo articulo
    viene en null y entonces el pack ES la orden -- que es el unico caso que
    el sondeo habia probado, y por eso parecia que alcanzaba con la orden.

    `resource_id` del reclamo es la ORDEN, asi que hay que pasar por aca
    antes de pedir la conversacion. Es una llamada mas por reclamo: con 24
    abiertos, 24 llamadas.

    Devuelve None si la orden no se puede leer: sin pack no hay conversacion
    que pedir, y es mejor saltearla que inventar un id.
    """
    try:
        datos = ml.llamar_ml(f"/orders/{orden}", token)
    except Exception as e:                           # noqa: BLE001
        print(f"  No se pudo leer la orden {orden}: {_detalle_error(e)}")
        return None
    if not isinstance(datos, dict):
        return None
    return datos.get("pack_id") or datos.get("id") or orden


def pedir_mensajes_orden(token, user_id, ordenes):
    """La charla post-venta, SOLO de las ordenes que tienen reclamo.

    `tag=post_sale` no es opcional: sin el, la ruta devuelve 404. Lo
    descubrio el sondeo despues de dos corridas creyendo que no existia.
    """
    filas = []
    for orden in ordenes:
        pack = pack_de_la_orden(token, orden)
        if pack is None:
            continue
        ruta = f"/messages/packs/{pack}/sellers/{user_id}"
        try:
            datos = ml.llamar_ml(ruta, token, {"tag": "post_sale"})
        except Exception as e:                       # noqa: BLE001
            # Se nombran LAS DOS: con un pack distinto de la orden, saber cual
            # de los dos id fallo es la mitad del diagnostico.
            print(f"  Sin conversacion de la orden {orden} "
                  f"(pack {pack}): {_detalle_error(e)}")
            continue
        for mensaje in _filas(datos, "messages"):
            if not isinstance(mensaje, dict) or mensaje.get("id") is None:
                continue
            de = mensaje.get("from")
            de = de.get("user_id") if isinstance(de, dict) else de
            filas.append({
                "id": str(mensaje["id"]),
                "orden": str(orden),
                "de": crm._texto(de),
                "texto": crm._texto(mensaje.get("text")),
                "fecha": crm._texto(mensaje.get("message_date")
                                    or mensaje.get("date_created")),
            })
    print(f"  Mensajes post-venta: {len(filas)}")
    return filas


# ============================================================

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--historico", action="store_true",
                        help="ademas, trae los cerrados viejos y todas las preguntas")
    parser.add_argument("--probar", action="store_true",
                        help="cuenta pero NO escribe en la base")
    args = parser.parse_args()

    user_id = os.getenv("ML_USER_ID")
    if not user_id:
        raise SystemExit("Falta ML_USER_ID.")

    token = token_ml()
    print("CRM de Mercado Libre")

    abiertos = pedir_abiertos(token, user_id)
    ids_ahora = {f["id"] for f in abiertos}

    # Los que estaban abiertos y ya no estan: esos se cerraron.
    ids_antes = (estado.leer(CLAVE_ABIERTOS, []) or [])
    cerrados = pedir_cerrados_nuevos(
        token, user_id, crm.cerrados_nuevos(ids_antes, ids_ahora))

    reclamos = abiertos + cerrados
    if args.historico:
        reclamos += pedir_historico(token, user_id)

    # Los mensajes, solo de los que estan vivos: un reclamo cerrado en 2021 no
    # va a recibir uno nuevo.
    mensajes = pedir_mensajes(token, sorted(ids_ahora))

    # Y la charla post-venta de las ordenes de esos reclamos.
    ordenes = sorted({f["orden"] for f in abiertos
                      if f.get("orden") and f.get("recurso") == "order"})
    mensajes_orden = pedir_mensajes_orden(token, user_id, ordenes)

    preguntas = pedir_preguntas(token, user_id, todas=args.historico)

    print("\n  Marcador de los que trae esta corrida:")
    for nombre, cuantos in crm.marcador(reclamos).items():
        print(f"    {nombre:<16} {cuantos}")

    if args.probar:
        print("\n  --probar: no se escribio nada.")
        return

    _guardar(reclamos, "ml_reclamos")
    _guardar(mensajes, "ml_reclamos_mensajes")
    _guardar(mensajes_orden, "ml_mensajes_orden")
    _guardar(preguntas, "ml_preguntas")

    # EL ESTADO SE GUARDA AL FINAL Y A PROPOSITO. Si la corrida se corta
    # antes, la lista de abiertos queda como estaba y la proxima vuelve a
    # detectar los mismos cierres. Guardarlo antes perderia un cierre para
    # siempre: el reclamo ya no estaria en abiertos y nadie le pediria el
    # detalle nunca mas.
    estado.guardar(CLAVE_ABIERTOS, sorted(ids_ahora))
    print(f"  Recordados {len(ids_ahora)} reclamos abiertos para la proxima.")


if __name__ == "__main__":
    main()
