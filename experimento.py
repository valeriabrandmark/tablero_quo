"""FUERA DE USO desde el 21/08/2026. No lo corre nadie.

El tablero de elasticidad se rehizo y ya no necesita nada de este archivo.

QUE CAMBIO
Este script asignaba a cada articulo una banda de markup por semana (un cuadrado
latino de 3 grupos x 3 semanas) y despues consolidaba los resultados contra esa
asignacion. La idea era controlar el experimento desde aca.

No hacia falta: CADA VENTA YA TRAE SU PRECIO Y SU COSTO, asi que el margen con
el que se vendio se calcula solo, y con el margen se sabe en que banda cayo. El
tablero deduce la banda de la venta y no la busca en ninguna tabla. Quien decide
el precio es el sistema de precios; el tablero solo observa el resultado.

Y ademas la asignacion tenia un problema escondido: si el precio asignado no se
cargaba, o se cargaba tarde, o el repricer lo movia, la tabla decia una cosa y
la realidad otra -- y el tablero le hubiera creido a la tabla.

QUE SIGUE VIVO
`ml_pulso.py`, que es el que guarda los tramos de disponibilidad. De ahi salen
los dias sin stock, que es lo que permite descontar un resultado falso: un
articulo que vendio poco no vendio poco por caro si estuvo cuatro dias quebrado.

QUE QUEDA EN LA BASE
`gold.experimento_markup` tiene 13.080 filas de una corrida de `--asignar` del
21/08. No molestan y no las lee nadie. `gold.fact_experimento` quedo vacia.

Se deja el archivo en vez de borrarlo por si algun dia se quiere volver a un
experimento controlado -- el algebra de intervalos y el cuadrado latino estan
probados. Pero NO esta en el orquestador y correrlo no aporta nada.

---------------------------------------------------------------------------

Medidor de elasticidad de precios en Mercado Libre.

QUE ES EL EXPERIMENTO
Los articulos se reparten en tres grupos. Cada grupo pasa una semana en cada
banda de markup sobre el costo (10-18%, 18-25%, 25-35%) y despues rota, de modo
que en tres semanas los tres grupos pasaron por las tres bandas. Es un cuadrado
latino: sirve para que el efecto "esta semana se vendio mas" -- feriados,
quincena, campanas de ML -- no se confunda con el efecto de la banda, porque
cada semana contiene a las tres bandas al mismo tiempo.

LO QUE ESTE SCRIPT AGREGA, Y ES EL PUNTO ENTERO
Comparar UNIDADES entre semanas no mide elasticidad: mide disponibilidad. Si un
SKU quebro stock el martes de la semana de markup alto, esa semana muestra menos
unidades y la lectura ingenua es "con markup alto vende menos". Con 56% del
catalogo hoy quebrado, ese sesgo es mas grande que el efecto que se busca medir.

Por eso la metrica es una TASA -- unidades por dia realmente a la venta -- y el
denominador sale de los tramos que deja `ml_pulso.py`.

    python experimento.py --asignar --desde 2026-08-25
    python experimento.py --consolidar
"""

import argparse
import datetime

import pandas as pd

from esquema import asegurar_tablas, crear_engine

# Las tres bandas. El limite de una es el piso de la siguiente a proposito: son
# un particion del rango 10-35%, no tres rangos sueltos con huecos en el medio.
BANDAS = [
    ("10-18", 0.10, 0.18),
    ("18-25", 0.18, 0.25),
    ("25-35", 0.25, 0.35),
]

GRUPOS = 3
SEMANAS = 3

# Cuanto puede pasar entre dos pulsos antes de que el hueco cuente como "no
# miramos".
#
# El orquestador corre cada HORA (era cada 2). Dos horas deja pasar una corrida
# que se atraso o que se salteo por presupuesto, sin perdonar una caida de
# verdad. Si se dejara en 3 como antes, se estarian tapando dos pulsos perdidos
# seguidos y esas horas entrarian al denominador como si las hubieramos mirado.
TOLERANCIA_HUECO = datetime.timedelta(hours=2)

