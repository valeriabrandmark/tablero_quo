"""Pruebas del control de costos que saltan, sin base ni Excel.

POR QUE EXISTE. El 07/09/2026 entro un archivo de costos con la coma decimal
borrada en el 63 % de los articulos: 10.979,019272 se cargo como 1.097.901.927.
Nadie lo noto en la carga. Se noto en el tablero, con el margen del dia en
-$ 42.421 millones.

El cargador no puede saber cuanto vale un articulo. Si puede saber que un costo
no se multiplica por cien de un mes al otro.

    python probar_costos_saltos.py
"""

import os

# costos.py arma el engine al importarse. NO se conecta a nada --SQLAlchemy solo
# arma la URL-- pero sin estas variables ni siquiera puede parsearla, y estas
# pruebas no tocan la base. Con valores de mentira alcanza, y asi el workflow de
# pull requests puede correrlas sin ningun secreto.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", "nadie")
os.environ.setdefault("DB_PASS", "nada")
os.environ.setdefault("DB_NAME", "ninguna")

from costos import (  # noqa: E402
    PROPORCION_PARA_ABORTAR,
    SALTO_SOSPECHOSO,
    _sin_separador_decimal,
    revisar_decimales,
    revisar_saltos,
)

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# --- Reconocer la coma borrada -------------------------------------------
#
# Los cuatro casos son reales, del archivo de septiembre de 2026.

revisar("6 decimales", _sin_separador_decimal(10979.019272, 1097901927), True)
revisar("4 decimales", _sin_separador_decimal(3608.4332, 36084332), True)
revisar("3 decimales", _sin_separador_decimal(3677.778, 3677778), True)
revisar("5 decimales", _sin_separador_decimal(9584.06605, 958406605), True)

# Con un aumento chico encima sigue siendo la coma borrada: el mes nuevo casi
# nunca trae el costo identico al anterior.
revisar("con 0,5 % de aumento", _sin_separador_decimal(1000.0, 1005000), True)

# Y lo que NO es la coma borrada.
revisar("aumento normal", _sin_separador_decimal(1000.0, 1300.0), False)
revisar("costo que baja", _sin_separador_decimal(1000.0, 800.0), False)
revisar("el doble", _sin_separador_decimal(1000.0, 2000.0), False)
revisar("cero adelante", _sin_separador_decimal(0, 1000.0), False)
revisar("cero atras", _sin_separador_decimal(1000.0, 0), False)
revisar("None", _sin_separador_decimal(None, 1000.0), False)
# 10x justo: puede ser un error de tipeo real, y el detector dice que si. Es lo
# conservador: frena y que lo mire una persona.
revisar("exactamente 10x", _sin_separador_decimal(1000.0, 10000.0), True)


# --- El informe sobre un mes entero ---------------------------------------

# Un mes sano: todos los costos se mueven poco.
sanos = {f"SKU{i}": 1000 + i for i in range(100)}
nuevos_sanos = {k: v * 1.15 for k, v in sanos.items()}
informe = revisar_saltos(nuevos_sanos, sanos)
revisar("mes sano: no salta ninguno", informe["saltos"], 0)
revisar("mes sano: no aborta", informe["abortar"], False)
revisar("mes sano: los compara a todos", informe["comparables"], 100)

# El mes de septiembre: la mayoria con la coma borrada.
rotos = {k: (v * 10000 if i % 3 else v * 1.1) for i, (k, v) in enumerate(sanos.items())}
informe = revisar_saltos(rotos, sanos)
revisar("mes roto: aborta", informe["abortar"], True)
revisar("mes roto: cuenta los saltos", informe["saltos"] > 60, True)
revisar("mes roto: los reconoce como coma borrada",
        informe["sin_separador"], informe["saltos"])
revisar("mes roto: trae ejemplos", len(informe["ejemplos"]), 5)

# UN SOLO articulo raro no frena el mes: puede ser un dato mal tipeado, y
# abortar la carga entera por uno seria peor que cargarlo.
casi_sano = dict(nuevos_sanos)
casi_sano["SKU7"] = sanos["SKU7"] * 5000
informe = revisar_saltos(casi_sano, sanos)
revisar("un solo raro: avisa", informe["saltos"], 1)
revisar("un solo raro: NO aborta", informe["abortar"], False)

# Justo en el umbral: 20 de 100 aborta, 19 no.
def con_n_saltos(n):
    d = dict(nuevos_sanos)
    for i, k in enumerate(list(sanos)[:n]):
        d[k] = sanos[k] * 1000
    return revisar_saltos(d, sanos)

revisar(f"{int(PROPORCION_PARA_ABORTAR*100)} % aborta", con_n_saltos(20)["abortar"], True)
revisar("uno menos no aborta", con_n_saltos(19)["abortar"], False)

# Sin mes anterior no hay con que comparar, y eso no puede frenar una carga:
# es lo que pasa la primera vez.
informe = revisar_saltos(nuevos_sanos, {})
revisar("sin mes anterior: no aborta", informe["abortar"], False)
revisar("sin mes anterior: no compara nada", informe["comparables"], 0)

# Los articulos sin costo --testers y exhibidores, que van en 0-- no entran en
# la cuenta: dividir por cero no dice nada.
informe = revisar_saltos({"A": 5000.0}, {"A": 0.0})
revisar("costo anterior en cero: se saltea", informe["comparables"], 0)

revisar("el umbral es el que dice la constante", SALTO_SOSPECHOSO, 50)


# --- Los decimales: una pista, no un corte --------------------------------
#
# En julio y agosto el 95 % de los costos tenia decimales. En el archivo roto de
# septiembre, el 33 %: justo los que vinieron como texto con coma, que son los
# unicos que se leyeron bien. Los otros perdieron la coma y quedaron enteros.

con_dec = {f"S{i}": 1000 + i + 0.25 for i in range(100)}
sin_dec = {f"S{i}": float(1000 + i) for i in range(100)}

d = revisar_decimales(sin_dec, con_dec)
revisar("perder los decimales es sospechoso", d["sospechoso"], True)
revisar("y lo dice con los dos numeros", (round(d["antes"], 2), round(d["ahora"], 2)), (1.0, 0.0))

# El caso real: baja de 95 % a 33 %.
mezcla = {k: (v if i % 3 else v + 0.25) for i, (k, v) in enumerate(sin_dec.items())}
revisar("de 95 % a 33 % tambien avisa",
        revisar_decimales(mezcla, con_dec)["sospechoso"], True)

# Un mes igual de decimal que el anterior no dice nada.
revisar("sin cambio, sin aviso", revisar_decimales(con_dec, con_dec)["sospechoso"], False)

# NO ABORTA POR SI SOLO. Un proveedor que redondea sus precios haria caer esta
# proporcion sin que nada este mal; el que corta es el salto de importes.
revisar("la señal de decimales no aborta", "abortar" in d, False)

# Sin nada con que comparar, no opina.
revisar("sin mes anterior", revisar_decimales(sin_dec, {})["sospechoso"], False)
revisar("sin costos nuevos", revisar_decimales({}, con_dec)["sospechoso"], False)


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
