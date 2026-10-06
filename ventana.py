"""Hasta donde tiene que mirar hacia atras una ventana movil.

============================================================================
 EL AGUJERO QUE ESTO TAPA
============================================================================

La ventana era una constante: `hoy - 7 dias`, siempre. Eso alcanza mientras
el pipeline corra, y falla justo cuando no corre.

Mientras el paso ande, una corrida que se cae no deja nada: la de la hora
siguiente vuelve a pedir los mismos 7 dias y reemplaza todo. El problema
empieza cuando el paso deja de andar VARIOS DIAS -- se vencio un token, la
API cambio, alguien rompio algo y no se miro el log. Al octavo dia, la
primera corrida que vuelve a funcionar pide `hoy - 7` y **el dia 1 ya no
entra**. Nadie lo vuelve a pedir nunca: la ventana siguiente esta todavia mas
adelante.

Ese agujero no da error. Queda ahi, y se descubre meses despues porque un
numero no cierra -- que es exactamente como se encontro el del 06/08/2026
(622 minutos sin una sola venta de ML) y el de las 31 lineas duplicadas de
SIGMA.

============================================================================
 LA REGLA
============================================================================

La ventana llega hasta el MAS VIEJO de dos puntos:

  * `hoy - dias`, la ventana normal; y
  * la ultima vez que ESTE paso termino bien, menos un dia de colchon.

O sea que un paso que no corre desde hace tres semanas pide tres semanas, y
uno que corrio hace una hora pide los 7 dias de siempre. **Cualquier parate
se repara solo en la primera corrida que vuelve a funcionar**, dure lo que
dure, sin que nadie tenga que acordarse de nada.

El colchon de un dia es por el huso: `ml_ventas.date_created` es texto con
offset -04:00 y el borrado resuelve `::date` en UTC, asi que una venta de las
21 de aca cae del lado de alla al dia siguiente. Sin el colchon, la fila del
borde podria quedar afuera.

============================================================================
 LO QUE ESTO **NO** ARREGLA, Y CONVIENE TENERLO CLARO
============================================================================

Solo sirve cuando el paso FALLA de verdad, porque lo que mira es el ultimo
`ok`. Un paso que reporta bien y trae de menos --que es lo que hacia el
extractor viejo de ML-- sigue sin dejar rastro por aca. Para eso esta el otro
lado del blindaje: que el guardado explote en vez de guardar a medias
(`guardado.py`) y que alguien mire despues (`auditoria.py`).
"""

import datetime

import estado


def registro(pasos, comando):
    """El estado de un paso, normalizado.

    Las versiones viejas guardaban solo la fecha del ultimo OK como texto; se
    acepta ese formato para no perder el estado al actualizar.

    VIVE ACA Y NO EN `orquestador.py` porque ahora lo leen los dos: el
    orquestador para decidir si un paso toca, y cada extractor para saber
    hasta donde estirar su ventana. Escrito dos veces, el dia que cambie el
    formato uno de los dos se entera y el otro no -- que es exactamente como
    `sigma.py` se quedo sin el arreglo del guardado.
    """
    valor = (pasos or {}).get(comando)
    if isinstance(valor, str):
        return {"ok": valor, "fallos": 0, "ultimo_fallo": None, "error": None}
    if isinstance(valor, dict):
        return {
            "ok": valor.get("ok"),
            "fallos": valor.get("fallos", 0),
            "ultimo_fallo": valor.get("ultimo_fallo"),
            "error": valor.get("error"),
        }
    return {"ok": None, "fallos": 0, "ultimo_fallo": None, "error": None}


def ultima_corrida(pasos, comando):
    """Cuando termino bien ese paso por ultima vez, o None."""
    valor = registro(pasos, comando).get("ok")
    if not valor:
        return None
    try:
        return datetime.datetime.fromisoformat(valor)
    except ValueError:
        return None


# Un dia de colchon sobre el ultimo OK. Ver el porque arriba (el huso).
COLCHON = datetime.timedelta(days=1)


def cutoff(comando, dias, piso, hoy=None, pasos=None):
    """El piso de la ventana movil para ese paso.

    `comando` es como lo nombra `PASOS` del orquestador ("sigma.py --ventas").
    `dias` es la ventana normal y `piso` el corte historico absoluto: nunca se
    pide nada anterior a esa fecha, por mas que el paso no corra desde antes.

    Si no se puede leer el estado --base caida, clave que no existe todavia--
    se devuelve la ventana normal. NUNCA se achica por no poder leer: ante la
    duda se pide de mas, que es barato, en vez de dejar un agujero.
    """
    hoy = hoy or datetime.date.today()
    normal = hoy - datetime.timedelta(days=dias)

    if pasos is None:
        try:
            pasos = estado.leer("pasos", {})
        except Exception as e:
            print(f"  (no se pudo leer el estado de los pasos: {e})")
            print(f"  -> se usa la ventana normal de {dias} dias")
            pasos = {}

    ultimo = ultima_corrida(pasos, comando)
    if ultimo is None:
        # Sin `ok` es la primera corrida de este paso. Se usa la ventana
        # normal: el piso historico ya marca donde empieza todo, y pedir
        # cuatro meses en la primera corrida no es lo que se quiere.
        return max(piso, normal)

    desde_el_ultimo = ultimo.date() - COLCHON
    elegido = min(normal, desde_el_ultimo)

    if elegido < normal:
        dias_sin_correr = (hoy - ultimo.date()).days
        print(
            f"  Este paso no corre bien desde hace {dias_sin_correr} dia(s): "
            f"la ventana se estira de {dias} a {(hoy - elegido).days} dias"
        )

    return max(piso, elegido)


# --- Cuando un fallo tiene que pintar la corrida de rojo -------------------

AVISAR_TRAS_POR_DEFECTO = 1


def corrida_en_rojo(fallados, pasos, umbrales=None):
    """De los pasos que fallaron, cuales ameritan terminar en rojo.

    El orquestador sale con error si fallo CUALQUIER paso, aunque no sea
    critico y el pipeline haya seguido de largo. Eso esta bien pensado: en
    GitHub Actions un `exit 0` no manda ningun mail, y un paso roto en
    silencio puede quedarse semanas asi.

    El problema aparece cuando el que falla es un proveedor con hipos. Entre
    el 04 y el 05/10 la API de DIGIP devolvio 500 ocho veces en dos dias,
    siempre con el paso recuperandose solo en la corrida siguiente: nunca
    hubo mas de dos fallos seguidos, o sea que el stock jamas estuvo mas de
    ~3 horas sin refrescarse. Ocho mails en rojo por eso no informan nada y
    encima gastan lo unico que el rojo tiene de valor, que es que alguien lo
    mire. El dia que se caiga Mercado Libre va a parecer "otro mas de DIGIP".

    Asi que un paso puede pedir `avisar_tras: N`: recien pinta rojo cuando
    acumulo N fallos SEGUIDOS. El contador sale de `fallos` en ops.estado,
    que ya se resetea solo con el primer exito, asi que no hace falta
    guardar nada nuevo.

    Sin `avisar_tras` el comportamiento es el de siempre (N = 1).

    `fallados` son los comandos que fallaron en ESTA corrida; `pasos` es el
    estado YA actualizado, de donde sale el contador.
    """
    umbrales = umbrales or {}
    en_rojo = []
    for comando in fallados:
        umbral = umbrales.get(comando) or AVISAR_TRAS_POR_DEFECTO
        if registro(pasos, comando)["fallos"] >= umbral:
            en_rojo.append(comando)
    return en_rojo
