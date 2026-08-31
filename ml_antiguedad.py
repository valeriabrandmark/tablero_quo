"""Antiguedad del stock en Full, reconstruida desde el libro de movimientos.

============================================================================
 POR QUE HAY QUE RECONSTRUIRLA
============================================================================

El reporte de Full que se baja a mano tiene la columna "Unidades que afectan la
metrica Con antiguedad", y el tablero de Stock la necesita para saber que
mercaderia esta por entrar en el cargo por almacenamiento prolongado (arranca a
los 120 dias).

Ese numero NO viene por API. Sondeado el 31/08/2026 (ver probar_antiguedad_ml.py):

    GET /inventories/{id}/stock/fulfillment
    -> total, available_quantity, not_available_quantity, not_available_detail,
       external_references. Ninguna fecha, ningun dia de permanencia.

Para Mercado Libre la antiguedad no es un atributo del stock: es la base de un
cobro, y vive en el reporte de costos de almacenamiento del Centro de
Vendedores, que es un Excel.

============================================================================
 DE DONDE SI SALE
============================================================================

Del libro de movimientos del inventario. La llamada que anda, confirmada contra
la cuenta real:

    GET /stock/fulfillment/operations/search
        ?seller_id=<obligatorio>&inventory_id=<obligatorio>
        &date_from=YYYY-MM-DD&date_to=YYYY-MM-DD

Tres cosas que costaron encontrarse y que conviene no volver a descubrir:

  1. `seller_id` es OBLIGATORIO. Sin el contesta 400 "The field seller_id is
     required". Ese era el 400 que nos frenaba.
  2. Las fechas van como FECHA SIMPLE. Con timestamp ISO contesta 400 "The
     field date_from has an invalid value".
  3. Sin fechas contesta 200 igual, pero con muchas menos operaciones (24 contra
     76 para el mismo inventario): usa una ventana corta por defecto. Hay que
     pedirlas siempre.

Cada operacion trae el DELTA en `detail.available_quantity` (negativo en una
venta, positivo en un ingreso) y el saldo despues en `result`:

    { "date_created": "2026-08-29T03:46:51Z", "type": "INBOUND_RECEPTION",
      "detail": { "available_quantity": 14 },
      "result": { "total": 132, "available_quantity": 132 } }

Tipos vistos: INBOUND_RECEPTION, SALE_CONFIRMATION, ADJUSTMENT,
WITHDRAWAL_RESERVATION, WITHDRAWAL_DELIVERY.

============================================================================
 COMO SE CALCULA
============================================================================

FIFO, que es como Mercado Libre cobra: se va la unidad mas vieja primero.

  1. Se juntan las ENTRADAS (delta > 0) en orden cronologico, cada una con su
     fecha y su cantidad.
  2. Se suman las SALIDAS (delta < 0) de toda la ventana.
  3. Se consumen las entradas mas viejas hasta cubrir las salidas.
  4. Lo que sobra son las unidades que hoy siguen en el deposito, cada una con
     la fecha de la entrada que la trajo. De ahi salen los dias.

CUANDO NO ALCANZA LA HISTORIA. Si despues de consumir todas las salidas quedan
menos unidades de las que el stock dice que hay, es que la mercaderia entro
antes del principio de la ventana. Esas unidades se marcan `incompleto` y se
cuentan como "mas viejas que la ventana" en vez de inventarles una fecha: un
articulo que lleva dos años ahi es exactamente el que mas importa, y darle la
fecha del primer ingreso que vimos lo haria parecer nuevo.

    python ml_antiguedad.py --probar 3   # 3 inventarios, imprime y no guarda
    python ml_antiguedad.py              # todos los que tienen stock
"""

import argparse
import itertools
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv

import requests

from conexion import crear_engine
from mercadolibre import llamar_ml, renovar_access_token

load_dotenv()
USER_ID = os.getenv("ML_USER_ID")

