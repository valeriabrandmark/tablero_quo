"""A favor de quien se resolvio un reclamo de Mercado Libre.

Es la cuenta que alimenta el listado de cerrados del tablero de CRM, y la
que el sondeo tuvo MAL hasta el 08/10/2026.

EL ERROR QUE ESTAS PRUEBAS FIJAN. Estaba escrito que nosotros somos siempre
el reclamado, asi que `complainant` era el comprador y `respondent` nosotros.
Es falso: en una cancelacion de venta el que reclama es el VENDEDOR. Con la
tabla fija, esos reclamos se daban vuelta -- los ganados contados como
perdidos y al reves. El rol sale de `players` en CADA reclamo.

El caso que lo destapo esta copiado tal cual de la corrida real, con su
user_id y todo, para que la prueba falle si alguien vuelve a la tabla fija.

    python probar_quien_gano.py
"""

import ast

OK = 0
MAL = 0

# Se cargan solo las funciones puras de probar_crm_ml.py: ese modulo importa
# mercadolibre, que abre la base al importarse.
_ARBOL = ast.parse(open("probar_crm_ml.py", encoding="utf-8").read())
_QUIERO = {"nuestro_rol", "beneficiados", "quien_gano"}
_MOD = ast.Module(
    body=[n for n in _ARBOL.body
          if isinstance(n, ast.FunctionDef) and n.name in _QUIERO],
    type_ignores=[],
)
_NS = {}
exec(compile(_MOD, "probar_crm_ml.py", "exec"), _NS)       # noqa: S102
nuestro_rol = _NS["nuestro_rol"]
beneficiados = _NS["beneficiados"]
quien_gano = _NS["quien_gano"]

YO = 270905522


def probar(nombre, obtenido, esperado):
    global OK, MAL
    if obtenido == esperado:
        OK += 1
        print(f"  ok   {nombre}")
    else:
        MAL += 1
        print(f"  MAL  {nombre}\n       esperaba {esperado!r}\n       obtuvo   {obtenido!r}")


# El reclamo real de la corrida del 08/10, copiado como vino.
CANCELACION = {
    "id": 5008793552, "resource_id": 2233738269, "status": "closed",
    "type": "cancel_sale", "stage": "none", "resource": "order",
    "reason_id": "CS6237", "fulfilled": False,
    "players": [
        {"role": "complainant", "type": "seller", "user_id": 270905522,
         "available_actions": []},
        {"role": "respondent", "type": "buyer", "user_id": 306890223,
         "available_actions": []},
    ],
    "resolution": None, "site_id": "MLA",
    "date_created": "2019-12-02T10:23:19.000-04:00",
}

# El caso clasico, al reves que el anterior.
DEL_COMPRADOR = {
    "players": [
        {"role": "complainant", "type": "buyer", "user_id": 306890223},
        {"role": "respondent", "type": "seller", "user_id": YO},
    ],
}

print("\nNUESTRO ROL CAMBIA SEGUN EL RECLAMO")
probar("en cancel_sale somos el que reclama",
       nuestro_rol(CANCELACION, YO), "complainant")
probar("en un reclamo del comprador somos el reclamado",
       nuestro_rol(DEL_COMPRADOR, YO), "respondent")
probar("el user_id como texto tambien matchea",
       nuestro_rol(DEL_COMPRADOR, str(YO)), "respondent")
probar("si no figuramos, None", nuestro_rol({"players": [
    {"role": "complainant", "user_id": 1}]}, YO), None)
probar("sin players no explota", nuestro_rol({}, YO), None)
probar("players con basura no explota",
       nuestro_rol({"players": ["x", None, 7]}, YO), None)

print("\n`benefited` VIENE COMO LISTA (lo que corto la corrida)")
probar("lista", beneficiados({"benefited": ["complainant"]}), ("complainant",))
probar("lista de dos, ordenada",
       beneficiados({"benefited": ["respondent", "complainant"]}),
       ("complainant", "respondent"))
probar("texto suelto", beneficiados({"benefited": "respondent"}), ("respondent",))
probar("null", beneficiados({"benefited": None}), ())
probar("resolution en null", beneficiados(None), ())
probar("un numero no explota", beneficiados({"benefited": 7}), ("7",))

print("\nQUIEN GANO, LEIDO CONTRA NUESTRO ROL")
probar("cancelacion a favor del que reclama = GANAMOS NOSOTROS",
       quien_gano(dict(CANCELACION, resolution={"benefited": ["complainant"]}), YO),
       "nosotros")
probar("cancelacion a favor del reclamado = gano la otra parte",
       quien_gano(dict(CANCELACION, resolution={"benefited": ["respondent"]}), YO),
       "la otra parte")
probar("reclamo del comprador a favor del reclamado = ganamos nosotros",
       quien_gano(dict(DEL_COMPRADOR, resolution={"benefited": ["respondent"]}), YO),
       "nosotros")
probar("reclamo del comprador a favor suyo = gano la otra parte",
       quien_gano(dict(DEL_COMPRADOR, resolution={"benefited": ["complainant"]}), YO),
       "la otra parte")
probar("los dos beneficiados no es ganar",
       quien_gano(dict(DEL_COMPRADOR,
                       resolution={"benefited": ["complainant", "respondent"]}), YO),
       "los dos")
probar("sin resolution no se inventa", quien_gano(CANCELACION, YO), None)
probar("si no figuramos tampoco se inventa",
       quien_gano({"players": [{"role": "complainant", "user_id": 1}],
                   "resolution": {"benefited": ["complainant"]}}, YO), None)

# LA PRUEBA QUE ATAJA LA VUELTA ATRAS. Las dos resoluciones dicen
# `complainant`, y la respuesta correcta es la OPUESTA en cada una. Con
# cualquier tabla fija de benefited -> ganador, una de las dos falla.
print("\nEL MISMO `benefited` DA RESULTADOS OPUESTOS")
a = quien_gano(dict(CANCELACION, resolution={"benefited": ["complainant"]}), YO)
b = quien_gano(dict(DEL_COMPRADOR, resolution={"benefited": ["complainant"]}), YO)
probar("mismo benefited, distinto resultado", (a, b), ("nosotros", "la otra parte"))

print(f"\n{'TODO OK' if not MAL else 'HAY FALLAS'}: {OK} ok, {MAL} mal\n")
raise SystemExit(1 if MAL else 0)
