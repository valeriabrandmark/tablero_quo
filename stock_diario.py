"""Cuantos dias TUVO STOCK cada articulo, en cada deposito.

============================================================================
 PARA QUE SIRVE
============================================================================

Para que el ritmo de venta del panel de Compras sea "lo vendido dividido los
dias que hubo para vender", y no "lo vendido dividido la ventana entera".

El caso que lo pidio (compras, 10/11/2026):

    Se compran 30 unidades. Llegan el 1 de enero y se venden 10 el 1, 10 el
    2 y 10 el 3. A fin de mes el sistema divide 30 unidades por 30 dias y
    dice "se vende 1 por dia". Pero se venden 10 por dia: los otros 27 dias
    no se vendio porque NO HABIA.

Con la ventana entera, un articulo que se agota rapido aparece con un ritmo
bajo, el sugerido pide lo mismo que la ultima vez (o menos), y el articulo
que mas se vende es justo el que nunca se compra en cantidad.

============================================================================
 POR DEPOSITO, PORQUE SON DOS STOCKS DISTINTOS
============================================================================

Tucuman (Digip) y Full (Mercado Libre) se agotan por separado. Que haya 40 en
Tucuman no hace que se pueda vender por Full, y al reves. Por eso se cuentan
los dias de cada uno y las ventas de cada uno:

    Full      ventas de Mercado Libre con logistica `fulfillment`.
              HASTA SEPTIEMBRE DE 2026 la venta de Meli no trae logistica
              (columna en null), y desde octubre el 99 % es fulfillment: un
              null se toma como Full. La misma regla vive en el tablero, en
              lib/queries-compras.ts (VENTA_DESDE_FULL): si se cambia aca, se
              cambia alla.
    Tucuman   todo el resto: mayorista, Tienda Nube y Meli no-Full.

============================================================================
 DE DONDE SALE EL STOCK DE CADA DIA
============================================================================

`bronze.digip_stock` y `bronze.ml_stock_full` se PISAN en cada corrida: dicen
cuanto hay hoy y nada de ayer. Hay dos fuentes de historia, y se usan las dos:

  1. FOTOS. Full tiene la suya (`bronze.ml_stock_full_historico`, desde el
     21/08/2026). Tucuman no tenia ninguna: este paso empieza a guardarla en
     `bronze.digip_stock_historico`. Es una serie que NO SE PUEDE
     RECONSTRUIR HACIA ATRAS con exactitud, por eso conviene que exista cuanto
     antes aunque hoy todavia no haga falta.

  2. RECONSTRUCCION, para los dias sin foto. Desde la ultima foto conocida se
     camina para atras deshaciendo los movimientos:

         apertura(d) = apertura(d+1) - entradas(d) + salidas(d)

         Tucuman  entradas = compras ingresadas (sigma_compras.fechaIngreso)
                  salidas  = ventas de Tucuman + envios a Full (digip)
         Full     entradas = envios a Full
                  salidas  = ventas de Full

     Si da negativo se corta en 0: falta un movimiento de entrada que no
     tenemos (una compra anterior a enero, un ajuste de inventario), y lo
     unico que se sabe seguro es que no habia MENOS de cero.

     Una foto real siempre le gana a la reconstruccion y la "re-ancla": el
     error de un dia no se arrastra mas alla de la foto anterior.

Un dia CUENTA COMO CON STOCK si abrio con stock, si entro mercaderia o si se
vendio algo (una venta prueba que habia, aunque la cuenta diga otra cosa).

============================================================================
 QUE SE ESCRIBE
============================================================================

`gold.dias_con_stock`: una fila por (sku, deposito, ventana) con los dias con
stock dentro de esa ventana y cuantos de ellos salen de una foto real. Las
ventanas son las mismas del selector del tablero (30/60/90/120). Se reemplaza
entera en cada corrida: son ~70 mil filas.
"""

import datetime
from collections import defaultdict

import pandas as pd

from conexion import crear_engine

# Las mismas que VENTANAS_RITMO en lib/stock.ts del tablero.
VENTANAS = (30, 60, 90, 120)
DIAS_HISTORIA = max(VENTANAS)

TABLA_FOTO = "digip_stock_historico"
TABLA_DIAS = "dias_con_stock"

# Ver "POR DEPOSITO" arriba. Logisticas de Meli que NO salen de Full.
LOGISTICAS_NO_FULL = ("cross_docking", "self_service", "xd_drop_off",
                      "drop_off", "custom", "not_specified")

DDL = f"""
create table if not exists bronze.{TABLA_FOTO} (
    fecha       date   not null,
    sku         text   not null,
    disponible  double precision not null,
    primary key (fecha, sku)
);
create schema if not exists gold;
create table if not exists gold.{TABLA_DIAS} (
    sku             text    not null,
    deposito        text    not null,
    ventana         integer not null,
    dias_con_stock  integer not null,
    dias_con_foto   integer not null,
    actualizado     timestamptz not null default now(),
    primary key (sku, deposito, ventana)
);
"""


