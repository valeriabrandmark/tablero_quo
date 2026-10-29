"""Foto del stock al cierre de cada mes comercial.

============================================================================
 PARA QUE SIRVE
============================================================================

Para poder LICUAR el descuento mes a mes. La regla del sell in es:

    desc del mes = (stock de cierre x desc heredado + compras del mes x desc)
                   --------------------------------------------------------
                            stock de cierre + compras del mes

y ese "stock de cierre" es el unico termino que hoy no existe en ninguna
tabla: `digip_stock` y `sigma_stock` se reemplazan enteras en cada corrida,
asi que dicen cuanto hay HOY y no dejan rastro de cuanto habia el 5 del mes
pasado. Sin esa serie, el descuento heredado no se puede recalcular ni
auditar: hay que creerle a la planilla.

Es una serie que NO SE PUEDE RECONSTRUIR HACIA ATRAS. Por eso el paso empieza
a correr antes de que exista quien la consuma: cada mes que pasa sin guardarla
es un mes que no se recupera.

============================================================================
 SE GUARDA TODOS LOS DIAS, NO EL DIA DEL CIERRE
============================================================================

El mes comercial va del 6 al 5 (con excepciones, ver calendario.py), asi que
"el cierre" seria la corrida del dia 5. Atarlo a esa fecha es fragil de la
peor manera: si ese dia el orquestador no corre --un feriado de GitHub, una
caida de Digip, un token vencido-- el mes se pierde PARA SIEMPRE, y nadie se
entera hasta que alguien pide el dato tres meses despues.

En cambio se escribe la foto del mes comercial EN CURSO en cada corrida,
pisando la del dia anterior. Entonces el cierre no es un evento: es,
simplemente, el ultimo valor que quedo escrito cuando el mes termino. Si
faltan los ultimos dias, queda el del dia que haya; si falta el mes entero,
queda vacio y se ve.

============================================================================
 SE GUARDAN LAS DOS FUENTES, Y NO ES INDECISION
============================================================================

Digip y Sigma no dicen lo mismo. GL05031 el 27/10/2026: Digip 27 unidades,
Sigma 84. El tablero valoriza stock con Digip (ver lib/queries-stock.ts) y
los costos salen del mundo Sigma --costo teorico, compras--, asi que cual de
las dos corresponde para licuar el descuento es una decision de negocio que
todavia no esta tomada.

Guardar una sola seria tomarla en silencio, y encima de forma irreversible:
la que no se guarde no se puede recuperar despues. Guardar las dos cuesta
9.000 filas por mes.
"""

import pandas as pd

from calendario import mes_comercial
from conexion import crear_engine
from guardado import sincronizar_columnas

TABLA = "stock_cierre_mes"


def armar_foto(digip, sigma, mes):
    """Una fila por SKU con las dos fuentes. Sin base, para poder probarla.

    `digip` y `sigma` son listas de (sku, unidades). Un SKU puede estar en una
    y no en la otra: queda con 0 en la que falta y NO se descarta -- que Sigma
    tenga 84 donde Digip tiene 0 es exactamente la clase de diferencia que esta
    tabla existe para dejar registrada.
    """
    por_sku = {}
    for sku, u in digip:
        if not sku:
            continue
        por_sku.setdefault(sku, {"digip": 0.0, "sigma": 0.0})["digip"] += float(u or 0)
    for sku, u in sigma:
        if not sku:
            continue
        por_sku.setdefault(sku, {"digip": 0.0, "sigma": 0.0})["sigma"] += float(u or 0)

    return pd.DataFrame(
        [{"mes_comercial": mes, "sku": sku,
          "stock_digip": v["digip"], "stock_sigma": v["sigma"]}
         for sku, v in sorted(por_sku.items())]
    )


def _leer(engine, sql):
    with engine.begin() as con:
        return [(str(r[0]).strip(), r[1]) for r in con.exec_driver_sql(sql).fetchall()]


def main():
    hoy = pd.Timestamp.now(tz="America/Argentina/Buenos_Aires").date()
    mes = mes_comercial(hoy)
    print(f"=== STOCK AL CIERRE · mes comercial {mes} (foto del {hoy}) ===")

    engine = crear_engine()
    digip = _leer(engine, """
        select trim(codigo), sum(coalesce("stock.disponible", 0))
          from bronze.digip_stock
         where nullif(trim(codigo), '') is not null
         group by 1""")
    sigma = _leer(engine, """
        select trim("articuloId"), sum(coalesce(stock, 0))
          from bronze.sigma_stock
         where nullif(trim("articuloId"), '') is not null
         group by 1""")
    print(f"  Digip: {len(digip)} articulos | Sigma: {len(sigma)} articulos")

    df = armar_foto(digip, sigma, mes)
    if df.empty:
        # NO SE PISA LA FOTO CON UNA VACIA. Si las dos fuentes vinieron sin
        # datos --Digip caido, por ejemplo-- lo que ya estaba guardado de este
        # mes vale mas que nada.
        print("  Las dos fuentes vinieron vacias: no se toca lo guardado.")
        return

    df["actualizado"] = pd.Timestamp.now(tz="America/Argentina/Buenos_Aires")
    guardar_foto(engine, df, mes)
    print(f"  Guardado: {len(df)} articulos en bronze.{TABLA} para {mes}")


def guardar_foto(engine, df, mes):
    """Reemplaza la foto del mes en bronze.stock_cierre_mes.

    SE PREGUNTA SI LA TABLA EXISTE ANTES DE BORRAR, en vez de intentar el
    DELETE y atrapar el error. Asi estaba, y no creo la tabla nunca: en
    Postgres una orden que falla deja la transaccion entera ABORTADA, y todo lo
    que viene despues en ella --el to_sql que la iba a crear-- explota con
    InFailedSqlTransaction. Fallo en todas las corridas desde que se agrego el
    paso (44 seguidas al 29/10/2026) y dejo el orquestador en rojo.
    """
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")
        existe = con.exec_driver_sql(
            "SELECT to_regclass(%(t)s) IS NOT NULL", {"t": f'bronze."{TABLA}"'}).scalar()
        if existe:
            sincronizar_columnas(con, TABLA, df)
            # BORRAR E INSERTAR EN LA MISMA TRANSACCION, igual que guardar_en_bd:
            # en el medio la tabla no queda sin el mes en curso para quien consulte.
            con.exec_driver_sql(
                f'DELETE FROM bronze."{TABLA}" WHERE mes_comercial = %(m)s', {"m": mes})
        else:
            print(f"  bronze.{TABLA} no existe todavia -> la crea el to_sql.")
        df.to_sql(TABLA, con, schema="bronze", if_exists="append", index=False)


if __name__ == "__main__":
    main()
