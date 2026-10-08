"""Las cuentas del CRM de Mercado Libre, sin tocar la red ni la base.

Vive aparte de `ml_crm.py` por lo mismo que `relleno.py` vive aparte de
`modelo.py`: ese modulo importa `mercadolibre`, que abre la base al
importarse, y entonces no se puede probar nada sin credenciales. Aca adentro
no hay un solo import que haga I/O, asi que `probar_crm.py` lo prueba entero.

============================================================================
 LO QUE MIDIO EL SONDEO (corrida del 08/10/2026)
============================================================================

  24 reclamos ABIERTOS y 8.431 CERRADOS, con historia desde 2019.
  Etapas: claim 872, dispute 3.598, recontact 55, none 3.930.
  Tipos vistos: cancel_purchase, cancel_sale, mediations.
  2.004 preguntas de publicacion, 5 sin contestar.
  2.790 ordenes en 7 dias, 11.243 en 30.

Y dos cosas que decidieron el diseno:

  LA BUSQUEDA DEVUELVE DE LA MAS VIEJA A LA MAS NUEVA. Los 20 cerrados que
  trajo el sondeo son de 2019 a 2022. Para llegar a los de esta semana por
  esa via habria que pasar 169 paginas en cada corrida.

  PERO NO HACE FALTA, y por eso `cerrados_nuevos` existe. Un reclamo no
  aparece cerrado de la nada: antes estuvo abierto. Como los abiertos son 24
  y se piden enteros en cada corrida, los que desaparecen de esa lista son
  exactamente los que se acaban de cerrar. Se les pide el detalle de a uno
  --son unos pocos por dia-- y ahi esta la resolucion. El historico viejo se
  llena UNA VEZ con `--historico` y no se vuelve a tocar: un reclamo cerrado
  ya no cambia.
"""

# `benefited` dice a quien favorecio la resolucion, en roles y no en personas.
ROL_RECLAMA = "complainant"
ROL_RECLAMADO = "respondent"


def nuestro_rol(reclamo, user_id):
    """Que rol tenemos nosotros en ESTE reclamo. None si no figuramos.

    NO HAY TABLA FIJA, y suponerla fue el error que el sondeo destapo. Estaba
    escrito que nosotros somos siempre el reclamado --porque los reclamos los
    abre el comprador-- y es falso:

        "type": "cancel_sale",
        "players": [
          {"role": "complainant", "type": "seller", "user_id": 270905522},
          {"role": "respondent",  "type": "buyer",  "user_id": 306890223}
        ]

    En una cancelacion de venta el que reclama es EL VENDEDOR. De los 20 que
    miro el sondeo, 18 nos tenian de reclamado y 2 de reclamante. Con la
    tabla fija esos 2 salian dados vuelta.
    """
    jugadores = (reclamo or {}).get("players")
    if not isinstance(jugadores, list):
        return None
    for jugador in jugadores:
        if isinstance(jugador, dict) and str(jugador.get("user_id")) == str(user_id):
            return jugador.get("role")
    return None


def beneficiados(resolucion):
    """Los roles beneficiados, siempre como tupla ordenada.

    `benefited` NO es un texto: viene como LISTA. Usarlo de clave de un
    contador corto la corrida del 08/10 con

        TypeError: cannot use 'list' as a dict key

    Se aceptan las dos formas por si cambia segun el caso.
    """
    if not isinstance(resolucion, dict):
        return ()
    valor = resolucion.get("benefited")
    if valor is None:
        return ()
    if isinstance(valor, str):
        return (valor,)
    if isinstance(valor, list):
        return tuple(sorted(str(v) for v in valor))
    return (str(valor),)


def quien_gano(reclamo, user_id):
    """'nosotros', 'la otra parte', 'los dos', o None si no se puede saber.

    None NO es "empate": es "todavia no se sabe". Un reclamo abierto no tiene
    resolucion, y uno donde no figuramos no se puede leer. El tablero tiene
    que mostrar esos en blanco y no contarlos en el marcador.
    """
    favorecidos = beneficiados((reclamo or {}).get("resolution"))
    if not favorecidos:
        return None
    rol = nuestro_rol(reclamo, user_id)
    if rol is None:
        return None
    if rol not in favorecidos:
        return "la otra parte"
    # Que figuren los dos roles pasa cuando la resolucion parte la diferencia
    # --el sondeo vio 2 de 5 asi--. No es ganar, y mezclarlo con los ganados
    # inflaria el marcador.
    return "los dos" if len(favorecidos) > 1 else "nosotros"