def hoy_argentina():
    """La fecha en hora ARGENTINA: corriendo despues de las 21, un
    `date.today()` en UTC ya seria manana y la foto quedaria mal fechada."""
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=-3))
    ).date()


# ============================================================
#  LA CUENTA (sin base, para poder probarla)
# ============================================================

def dias_con_stock(hoy, dias, fotos, entradas, salidas, ventas,
                   primera_evidencia=None):
    """Para cada dia de (hoy - dias, hoy], si tuvo stock y si salio de foto.

    fotos     {fecha: unidades al abrir ese dia}. La de `hoy` es el ancla; si
              no esta, se arranca de 0 (no hay nada hoy).
    entradas  {fecha: unidades que entraron ese dia}
    salidas   {fecha: unidades que salieron ese dia} (ventas + traspasos)
    ventas    {fecha: unidades vendidas ese dia}. Va aparte de `salidas`
              porque una venta prueba que habia stock; un traspaso, tambien,
              pero se cuenta igual por las salidas.

    primera_evidencia
              el primer dia en que HAY PRUEBA de que el articulo estuvo en el
              deposito (ver `primeras_evidencias`). Antes de esa fecha un dia
              reconstruido no cuenta como con stock, por mas que la cuenta diga
              que si. `None` = sin tope.

    Devuelve una lista de (fecha, con_stock: bool, de_foto: bool), de la mas
    nueva a la mas vieja.

    POR QUE HACE FALTA EL TOPE. La reconstruccion es un TECHO: deshace las
    entradas que conocemos, y si falta una el stock "se arrastra" hacia atras
    hasta antes de que la mercaderia llegara. Y faltan casi todas: el
    10/11/2026, 583 de las 692 facturas de compra de los ultimos 120 dias
    llegaron de Sigma sin el detalle de articulos. El caso que lo mostro:
    AL01081 (panales Algabo XG), primera venta el 22/10, figuraba con 101 dias
    con stock en Tucuman -- desde julio -- y el ritmo daba MENOS que antes.
    """
    out = []
    apertura = float(fotos.get(hoy, 0) or 0)
    de_foto = hoy in fotos
    d = hoy
    for _ in range(dias):
        if d != hoy:
            if d in fotos:
                apertura, de_foto = float(fotos[d] or 0), True
            else:
                # Deshacer el dia d: lo que habia al abrir d+1 (`apertura`
                # todavia vale eso) menos lo que entro en d mas lo que salio.
                apertura = max(0.0, apertura
                               - float(entradas.get(d, 0) or 0)
                               + float(salidas.get(d, 0) or 0))
                de_foto = False
        con = (apertura > 0
               or float(entradas.get(d, 0) or 0) > 0
               or float(ventas.get(d, 0) or 0) > 0
               or float(salidas.get(d, 0) or 0) > 0)
        # Una FOTO siempre vale; una reconstruccion anterior a la primera
        # prueba de que el articulo existia en el deposito, no.
        if con and not de_foto and primera_evidencia is not None and d < primera_evidencia:
            con = False
        out.append((d, con, de_foto))
        d -= datetime.timedelta(days=1)
    return out


def resumir(dias, hoy):
    """De la lista de `dias_con_stock` a {ventana: (con_stock, con_foto)}."""
    res = {}
    for v in VENTANAS:
        desde = hoy - datetime.timedelta(days=v)
        sel = [x for x in dias if x[0] > desde]
        res[v] = (sum(1 for x in sel if x[1]), sum(1 for x in sel if x[2]))
    return res


