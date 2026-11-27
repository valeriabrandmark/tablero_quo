"""El catalogo de ML no se puede guardar recortado. Sin base ni red.

Entre el 24 y el 27/11/2026 el catalogo perdio 43, 155, 597 y 1.201
publicaciones en cuatro mananas seguidas, sin que el paso fallara: los lotes
de detalle que ML frenaba se descartaban y el scan cortaba en la primera
pagina vacia. Con ellas desaparecio su stock de Full (GL44052: 24 u.).
"""

import mercadolibre as ml

FALLOS = []


def revisar(nombre, obtenido, esperado):
    ok = obtenido == esperado
    print(f"OK  {nombre}" if ok else
          f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
    if not ok:
        FALLOS.append(nombre)


def respuesta(lote, codigo=200):
    return [{"code": codigo, "body": {"id": i}} for i in lote]


ids = [f"MLA{i}" for i in range(95)]

# --- Un lote que falla en la primera ronda se recupera en la segunda --------
llamadas = {"n": 0}
def pedir_que_falla_una_vez(lote):
    llamadas["n"] += 1
    if lote[0] == "MLA20" and llamadas.setdefault("ya", 0) == 0:
        llamadas["ya"] = 1
        raise RuntimeError("429 Too Many Requests")
    return respuesta(lote)

det, defin, pend = ml.detalle_en_rondas(ids, pedir_que_falla_una_vez, dormir=lambda s: None)
revisar("un lote frenado por ML se reintenta y no se pierde", (len(det), len(pend)), (95, 0))

# --- Lo que ML contesta 404 no se reintenta ni cuenta como faltante --------
def pedir_con_borrada(lote):
    return [{"code": 404, "body": {"id": i}} if i == "MLA7" else {"code": 200, "body": {"id": i}}
            for i in lote]

det, defin, pend = ml.detalle_en_rondas(ids, pedir_con_borrada, dormir=lambda s: None)
revisar("una publicacion inexistente es definitiva, no pendiente", (len(det), defin, pend),
        (94, {"MLA7"}, []))

# --- Si ML no contesta nunca, quedan pendientes y el catalogo NO se guarda --
def pedir_siempre_falla(lote):
    if "MLA40" in lote:
        raise RuntimeError("500")
    return respuesta(lote)

det, defin, pend = ml.detalle_en_rondas(ids, pedir_siempre_falla, dormir=lambda s: None)
revisar("un lote que falla en todas las rondas queda pendiente", len(pend), 20)
try:
    ml.verificar_completo("detalle", len(ids), len(det))
    revisar("faltar 20 de 95 corta el reemplazo", "no corto", "corto")
except ml.CatalogoIncompleto:
    revisar("faltar 20 de 95 corta el reemplazo", "corto", "corto")

# Faltar una sola de 7.600 esta dentro de la tolerancia: no se frena el paso
# por una publicacion rara.
try:
    ml.verificar_completo("detalle", 7600, 7599)
    revisar("una de 7.600 no frena nada", True, True)
except ml.CatalogoIncompleto:
    revisar("una de 7.600 no frena nada", False, True)

# --- El scan: una pagina vacia antes de tiempo se reintenta ----------------
paginas = [
    {"paging": {"total": 250}, "results": [f"A{i}" for i in range(100)], "scroll_id": "s1"},
    {"results": [], "scroll_id": "s1"},                       # vacia antes de tiempo
    {"results": [f"A{i}" for i in range(100, 200)], "scroll_id": "s2"},
    {"results": [f"A{i}" for i in range(200, 250)], "scroll_id": "s3"},
    {"results": [], "scroll_id": None},
]
cola = iter(paginas)
ids_scan = ml.obtener_ids_publicaciones("tok", llamar=lambda *a, **k: next(cola),
                                        dormir=lambda s: None)
revisar("el scan no corta en una pagina vacia si faltan", len(ids_scan), 250)

# --- Y si el scan igual termina corto, falla --------------------------------
cola = iter([
    {"paging": {"total": 300}, "results": [f"B{i}" for i in range(100)], "scroll_id": None},
])
try:
    ml.obtener_ids_publicaciones("tok", llamar=lambda *a, **k: next(cola), dormir=lambda s: None)
    revisar("un scan corto corta el reemplazo", "no corto", "corto")
except ml.CatalogoIncompleto:
    revisar("un scan corto corta el reemplazo", "corto", "corto")

print(f"\n{len(FALLOS)} pruebas fallaron" if FALLOS else "\nTodo OK")
raise SystemExit(1 if FALLOS else 0)
