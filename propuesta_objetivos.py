"""La propuesta automatica de objetivos de producto. Pura: ni red ni base.

proponer_objetivos.py trae los candidatos de la base (propuesta_objetivos.sql)
y escribe el Excel; aca se decide cuales van y con que numero, para poder
probarlo sin credenciales (probar_propuesta_objetivos.py).

============================================================================
 QUE PROPONE Y POR QUE
============================================================================

CINCO OBJETIVOS DE PRODUCTO POR VENDEDOR, hasta dos de temporada y el resto
de "nucleo" -- lo que vende todos los meses.

NUCLEO: EL PEOR DE LOS ULTIMOS 3 MESES + 10 %. Se probo contra lo que paso de
verdad, armando septiembre y octubre de 2026 con los tres meses anteriores:

    regla                      se cumplio en 2026-09   en 2026-10
    mediana de 3 meses              24 %                 37 %
    peor mes                        49 %                 53 %
    peor mes + 10 %                 39 %                 49 %

La venta mayorista por SKU va a los saltos -- un cliente compra una caja un
mes y nada el siguiente --, asi que lo "normal" (la mediana) sobreestima lo que
un mes cualquiera trae. Peor mes + 10 % se cumple mas o menos la mitad de las
veces: desafiante, porque no se regala, y alcanzable, porque se puede.

Como el peor mes puede ser un pozo (18, 212, 113 da un objetivo de 20, que
parece un regalo), el puntaje prefiere los articulos que se venden PAREJO:
ahi el objetivo es lo de todos los meses y empuja de verdad.

TEMPORADA: EL RITMO DEL VERANO PASADO, ESCALADO AL MOMENTO DE LA TEMPORADA.
El unico verano medido es feb-abr 2026 (bronze.sigma_ventas; gold.fact_ventas
arranca en mayo), con el pico en marzo. En octubre de 2026 las espirales X5
tuvieron objetivo de 480 con 1.945 en stock y no se vendio NINGUNA: en
mayorista la temporada arranca despues que en el mostrador -- en Mercado Libre,
los repelentes de los primeros 10 dias de noviembre ya igualaban todo
febrero. RITMO_TEMPORADA dice que parte del ritmo de feb-abr se espera cada
mes.

EL STOCK SE REPARTE. Un objetivo sin mercaderia no se puede cumplir, y si dos
vendedores tienen objetivo sobre el mismo articulo, la suma tiene que entrar.
Se usa como maximo STOCK_USABLE de lo disponible en Digip: el resto es para
Mercado Libre, Tienda Nube y los demas vendedores.
"""

import math

DESAFIO = 0.10
MIN_OBJETIVO = 12
STOCK_USABLE = 0.8
CANT_PRODUCTOS = 5
MAX_TEMPORADA = 2

# Que parte del ritmo de feb-abr se espera en cada mes calendario del mes
# comercial. Febrero a abril estan MEDIDOS (son la base). Noviembre sale de
# octubre: con stock de sobra no se vendio ninguna espiral, asi que se
# arranca con un cuarto. Diciembre y enero son SUPUESTOS -- no hay ningun
# diciembre ni enero cargado -- y hay que revisarlos en marzo de 2027 con los
# datos de este verano.
RITMO_TEMPORADA = {
    "11": 0.25,
    "12": 0.50,
    "01": 0.75,
    "02": 1.00,
    "03": 1.00,
    "04": 0.75,
}

# Los articulos de verano, sobre la descripcion de SIGMA. Va a proposito sin
# INSECTICIDA: el X5 MAX de cucarachas se vende todo el año.
PATRON_VERANO = r"REPELEN|ESPIRAL|MOSQUI|ZANCUD|SOLAR|FPS|AFTER SUN|POST SOL"

# El ultimo verano medido, en meses calendario de bronze.sigma_ventas.
VERANO_DESDE, VERANO_HASTA = "2026-02", "2026-04"

# Los codigos de SIGMA, que es como bronze.sigma_ventas guarda al vendedor.
# Copia de modelo.VENDEDORES (al reves): modelo.py abre la base al importarse.
CODIGO_SIGMA = {"SILVIO": "006", "GERMAN": "007", "PABLO": "008", "RICARDO": "014"}

# A quienes se les propone. PABLO y RICARDO quedan afuera: no tienen ventas
# mayoristas y en 2026-08 a 2026-10 sus objetivos quedaron en 0 %
# (decision del 10/11/2026).
VENDEDORES_PROPUESTA = ["SILVIO", "GERMAN"]


def ritmo(mes):
    """Que parte del verano se espera en `mes` ('2026-11'). None fuera de temporada."""
    return RITMO_TEMPORADA.get(mes[5:7])