# Las primeras horas de cada semana no se miden.
#
# ML tarda en digerir un cambio de precio: la posicion en el listado y el
# rendimiento de la publicidad se reacomodan durante el dia siguiente. Sin este
# descarte, las ventas del lunes -- que todavia responden al precio de la semana
# anterior -- se le atribuyen a la banda nueva, y las tres bandas terminan
# midiendose contaminadas entre si.
LAVADO = datetime.timedelta(hours=24)


# ---------------------------------------------------------------------------
#  Algebra de intervalos
# ---------------------------------------------------------------------------
#
# POR QUE ESTO NO ESTA EN SQL
# Un SKU puede tener varias publicaciones, y "el SKU estuvo a la venta" es la
# UNION de los tramos de todas ellas: si una quebro pero otra tenia stock, el
# articulo se podia comprar. Sumar los tramos contaria dos veces las horas en
# que las dos estaban vendibles, e inventaria disponibilidad que no existio.
#
# Unir intervalos que se solapan, y despues restarle esa union a otra, se puede
# hacer en SQL con funciones de ventana, pero queda un bloque que nadie vuelve a
# leer. Aca son treinta lineas que se pueden seguir con el dedo, y el script
# corre una vez por dia sobre unas decenas de miles de tramos.

def unir(intervalos):
    """Funde los intervalos que se solapan o se tocan. Devuelve una lista
    ordenada y sin solapamientos."""
    ordenados = sorted((i for i in intervalos if i[0] < i[1]))
    fundidos = []
    for ini, fin in ordenados:
        if fundidos and ini <= fundidos[-1][1]:
            fundidos[-1][1] = max(fundidos[-1][1], fin)
        else:
            fundidos.append([ini, fin])
    return [tuple(i) for i in fundidos]


def restar(a, b):
    """Lo que queda de `a` despues de sacarle `b`. Ambos ya unidos."""
    resto = []
    for ini, fin in a:
        actual = ini
        for bini, bfin in b:
            if bfin <= actual or bini >= fin:
                continue
            if bini > actual:
                resto.append((actual, bini))
            actual = max(actual, bfin)
            if actual >= fin:
                break
        if actual < fin:
            resto.append((actual, fin))
    return resto


def intersectar(a, b):
    """Lo que `a` y `b` tienen en comun. Ambos ya unidos."""
    comun = []
    for ini, fin in a:
        for bini, bfin in b:
            lo, hi = max(ini, bini), min(fin, bfin)
            if lo < hi:
                comun.append((lo, hi))
    return unir(comun)


def horas(intervalos):
    return sum((fin - ini).total_seconds() for ini, fin in intervalos) / 3600


# ---------------------------------------------------------------------------
#  Cobertura: que horas miramos de verdad
# ---------------------------------------------------------------------------

def cobertura(engine, desde, hasta):
    """Los tramos en los que el pulso estuvo corriendo.

    Es la pieza que evita el error mas caro de todo esto: si el orquestador
    estuvo caido dos dias, el ultimo tramo abierto sigue diciendo "vendible" y
    sin este recorte esas 48 horas entrarian al denominador como horas a la
    venta. El experimento mediria peor justo las semanas en que fallo el
    pipeline, y nada lo delataria.

    Un hueco entre dos pulsos mas largo que `TOLERANCIA_HUECO` no se cubre: esas
    horas no son ni vendibles ni quebradas, son horas sin dato, y se reportan
    como tales.
    """
    pulsos = pd.read_sql(
        """select momento from bronze.ml_pulso_corrida
            where momento between %(d)s and %(h)s order by momento""",
        engine, params={"d": desde, "h": hasta},
    )["momento"].tolist()

    if not pulsos:
        return []

    tramos = []
    for anterior, siguiente in zip(pulsos, pulsos[1:]):
        if siguiente - anterior <= TOLERANCIA_HUECO:
            tramos.append((anterior, siguiente))
    # El ultimo pulso cubre hacia adelante lo que se le tolera, pero nunca mas
    # alla del fin de la ventana.
    tramos.append((pulsos[-1], min(pulsos[-1] + TOLERANCIA_HUECO, hasta)))
    return unir(tramos)