def calcular(hoy, foto_tuc, foto_full, mov, primeras=None):
    """Arma las filas de gold.dias_con_stock.

    foto_tuc / foto_full  {sku: {fecha: unidades}}
    mov                   {sku: {"compras", "envios", "ventas_tuc",
                                 "ventas_full": {fecha: unidades}}}
    primeras              {sku: {"tuc": fecha, "full": fecha}}: la primera
                          prueba, en TODA la historia, de que el articulo
                          estuvo en cada deposito. Ver `dias_con_stock`.
    """
    # Sin `primeras` (pruebas, o una llamada que no las tiene) no hay tope.
    # Con `primeras`, un articulo sin ninguna prueba toma hoy como tope.
    sin_tope = primeras is None
    primeras = primeras or {}
    skus = set(foto_tuc) | set(foto_full) | set(mov)
    filas = []
    vacio = {}
    for sku in sorted(skus):
        m = mov.get(sku, {})
        compras = m.get("compras", vacio)
        envios = m.get("envios", vacio)
        v_tuc = m.get("ventas_tuc", vacio)
        v_full = m.get("ventas_full", vacio)

        sal_tuc = defaultdict(float)
        for src in (v_tuc, envios):
            for f, u in src.items():
                sal_tuc[f] += float(u or 0)

        p = primeras.get(sku, {})
        tope_tuc = None if sin_tope else p.get("tuc", hoy)
        tope_full = None if sin_tope else p.get("full", hoy)
        tuc = dias_con_stock(hoy, DIAS_HISTORIA, foto_tuc.get(sku, vacio),
                             compras, sal_tuc, v_tuc, tope_tuc)
        full = dias_con_stock(hoy, DIAS_HISTORIA, foto_full.get(sku, vacio),
                              envios, v_full, v_full, tope_full)

        for deposito, dias in (("tuc", tuc), ("full", full)):
            for v, (con, foto) in resumir(dias, hoy).items():
                filas.append({"sku": sku, "deposito": deposito, "ventana": v,
                              "dias_con_stock": con, "dias_con_foto": foto})
    return pd.DataFrame(filas, columns=["sku", "deposito", "ventana",
                                        "dias_con_stock", "dias_con_foto"])


# ============================================================
#  LECTURA Y ESCRITURA
# ============================================================

def _serie(con, sql, params):
    """{sku: {fecha: unidades}} a partir de un SELECT sku, fecha, unidades."""
    out = defaultdict(dict)
    for sku, fecha, u in con.exec_driver_sql(sql, params).fetchall():
        if not sku or fecha is None:
            continue
        sku = str(sku).strip()
        out[sku][fecha] = out[sku].get(fecha, 0.0) + float(u or 0)
    return out


def guardar_foto_digip(con, hoy):
    """La foto de HOY de Tucuman. Idempotente: pisa la del mismo dia.

    Si `digip_stock` vino vacia (Digip caido) NO se guarda nada: una foto en
    cero diria que se agoto todo, justo el falso quiebre que esto evita.
    """
    n = con.exec_driver_sql("select count(*) from bronze.digip_stock").scalar()
    if not n:
        print("  digip_stock vacia: no se guarda foto de Tucuman hoy")
        return
    con.exec_driver_sql(
        f"delete from bronze.{TABLA_FOTO} where fecha = %(h)s", {"h": hoy})
    con.exec_driver_sql(f"""
        insert into bronze.{TABLA_FOTO} (fecha, sku, disponible)
        select %(h)s, trim(codigo), sum(coalesce("stock.disponible", 0))
          from bronze.digip_stock
         where nullif(trim(codigo), '') is not null
         group by 2""", {"h": hoy})
    print(f"  Foto de Tucuman del {hoy}: {n} renglones de digip_stock")


def leer(con, hoy):
    desde = hoy - datetime.timedelta(days=DIAS_HISTORIA + 1)
    p = {"desde": desde, "hoy": hoy, "no_full": list(LOGISTICAS_NO_FULL)}

    foto_tuc = _serie(con, f"""
        select sku, fecha, disponible from bronze.{TABLA_FOTO}
         where fecha >= %(desde)s""", p)
    # Si hoy todavia no hay foto propia (primera corrida), el ancla es el
    # stock de ahora.
    for sku, u in con.exec_driver_sql("""
            select trim(codigo), sum(coalesce("stock.disponible", 0))
              from bronze.digip_stock group by 1""").fetchall():
        if sku:
            foto_tuc[str(sku).strip()].setdefault(hoy, float(u or 0))

    # Full: apto para la venta, por SKU (el mismo mapa inventario -> SKU que
    # usa el tablero en FULL_POR_SKU).
    foto_full = _serie(con, """
        select m.sku, h.fecha, sum(coalesce(h.available_quantity, 0))
          from bronze.ml_stock_full_historico h
          join bronze.ml_inventario_sku m on m.inventory_id = h.inventory_id
         where h.fecha >= %(desde)s and m.sku is not null
         group by 1, 2""", p)
    # Mismo criterio que Tucuman: si la foto de hoy todavia no se saco, el
    # ancla es lo que hay ahora en bronze.ml_stock_full.
    for sku, u in con.exec_driver_sql("""
            select m.sku, sum(coalesce(f.available_quantity, 0))
              from bronze.ml_stock_full f
              join bronze.ml_inventario_sku m on m.inventory_id = f.inventory_id
             where m.sku is not null
             group by 1""").fetchall():
        if sku:
            foto_full[str(sku).strip()].setdefault(hoy, float(u or 0))

    mov = defaultdict(dict)
    for clave, sql in (
        ("compras", """
            select i->>'articuloId',
                   coalesce(nullif(c."fechaIngreso", ''), c."fechaFactura")::date,
                   (i->>'cantidad')::numeric
              from bronze.sigma_compras c,
                   jsonb_array_elements(c.items::jsonb) i
             where coalesce(nullif(c."fechaIngreso", ''), c."fechaFactura")::date
                   >= %(desde)s"""),
        ("envios", """
            select sku, left(fecha_estado, 10)::date, sum(unidades_satisfecha)
              from bronze.digip_envios_full
             where sku is not null and fecha_estado is not null
               and left(fecha_estado, 10)::date >= %(desde)s
             group by 1, 2"""),
        ("ventas_full", """
            select sku, fecha, sum(cantidad) from gold.fact_ventas
             where fecha >= %(desde)s and canal = 'Mercado Libre'
               and coalesce(logistica, 'fulfillment') <> all(%(no_full)s)
             group by 1, 2"""),
        ("ventas_tuc", """
            select sku, fecha, sum(cantidad) from gold.fact_ventas
             where fecha >= %(desde)s
               and not (canal = 'Mercado Libre'
                        and coalesce(logistica, 'fulfillment') <> all(%(no_full)s))
             group by 1, 2"""),
    ):
        for sku, serie in _serie(con, sql, p).items():
            mov[sku][clave] = serie
    return foto_tuc, foto_full, mov, leer_primeras(con, p)