# Hasta donde se pide historia.
#
# 120 Y NO 365. Es exactamente lo que hace falta: el ultimo tramo del cargo por
# almacenamiento es "mas de 120 dias", asi que todo lo que la historia de 120
# dias no explique YA cae ahi. Pedir mas seria afinar un numero que no cambia
# ninguna decision, y cada mes de historia son ~1.800 llamadas mas.
#
# Son 2 ventanas por inventario en vez de 7.
DIAS_HISTORIA = 120

# EL RANGO MAXIMO POR LLAMADA SON 60 DIAS. Con 90 la API contesta 400 en TODAS
# las llamadas -- probado contra la cuenta real. No es un limite que convenga
# estirar "por las dudas": es el que dice la documentacion y el que aplica el
# servidor.
DIAS_POR_LLAMADA = 60

# CUATRO Y NO DOCE. Este endpoint aguanta MUCHO menos que el de stock: con 12
# hilos, 21 llamadas (3 inventarios) ya dieron 13 reintentos por 429. Subir los
# hilos no acelera nada cuando la API frena: sube la espera.
HILOS = 4

# Los cortes del cargo por almacenamiento de Mercado Libre.
TRAMOS = [(30, "u_0_30"), (60, "u_31_60"), (90, "u_61_90"), (120, "u_91_120")]

DDL = """
create table if not exists bronze.ml_stock_antiguedad (
  fecha          date not null,
  -- La clave es el INVENTARIO porque es la unidad fisica de stock que Mercado
  -- Libre mueve y factura. `sku` va al lado como atributo, que es lo que hace
  -- la tabla usable: `LSGZ75310` no le dice nada a nadie, `GL26017` si.
  inventory_id   text not null,
  sku            text,
  unidades       integer not null,
  dias_promedio  numeric,
  u_0_30         integer not null default 0,
  u_31_60        integer not null default 0,
  u_61_90        integer not null default 0,
  u_91_120       integer not null default 0,
  u_mas_120      integer not null default 0,
  -- true = la historia no alcanzo para explicar todas las unidades que hay.
  -- Las que faltan se cuentan en u_mas_120, que es lo conservador.
  incompleto     boolean not null default false,
  primary key (fecha, inventory_id)
);
-- Para la tabla que ya se haya creado sin la columna: `create table if not
-- exists` no agrega columnas a una que ya existe, asi que sin esto una base que
-- corrio la version anterior se quedaria sin `sku` para siempre.
alter table bronze.ml_stock_antiguedad add column if not exists sku text;
-- El tablero busca por SKU y por dia, nunca por inventory_id suelto.
create index if not exists ml_stock_antiguedad_sku_fecha
  on bronze.ml_stock_antiguedad (sku, fecha desc);
"""


def _engine():
    return crear_engine()


def inventarios_con_stock(limite=None):
    """Los inventarios que HOY tienen unidades, con SU SKU y su descripcion.

    Preguntar por los vacios seria pedir la historia de algo que ya no esta en
    el deposito.

    EL `inventory_id` NO ES NUESTRO SKU. Es un codigo interno de Mercado Libre
    (`LSGZ75310`), y guardar la antiguedad solo con eso deja una tabla que no se
    puede cruzar con nada: ni con el stock de Tucuman, ni con las ventas, ni con
    los costos. El SKU vive dentro del array `attributes` de la publicacion, en
    `SELLER_SKU` -- el mismo rodeo que hace extraer_stock_full, por lo mismo.

    Empalma bien: de los 1.853 inventarios con stock, los 1.853 tienen SKU, y
    solo 5 SKU usan mas de un inventario.
    """
    sql = """
        with mapa as (
          select p.inventory_id,
                 max((select a->>'value_name'
                        from jsonb_array_elements(p.attributes::jsonb) a
                       where a->>'id' = 'SELLER_SKU'
                       limit 1)) as sku
          from bronze.ml_publicaciones p
          where p."shipping.logistic_type" = 'fulfillment'
            and p.inventory_id is not null
          group by p.inventory_id
        )
        select f.inventory_id,
               f.available_quantity,
               m.sku,
               art.descripcion as articulo
        from bronze.ml_stock_full f
        left join mapa m on m.inventory_id = f.inventory_id
        left join bronze.sigma_articulos art on trim(art.id) = m.sku
        where f.available_quantity > 0
        order by f.available_quantity desc
    """
    if limite:
        sql += f" limit {int(limite)}"
    return pd.read_sql(sql, _engine())


