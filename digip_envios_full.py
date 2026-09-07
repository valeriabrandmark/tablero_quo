"""Lo que MANDAMOS al deposito de Mercado Libre, articulo por articulo.

POR QUE EXISTE
--------------
Mandamos mercaderia a Full, las ventas la van descontando, y cada tanto el
stock que Mercado Libre declara NO es el que mandamos: aparecen unidades de
menos que nadie vendio. Hoy eso no se puede reclamar porque no hay con que
comparar -- tenemos lo que ML dice que tiene, pero no lo que le entregamos.

Esta es esa pata. Con esto se puede armar, dia por dia:

    esperado(hoy) = declarado(ayer) + ingresos(hoy) - ventas(hoy)

y todo lo que falte sin explicacion es una unidad para reclamar.

QUE SE GUARDA Y POR QUE `unidadesSatisfecha`
--------------------------------------------
Un item de preparacion trae tres numeros parecidos y NO son lo mismo:

    unidades            lo que el pedido pidio
    unidadesReservada   lo que se pudo reservar
    unidadesSatisfecha  lo que efectivamente se preparo  <-- este

En el pedido 74430467 (ANDREANI, 145 items) hay articulos que pidieron 4 y
satisficieron 1, y otros que pidieron 12 y satisficieron 9. Lo que sale del
deposito es lo satisfecho; usar `unidades` contaria envios que no ocurrieron y
haria ver faltantes de Mercado Libre donde el faltante es nuestro.

Se guardan los tres igual, porque la diferencia entre lo pedido y lo satisfecho
es informacion propia -- dice cuando el deposito no pudo completar un envio.

POR QUE NO SE FILTRA POR ESTADO AL ENTRAR
-----------------------------------------
Los envios a Full NO SE REMITEN: son mercaderia que va a un deposito nuestro,
no a un cliente. Por eso nunca llegan a `RemitidoExterno` --que es lo unico que
ingesta digip_pedidos.py-- y por eso no estaban en la base: de 210 pedidos de
un mes, 138 son RemitidoExterno y los 72 restantes (Completo, Preparacion,
Eliminado) quedaban afuera.

Este script pide los pedidos SIN filtrar por estado y guarda el estado en cada
fila. Decidir al guardar cual cuenta como "ya salio" seria congelar hoy una
regla que todavia estamos entendiendo, y para cambiarla habria que volver a
pedirle todo a la API. Guardado el estado, la regla vive en la consulta.

POR QUE ES UN SCRIPT APARTE Y NO UN CAMBIO EN digip_pedidos.py
--------------------------------------------------------------
Ampliarle el filtro de estado a ese script le agregaria filas a
`bronze.digip_pedidos`, de la que cuelgan el tablero de Logistica y el de
Antiguedad. Serian numeros movidos de rebote en pantallas que nadie pidio
tocar. Este script no escribe en ninguna tabla existente.
"""

import argparse
import os
from collections import Counter
from datetime import date, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv

from conexion import crear_engine
from errores_bd import es_tabla_inexistente

load_dotenv()

BASE = os.getenv("DIGIP_URL_BASE")
API_KEY = os.getenv("DIGIP_API_KEY")
HEADERS = {"X-API-Key": API_KEY}

engine = crear_engine(connect_args={"client_encoding": "utf8"})

# Los dos despachos que van al deposito de Mercado Libre. Se comparan en
# mayusculas y sin espacios de sobra: en la base ya hay un "CONSUMO INTERNO "
# con espacio al final, asi que el dato viene como viene.
DESPACHOS_A_FULL = {"ETIQUETADO MELI QUO", "ANDREANI BRANDMARK"}

# Desde cuando hay datos utiles. La misma que usan los otros scripts de Digip.
FECHA_CORTE = date(2026, 5, 6)

# La ventana de la corrida diaria. Mas larga que la de digip_preparaciones.py
# (7 dias) a proposito: una preparacion cambia de estado despues de creada
# --pasa de Preparacion a Completo-- y con 14 dias esa transicion se vuelve a
# leer sin depender de que la corrida del dia justo la agarre.
DIAS_VENTANA = 14

DDL = """
create table if not exists bronze.digip_envios_full (
    preparacion_id      bigint  not null,
    item_id             bigint  not null,
    pedido_codigo       text,
    pedido_estado       text,
    despacho            text,
    despacho_estado     text,
    preparacion_estado  text,
    fecha_estado        text,
    sku                 text,
    articulo            text,
    unidades            double precision,
    unidades_reservada  double precision,
    unidades_satisfecha double precision,
    lote                text,
    fecha_vencimiento   text,
    primary key (preparacion_id, item_id)
);
"""


def llamar(endpoint, params=None):
    """Una llamada. Devuelve el json, o None si algo salio mal."""
    try:
        r = requests.get(f"{BASE}{endpoint}", headers=HEADERS, params=params, timeout=30)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def pedidos_de_la_ventana(desde, hasta):
    """Los codigos de pedido del periodo, SIN filtrar por estado."""
    codigos = []
    page = 1
    while True:
        datos = llamar("Pedidos", {
            "FechaPedidoDesde": f"{desde.isoformat()}T00:00:00",
            "FechaPedidoHasta": f"{hasta.isoformat()}T23:59:59",
            "Page": page, "PerPage": 500, "OrderCriteria": "CodigoPedido",
        })
        if not datos:
            break
        codigos += [(str(p.get("codigo")), p.get("estado")) for p in datos if p.get("codigo")]
        if len(datos) < 500:
            break
        page += 1
    return codigos