# ---------------------------------------------------------------------------
#  Asignacion
# ---------------------------------------------------------------------------

def universo(engine):
    """Los SKU que entran al experimento: TODOS los que tienen publicacion en ML.

    ANTES SE PEDIA HABER VENDIDO ALGO EN 60 DIAS, Y ESTABA MAL.
    El filtro dejaba afuera 2.077 de 4.360 articulos -- casi la mitad -- con el
    argumento de que sin ventas recientes no aportan senal. Pero el objetivo del
    experimento es encontrar el markup de CADA articulo, y un articulo que no
    vendio nada en dos meses es justamente uno de los que hay que revisar: puede
    no estar vendiendo porque esta caro. Excluirlo daba por sentada la respuesta
    que el experimento tiene que dar.

    Lo que sigue siendo cierto es que un articulo sin ventas no va a producir una
    conclusion propia en tres semanas. Pero eso se resuelve marcando la fila como
    no legible en el tablero, no sacandolo de la medicion: adentro suma al
    agregado de su banda, que es donde de verdad hay senal.

    `uds60` se sigue trayendo, pero ahora solo para repartir los grupos parejos.
    """
    return pd.read_sql("""
        with pub as (
          select (select a->>'value_name'
                    from jsonb_array_elements(p.attributes::jsonb) a
                   where a->>'id' = 'SELLER_SKU' limit 1) as sku,
                 bool_or(p.status = 'active')       as alguna_activa,
                 bool_or(p.catalog_listing is true) as en_catalogo
            from bronze.ml_publicaciones p
           group by 1
        ),
        ventas as (
          select sku,
                 sum(cantidad)             as uds60,
                 count(distinct nro_orden) as ordenes60
            from gold.fact_ventas
           where canal = 'Mercado Libre'
             and fecha >= current_date - 60
           group by sku
        )
        select p.sku, p.alguna_activa, p.en_catalogo,
               coalesce(v.uds60, 0)    as uds60,
               coalesce(v.ordenes60, 0) as ordenes60
          from pub p
          left join ventas v on v.sku = p.sku
         where p.sku is not null
         order by coalesce(v.uds60, 0) desc, p.sku
    """, engine)


def repartir(skus):
    """Reparte los SKU en tres grupos parejos, sin azar.

    POR QUE NO ES `random.shuffle`
    Con 2.000 SKU al azar los grupos quedarian parecidos en promedio, pero el
    experimento no se juega en el promedio: se juega en los pocos articulos que
    concentran la mayor parte de las unidades. Un solo SKU de alta rotacion que
    caiga en el grupo 1 mueve su total mas que cien SKU de cola, y ahi la
    diferencia entre grupos ya no se puede atribuir a la banda de markup.

    Se ordena por unidades y se reparte en serpentina (1-2-3, 3-2-1, 1-2-3...),
    que deja los tres grupos con volumen casi identico y ademas es
    REPRODUCIBLE: cualquiera puede volver a correr esto y obtener la misma
    asignacion, que es lo que permite auditar un resultado meses despues.

    La segunda clave de orden es la disponibilidad de hoy: asi los articulos que
    ya arrancan quebrados quedan repartidos entre los tres grupos y no
    amontonados en uno.
    """
    orden = skus.sort_values(
        ["alguna_activa", "uds60", "sku"], ascending=[False, False, True]
    ).reset_index(drop=True)

    grupo = []
    for i in range(len(orden)):
        vuelta, posicion = divmod(i, GRUPOS)
        grupo.append(posicion + 1 if vuelta % 2 == 0 else GRUPOS - posicion)
    orden["grupo"] = grupo
    return orden


