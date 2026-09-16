"""La foto de cuentas corrientes: de "como esta hoy" a "como venia".

============================================================================
 EL PROBLEMA
============================================================================

En Supabase hay cinco tablas de cuentas corrientes y NO son lo mismo:

    _scoring          como esta cada cliente HOY. Se pisa entera en cada carga.
    _aging            un renglon por comprobante impago. Tambien se pisa.
    _cancelaciones    quien salio de mora. Tambien se pisa.
    _historial_diario  una foto por cliente y por dia
    _historial_scoring una foto por cliente y por MES

Las tres primeras las sube una persona a mano y quedan al dia. Las dos de
historial no las escribia nadie: se cargaron una sola vez el 26/08/2026 y ahi
se quedaron. El grafico "Evolucion del saldo vencido" del tablero lee la
mensual, asi que mostraba julio y agosto y septiembre no aparecia -- no porque
fallara nada, sino porque la foto de septiembre no existia.

Una tabla que se pisa entera no tiene historia. Si nadie le saca una foto
antes de que la pisen, ese dia no volvio a existir nunca mas.

============================================================================
 QUE HACE ESTE SCRIPT
============================================================================

Le saca la foto a `_scoring` y la guarda en las dos de historial.

LA FOTO SE FECHA CON `fecha_carga`, NO CON HOY. Es la fecha que trae la carga
--la columna tiene default now(), asi que cada subida se estampa sola-- y es lo
unico que dice de cuando son esos numeros. Con "hoy" en su lugar, una tabla que
lleva dos semanas sin actualizarse quedaria fotografiada como si fuera de hoy,
que es justo la clase de mentira que despues nadie puede detectar mirando el
grafico.

Consecuencia buena: correrlo diez veces el mismo dia escribe diez veces la
misma fila. Y si nadie subio nada nuevo, no aparece ninguna foto nueva.

LA MENSUAL SE REARMA DESDE LA DIARIA, y es la foto MAS NUEVA de cada cliente
dentro del mes. Es el mismo criterio con el que estaban armados julio y agosto
(129 de 145 clientes con fecha 14/07, 122 de 146 con fecha 26/08; los demas son
los que dejaron de aparecer antes de fin de mes y se quedan con su ultima).

SOLO SE TOCA EL MES DE LA FOTO. Los meses viejos no se recalculan: la tabla
diaria arranca el 10/08, asi que rearmar julio desde ahi lo borraria.
"""

import argparse

from dotenv import load_dotenv
from sqlalchemy import text

from conexion import crear_engine

load_dotenv()

engine = crear_engine()

# La zona horaria importa: `fecha_carga` es timestamptz y una subida de las 22 hs
# de Argentina es del dia siguiente en UTC. Fechar la foto un dia adelante la
# mandaria al mes que viene los ultimos dias de cada mes.
ZONA = "America/Argentina/Buenos_Aires"

# Las columnas que comparten scoring y las dos de historial. `cliente_id` no
# esta en scoring --se busca aparte-- y `periodo` es de la mensual.
COLUMNAS = [
    "razon_social", "vendedor", "empresa", "saldo_total", "fact_pendientes",
    "fact_vencidas", "fact_mayor_45", "atraso_max", "atraso_prom_general",
    "categoria", "saldo_vencido",
]

SQL_CUANDO = f"""
select to_char((max(fecha_carga) at time zone '{ZONA}')::date, 'DD/MM/YYYY') as fecha,
       to_char((max(fecha_carga) at time zone '{ZONA}')::date, 'YYYY-MM')    as periodo,
       count(*)                                                              as clientes
  from bronze.cuentas_corrientes_scoring
"""

# `cliente_id` no viene en scoring y el tablero no lo usa, pero la columna existe
# en las dos de historial: se arrastra el ultimo conocido de cada cuit para no
# dejar la foto nueva mas pobre que las viejas.
SQL_DIARIO = """
with candidatos as (
    select cuit, cliente_id, to_date(fecha, 'DD/MM/YYYY') as visto
      from bronze.cuentas_corrientes_historial_diario  where cliente_id is not null
     union all
    select cuit, cliente_id, to_date(fecha, 'DD/MM/YYYY')
      from bronze.cuentas_corrientes_historial_scoring where cliente_id is not null
),
ids as (
    select distinct on (cuit) cuit, cliente_id
      from candidatos order by cuit, visto desc
)
insert into bronze.cuentas_corrientes_historial_diario
       (cuit, fecha, cliente_id, {columnas})
select s.cuit, :fecha, i.cliente_id, {columnas_s}
  from bronze.cuentas_corrientes_scoring s
  left join ids i on i.cuit = s.cuit
 where s.cuit is not null
    on conflict (cuit, fecha) do update set
       cliente_id = coalesce(excluded.cliente_id,
                             bronze.cuentas_corrientes_historial_diario.cliente_id),
       {asignaciones},
       fecha_carga = now()
""".format(
    columnas=", ".join(COLUMNAS),
    columnas_s=", ".join("s." + c for c in COLUMNAS),
    asignaciones=", ".join(f"{c} = excluded.{c}" for c in COLUMNAS),
)

