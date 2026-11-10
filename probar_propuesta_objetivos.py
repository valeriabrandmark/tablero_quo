"""Pruebas de la propuesta automatica de objetivos, sin base ni red.

Lo que se fija es que la propuesta no proponga lo que no se puede cumplir:
un objetivo sin stock, dos vendedores que entre los dos piden mas de lo que
hay, cinco objetivos de la misma marca, o un objetivo de 3 unidades. Los
casos son los candidatos reales de noviembre de 2026.

    python probar_propuesta_objetivos.py
"""

import os
import shutil
import sys
import tempfile

from openpyxl import load_workbook

import objetivos_planilla as planilla
import propuesta_objetivos as p

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado!r}\n     obtenido: {obtenido!r}")
        FALLOS.append(nombre)


def cand(tipo, vendedor, sku, base, stock, puntaje, marca="X", u=(0, 0, 0),
         verano_mes=None, descripcion=None, sell_in_pct=0, grupo_existente=None):
    return {"tipo": tipo, "vendedor": vendedor, "sku": sku, "base": base,
            "stock": stock, "puntaje": puntaje, "marca": marca,
            "u1": u[0], "u2": u[1], "u3": u[2], "verano_mes": verano_mes,
            "descripcion": descripcion or sku, "sell_in_pct": sell_in_pct,
            "margen_pct": 0.2, "grupo_existente": grupo_existente}


MES, MESES = "2026-11", ["2026-08", "2026-09", "2026-10"]

# --- Lo chico -----------------------------------------------------------------

revisar("redondear: 46,2 -> 50", p.redondear(46.2), 50)
revisar("redondear: 45 justo queda", p.redondear(45), 45)
revisar("redondear: 140,8 -> 150 (a 10 desde 100)", p.redondear(140.8), 150)
revisar("redondear: 187 -> 190", p.redondear(187), 190)
revisar("meses anteriores", p.meses_anteriores("2026-11"), MESES)
revisar("meses anteriores cruzando el año", p.meses_anteriores("2027-01", 2),
        ["2026-11", "2026-12"])
revisar("ritmo de noviembre", p.ritmo("2026-11"), 0.25)
revisar("julio no es temporada", p.ritmo("2026-07"), None)
revisar("nombre: el grupo que ya existe manda",
        p.nombre_de(cand("t", "S", "SS07010", 1, 1, 1, descripcion="X5 ESPIRALES",
                         grupo_existente="X5 ESPIRALES X-5 12DC")),
        "X5 ESPIRALES X-5 12DC")
# Lo de 2026-10: la descripcion copiada con la coma colgando.
revisar("nombre: sin la coma del final",
        p.nombre_de(cand("n", "S", "GL44016", 1, 1, 1,
                         descripcion="SCOTCH-BRITE ESPONJA LIMPIEZA PESADA,")),
        "SCOTCH-BRITE ESPONJA LIMPIEZA PESADA")
revisar("la propuesta no la levanta la carga",
        planilla.mes_del_archivo(p.nombre_archivo_propuesta("2026-11")), None)

# --- Elegir -------------------------------------------------------------------

# Noviembre de verdad, recortado: espirales para los dos, tabletas para GERMAN.
cands = [
    cand("temporada", "SILVIO", "SS07010", 128, 1945, 750_000, "X5", verano_mes=512),
    cand("temporada", "SILVIO", "SS07022", 4.75, 233, 150_000, "X5", verano_mes=19),
    cand("temporada", "GERMAN", "SS07010", 170, 1945, 1_000_000, "X5", verano_mes=680),
    cand("temporada", "GERMAN", "SS07027", 80, 130, 255_000, "X5", verano_mes=320),
    cand("nucleo", "SILVIO", "CP10005", 42, 140, 225_000, "KEVIN", (49, 42, 114)),
    cand("nucleo", "SILVIO", "AL06009", 130, 1420, 220_000, "ALGABO", (172, 329, 130)),
    cand("nucleo", "SILVIO", "AL08004", 138, 62, 192_000, "ALGABO", (138, 168, 148)),
    cand("nucleo", "SILVIO", "GL48001", 63, 660, 185_000, "TODDLER", (85, 300, 63)),
    cand("nucleo", "SILVIO", "CP08029", 43, 23, 179_000, "CIEL", (44, 43, 59)),
    cand("nucleo", "SILVIO", "LF02005", 60, 900, 100_000, "VERITAS", (60, 70, 80)),
    cand("nucleo", "GERMAN", "CP10005", 36, 140, 191_000, "KEVIN", (68, 36, 117)),
    cand("nucleo", "GERMAN", "CP10004", 22, 205, 101_000, "KEVIN", (71, 22, 104)),
    cand("nucleo", "GERMAN", "SS07001", 84, 6, 87_000, "X5", (84, 144, 264)),
    cand("nucleo", "GERMAN", "AL06010", 30, 114, 64_000, "ALGABO", (116, 30, 82)),
    cand("nucleo", "GERMAN", "CU10003", 25, 133, 60_000, "L EAU VIVE", (90, 25, 25)),
]
elegidos, descartados = p.elegir(cands, ["SILVIO", "GERMAN"], MES, MESES)
skus = {v: [(e["sku"], e["objetivo"]) for e in l] for v, l in elegidos.items()}
motivo = {(d["vendedor"], d["sku"]): d["motivo"] for d in descartados}