def asignar(engine, nombre, desde):
    """Escribe el cuadrado latino: que banda le toca a cada grupo cada semana.

    El grupo G en la semana S recibe la banda (G + S) mod 3. Asi cada grupo pasa
    por las tres bandas y cada semana contiene a las tres, que es la unica forma
    de que el efecto de la semana y el de la banda no queden pegados.
    """
    skus = repartir(universo(engine))
    print(f"  {len(skus)} SKU en el experimento")
    for g in range(1, GRUPOS + 1):
        gr = skus[skus["grupo"] == g]
        print(f"    grupo {g}: {len(gr)} SKU · {int(gr['uds60'].sum())} uds en 60 dias")

    filas = []
    for _, fila in skus.iterrows():
        for semana in range(1, SEMANAS + 1):
            banda, mn, mx = BANDAS[(fila["grupo"] - 1 + semana - 1) % GRUPOS]
            ini = desde + datetime.timedelta(weeks=semana - 1)
            filas.append({
                "experimento": nombre,
                "sku": fila["sku"],
                "grupo": int(fila["grupo"]),
                "semana": semana,
                "banda": banda,
                "markup_min": mn,
                "markup_max": mx,
                "desde": ini,
                "hasta": ini + datetime.timedelta(weeks=1),
            })

    df = pd.DataFrame(filas)
    with engine.begin() as con:
        # Se borra y se reescribe SOLO este experimento. Reescribir uno que ya
        # empezo cambiaria la banda de semanas ya medidas, asi que el nombre del
        # experimento es la proteccion: para rehacer la asignacion hay que
        # decidir borrarlo por nombre, a mano.
        borradas = con.exec_driver_sql(
            "delete from gold.experimento_markup where experimento = %(n)s",
            {"n": nombre},
        ).rowcount
        if borradas:
            print(f"  (se reemplaza la asignacion anterior de '{nombre}': {borradas} filas)")
        df.to_sql("experimento_markup", con, schema="gold",
                  if_exists="append", index=False)

    print(f"  gold.experimento_markup: {len(df)} filas "
          f"({desde.date()} a {(desde + datetime.timedelta(weeks=SEMANAS)).date()})")


# ---------------------------------------------------------------------------
#  Consolidacion
# ---------------------------------------------------------------------------

def _tramos_por_sku(engine, desde, hasta):
    """Tramos de estado de todas las publicaciones, ya atados a su SKU."""
    return pd.read_sql("""
        select e.sku, e.item_id, e.desde, 
               least(coalesce(e.hasta, e.visto_hasta), %(h)s) as fin,
               e.vendible, e.motivo
          from bronze.ml_estado_item e
         where e.sku is not null
           and e.desde < %(h)s
           and coalesce(e.hasta, e.visto_hasta) > %(d)s
    """, engine, params={"d": desde, "h": hasta})


def _precios_por_sku(engine, desde, hasta):
    return pd.read_sql("""
        select p.sku, p.item_id, p.desde,
               least(coalesce(p.hasta, p.visto_hasta), %(h)s) as fin,
               p.precio
          from bronze.ml_precio_item p
         where p.sku is not null and p.precio is not null
           and p.desde < %(h)s
           and coalesce(p.hasta, p.visto_hasta) > %(d)s
    """, engine, params={"d": desde, "h": hasta})


def _buybox_por_sku(engine, desde, hasta):
    """Tramos de caja de compra, atados al SKU via `bronze.ml_publicaciones`.

    La tabla guarda `item_id` porque la caja se gana por publicacion, no por
    articulo. Para el experimento la pregunta es del SKU, asi que aca se sube de
    nivel: el SKU "estuvo ganando" si alguna de sus publicaciones lo estaba.
    """
    return pd.read_sql("""
        select (select a->>'value_name'
                  from jsonb_array_elements(p.attributes::jsonb) a
                 where a->>'id' = 'SELLER_SKU' limit 1) as sku,
               b.desde,
               least(coalesce(b.hasta, b.visto_hasta), %(h)s) as fin
          from bronze.ml_buybox_item b
          join bronze.ml_publicaciones p on p.id = b.item_id
         where b.ganando is true
           and b.desde < %(h)s
           and coalesce(b.hasta, b.visto_hasta) > %(d)s
    """, engine, params={"d": desde, "h": hasta})