def _con_cuerpo(fn):
    """Ejecuta y, si la API rechaza, agrega el CUERPO de la respuesta al error.

    `raise_for_status()` arma un mensaje con la URL y tira el cuerpo, que es
    justo donde Mercado Libre explica que parametro esta mal. Sin esto un 400
    dice "Bad Request for url: ..." y hay que adivinar; con esto dice "The field
    date_from has an invalid value" y no hay nada que adivinar.

    Nos costo dos vueltas aprenderlo. No se repite.
    """
    try:
        return fn()
    except requests.HTTPError as e:
        cuerpo = ""
        if e.response is not None:
            cuerpo = e.response.text[:300].replace("\n", " ")
        raise RuntimeError(f"{e.response.status_code if e.response is not None else '?'} · {cuerpo}") from e


def ventanas(hoy=None):
    """Los tramos de fechas a pedir, del mas viejo al mas nuevo.

    SE ARMAN DESDE HOY HACIA ATRAS, y no al reves. Yendo hacia adelante, cuando
    la historia es multiplo del tamaño de ventana el ultimo tramo queda de UN
    SOLO DIA -- desde == hasta -- y la API lo rechaza con "The field date_from
    can't be greater or equal to date_to". Con 180 dias y ventanas de 60 pasaba
    siempre: fallaban los 20 inventarios de la prueba.

    Hacia atras el sobrante cae en el tramo mas VIEJO, que es el que no importa
    si queda corto, y el mas nuevo siempre sale entero.

    OJO TAMBIEN CON EL OFF-BY-ONE: la API cuenta los dos extremos, asi que una
    ventana de "60 dias" va de D a D+59, no a D+60.
    """
    hoy = hoy or date.today()
    limite = hoy - timedelta(days=DIAS_HISTORIA)
    tramos = []
    hasta = hoy
    while hasta > limite:
        desde = max(limite, hasta - timedelta(days=DIAS_POR_LLAMADA - 1))
        if desde >= hasta:
            break          # un tramo de un dia no lo acepta la API
        tramos.append((desde, hasta))
        hasta = desde - timedelta(days=1)
    return list(reversed(tramos))


def operaciones(inv_id, token):
    """Todas las operaciones del inventario en el ultimo año, mas viejas primero."""
    filas = []

    for desde, hasta in ventanas():
        datos = _con_cuerpo(lambda: llamar_ml(
            "/stock/fulfillment/operations/search",
            token,
            params={
                "seller_id": USER_ID,
                "inventory_id": inv_id,
                "date_from": desde.isoformat(),
                "date_to": hasta.isoformat(),
            },
            pausa=False,
        ))
        filas += datos.get("results", [])

    # La API pagina con `scroll` y no con offset, asi que puede repetir filas
    # entre ventanas contiguas. El id es unico por operacion: con el alcanza.
    unicas = {f["id"]: f for f in filas if "id" in f}
    return sorted(unicas.values(), key=lambda f: f.get("date_created", ""))


def stock_segun_operaciones(ops):
    """El saldo que deja la ultima operacion, o None si no hay ninguna.

    ES MEJOR QUE `bronze.ml_stock_full`, y no por poco. Esa tabla la refresca el
    catalogo de Mercado Libre una vez por dia (01:05), asi que a la tarde ya
    esta vieja: para LSGZ75310 decia 303 unidades cuando la API contestaba 286.
    Mezclar las salidas de HOY con un total de AYER daba 303 unidades repartidas
    en tramos, 17 mas de las que hay.

    Cada operacion trae en `result` el saldo que quedo despues de aplicarla, asi
    que la ultima ES el stock actual -- y viene de la misma respuesta que las
    entradas y las salidas, que es lo que hace que la cuenta cierre.
    """
    if not ops:
        return None
    return (ops[-1].get("result") or {}).get("available_quantity")


