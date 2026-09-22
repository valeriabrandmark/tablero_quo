"""Revisa que lo que se cargo tenga sentido. Corre al final de cada corrida.

============================================================================
 POR QUE EXISTE
============================================================================

Los dos incidentes de datos que tuvo este pipeline tienen la misma forma: NO
DIERON ERROR. El paso reporto OK, el tablero mostro numeros, y el problema
se descubrio semanas despues porque una cuenta hecha a mano no cerraba.

  * 31 lineas duplicadas de SIGMA el 14/08 -> se vieron el 22/09, mes y medio
    despues, porque el sell out del SKU PR02007 daba 48 unidades y eran 36.

  * 622 minutos sin una sola venta de ML el 06/08 -> se vieron el 21/09, seis
    semanas despues, porque faltaba una venta puntual que alguien busco.

Todo lo demas que se blindo --que el guardado explote en vez de guardar a
medias, los indices unicos, la ventana que se estira sola-- ataca las causas
conocidas. Esto ataca el problema de fondo, que es otro: **que nadie estaba
mirando**. Una carga rara puede aparecer por un camino que todavia no se nos
ocurrio, y lo unico que hay que garantizar es que no pase seis semanas
invisible.

============================================================================
 QUE SE MIRA, Y CON QUE CRITERIO
============================================================================

1. DUPLICADOS por clave natural. Esto FALLA la corrida.

   Con los indices unicos puestos ya no deberia poder pasar -- justamente por
   eso, si pasa es que alguien borro un indice o que hay una tabla nueva sin
   el. Cero falsos positivos posibles: dos filas con la misma clave nunca son
   correctas.

2. HUECOS en las ventas de ML. Esto AVISA, no falla.

   Mercado Libre vende las 24 horas, asi que un rato largo sin una sola venta
   es sospechoso. Medido sobre 134 dias de historia, el hueco legitimo mas
   grande fue de 285 minutos y cayo de madrugada; los seis mas grandes, todos
   entre las 02 y las 08. El del 06/08 fue de 622 minutos en pleno dia habil.

   Con el umbral en 6 horas: cero falsos positivos en toda la historia, y el
   del 06/08 habria saltado en la corrida siguiente.

   AVISA Y NO FALLA a proposito. Un feriado largo o una caida de ML podrian
   dar un hueco real y legitimo, y poner el pipeline en rojo por eso enseña a
   ignorar el rojo. Lo que se necesita es que se VEA en el log, no que trabe
   la carga.

    python auditoria.py
    python auditoria.py --dias 30     # mira mas atras que la ventana
"""

import argparse
import sys

from conexion import crear_engine

# Cuantos dias hacia atras se revisa por defecto. Un poco mas que la ventana
# movil de 7: asi el dia que se cae del borde todavia se mira una vez mas.
DIAS_POR_DEFECTO = 10

# A partir de cuantos minutos sin una sola venta de ML se avisa. Ver arriba:
# el hueco legitimo mas grande de la historia fue de 285 minutos.
HUECO_MINUTOS = 6 * 60

# Las claves naturales de cada tabla. Si aparece una tabla nueva con ventana
# movil, va aca -- y si no tiene clave natural, ese es el problema a resolver
# antes que este.
CLAVES = {
    "sigma_ventas": ("id", "item"),
    "sigma_compras": ("id",),
    "ml_ventas": ("id",),
}


def _duplicados(con, tabla, clave):
    """Cuantas filas sobran por clave repetida."""
    columnas = ", ".join(f'"{c}"' for c in clave)
    return con.exec_driver_sql(
        f"""select coalesce(sum(sobran), 0)::int from (
                select count(*) - 1 as sobran
                  from bronze."{tabla}" group by {columnas} having count(*) > 1
            ) d"""
    ).scalar()


def _hueco_mas_grande(con, dias):
    """(minutos, desde, hasta) del rato mas largo sin ventas de ML."""
    fila = con.exec_driver_sql(
        """
        with t as (
          select date_created::timestamptz as ts,
                 lag(date_created::timestamptz) over (order by date_created::timestamptz) as prev
          from bronze.ml_ventas
          where date_created >= (now() - make_interval(days => %(dias)s))::text
        )
        select round(extract(epoch from (ts - prev)) / 60)::int as minutos,
               to_char(prev at time zone 'America/Argentina/Buenos_Aires', 'DD/MM HH24:MI') as desde,
               to_char(ts   at time zone 'America/Argentina/Buenos_Aires', 'DD/MM HH24:MI') as hasta
          from t where prev is not null
         order by ts - prev desc limit 1
        """,
        {"dias": dias},
    ).fetchone()
    return fila


def revisar(dias=DIAS_POR_DEFECTO):
    """Devuelve (fallas, avisos), las dos listas de texto."""
    fallas, avisos = [], []
    engine = crear_engine()

    with engine.begin() as con:
        # --- 1. Duplicados ---------------------------------------------------
        for tabla, clave in CLAVES.items():
            try:
                sobran = _duplicados(con, tabla, clave)
            except Exception as e:
                # Una tabla que todavia no existe no es una falla de datos.
                print(f"  (no se pudo revisar bronze.{tabla}: {str(e)[:80]})")
                continue
            etiqueta = "+".join(clave)
            if sobran:
                fallas.append(
                    f"bronze.{tabla}: {sobran} fila(s) de mas por {etiqueta} repetida"
                )
            else:
                print(f"  OK  bronze.{tabla} sin duplicados por {etiqueta}")

        # --- 2. Huecos en ML -------------------------------------------------
        try:
            hueco = _hueco_mas_grande(con, dias)
        except Exception as e:
            print(f"  (no se pudo revisar los huecos de ml_ventas: {str(e)[:80]})")
            hueco = None

        if hueco:
            minutos, desde, hasta = hueco
            if minutos >= HUECO_MINUTOS:
                horas = minutos / 60
                avisos.append(
                    f"ml_ventas: {horas:.1f} h sin una sola venta, "
                    f"entre el {desde} y el {hasta}. "
                    f"De madrugada puede ser normal; en horario comercial, no."
                )
            else:
                print(
                    f"  OK  ml_ventas sin huecos grandes "
                    f"(el mayor: {minutos} min, umbral {HUECO_MINUTOS})"
                )

    return fallas, avisos


def main():
    parser = argparse.ArgumentParser(
        description="Revisa que lo cargado en bronze tenga sentido."
    )
    parser.add_argument(
        "--dias", type=int, default=DIAS_POR_DEFECTO,
        help=f"Cuantos dias hacia atras revisar (por defecto {DIAS_POR_DEFECTO})",
    )
    args = parser.parse_args()

    print(f"\n=== AUDITORIA DE LA CARGA (ultimos {args.dias} dias) ===")
    fallas, avisos = revisar(args.dias)

    for a in avisos:
        print(f"\n  AVISO: {a}")

    if fallas:
        print()
        for f in fallas:
            print(f"  FALLA: {f}")
        print("\n  Un duplicado no se arregla solo: hay que borrarlo a mano y")
        print("  averiguar por donde entro. Ver claves_unicas_bronze.sql.")
        sys.exit(1)

    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