def _costos(engine):
    """Ultimo costo e IVA conocidos de cada SKU, de `gold.fact_ventas`.

    Se toma de ahi y no de `bronze.costos_historicos` porque `fact_ventas` ya
    dejo el costo con el descuento del proveedor aplicado -- es el numero contra
    el que el tablero calcula los margenes. Usar otro haria que el markup de
    esta tabla y el margen del tablero hablen de costos distintos.
    """
    return pd.read_sql("""
        select distinct on (sku) sku, costo_unitario, iva_pct
          from gold.fact_ventas
         where canal = 'Mercado Libre' and costo_unitario > 0
         order by sku, fecha desc
    """, engine).set_index("sku")


def _ventas(engine, desde, hasta):
    """Ventas por SKU y dia. El grano diario alcanza: las semanas del
    experimento arrancan a las 00:00, asi que ningun dia cae partido entre dos.

    OJO CON LAS COLUMNAS: en `gold.fact_ventas`, `comision` es POR UNIDAD y
    `envio` es POR LINEA (verificado con datos, ver lib/meli.ts del tablero).
    Multiplicar el envio por la cantidad lo contaria de mas.
    """
    return pd.read_sql("""
        select sku, fecha,
               sum(cantidad)                        as unidades,
               count(distinct nro_orden)            as ordenes,
               sum(total_linea)                     as facturacion,
               sum(costo_unitario * cantidad)       as costo,
               sum(comision * cantidad)             as comision,
               sum(envio)                           as envio,
               sum(precio_neto * cantidad)          as neto
          from gold.fact_ventas
         where canal = 'Mercado Libre'
           and fecha >= %(d)s and fecha < %(h)s
         group by sku, fecha
    """, engine, params={"d": desde.date(), "h": hasta.date()})


