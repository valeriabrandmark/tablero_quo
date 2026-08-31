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
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv

from conexion import crear_engine
from mercadolibre import llamar_ml, renovar_access_token

load_dotenv()
USER_ID = os.getenv("ML_USER_ID")

# Hasta donde se pide historia. Un año es lo que la API conserva, y de todas
# formas pasados los 120 dias ya cae en el ultimo tramo del cargo: saber si algo
# lleva 300 o 400 dias no cambia ninguna decision.
DIAS_HISTORIA = 365

# La API acepta rangos largos, pero pedir un año de una para ~1.800 inventarios
# es mucha respuesta por llamada. En tramos de 90 dias entra comodo y son 5
# llamadas por inventario.
DIAS_POR_LLAMADA = 90

# Mismo criterio que el stock: rapido sin acercarse al 429. `llamar_ml` ademas
# reintenta esperando lo que la API pida.
HILOS = 12

# Los cortes del cargo por almacenamiento de Mercado Libre.
TRAMOS = [(30, "u_0_30"), (60, "u_31_60"), (90, "u_61_90"), (120, "u_91_120")]

DDL = """
create table if not exists bronze.ml_stock_antiguedad (
  fecha          date not null,
  inventory_id   text not null,
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
"""


def _engine():
    return crear_engine()


def inventarios_con_stock(limite=None):
    """Solo los que HOY tienen unidades. Preguntar por los vacios seria pedir la
    historia de algo que ya no esta en el deposito."""
    sql = """
        select inventory_id, available_quantity
        from bronze.ml_stock_full
        where available_quantity > 0
        order by available_quantity desc
    """
    if limite:
        sql += f" limit {int(limite)}"
    return pd.read_sql(sql, _engine())


def operaciones(inv_id, token):
    """Todas las operaciones del inventario en el ultimo año, mas viejas primero."""
    hoy = date.today()
    filas = []
    desde = hoy - timedelta(days=DIAS_HISTORIA)

    while desde < hoy:
        hasta = min(hoy, desde + timedelta(days=DIAS_POR_LLAMADA))
        datos = llamar_ml(
            "/stock/fulfillment/operations/search",
            token,
            params={
                "seller_id": USER_ID,
                "inventory_id": inv_id,
                "date_from": desde.isoformat(),
                "date_to": hasta.isoformat(),
            },
            pausa=False,
        )
        filas += datos.get("results", [])
        desde = hasta + timedelta(days=1)

    # La API pagina con `scroll` y no con offset, asi que puede repetir filas
    # entre ventanas contiguas. El id es unico por operacion: con el alcanza.
    unicas = {f["id"]: f for f in filas if "id" in f}
    return sorted(unicas.values(), key=lambda f: f.get("date_created", ""))


def antiguedad(ops, stock_actual, hoy=None):
    """FIFO sobre las operaciones -> cuantas unidades hay en cada tramo de dias."""
    hoy = hoy or datetime.now(timezone.utc)

    entradas = []   # [(fecha, cantidad)] mas viejas primero
    salidas = 0
    for op in ops:
        delta = (op.get("detail") or {}).get("available_quantity") or 0
        if delta > 0:
            fecha = datetime.fromisoformat(op["date_created"].replace("Z", "+00:00"))
            entradas.append([fecha, delta])
        elif delta < 0:
            salidas += -delta

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
    token = renovar_access_token()

    df_inv = inventarios_con_stock(args.probar)
    print(f"  {len(df_inv)} inventarios con stock (de a {HILOS})")

    hechos = itertools.count(1)

    def procesar(fila):
        inv_id = fila.inventory_id
        try:
            ops = operaciones(inv_id, token)
            r = antiguedad(ops, int(fila.available_quantity))
            r["inventory_id"] = inv_id
            r["operaciones"] = len(ops)
        except Exception as e:
            # Como en el stock: el error queda a la vista y no como un cero.
            r = {"inventory_id": inv_id, "error": str(e)}
        n = next(hechos)
        if n % 200 == 0:
            print(f"    Procesados: {n} de {len(df_inv)}")
        return r

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        filas = list(pool.map(procesar, df_inv.itertuples(index=False)))

    fallados = [f for f in filas if "error" in f]
    buenas = [f for f in filas if "error" not in f]
    if fallados:
        print(f"  ATENCION: {len(fallados)} inventarios fallaron")
        for f in fallados[:5]:
            print(f"    {f['inventory_id']}: {f['error'][:120]}")

    if args.probar:
        for f in buenas:
            print(f"\n  {f['inventory_id']}  ({f.pop('operaciones')} operaciones)")
            for k, v in f.items():
                if k != "inventory_id":
                    print(f"     {k:<16} {v}")
        print("\n  (modo prueba: no se guardo nada)")
        return

    if not buenas:
        print("  Sin datos para guardar.")
        return

    df = pd.DataFrame(buenas).drop(columns=["operaciones"], errors="ignore")
    df["fecha"] = date.today()
    incompletos = int(df["incompleto"].sum())
    print(f"  {len(df)} inventarios calculados · {incompletos} con historia incompleta")
    guardar(df)


if __name__ == "__main__":
    main()
