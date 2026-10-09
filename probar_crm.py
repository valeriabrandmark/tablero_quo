"""Las cuentas del CRM de Mercado Libre.

Todo sale de `crm.py`, que es puro: ni red ni base. Los casos estan copiados
de la corrida real del sondeo (08/10/2026), con su user_id y todo.

    python probar_crm.py
"""

import crm

OK = 0
MAL = 0
YO = 270905522


def probar(nombre, obtenido, esperado):
    global OK, MAL
    if obtenido == esperado:
        OK += 1
        print(f"  ok   {nombre}")
    else:
        MAL += 1
        print(f"  MAL  {nombre}\n       esperaba {esperado!r}\n       obtuvo   {obtenido!r}")


# El reclamo real que destapo el error, copiado tal cual del sondeo.
CANCELACION = {
    "id": 5008793552, "resource_id": 2233738269, "status": "closed",
    "type": "cancel_sale", "stage": "none", "parent_id": None,
    "resource": "order", "reason_id": "CS6237", "fulfilled": False,
    "quantity_type": None,
    "players": [
        {"role": "complainant", "type": "seller", "user_id": 270905522,
         "available_actions": []},
        {"role": "respondent", "type": "buyer", "user_id": 306890223,
         "available_actions": []},
    ],
    "resolution": None, "site_id": "MLA",
    "date_created": "2019-12-02T10:23:19.000-04:00",
    "last_updated": "2019-12-02T10:23:19.000-04:00",
}

DEL_COMPRADOR = {
    "id": 777, "resource_id": 2000018876214156, "status": "closed",
    "type": "mediations", "stage": "dispute", "resource": "order",
    "players": [
        {"role": "complainant", "type": "buyer", "user_id": 306890223},
        {"role": "respondent", "type": "seller", "user_id": YO},
    ],
    "date_created": "2026-10-01T09:00:00.000-03:00",
    "last_updated": "2026-10-05T09:00:00.000-03:00",
}

print("\nNUESTRO ROL CAMBIA SEGUN EL RECLAMO")
probar("en cancel_sale somos el que reclama",
       crm.nuestro_rol(CANCELACION, YO), "complainant")
probar("en un reclamo del comprador somos el reclamado",
       crm.nuestro_rol(DEL_COMPRADOR, YO), "respondent")
probar("el user_id como texto tambien matchea",
       crm.nuestro_rol(DEL_COMPRADOR, str(YO)), "respondent")
probar("si no figuramos, None",
       crm.nuestro_rol({"players": [{"role": "complainant", "user_id": 1}]}, YO), None)
probar("sin players no explota", crm.nuestro_rol({}, YO), None)
probar("players con basura no explota",
       crm.nuestro_rol({"players": ["x", None, 7]}, YO), None)

print("\n`benefited` VIENE COMO LISTA (lo que corto la corrida)")
probar("lista", crm.beneficiados({"benefited": ["complainant"]}), ("complainant",))
probar("lista de dos, ordenada",
       crm.beneficiados({"benefited": ["respondent", "complainant"]}),
       ("complainant", "respondent"))
probar("texto suelto", crm.beneficiados({"benefited": "respondent"}), ("respondent",))
probar("null", crm.beneficiados({"benefited": None}), ())
probar("resolution en null", crm.beneficiados(None), ())
probar("un numero no explota", crm.beneficiados({"benefited": 7}), ("7",))

print("\nQUIEN GANO, LEIDO CONTRA NUESTRO ROL")
probar("cancelacion a favor del que reclama = GANAMOS NOSOTROS",
       crm.quien_gano(dict(CANCELACION, resolution={"benefited": ["complainant"]}), YO),
       "nosotros")
probar("cancelacion a favor del reclamado = gano la otra parte",
       crm.quien_gano(dict(CANCELACION, resolution={"benefited": ["respondent"]}), YO),
       "la otra parte")
probar("reclamo del comprador a favor del reclamado = ganamos nosotros",
       crm.quien_gano(dict(DEL_COMPRADOR, resolution={"benefited": ["respondent"]}), YO),
       "nosotros")
probar("reclamo del comprador a favor suyo = gano la otra parte",
       crm.quien_gano(dict(DEL_COMPRADOR, resolution={"benefited": ["complainant"]}), YO),
       "la otra parte")
probar("los dos beneficiados no es ganar",
       crm.quien_gano(dict(DEL_COMPRADOR,
                           resolution={"benefited": ["complainant", "respondent"]}), YO),
       "los dos")
probar("sin resolution no se inventa", crm.quien_gano(CANCELACION, YO), None)
probar("si no figuramos tampoco se inventa",
       crm.quien_gano({"players": [{"role": "complainant", "user_id": 1}],
                       "resolution": {"benefited": ["complainant"]}}, YO), None)