def consolidar(engine, nombre):
    asignacion = pd.read_sql(
        "select * from gold.experimento_markup where experimento = %(n)s",
        engine, params={"n": nombre},
    )
    if asignacion.empty:
        print(f"  No hay asignacion para '{nombre}'. Corre primero --asignar.")
        return

    inicio = asignacion["desde"].min()
    fin = min(asignacion["hasta"].max(), pd.Timestamp.now(tz=inicio.tz))
    print(f"  Ventana: {inicio} -> {fin}")

    cob = cobertura(engine, inicio, fin)
    print(f"  Cobertura del pulso: {horas(cob):.0f}h de "
          f"{(fin - inicio).total_seconds() / 3600:.0f}h de calendario")
    if not cob:
        print("  ATENCION: no hay ningun pulso registrado en la ventana. "
              "Sin cobertura no se puede separar 'no estaba a la venta' de "
              "'no lo miramos', asi que no se consolida nada.")
        return

    tramos = _tramos_por_sku(engine, inicio, fin)
    precios = _precios_por_sku(engine, inicio, fin)
    ventas = _ventas(engine, inicio, fin)
    buybox = _buybox_por_sku(engine, inicio, fin)
    costos = _costos(engine)

    por_sku_estado = {s: g for s, g in tramos.groupby("sku")}
    por_sku_precio = {s: g for s, g in precios.groupby("sku")}
    por_sku_venta = {s: g for s, g in ventas.groupby("sku")}
    por_sku_bb = {s: g for s, g in buybox.groupby("sku")} if not buybox.empty else {}

    filas = []
    for _, a in asignacion.iterrows():
        # El lavado se saca del ARRANQUE de la ventana, no del final: lo que se
        # descarta son las horas en que todavia arrastra el precio anterior.
        v_ini, v_fin = a["desde"] + LAVADO, min(a["hasta"], fin)
        if v_fin <= v_ini:
            continue                      # semana que todavia no empezo
        ventana = [(v_ini, v_fin)]
        observado = intersectar(ventana, cob)

        te = por_sku_estado.get(a["sku"])
        if te is None:
            vendible = quebrado = otros = []
        else:
            def tr(sel):
                return unir([(r.desde, r.fin) for r in sel.itertuples()])
            vendible = intersectar(tr(te[te["vendible"]]), observado)
            # Lo quebrado se le RESTA lo vendible: si el SKU tenia dos
            # publicaciones y una quebro mientras la otra vendia, el articulo se
            # podia comprar. Sin esta resta, el mismo rato contaria como
            # disponible y como quiebre a la vez.
            quebrado = restar(
                intersectar(tr(te[te["motivo"] == "sin_stock"]), observado), vendible
            )
            otros = restar(observado, unir(vendible + quebrado))

        h_obs = horas(observado)
        h_vend = horas(vendible)

        # Precio publicado y markup, ponderados por las horas VENDIBLES.
        #
        # Por horas vendibles y no por horas de calendario: el precio que
        # estuvo puesto mientras la publicacion estaba pausada no lo vio nadie,
        # y meterlo en el promedio corre el markup hacia un numero que ningun
        # comprador enfrento.
        precio_pub = markup = None
        tp = por_sku_precio.get(a["sku"])
        if tp is not None and h_vend > 0:
            peso = suma = 0.0
            for r in tp.itertuples():
                h = horas(intersectar([(r.desde, r.fin)], vendible))
                if h > 0:
                    peso += h
                    suma += r.precio * h
            if peso > 0:
                precio_pub = suma / peso
                c = costos.loc[a["sku"]] if a["sku"] in costos.index else None
                if c is not None and c["costo_unitario"] > 0:
                    # El precio de ML lleva IVA adentro y el costo no: sin
                    # sacarselo, el markup saldria 21 puntos mas alto y las tres
                    # bandas caerian todas arriba de su techo.
                    sin_iva = precio_pub / (1 + (c["iva_pct"] or 0))
                    markup = sin_iva / c["costo_unitario"] - 1

        # Horas ganando la caja, y solo dentro de las horas VENDIBLES: ganar la
        # caja con la publicacion pausada no vende nada, asi que contarlo
        # inflaria la unica covariable que sirve para explicar por que un
        # articulo disponible igual no vendio.
        #
        # `None` y no 0 cuando el SKU no tiene tramos de caja: puede ser que no
        # este en catalogo (no aplica) o que la consulta a ML fallara (no se
        # sabe). Un cero diria "estuvo perdiendo", que es una tercera cosa.
        tb = por_sku_bb.get(a["sku"])
        h_bb = None
        if tb is not None:
            h_bb = horas(intersectar(
                unir([(r.desde, r.fin) for r in tb.itertuples()]), vendible
            ))

        tv = por_sku_venta.get(a["sku"])
        if tv is not None:
            tv = tv[(tv["fecha"] >= v_ini.date()) & (tv["fecha"] < v_fin.date())]
        vacio = tv is None or tv.empty
        uds = 0.0 if vacio else float(tv["unidades"].sum())
        fact = 0.0 if vacio else float(tv["facturacion"].sum())
        cos = 0.0 if vacio else float(tv["costo"].sum())
        com = 0.0 if vacio else float(tv["comision"].sum())
        env = 0.0 if vacio else float(tv["envio"].sum())

        filas.append({
            "experimento": nombre,
            "sku": a["sku"],
            "semana": int(a["semana"]),
            "grupo": int(a["grupo"]),
            "banda": a["banda"],
            "desde": v_ini,
            "hasta": v_fin,
            "horas_ventana": (v_fin - v_ini).total_seconds() / 3600,
            "horas_vendible": h_vend,
            "horas_sin_stock": horas(quebrado),
            "horas_pausada": horas(otros),
            "horas_sin_dato": (v_fin - v_ini).total_seconds() / 3600 - h_obs,
            "horas_ganando_bb": h_bb,
            "unidades": uds,
            "ordenes": 0 if vacio else int(tv["ordenes"].sum()),
            "facturacion": fact,
            "costo": cos,
            "comision": com,
            "envio": env,
            "margen": fact - cos - com - env,
            # El criterio de DESEMPATE. Entre dos bandas que dejan lo mismo por
            # dia conviene la de markup mas alto, porque vende menos unidades
            # para ganar la misma plata: el stock dura mas y cada unidad movida
            # cuesta trabajo que no depende de a cuanto se vendio. Vender 50
            # marcando 10 y vender 20 marcando 30 no son equivalentes aunque den
            # el mismo total. Quien aplica la regla es el tablero; aca solo se
            # guarda el numero.
            "margen_por_unidad": None if uds <= 0 else (fact - cos - com - env) / uds,
            "precio_prom": None if vacio or uds == 0 else fact / uds,
            "precio_publicado": precio_pub,
            "markup_realizado": markup,
            # LA metrica. En null y no en cero cuando no hubo horas a la venta:
            # cero ventas sin exposicion no es un fracaso comercial, es un dato
            # que no existe, y un cero lo arrastraria a la baja el promedio de
            # su banda.
            "uds_por_dia_vendible": None if h_vend <= 0 else uds / (h_vend / 24),
        })

    df = pd.DataFrame(filas)
    with engine.begin() as con:
        con.exec_driver_sql(
            "delete from gold.fact_experimento where experimento = %(n)s",
            {"n": nombre},
        )
        df.to_sql("fact_experimento", con, schema="gold",
                  if_exists="append", index=False)

    medidas = df[df["horas_vendible"] > 0]
    print(f"  gold.fact_experimento: {len(df)} filas "
          f"({len(medidas)} con horas a la venta, {len(df) - len(medidas)} sin exposicion)")
    if not medidas.empty:
        # Se imprimen las tres cifras SIN elegir ganador. La regla de decision
        # -- maximizar el margen por dia y desempatar hacia el markup mas alto
        # -- vive en el tablero (lib/elasticidad.ts), y escribirla tambien aca
        # garantizaria que algun dia las dos digan cosas distintas.
        print("\n  Por banda (sin elegir ganador, eso lo hace el tablero):")
        print(f"    {'banda':>6}  {'margen/dia':>12} {'margen/unidad':>14} "
              f"{'uds/dia':>9} {'markup':>8}  SKU-semana")
        for banda, g in medidas.groupby("banda"):
            dias = g["horas_vendible"].sum() / 24
            m_dia = g["margen"].sum() / dias if dias else float("nan")
            u = g["unidades"].sum()
            m_uni = g["margen"].sum() / u if u else float("nan")
            print(f"    {banda:>6}  {m_dia:>12,.0f} {m_uni:>14,.0f} "
                  f"{u / dias if dias else 0:>9.2f} "
                  f"{g['markup_realizado'].mean():>7.1%}  {len(g)}")