def antiguedad(ops, stock_actual, hoy=None):
    """FIFO sobre las operaciones -> cuantas unidades hay en cada tramo de dias."""
    hoy = hoy or datetime.now(timezone.utc)

    # SOLO `INBOUND_RECEPTION` ARRANCA EL RELOJ. Los otros deltas positivos son
    # unidades que VUELVEN --una venta cancelada, una devolucion, una reserva de
    # retiro que se dio de baja-- y esas ya tenian una edad: contarlas como
    # ingreso nuevo rejuvenece stock viejo, que es justo el error que no
    # queremos. Se restan de las salidas, que es lo que en realidad hacen.
    entradas = []   # [[fecha, cantidad]] mas viejas primero
    salidas = 0
    for op in ops:
        delta = (op.get("detail") or {}).get("available_quantity") or 0
        if delta == 0:
            continue
        if delta > 0 and op.get("type") == "INBOUND_RECEPTION":
            fecha = datetime.fromisoformat(op["date_created"].replace("Z", "+00:00"))
            entradas.append([fecha, delta])
        else:
            salidas += -delta          # delta<0 suma, delta>0 (retorno) resta

    salidas = max(0, salidas)

    # Se consumen las entradas mas viejas primero.
    for e in entradas:
        if salidas <= 0:
            break
        usado = min(e[1], salidas)
        e[1] -= usado
        salidas -= usado

    # Lista de listas y no de tuplas: mas abajo hay que poder descontarles
    # unidades, y sobre una tupla eso se pierde en silencio.
    quedan = [e for e in entradas if e[1] > 0]
    explicadas = sum(c for _, c in quedan)

    # Lo que la historia no explica entro antes de la ventana: son las MAS
    # viejas, no unas cualesquiera.
    faltantes = max(0, stock_actual - explicadas)

    tramos = {clave: 0 for _, clave in TRAMOS}
    tramos["u_mas_120"] = faltantes
    dias_por_unidad = []

    # Si la historia explica MAS unidades de las que hay (pasa con ajustes que
    # no vienen como delta), se descartan las mas nuevas: quedarse con las
    # viejas es el lado seguro para una alerta de antiguedad.
    sobrantes = max(0, explicadas - stock_actual)
    for e in reversed(quedan):
        if sobrantes <= 0:
            break
        quita = min(e[1], sobrantes)
        e[1] -= quita
        sobrantes -= quita

    for fecha, cant in quedan:
        if cant <= 0:
            continue
        dias = (hoy - fecha).days
        dias_por_unidad += [dias] * cant
        for tope, clave in TRAMOS:
            if dias <= tope:
                tramos[clave] += cant
                break
        else:
            tramos["u_mas_120"] += cant

    # LAS NO EXPLICADAS TAMBIEN CUENTAN EN EL PROMEDIO, con la edad minima que
    # se les conoce: DIAS_HISTORIA.
    #
    # Sin esto el promedio miente y miente FEO. JMEQ05485 tiene 42 unidades de
    # dos dias y 86 de mas de 120; promediando solo las explicadas daba
    # "2 dias", que es exactamente lo contrario de lo que pasa. Contandolas da
    # 81, que si describe el stock que hay.
    #
    # Es un PISO: las viejas pueden llevar 400 dias y aca cuentan como 120. Un
    # piso sirve --dice "por lo menos tanto"--; el promedio de arriba no servia
    # para nada.
    dias_por_unidad += [DIAS_HISTORIA] * faltantes

    total = sum(tramos.values())
    promedio = round(sum(dias_por_unidad) / len(dias_por_unidad), 1) if dias_por_unidad else None

    return {
        "unidades": total,
        "dias_promedio": promedio,
        **tramos,
        "incompleto": faltantes > 0,
    }