# LA PRUEBA QUE ATAJA LA VUELTA ATRAS: mismo `benefited`, resultado OPUESTO.
print("\nEL MISMO `benefited` DA RESULTADOS OPUESTOS")
a = crm.quien_gano(dict(CANCELACION, resolution={"benefited": ["complainant"]}), YO)
b = crm.quien_gano(dict(DEL_COMPRADOR, resolution={"benefited": ["complainant"]}), YO)
probar("cualquier tabla fija falla aca", (a, b), ("nosotros", "la otra parte"))

print("\nLA FILA QUE VA A LA BASE")
fila = crm.fila_reclamo(CANCELACION, YO)
probar("id como texto", fila["id"], "5008793552")
probar("la orden sale de resource_id", fila["orden"], "2233738269")
probar("tipo", fila["tipo"], "cancel_sale")
probar("etapa none se guarda, no se pierde", fila["etapa"], "none")
probar("nuestro rol queda escrito", fila["nuestro_rol"], "complainant")
probar("sin resolucion, quien_gano en None", fila["quien_gano"], None)
probar("sin resolucion, beneficiado en None", fila["beneficiado"], None)
probar("fecha de creacion entera", fila["fecha_creado"], "2019-12-02T10:23:19.000-04:00")

resuelto = dict(DEL_COMPRADOR, resolution={
    "benefited": ["complainant", "respondent"], "reason": "refund",
    "closed_by": "mediator", "applied_coverage": True,
    "date_created": "2026-10-05T10:00:00.000-03:00"})
fila2 = crm.fila_reclamo(resuelto, YO)
probar("beneficiado con los dos, en texto", fila2["beneficiado"], "complainant, respondent")
probar("quien gano", fila2["quien_gano"], "los dos")
probar("cerrado por", fila2["cerrado_por"], "mediator")
probar("cobertura de ML", fila2["cobertura_ml"], True)
probar("motivo de la resolucion", fila2["resolucion_motivo"], "refund")
probar("sin id no hay fila", crm.fila_reclamo({"status": "closed"}, YO), None)
probar("basura no explota", crm.fila_reclamo("no soy un dict", YO), None)

print("\nPREGUNTAS DE PUBLICACION")
# Campos tal como los devolvio la corrida real.
PREGUNTA = {
    "id": 123, "item_id": "MLA123", "status": "UNANSWERED",
    "text": "¿viene con garantia?", "date_created": "2026-10-07T12:00:00.000-03:00",
    "from": {"id": 55}, "answer": None, "tags": [], "hold": False,
    "deleted_from_listing": False, "ai_categories": None, "seller_id": YO,
}
p = crm.fila_pregunta(PREGUNTA)
probar("sin contestar se marca", p["sin_contestar"], True)
probar("respuesta en None", p["respuesta"], None)
probar("publicacion", p["publicacion"], "MLA123")
probar("quien pregunto", p["de"], "55")
contestada = dict(PREGUNTA, status="ANSWERED", answer={
    "text": "si, 12 meses", "status": "ACTIVE",
    "date_created": "2026-10-07T13:00:00.000-03:00"})
q = crm.fila_pregunta(contestada)
probar("contestada no se marca", q["sin_contestar"], False)
probar("el texto de la respuesta", q["respuesta"], "si, 12 meses")
probar("sin id no hay fila", crm.fila_pregunta({"text": "hola"}), None)

print("\nQUE SE CERRO ENTRE DOS CORRIDAS (evita paginar 8.431)")
probar("el que se cayo de la lista",
       crm.cerrados_nuevos({"1", "2", "3"}, {"1", "3"}), ["2"])
probar("ninguno", crm.cerrados_nuevos({"1"}, {"1"}), [])
probar("uno nuevo abierto no cuenta",
       crm.cerrados_nuevos({"1"}, {"1", "9"}), [])
probar("varios, ordenados",
       crm.cerrados_nuevos({"5", "2", "9"}, set()), ["2", "5", "9"])
probar("mezcla de numeros y texto",
       crm.cerrados_nuevos([1, 2], ["1"]), ["2"])
probar("primera corrida, sin estado previo",
       crm.cerrados_nuevos(None, {"1"}), [])

print("\nEL MARCADOR DEL LISTADO")
filas = [{"quien_gano": "nosotros"}, {"quien_gano": "nosotros"},
         {"quien_gano": "la otra parte"}, {"quien_gano": "los dos"},
         {"quien_gano": None}, {"quien_gano": None}]
m = crm.marcador(filas)
probar("ganados", m["nosotros"], 2)
probar("perdidos", m["la otra parte"], 1)
probar("partidos", m["los dos"], 1)
probar("los sin resolucion NO se cuentan como perdidos", m["sin resolucion"], 2)
probar("el total son solo los resueltos", m["con resolucion"], 4)
probar("lista vacia no explota", crm.marcador([])["con resolucion"], 0)
probar("None no explota", crm.marcador(None)["con resolucion"], 0)

# ===========================================================================
#  EL PAGINADO DE ml_crm.py
# ===========================================================================
#
# Se carga por AST y no con un import porque `ml_crm` importa `mercadolibre`,
# que abre la base al importarse. Estas dos funciones son puras.

import ast
import types