def main():
    parser = argparse.ArgumentParser(description="Experimento de elasticidad de precios en ML.")
    parser.add_argument("--asignar", action="store_true", help="Arma la asignacion de bandas")
    parser.add_argument("--consolidar", action="store_true", help="Calcula gold.fact_experimento")
    parser.add_argument("--nombre", default="elasticidad-2026-08",
                        help="Nombre del experimento (permite tener mas de uno)")
    parser.add_argument("--desde", help="Arranque de la semana 1 (YYYY-MM-DD), para --asignar")
    args = parser.parse_args()

    engine = asegurar_tablas(crear_engine())

    if args.asignar:
        if not args.desde:
            parser.error("--asignar necesita --desde YYYY-MM-DD")
        arranque = datetime.datetime.fromisoformat(args.desde).replace(
            tzinfo=datetime.timezone(datetime.timedelta(hours=-3))
        )
        print(f"\n=== ASIGNACION '{args.nombre}' ===")
        asignar(engine, args.nombre, arranque)

    if args.consolidar:
        print(f"\n=== CONSOLIDADO '{args.nombre}' ===")
        consolidar(engine, args.nombre)

    if not (args.asignar or args.consolidar):
        parser.error("elegi --asignar o --consolidar")


if __name__ == "__main__":
    main()