SQL_MENSUAL = """
insert into bronze.cuentas_corrientes_historial_scoring
       (cuit, periodo, fecha, cliente_id, {columnas})
select d.cuit, :periodo, d.fecha, d.cliente_id, {columnas_d}
  from bronze.cuentas_corrientes_historial_diario d
 where to_char(to_date(d.fecha, 'DD/MM/YYYY'), 'YYYY-MM') = :periodo
   and to_date(d.fecha, 'DD/MM/YYYY') = (
        select max(to_date(d2.fecha, 'DD/MM/YYYY'))
          from bronze.cuentas_corrientes_historial_diario d2
         where d2.cuit = d.cuit
           and to_char(to_date(d2.fecha, 'DD/MM/YYYY'), 'YYYY-MM') = :periodo)
    on conflict (cuit, periodo) do update set
       fecha = excluded.fecha,
       cliente_id = coalesce(excluded.cliente_id,
                             bronze.cuentas_corrientes_historial_scoring.cliente_id),
       {asignaciones},
       fecha_carga = now()
""".format(
    columnas=", ".join(COLUMNAS),
    columnas_d=", ".join("d." + c for c in COLUMNAS),
    asignaciones=", ".join(f"{c} = excluded.{c}" for c in COLUMNAS),
)


def sacar_foto(solo_ver=False):
    print("=== Foto de cuentas corrientes ===")

    with engine.begin() as con:
        cuando = con.execute(text(SQL_CUANDO)).mappings().first()

        if not cuando or not cuando["fecha"]:
            # Sin datos o sin fecha_carga no hay de cuando es la foto, y una foto
            # sin fecha no sirve para una serie de tiempo.
            print("  bronze.cuentas_corrientes_scoring esta vacia o sin fecha_carga.")
            print("  No hay nada que fotografiar.")
            return

        fecha, periodo = cuando["fecha"], cuando["periodo"]
        print(f"  scoring trae {cuando['clientes']} clientes, cargados el {fecha}")

        if solo_ver:
            ya = con.execute(text(
                "select count(*) from bronze.cuentas_corrientes_historial_diario "
                "where fecha = :f"), {"f": fecha}).scalar()
            print(f"  (modo ver: no se guardo nada) La diaria ya tiene {ya} filas "
                  f"con fecha {fecha}; la mensual se rearmaria para {periodo}")
            return

        diario = con.execute(text(SQL_DIARIO), {"fecha": fecha}).rowcount
        mensual = con.execute(text(SQL_MENSUAL), {"periodo": periodo}).rowcount

    print(f"  Foto diaria {fecha}: {diario} clientes")
    print(f"  Foto mensual {periodo}: {mensual} clientes "
          "(la mas nueva de cada uno dentro del mes)")

    resumen = engine.connect().execute(text(
        "select periodo, count(*) clientes, "
        "       round(sum(saldo_vencido)::numeric, 2) vencido "
        "  from bronze.cuentas_corrientes_historial_scoring "
        " group by 1 order by 1")).mappings().all()
    print("\n  Historial mensual completo:")
    for r in resumen:
        print(f"    {r['periodo']}  {r['clientes']:>4} clientes  "
              f"$ {r['vencido']:>16,.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Guarda la foto de bronze.cuentas_corrientes_scoring en las "
                    "tablas de historial, para que el grafico de evolucion del "
                    "saldo vencido tenga el mes en curso.",
        epilog="La foto se fecha con el fecha_carga de scoring, no con hoy: "
               "correrlo dos veces el mismo dia reescribe la misma fila.",
    )
    parser.add_argument("--ver", action="store_true",
                        help="Dice que haria y no guarda nada")
    args = parser.parse_args()

    sacar_foto(solo_ver=args.ver)
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