def leer_primeras(con, p):
    """La primera prueba de que cada articulo estuvo en cada deposito.

    En TODA la historia y no en la ventana: un articulo que se vende desde
    mayo no puede quedar recortado a la ventana por no haber vendido en ella.

        Tucuman  primera venta de Tucuman, primera compra con detalle,
                 primer envio a Full (salio de Tucuman), primera foto > 0.
        Full     primera venta de Full, primer envio a Full, primera foto > 0.

    Un articulo sin ninguna prueba no figura: `calcular` toma hoy como tope,
    o sea que solo cuenta la foto de hoy.
    """
    out = defaultdict(dict)
    for deposito, sql in (
        ("tuc", f"""
            select sku, min(f) from (
              select sku, min(fecha) f from gold.fact_ventas
               where not (canal = 'Mercado Libre'
                          and coalesce(logistica, 'fulfillment') <> all(%(no_full)s))
               group by sku
              union all
              select i->>'articuloId',
                     min(coalesce(nullif(c."fechaIngreso", ''), c."fechaFactura")::date)
                from bronze.sigma_compras c, jsonb_array_elements(c.items::jsonb) i
               group by 1
              union all
              select sku, min(left(fecha_estado, 10)::date) from bronze.digip_envios_full
               where sku is not null and fecha_estado is not null group by sku
              union all
              select sku, min(fecha) from bronze.{TABLA_FOTO}
               where disponible > 0 group by sku
            ) t where sku is not null group by sku"""),
        ("full", """
            select sku, min(f) from (
              select sku, min(fecha) f from gold.fact_ventas
               where canal = 'Mercado Libre'
                 and coalesce(logistica, 'fulfillment') <> all(%(no_full)s)
               group by sku
              union all
              select sku, min(left(fecha_estado, 10)::date) from bronze.digip_envios_full
               where sku is not null and fecha_estado is not null group by sku
              union all
              select m.sku, min(h.fecha) from bronze.ml_stock_full_historico h
                join bronze.ml_inventario_sku m on m.inventory_id = h.inventory_id
               where coalesce(h.available_quantity, 0) > 0 group by m.sku
            ) t where sku is not null group by sku"""),
    ):
        for sku, fecha in con.exec_driver_sql(sql, p).fetchall():
            if sku and fecha is not None:
                out[str(sku).strip()][deposito] = fecha
    return out


def main():
    hoy = hoy_argentina()
    print(f"=== DIAS CON STOCK por deposito (al {hoy}) ===")
    engine = crear_engine()
    with engine.begin() as con:
        for sentencia in DDL.split(";"):
            if sentencia.strip():
                con.exec_driver_sql(sentencia)
        guardar_foto_digip(con, hoy)

    with engine.begin() as con:
        foto_tuc, foto_full, mov, primeras = leer(con, hoy)
    print(f"  {len(foto_tuc)} SKU con foto de Tucuman, {len(foto_full)} con foto "
          f"de Full, {len(mov)} con movimientos")

    df = calcular(hoy, foto_tuc, foto_full, mov, primeras)
    if df.empty:
        print("  Nada que escribir: no se toca lo que ya estaba.")
        return

    with engine.begin() as con:
        con.exec_driver_sql(f"delete from gold.{TABLA_DIAS}")
        df.to_sql(TABLA_DIAS, con, schema="gold", if_exists="append",
                  index=False, method="multi", chunksize=5000)
    print(f"  Guardado: {len(df)} filas en gold.{TABLA_DIAS}")


if __name__ == "__main__":
    main()