_ARBOL = ast.parse(open("ml_crm.py", encoding="utf-8").read())
_MOD = ast.Module(
    body=[n for n in _ARBOL.body
          if isinstance(n, ast.FunctionDef)
          and n.name in {"_filas", "_paginar", "_detalle_error",
                         "pack_de_la_orden"}],
    type_ignores=[],
)
_NS = {"PAGINA": 50, "PAGINAS_MAX": 400}
exec(compile(_MOD, "ml_crm.py", "exec"), _NS)            # noqa: S102
_filas, _paginar = _NS["_filas"], _NS["_paginar"]
_detalle_error = _NS["_detalle_error"]
_pack_de_la_orden = _NS["pack_de_la_orden"]

print("\nCADA RUTA DEVUELVE LAS FILAS EN OTRA CLAVE")
probar("data, la de reclamos", _filas({"data": [1, 2]}, "data", "results"), [1, 2])
probar("questions, la de preguntas", _filas({"questions": [1]}, "questions"), [1])
probar("messages, la de post-venta", _filas({"messages": [1]}, "messages"), [1])
probar("los mensajes de un reclamo vienen en lista pelada",
       _filas([1, 2], "messages"), [1, 2])
probar("clave que no esta", _filas({"otra": [1]}, "data"), [])
probar("None no explota", _filas(None, "data"), [])


class _ApiFalsa:
    """Contesta paginado, para contar cuantas llamadas hace `_paginar`."""

    def __init__(self, total):
        self.total = total
        self.llamadas = 0

    def __call__(self, ruta, token, params):
        self.llamadas += 1
        desde = params["offset"]
        hasta = min(desde + params["limit"], self.total)
        return {"data": list(range(desde, max(desde, hasta)))}


print("\nEL PAGINADO PIDE LO JUSTO")
for _total, _llamadas in ((0, 1), (10, 1), (50, 2), (100, 3), (120, 3)):
    _api = _ApiFalsa(_total)
    _NS["ml"] = types.SimpleNamespace(llamar_ml=_api)
    probar(f"{_total} filas -> las trae todas", len(_paginar("t", "/x", {}, ("data",))), _total)
    probar(f"{_total} filas -> {_llamadas} llamadas", _api.llamadas, _llamadas)

# Una API que nunca devuelve una pagina corta dejaria el script dando vueltas
# para siempre. El tope esta para eso.
_NS["ml"] = types.SimpleNamespace(
    llamar_ml=lambda ruta, token, params: {"data": list(range(params["limit"]))})
probar("el tope corta el bucle", len(_paginar("t", "/x", {}, ("data",), tope=3)), 150)


class _Respuesta:
    def __init__(self, codigo, texto):
        self.status_code = codigo
        self.text = texto


class _ErrorHttp(Exception):
    def __init__(self, codigo, texto):
        super().__init__(f"{codigo}")
        self.response = _Respuesta(codigo, texto)


print("\nUN ERROR TIENE QUE DECIR QUE PASO")
probar("el codigo y el cuerpo, no la clase",
       _detalle_error(_ErrorHttp(404, '{"error":"resource not found"}')),
       'HTTP 404 — {"error":"resource not found"}')
probar("un cuerpo largo se recorta pero se ve",
       _detalle_error(_ErrorHttp(500, "x" * 500)).startswith("HTTP 500 — xxx"), True)
probar("sin response, la clase y el texto",
       _detalle_error(ValueError("se rompio")), "ValueError: se rompio")


print("\nLA RUTA DE MENSAJES PIDE UN PACK, NO UNA ORDEN")
# Lo que dejo 24 conversaciones en 404: una orden de carrito tiene pack_id
# propio y distinto del id de la orden.
_NS["ml"] = types.SimpleNamespace(
    llamar_ml=lambda ruta, token, params=None: {"id": 111, "pack_id": 999})
probar("con pack_id se usa el pack", _pack_de_la_orden("t", 111), 999)

_NS["ml"] = types.SimpleNamespace(
    llamar_ml=lambda ruta, token, params=None: {"id": 111, "pack_id": None})
probar("sin pack_id el pack es la orden", _pack_de_la_orden("t", 111), 111)

_NS["ml"] = types.SimpleNamespace(
    llamar_ml=lambda ruta, token, params=None: {})
probar("una orden sin id cae en la que se pidio", _pack_de_la_orden("t", 111), 111)


def _explota(ruta, token, params=None):
    raise _ErrorHttp(404, "no existe")


_NS["ml"] = types.SimpleNamespace(llamar_ml=_explota)
probar("si la orden no se puede leer, None y no un id inventado",
       _pack_de_la_orden("t", 111), None)

_NS["ml"] = types.SimpleNamespace(
    llamar_ml=lambda ruta, token, params=None: "no soy un dict")
probar("una respuesta rara no explota", _pack_de_la_orden("t", 111), None)


print(f"\n{'TODO OK' if not MAL else 'HAY FALLAS'}: {OK} ok, {MAL} mal\n")
raise SystemExit(1 if MAL else 0)