def filas_de_preparacion(prep, pedido_codigo, pedido_estado):
    """Las filas de una preparacion, o [] si no va a Full."""
    despacho = prep.get("despachoDescripcion") or prep.get("despachoCodigo") or ""
    if str(despacho).strip().upper() not in DESPACHOS_A_FULL:
        return []

    filas = []
    for it in (prep.get("items") or []):
        filas.append({
            "preparacion_id": prep.get("id"),
            "item_id": it.get("id"),
            "pedido_codigo": pedido_codigo,
            "pedido_estado": pedido_estado,
            "despacho": despacho,
            "despacho_estado": prep.get("despachoEstado"),
            "preparacion_estado": prep.get("preparacionEstado"),
            "fecha_estado": prep.get("fechaHoraEstado"),
            "sku": (it.get("codigoArticulo") or "").strip() or None,
            "articulo": it.get("articulo"),
            "unidades": it.get("unidades"),
            "unidades_reservada": it.get("unidadesReservada"),
            "unidades_satisfecha": it.get("unidadesSatisfecha"),
            "lote": it.get("lote"),
            "fecha_vencimiento": it.get("fechaVencimiento"),
        })
    return filas


def extraer(desde, hasta):
    print(f"=== Envios a Full ({desde} a {hasta}) ===")
    codigos = pedidos_de_la_ventana(desde, hasta)
    print(f"  {len(codigos)} pedidos en el periodo (todos los estados)")
    if not codigos:
        print("  Sin pedidos: no se toca nada.")
        return None, []

    filas = []
    despachos = Counter()
    fallados = 0

    for i, (cod, estado) in enumerate(codigos, 1):
        prep = llamar(f"Preparaciones/{cod}")
        if prep is None:
            fallados += 1
            continue
        despacho = prep.get("despachoDescripcion") or prep.get("despachoCodigo") or "(sin despacho)"
        despachos[despacho] += 1
        filas += filas_de_preparacion(prep, cod, estado)
        if i % 100 == 0:
            print(f"    {i}/{len(codigos)} preparaciones consultadas...")

    print(f"\n  Despachos vistos:")
    for d, n in despachos.most_common(12):
        marca = "   <-- a Full" if str(d).strip().upper() in DESPACHOS_A_FULL else ""
        print(f"    {str(d):<32} {n:>5}{marca}")

    if fallados:
        # Se avisa aunque no corte: un envio que no se pudo consultar se parece
        # demasiado a un envio que no existio, y esa diferencia es justamente
        # la que este script viene a medir.
        print(f"\n  ATENCION: {fallados} preparaciones no se pudieron consultar.")

    df = pd.DataFrame(filas)
    if not df.empty:
        # Una preparacion consolidada trae el mismo item una sola vez, pero un
        # pedido puede aparecer en dos paginas si algo se movio entre llamadas.
        df = df.drop_duplicates(subset=["preparacion_id", "item_id"], keep="last")
        print(f"\n  {len(df)} items a Full, "
              f"{df['sku'].nunique()} SKU, "
              f"{df['unidades_satisfecha'].sum():,.0f} unidades satisfechas")
        parciales = int((df["unidades_satisfecha"] < df["unidades"]).sum())
        if parciales:
            print(f"  {parciales} items salieron incompletos (satisfecho < pedido)")
    return df, [c for c, _ in codigos]


def guardar(df, codigos):
    """Reemplaza SOLO los pedidos de esta ventana, en una sola transaccion."""
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")
        con.exec_driver_sql(DDL)

    # SI NO HAY NADA CON QUE REEMPLAZAR, NO SE BORRA. Con Digip caido todas las
    # llamadas fallan, el df queda vacio, y borrar igual dejaria la ventana sin
    # envios -- que se leeria como "Mercado Libre nos comio todo eso". Es la
    # misma trampa que dejo a Tienda Nube sin datos desde el 12/06.
    if df is None or df.empty:
        print("\n  (sin envios a Full en esta ventana: NO se toca lo que ya estaba)")
        return

    try:
        with engine.begin() as con:
            borradas = con.exec_driver_sql(
                "DELETE FROM bronze.digip_envios_full WHERE pedido_codigo = ANY(%(c)s)",
                {"c": codigos},
            ).rowcount
            print(f"\n  Filas viejas de la ventana borradas: {borradas}")
            df.to_sql("digip_envios_full", con, schema="bronze",
                      if_exists="append", index=False)
    except Exception as e:
        if not es_tabla_inexistente(e):
            raise
        df.to_sql("digip_envios_full", engine, schema="bronze",
                  if_exists="append", index=False)

    print(f"  Guardado: bronze.digip_envios_full (+{len(df)} filas)")

    resumen = pd.read_sql(
        """select despacho, count(*) items, count(distinct sku) skus,
                  sum(unidades_satisfecha) unidades
             from bronze.digip_envios_full group by 1 order by 1""",
        engine)
    print("\n  Tabla completa:")
    print(resumen.to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", help="Fecha inicial YYYY-MM-DD. Para el backfill "
                                    "de una vez; sin esto usa la ventana movil.")
    args = ap.parse_args()

    if not BASE or not API_KEY:
        raise SystemExit("Faltan DIGIP_URL_BASE o DIGIP_API_KEY en el entorno.")

    hoy = date.today()
    if args.desde:
        desde = max(FECHA_CORTE, date.fromisoformat(args.desde))
        print(f"BACKFILL desde {desde}. Puede tardar varios minutos.")
    else:
        desde = max(FECHA_CORTE, hoy - timedelta(days=DIAS_VENTANA))

    df, codigos = extraer(desde, hoy)
    guardar(df, codigos)
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