def _texto(valor):
    """Un valor de la API como texto para la base, o None."""
    if valor is None:
        return None
    if isinstance(valor, (list, tuple)):
        return ", ".join(str(v) for v in valor) or None
    return str(valor)


def fila_reclamo(reclamo, user_id):
    """Un reclamo de la API como la fila que va a `bronze.ml_reclamos`.

    Devuelve None si no trae `id`: sin eso no hay clave y la fila no sirve.
    """
    if not isinstance(reclamo, dict) or reclamo.get("id") is None:
        return None

    resolucion = reclamo.get("resolution")
    resolucion = resolucion if isinstance(resolucion, dict) else {}

    return {
        "id": str(reclamo["id"]),
        # `resource_id` es la orden cuando `resource` dice "order". Se guarda
        # como texto porque los id de Mercado Libre pasan los 15 digitos y en
        # el tablero se cruzan contra `nro_orden`, que ya viaja como texto.
        "orden": _texto(reclamo.get("resource_id")),
        "recurso": _texto(reclamo.get("resource")),
        "estado": _texto(reclamo.get("status")),
        "etapa": _texto(reclamo.get("stage")),
        "tipo": _texto(reclamo.get("type")),
        "motivo": _texto(reclamo.get("reason_id")),
        "nuestro_rol": _texto(nuestro_rol(reclamo, user_id)),
        "quien_gano": _texto(quien_gano(reclamo, user_id)),
        "beneficiado": _texto(list(beneficiados(resolucion))),
        "resolucion_motivo": _texto(resolucion.get("reason")),
        "cerrado_por": _texto(resolucion.get("closed_by")),
        "cobertura_ml": resolucion.get("applied_coverage"),
        "fecha_resolucion": _texto(resolucion.get("date_created")),
        "fecha_creado": _texto(reclamo.get("date_created")),
        "fecha_actualizado": _texto(reclamo.get("last_updated")),
    }


def fila_pregunta(pregunta):
    """Una pregunta de publicacion como fila de `bronze.ml_preguntas`."""
    if not isinstance(pregunta, dict) or pregunta.get("id") is None:
        return None
    respuesta = pregunta.get("answer")
    respuesta = respuesta if isinstance(respuesta, dict) else {}
    de = pregunta.get("from")
    de = de if isinstance(de, dict) else {}
    return {
        "id": str(pregunta["id"]),
        "publicacion": _texto(pregunta.get("item_id")),
        "estado": _texto(pregunta.get("status")),
        "texto": _texto(pregunta.get("text")),
        "de": _texto(de.get("id")),
        "fecha": _texto(pregunta.get("date_created")),
        "respuesta": _texto(respuesta.get("text")),
        "respuesta_fecha": _texto(respuesta.get("date_created")),
        # Sin contestar es lo unico que obliga a hacer algo hoy, asi que se
        # calcula aca y no en cada consulta del tablero.
        "sin_contestar": pregunta.get("status") == "UNANSWERED",
    }


def cerrados_nuevos(abiertos_antes, abiertos_ahora):
    """Los reclamos que dejaron de estar abiertos entre dos corridas.

    ESTA FUNCION ES LA QUE EVITA PAGINAR 8.431 CERRADOS EN CADA CORRIDA.

    La busqueda devuelve de la mas vieja a la mas nueva, asi que llegar a los
    cerrados recientes por paginado serian 169 paginas cada vez. Pero un
    reclamo no se cierra sin haber estado abierto: alcanza con mirar quien se
    cayo de la lista de abiertos, que son 24 y se piden enteros igual.

    Los dos argumentos son conjuntos (o cualquier iterable) de ids como
    texto. Se devuelve ordenado para que el log sea estable.
    """
    antes = {str(i) for i in (abiertos_antes or ())}
    ahora = {str(i) for i in (abiertos_ahora or ())}
    return sorted(antes - ahora)


def marcador(filas):
    """El resumen del listado de cerrados: cuantos ganamos y cuantos no.

    Los que no tienen resolucion NO entran en el total. Contarlos como
    perdidos --o como ganados-- es inventar: el sondeo vio que solo 5 de 20
    cerrados traen `resolution`, asi que son la mayoria.
    """
    cuenta = {"nosotros": 0, "la otra parte": 0, "los dos": 0, "sin resolucion": 0}
    for fila in filas or ():
        valor = (fila or {}).get("quien_gano")
        if valor in cuenta:
            cuenta[valor] += 1
        else:
            cuenta["sin resolucion"] += 1
    cuenta["con resolucion"] = (cuenta["nosotros"] + cuenta["la otra parte"]
                                + cuenta["los dos"])
    return cuenta