def meses_anteriores(mes, n=3):
    """'2026-11' -> ['2026-08', '2026-09', '2026-10']."""
    anio, m = int(mes[:4]), int(mes[5:7])
    salida = []
    for _ in range(n):
        m -= 1
        if m == 0:
            anio, m = anio - 1, 12
        salida.append(f"{anio:04d}-{m:02d}")
    return list(reversed(salida))


def redondear(x):
    """Hacia arriba, a 5 debajo de 100 y a 10 desde 100. Un objetivo de 47 se
    lee como un calculo; uno de 50, como una meta."""
    paso = 10 if x >= 100 else 5
    return int(math.ceil(x / paso - 1e-9) * paso)


def nombre_de(candidato):
    """El nombre del objetivo: el del grupo que ya existe para ese SKU, o la
    descripcion de SIGMA. Con otro nombre que el existente, la carga lo
    tomaria como un grupo nuevo con el mismo SKU."""
    if candidato.get("grupo_existente"):
        return candidato["grupo_existente"]
    desc = " ".join(str(candidato.get("descripcion") or candidato["sku"]).split())
    # "SCOTCH-BRITE ESPONJA LIMPIEZA PESADA," quedo asi en 2026-10 por copiar
    # la descripcion con la coma del medio.
    return desc.rstrip(" ,;.-")


def explicar(c, mes, meses, objetivo):
    """Por que ese numero, en una oracion que se pueda leer en la planilla."""
    if c["tipo"] == "temporada":
        r = ritmo(mes) or 0
        partes = [
            f"Temporada. En feb-abr vendio {c['verano_mes']:.0f} por mes; "
            f"para este mes se toma el {r:.0%} de ese ritmo ({c['verano_mes'] * r:.0f})"
        ]
        if c["u3"] > c["verano_mes"] * r:
            partes.append(f", o lo del ultimo mes ({c['u3']:.0f}) que fue mas")
        partes.append(f". + {DESAFIO:.0%} y redondeado: {objetivo}.")
    else:
        partes = [
            f"Vendio {c['u1']:.0f}, {c['u2']:.0f} y {c['u3']:.0f} en "
            f"{', '.join(meses)}. El peor mes ({c['base']:.0f}) + {DESAFIO:.0%} "
            f"y redondeado: {objetivo}."
        ]
    if c.get("sell_in_pct"):
        partes.append(f" Sell in del proveedor: {c['sell_in_pct']:g} %.")
    partes.append(f" Stock en Digip: {c['stock']:.0f}.")
    return "".join(partes)


def elegir(candidatos, vendedores, mes, meses):
    """Los objetivos de producto de cada vendedor.

    Devuelve (elegidos, descartados). Cada elegido es el candidato con
    `nombre`, `objetivo` y `explicacion` agregados; cada descartado, con
    `motivo`. Los descartados van a la hoja "Por que" para que se vea que
    tambien se miraron y por que no entraron.

    SE ELIGE DE A UNO POR VENDEDOR, POR TURNOS, primero la temporada y despues
    el nucleo. Asi, si dos vendedores quieren el mismo articulo y el stock no
    alcanza para los dos, no se lo lleva entero el primero de la lista.
    """
    hay_temporada = ritmo(mes) is not None
    restante = {}
    for c in candidatos:
        restante.setdefault(c["sku"], math.floor(c["stock"] * STOCK_USABLE))

    elegidos = {v: [] for v in vendedores}
    descartados = []

    def cola(tipo, v):
        return sorted((c for c in candidatos if c["tipo"] == tipo and c["vendedor"] == v),
                      key=lambda c: -c["puntaje"])

    for tipo, cupo in (("temporada", MAX_TEMPORADA if hay_temporada else 0),
                       ("nucleo", CANT_PRODUCTOS)):
        colas = {v: cola(tipo, v) for v in vendedores}
        avanzo = True
        while avanzo:
            avanzo = False
            for v in vendedores:
                ya = elegidos[v]
                if len(ya) >= CANT_PRODUCTOS or sum(e["tipo"] == tipo for e in ya) >= cupo:
                    continue
                while colas[v]:
                    c = colas[v].pop(0)
                    motivo = _motivo_para_no(c, ya, restante)
                    if motivo:
                        descartados.append({**c, "motivo": motivo})
                        continue
                    objetivo = redondear(c["base"] * (1 + DESAFIO))
                    restante[c["sku"]] -= objetivo
                    ya.append({**c, "nombre": nombre_de(c), "objetivo": objetivo,
                               "explicacion": explicar(c, mes, meses, objetivo)})
                    avanzo = True
                    break
    return elegidos, descartados


