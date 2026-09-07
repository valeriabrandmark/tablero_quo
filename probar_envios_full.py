"""Pruebas del lector de envios a Full.

POR QUE ESTO TIENE PRUEBAS. De esta tabla sale la pata de INGRESOS de la
trazabilidad: contra ella se va a comparar lo que Mercado Libre declara para
decidir si hay unidades para reclamar. Un item contado de mas hace ver un
faltante de ML donde no lo hay; uno contado de menos tapa uno real.

Los dos errores faciles son elegir el numero equivocado --hay tres columnas de
unidades parecidas-- y dejar entrar despachos que no van a Full.

    python probar_envios_full.py
"""

import sys
import types

# El modulo abre la base al importarse. Aca solo se prueba la funcion que
# arma las filas, que es pura.
sys.modules.setdefault("conexion", types.SimpleNamespace(crear_engine=lambda **k: None))
sys.modules.setdefault("errores_bd", types.SimpleNamespace(es_tabla_inexistente=lambda e: False))

from digip_envios_full import DESPACHOS_A_FULL, filas_de_preparacion  # noqa: E402

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


def prep(despacho, items, **extra):
    base = {
        "id": 900001,
        "despachoDescripcion": despacho,
        "despachoEstado": "Despachado",
        "preparacionEstado": "Completo",
        "fechaHoraEstado": "2026-09-05T10:00:00",
        "items": items,
    }
    base.update(extra)
    return base


# Un item real, copiado de la respuesta de la API (pedido 2040941257TN).
ITEM = {
    "id": 175251870,
    "codigoArticulo": "PR01002",
    "articulo": "IMPULSE DEO AER MUSK 150 ML",
    "unidades": 12,
    "unidadesReservada": 12,
    "unidadesSatisfecha": 12,
    "lote": None,
    "fechaVencimiento": None,
    "volumen": 4058100,
    "peso": 1836,
}

# --- Que despachos entran -------------------------------------------------

for despacho in DESPACHOS_A_FULL:
    revisar(f"entra '{despacho}'",
            len(filas_de_preparacion(prep(despacho, [ITEM]), "1", "Completo")), 1)

for despacho in ("TIENDA NUBE", "CONSUMO INTERNO", "SEBASTIAN BRANDMARK",
                 "VENTAS DIARIAS MELI", "SEVILLANITA NOA", "RETIRO DE DEPOSITO"):
    revisar(f"NO entra '{despacho}'",
            filas_de_preparacion(prep(despacho, [ITEM]), "1", "Completo"), [])

# "VENTAS DIARIAS MELI" tiene "MELI" en el nombre y NO va a Full: son las
# ventas del dia que salen a clientes. Si alguna vez alguien reemplaza la
# comparacion exacta por un `"MELI" in despacho`, esta prueba lo agarra.
revisar("'VENTAS DIARIAS MELI' no se cuela por tener MELI en el nombre",
        filas_de_preparacion(prep("VENTAS DIARIAS MELI", [ITEM]), "1", "Completo"), [])

# La API devolvio "CONSUMO INTERNO " con un espacio al final, asi que la
# comparacion tiene que aguantar espacios y mayusculas de cualquier lado.
revisar("aguanta espacios y minusculas en el nombre del despacho",
        len(filas_de_preparacion(prep("  andreani brandmark  ", [ITEM]), "1", "Completo")), 1)

# --- Que numero se guarda -------------------------------------------------

# EL CASO QUE IMPORTA. En el pedido 74430467 hay articulos que pidieron 4 y
# satisficieron 1. Lo que salio del deposito es lo satisfecho.
PARCIAL = {**ITEM, "id": 2, "unidades": 4, "unidadesReservada": 1, "unidadesSatisfecha": 1}
fila = filas_de_preparacion(prep("ANDREANI BRANDMARK", [PARCIAL]), "73968837", "Completo")[0]

revisar("guarda lo SATISFECHO, no lo pedido", fila["unidades_satisfecha"], 1)
revisar("guarda tambien lo pedido, para poder ver el faltante", fila["unidades"], 4)
revisar("guarda lo reservado", fila["unidades_reservada"], 1)

# --- Que el resto del contexto viaje --------------------------------------

fila = filas_de_preparacion(prep("ETIQUETADO MELI QUO", [ITEM]), "73968838", "Preparacion")[0]
revisar("el sku sale de codigoArticulo", fila["sku"], "PR01002")
revisar("guarda el estado del PEDIDO", fila["pedido_estado"], "Preparacion")
revisar("guarda el estado de la PREPARACION", fila["preparacion_estado"], "Completo")
revisar("guarda la fecha del estado", fila["fecha_estado"], "2026-09-05T10:00:00")
revisar("guarda el id de la preparacion", fila["preparacion_id"], 900001)
revisar("guarda el id del item, que es la otra mitad de la clave",
        fila["item_id"], 175251870)

# --- Los bordes -----------------------------------------------------------

revisar("una preparacion sin items no rompe",
        filas_de_preparacion(prep("ANDREANI BRANDMARK", []), "1", "Completo"), [])
revisar("una preparacion con items en null no rompe",
        filas_de_preparacion(prep("ANDREANI BRANDMARK", None), "1", "Completo"), [])
revisar("sin despacho no entra",
        filas_de_preparacion({"id": 1, "items": [ITEM]}, "1", "Completo"), [])

# Un sku vacio queda en None y no en cadena vacia: en la comparacion contra el
# stock de ML, '' se uniria con otros '' y sumaria unidades de articulos
# distintos en una fila sola.
VACIO = {**ITEM, "codigoArticulo": "   "}
fila = filas_de_preparacion(prep("ANDREANI BRANDMARK", [VACIO]), "1", "Completo")[0]
revisar("un sku vacio queda en None, no en cadena vacia", fila["sku"], None)

# El despacho se puede llamar por codigo cuando no viene la descripcion.
revisar("cae a despachoCodigo si falta la descripcion",
        len(filas_de_preparacion(
            {"id": 1, "despachoCodigo": "ANDREANI BRANDMARK", "items": [ITEM]},
            "1", "Completo")), 1)

# Varios items de la misma preparacion salen todos.
revisar("una preparacion de 3 items da 3 filas",
        len(filas_de_preparacion(
            prep("ANDREANI BRANDMARK",
                 [ITEM, {**ITEM, "id": 2}, {**ITEM, "id": 3}]), "1", "Completo")), 3)


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