def guardar(df):
    engine = _engine()
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")
        con.exec_driver_sql(DDL)
        # Una foto por dia: si el paso corre dos veces, la segunda pisa la
        # primera en vez de duplicar.
        con.exec_driver_sql(
            'DELETE FROM bronze.ml_stock_antiguedad WHERE fecha = %(f)s',
            {"f": date.today()},
        )
    df.to_sql("ml_stock_antiguedad", engine, schema="bronze",
              if_exists="append", index=False)
    print(f"  Guardado: bronze.ml_stock_antiguedad ({len(df)} filas)")


def main():
    parser = argparse.ArgumentParser(description="Antiguedad del stock en Full.")
    parser.add_argument("--probar", type=int, metavar="N",
                        help="Solo N inventarios, imprime y NO guarda")
    args = parser.parse_args()

    print("\n=== ANTIGUEDAD DEL STOCK EN FULL ===")
    arranque = time.monotonic()
    token = renovar_access_token()

    df_inv = inventarios_con_stock(args.probar)
    print(f"  {len(df_inv)} inventarios con stock (de a {HILOS})")

    hechos = itertools.count(1)

    def procesar(fila):
        inv_id = fila.inventory_id
        try:
            ops = operaciones(inv_id, token)
            # El stock sale de la ultima operacion; la tabla es el respaldo para
            # el inventario que no tuvo ningun movimiento en la ventana.
            stock = stock_segun_operaciones(ops)
            if stock is None:
                stock = int(fila.available_quantity)
            r = antiguedad(ops, int(stock))
            r["inventory_id"] = inv_id
            r["sku"] = fila.sku
            r["operaciones"] = len(ops)
            r["articulo"] = fila.articulo
        except Exception as e:
            # Como en el stock: el error queda a la vista y no como un cero.
            r = {"inventory_id": inv_id, "sku": fila.sku, "error": str(e)}
        n = next(hechos)
        if n % 200 == 0:
            print(f"    Procesados: {n} de {len(df_inv)}")
        return r

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        filas = list(pool.map(procesar, df_inv.itertuples(index=False)))

    # El tiempo por inventario es EL dato para decidir si esto entra en la
    # corrida diaria: multiplicado por los ~1.800 con stock da el techo que hay
    # que ponerle al paso. Medirlo con --probar sale mucho mas barato que
    # descubrirlo cuando corta por tiempo en produccion.
    tardo = time.monotonic() - arranque
    por_inv = tardo / max(1, len(df_inv))
    print(f"  Tardo {tardo/60:.1f} min · {por_inv:.1f} s por inventario")
    print(f"  Proyeccion para 1.845 inventarios: {por_inv * 1845 / 60:.0f} min")

    fallados = [f for f in filas if "error" in f]
    buenas = [f for f in filas if "error" not in f]
    if fallados:
        print(f"  ATENCION: {len(fallados)} inventarios fallaron")
        for f in fallados[:5]:
            print(f"    {f['inventory_id']}: {f['error'][:400]}")

    if args.probar:
        for f in buenas:
            ops_n = f.pop("operaciones")
            art = f.pop("articulo", None) or "(sin maestro)"
            print(f"\n  {f['sku']}  {art[:44]}")
            print(f"     ({f['inventory_id']} · {ops_n} operaciones)")
            for k, v in f.items():
                if k not in ("inventory_id", "sku"):
                    print(f"     {k:<16} {v}")
        print("\n  (modo prueba: no se guardo nada)")
        return

    if not buenas:
        print("  Sin datos para guardar.")
        return

    # `articulo` no se guarda: ya vive en bronze.sigma_articulos y copiarlo aca
    # seria tener el nombre en dos lados, con uno de los dos siempre viejo.
    df = pd.DataFrame(buenas).drop(columns=["operaciones", "articulo"], errors="ignore")
    df["fecha"] = date.today()
    incompletos = int(df["incompleto"].sum())
    print(f"  {len(df)} inventarios calculados · {incompletos} con historia incompleta")
    guardar(df)


if __name__ == "__main__":
    main()