revisar("SILVIO: espirales, y despues el nucleo",
        skus["SILVIO"], [("SS07010", 150), ("CP10005", 50), ("AL06009", 150),
                         ("GL48001", 70), ("LF02005", 70)])
revisar("GERMAN: espirales y tabletas, y despues el nucleo",
        skus["GERMAN"], [("SS07010", 190), ("SS07027", 90), ("CP10005", 40),
                         ("AL06010", 35), ("CU10003", 30)])
revisar("cinco por vendedor", [len(l) for l in elegidos.values()], [5, 5])
revisar("el repelente de SILVIO vende muy poco",
        motivo[("SILVIO", "SS07022")].startswith("vende muy poco"), True)
revisar("una marca por vendedor en el nucleo",
        motivo[("SILVIO", "AL08004")], "ya tiene un objetivo de ALGABO")
# Lo que paso en 2026-11: el catalogo los guarda como marcas distintas.
foot = cands + [cand("nucleo", "SILVIO", "AL06007", 63, 156, 215_000, "ALGABO FOOT", (211, 122, 63))]
revisar("ALGABO FOOT no es un segundo Algabo",
        {(d["vendedor"], d["sku"]): d["motivo"] for d in p.elegir(foot, ["SILVIO", "GERMAN"], MES, MESES)[1]}
        [("SILVIO", "AL06007")], "ya tiene un objetivo de ALGABO")
revisar("familia: ALGABO FOOT es Algabo", p.familia("ALGABO FOOT"), p.familia("ALGABO-"))
revisar("familia: L EAU VIVE no se confunde con otra", p.familia("L EAU VIVE"), "L")
revisar("Kevin 150 de GERMAN tambien es Kevin",
        motivo[("GERMAN", "CP10004")], "ya tiene un objetivo de KEVIN")
# 6 lavandinas en Digip no alcanzan para un objetivo de 95.
revisar("sin stock no hay objetivo",
        motivo[("GERMAN", "SS07001")].startswith("stock insuficiente"), True)
revisar("Ciel con 23 en stock tampoco",
        motivo[("SILVIO", "CP08029")].startswith("stock insuficiente"), True)

# El stock es UNO para los dos: Kevin 250 tiene 140, se usan 112 (80 %).
# SILVIO pide 50 y GERMAN 40: 90, entra. Con 100 de stock (80 usables) el
# segundo ya no entra entero.
corto = [dict(c, stock=100) if c["sku"] == "CP10005" else c for c in cands]
el2, de2 = p.elegir(corto, ["SILVIO", "GERMAN"], MES, MESES)
revisar("el stock compartido deja afuera al segundo",
        [e["sku"] for e in el2["GERMAN"]].count("CP10005"), 0)
revisar("y dice cuanto quedaba",
        [d["motivo"] for d in de2 if d["vendedor"] == "GERMAN" and d["sku"] == "CP10005"],
        ["stock insuficiente: el objetivo seria 40 y quedan 30 de 100 en Digip"])

# Por turnos: con el stock justo para uno, se lo lleva el de mas puntaje en su
# turno, no el primero de la lista entera.
el3, _ = p.elegir([dict(c, stock=200) if c["sku"] == "SS07010" else c for c in cands],
                  ["SILVIO", "GERMAN"], MES, MESES)
revisar("con 160 espirales usables entra la de SILVIO (150) y no la de GERMAN (190)",
        (["SS07010" in [e["sku"] for e in el3[v]] for v in ("SILVIO", "GERMAN")]),
        [True, False])

# Fuera de temporada, nada de verano.
el4, _ = p.elegir(cands, ["SILVIO", "GERMAN"], "2026-07", ["2026-04", "2026-05", "2026-06"])
revisar("en julio no hay objetivos de temporada",
        [e["tipo"] for l in el4.values() for e in l].count("temporada"), 0)

# --- La explicacion -----------------------------------------------------------

esp = elegidos["GERMAN"][0]
revisar("temporada: dice de donde sale",
        esp["explicacion"].startswith("Temporada. En feb-abr vendio 680 por mes; para este "
                                      "mes se toma el 25% de ese ritmo (170)"), True)
kev = elegidos["SILVIO"][1]
revisar("nucleo: dice los tres meses y el peor",
        kev["explicacion"].startswith("Vendio 49, 42 y 114 en 2026-08, 2026-09, 2026-10. "
                                      "El peor mes (42) + 10% y redondeado: 50."), True)

# --- La planilla que sale pasa la carga ---------------------------------------

carpeta = tempfile.mkdtemp(prefix="propuesta-")
try:
    ruta = os.path.join(carpeta, planilla.nombre_de_archivo(MES))
    planilla.escribir_planilla(ruta, p.filas_producto(MES, elegidos))
    _, grupos, errores = planilla.leer_hoja(
        planilla.nombre_de_archivo(MES),
        load_workbook(ruta, data_only=True)[planilla.HOJA].iter_rows(values_only=True))
    revisar("la propuesta pasa la validacion de la carga", errores, [])
    revisar("un SKU sin descripcion se llama por su codigo", "SS07010" in grupos, True)
finally:
    shutil.rmtree(carpeta)

print()
if FALLOS:
    print(f"{len(FALLOS)} prueba(s) fallaron: {', '.join(FALLOS)}")
    sys.exit(1)
print("Todas las pruebas pasaron.")