def familia(marca):
    """La marca de verdad, para no repetirla. El catalogo de SIGMA tiene
    'ALGABO-', 'ALGABO MEN' y 'ALGABO FOOT' como marcas distintas: sin esto
    SILVIO quedaba en 2026-11 con dos objetivos de Algabo."""
    palabras = str(marca or "").replace("-", " ").split()
    return palabras[0].upper() if palabras else ""


def _motivo_para_no(c, ya, restante):
    """Por que un candidato no entra, o None si entra."""
    if any(e["sku"] == c["sku"] for e in ya):
        return "ya elegido para este vendedor"
    objetivo = redondear(c["base"] * (1 + DESAFIO))
    if c["base"] * (1 + DESAFIO) < MIN_OBJETIVO:
        return f"vende muy poco: el objetivo seria {c['base'] * (1 + DESAFIO):.0f}"
    if c["tipo"] == "nucleo" and familia(c.get("marca")) and any(
            e["tipo"] == "nucleo" and familia(e.get("marca")) == familia(c.get("marca"))
            for e in ya):
        # Cinco objetivos de la misma marca son uno solo repartido en cinco.
        return f"ya tiene un objetivo de {familia(c['marca'])}"
    if objetivo > restante[c["sku"]]:
        return (f"stock insuficiente: el objetivo seria {objetivo} y quedan "
                f"{max(restante[c['sku']], 0)} de {c['stock']:.0f} en Digip")
    return None


def filas_producto(mes, elegidos):
    """Las filas de la hoja Objetivos, una por vendedor y producto."""
    filas = []
    for v, lista in elegidos.items():
        for e in lista:
            filas.append({
                "MES_COMERCIAL": mes, "NOMBRE": e["nombre"], "TIPO": "SKU",
                "SKU": e["sku"], "MEDIDA": "UNIDADES", "VENDEDORES": v,
                "CANTIDAD": e["objetivo"], "NOTA": e["explicacion"],
            })
    return filas


def nombre_archivo_propuesta(mes):
    """'2026-11 objetivos (propuesta).xlsx'. La carga lo IGNORA a proposito
    (objetivos_planilla.mes_del_archivo no lo reconoce): no entra al tablero
    hasta que alguien lo revise y le saque el '(propuesta)'."""
    return f"{mes} objetivos (propuesta).xlsx"


# --- La hoja "Por que" --------------------------------------------------------

ENCABEZADO_POR_QUE = ["Vendedor", "Objetivo", "Tipo", "Propuesto", "Como se calculo",
                      "Ultimos 3 meses", "Verano (por mes)", "Stock Digip",
                      "Sell in %", "Margen %"]


def filas_por_que(elegidos, descartados, meses):
    filas = []
    for v, lista in elegidos.items():
        for e in lista:
            filas.append(_fila_detalle(v, e, e["objetivo"], e["explicacion"], meses))
    for d in descartados:
        filas.append(_fila_detalle(d["vendedor"], {**d, "nombre": nombre_de(d)},
                                   "no", "Descartado: " + d["motivo"], meses))
    return filas


def _fila_detalle(v, c, propuesto, texto, meses):
    return [
        v, c["nombre"], c["tipo"], propuesto, texto,
        f"{c['u1']:.0f} / {c['u2']:.0f} / {c['u3']:.0f}",
        "" if c.get("verano_mes") is None else round(c["verano_mes"]),
        round(c["stock"]),
        c.get("sell_in_pct") or "",
        "" if c.get("margen_pct") is None else round(c["margen_pct"] * 100),
    ]


def reglas_por_que(mes, meses):
    """El encabezado de la hoja: las reglas con las que se armo, en castellano."""
    r = ritmo(mes)
    return [
        f"Propuesta para {mes}. Revisala, cambia lo que quieras y guardala como "
        f"'{mes} objetivos.xlsx' para que se cargue.",
        f"Producto, nucleo: el peor de {', '.join(meses)} + {DESAFIO:.0%}. Probado con "
        "sep y oct 2026: se cumple 4 o 5 de cada 10. La mediana solo 2 o 3.",
        ("Producto, temporada: lo vendido por mes en feb-abr 2026 x "
         f"{r:.0%} (arranque de temporada), o el ultimo mes si fue mas."
         if r is not None else "Fuera de temporada: no se proponen objetivos de verano."),
        f"Stock: entre todos los vendedores, como maximo el {STOCK_USABLE:.0%} de lo "
        "disponible en Digip.",
        "Empresa (ventas netas y clientes): los mismos del mes anterior, sin cambios.",
    ]
