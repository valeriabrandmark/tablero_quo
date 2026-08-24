import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)


def calcular_clave_fila(row):
    """Misma logica que gold.reporte_logistica: SEBASTIAN parte por Quo/Noa,
       el resto consolida por preparacion_id solo."""
    if pd.isna(row["preparacion_id"]):
        return None
    prep_id = int(row["preparacion_id"])
    transporte = (row["transporte"] or "").upper()
    if "SEBASTIAN" in transporte:
        sufijo = "Quo" if row["unidad"] == "Quo" else "Noa"
        return f"{prep_id}-{sufijo}"
    return str(prep_id)


def construir_fact_ventas_flete():
    print("=== Construyendo gold.fact_ventas_flete ===")

    # --- 1) Traer lineas de la distribuidora (Mayorista) ---
    print("Leyendo fact_ventas (Mayorista)...")
    fact = pd.read_sql("""
        SELECT nro_orden, sku, unidad, cantidad, precio_neto
        FROM gold.fact_ventas
        WHERE canal = 'Mayorista'
          AND unidad IN ('Quo', 'Noa')
          AND nro_orden IS NOT NULL
    """, engine)
    print(f"  Lineas de distri: {len(fact)}")

    # Identidad de la linea ANTES de cualquier merge. Con esto se puede saber
    # despues si dos renglones son la misma linea repetida por un join o dos
    # lineas de verdad. Ver el drop_duplicates del paso 4.
    fact["_fila_id"] = range(len(fact))

    # --- 1b) Traer volumetria por SKU (litros por unidad, cargados en Sigma) ---
    print("Leyendo volumetria (litrosUnitarios) de sigma_articulos...")
    vol = pd.read_sql('SELECT id AS sku, "litrosUnitarios" FROM bronze.sigma_articulos', engine)
    litros_por_sku = dict(zip(vol["sku"].astype(str), vol["litrosUnitarios"]))

    def litros_de(sku):
        v = litros_por_sku.get(str(sku))
        return float(v) if v is not None and v != 0 else None

    # --- 2) Traer preparaciones (para saber preparacion_id y transporte por pedido) ---
    print("Leyendo digip_preparaciones...")
    prep = pd.read_sql("""
        SELECT pedido_codigo, preparacion_id, transporte
        FROM bronze.digip_preparaciones
    """, engine)

    # --- 3) Traer fletes reales cargados ---
    print("Leyendo bronze.fletes...")
    fletes = pd.read_sql("""
        SELECT clave_fila, neto_cobrado_transporte
        FROM bronze.fletes
        WHERE neto_cobrado_transporte IS NOT NULL
    """, engine)
    flete_map = dict(zip(fletes["clave_fila"], fletes["neto_cobrado_transporte"]))
    print(f"  Claves con flete real cargado: {len(flete_map)}")

    # --- 4) Unir fact con preparaciones (nro_orden = pedido_codigo) ---
    fact["nro_orden_str"] = fact["nro_orden"].astype("Int64").astype(str)
    merged = fact.merge(
        prep, left_on="nro_orden_str", right_on="pedido_codigo", how="left"
    )

    # EL MERGE PUEDE MULTIPLICAR LAS LINEAS, Y ESO INFLA EL FLETE.
    #
    # `prep` tiene una fila por (preparacion_id, pedido_codigo). Si el mismo par
    # aparece dos veces, este merge devuelve la linea de venta DUPLICADA, y de
    # ahi para abajo todo la cuenta dos veces.
    #
    # No es hipotetico: el 22/08 quedaron 741 lineas con un flete estimado del
    # 50 % de la venta en vez del 5 % -- $ 8,53 M cargados contra $ 853 mil que
    # correspondian, $ 7,68 M de flete fantasma sobre $ 17 M de venta. El origen
    # fueron filas repetidas en bronze.digip_preparaciones, que a su vez salieron
    # del DELETE que se tragaba el timeout e insertaba igual (el mismo bug que
    # duplico 2.548 ordenes de ML el 21/08; arreglado en digip_preparaciones.py).
    #
    # Aquel arreglo evita que se vuelvan a generar duplicados. Este evita que un
    # duplicado, venga de donde venga, se convierta en plata mal contada.
    #
    # Se deduplica por (linea, preparacion): si una linea entra de verdad en dos
    # preparaciones distintas -- envio partido -- las dos filas se conservan.
    antes = len(merged)
    merged = merged.drop_duplicates(subset=["_fila_id", "preparacion_id"])
    if len(merged) < antes:
        print(f"  Aviso: {antes - len(merged)} filas repetidas por preparaciones "
              f"duplicadas. Se descartan (si no, el flete se contaria de mas).")

    # Aviso informativo: un mismo (nro_orden, sku) puede repetirse porque Sigma
    # cargo el mismo articulo en 2 renglones de factura (normal en el ERP).
    # Confirmado que cada pedido va a UNA sola preparacion (sin envios partidos),
    # asi que no hace falta repartir nada: cada linea prorratea normal y al final
    # se suman las que comparten nro_orden+sku en una sola fila (paso 7).
    dup = merged.groupby(["nro_orden", "sku"]).size()
    duplicados = dup[dup > 1]
    if len(duplicados) > 0:
        print(f"  Info: {len(duplicados)} combinaciones nro_orden+sku con mas de un renglon "
              f"de factura (normal). Se suman en una sola fila al final.")

    # --- 5) Calcular clave_fila por linea (misma logica que la vista) ---
    merged["clave_fila"] = merged.apply(calcular_clave_fila, axis=1)

    # Base de prorrateo: litros x cantidad (volumen real de la linea).
    # Si el SKU no tiene litrosUnitarios cargado (raro, ~0.3% de los casos),
    # usamos cantidad sola como respaldo para no perder la linea del calculo.
    def volumen_de_fila(row):
        litros = litros_de(row["sku"])
        if litros is not None:
            return litros * (row["cantidad"] or 0)
        return row["cantidad"] or 0  # fallback: sin dato de volumen, usar unidades

    merged["volumen_linea"] = merged.apply(volumen_de_fila, axis=1)
    merged["flete_total_clave"] = merged["clave_fila"].map(flete_map)

    # Volumen total de la preparacion, para prorratear proporcionalmente
    merged["volumen_total_clave"] = merged.groupby("clave_fila")["volumen_linea"].transform("sum")

    # --- 6) Prorrateo por linea (real donde hay dato, estimado 5% donde no) ---
    lineas = []
    for _, row in merged.iterrows():
        clave = row["clave_fila"]
        flete_total = row["flete_total_clave"]
        volumen_total_clave = row["volumen_total_clave"]

        linea_real = (
            pd.notna(clave)
            and pd.notna(flete_total)
            and volumen_total_clave and volumen_total_clave > 0
        )

        if linea_real:
            flete_linea_calc = flete_total * (row["volumen_linea"] / volumen_total_clave)
        else:
            flete_linea_calc = None  # se recalcula como estimado al reunir, si hace falta

        lineas.append({
            "_fila_id": row["_fila_id"],
            "nro_orden": row["nro_orden_str"],
            "sku": row["sku"],
            "cantidad": row["cantidad"],
            "precio_neto": row["precio_neto"],
            "clave_fila": clave if pd.notna(clave) else None,
            "flete_linea_real": flete_linea_calc,
            "linea_real": linea_real,
        })

    df_lineas = pd.DataFrame(lineas)

    # --- 7) Reunir en UNA fila por linea (nro_orden + sku) ---
    resultado = []
    for (nro_orden, sku), grupo in df_lineas.groupby(["nro_orden", "sku"], dropna=False):
        todas_reales = grupo["linea_real"].all()
        if todas_reales:
            flete_final = grupo["flete_linea_real"].sum()
            tiene_real = True
        else:
            # si algun renglon no tuvo flete real, la combinacion entera cae a estimado
            # (evita mezclar "un poco real + un poco estimado" en la misma linea)
            # EL 5 % VA SOBRE LA VENTA NETA DE LA LINEA, sumada renglon por
            # renglon. Antes era `precio_neto.iloc[0] * cantidad.sum()`, que
            # toma el precio del PRIMER renglon y lo multiplica por la cantidad
            # de TODOS. Eso da bien solo si todos los renglones valen lo mismo;
            # con un renglon bonificado a $ 0, o con la linea repetida por el
            # merge, se dispara.
            #
            # `drop_duplicates` por las dudas: si algo volviera a repetir la
            # linea, el 5 % se calcula igual una sola vez.
            unicas = grupo.drop_duplicates(subset=["_fila_id"])
            venta_linea = (
                unicas["precio_neto"].fillna(0) * unicas["cantidad"].fillna(0)
            ).sum()
            flete_final = venta_linea * 0.05
            tiene_real = False

        # Venta anulada: la factura y su nota de credito se cancelan y no queda
        # nada vendido. El flete tiene que ser 0, no importa por que camino se
        # haya calculado arriba. Si no, la linea queda con costo de transporte
        # sobre una venta de $ 0 y la rentabilidad se va a menos infinito.
        #
        # Las devoluciones PARCIALES no entran aca a proposito: ahi si quedaron
        # unidades vendidas, y como el prorrateo usa la cantidad con signo, el
        # flete que les toca ya sale proporcional a lo que quedo. Una devolucion
        # parcial tiene que mostrar rentabilidad baja, no cero.
        unidades_netas = (
            grupo.drop_duplicates(subset=["_fila_id"])["cantidad"].fillna(0).sum()
        )
        if unidades_netas == 0:
            flete_final = 0.0

        claves_validas = [c for c in grupo["clave_fila"] if pd.notna(c)]
        clave_repr = ", ".join(sorted(set(claves_validas))) if claves_validas else None
        resultado.append({
            "nro_orden": nro_orden,
            "sku": sku,
            "clave_fila": clave_repr,
            "flete_prorrateado": round(float(flete_final), 2),
            "tiene_flete_real": bool(tiene_real),
            # Cuantos renglones de fact_ventas se juntaron en esta fila. Es el
            # divisor que necesita el tablero: alla el join va por
            # (nro_orden, sku), asi que una linea facturada en dos comprobantes
            # trae el flete DOS veces y lo suma de mas. Con este numero cada
            # renglon se lleva su parte y el total cierra.
            "lineas_venta": int(grupo["_fila_id"].nunique()),
        })

    df_resultado = pd.DataFrame(resultado)
    print(f"\nTotal lineas procesadas: {len(df_resultado)}")
    print(f"  Con flete real prorrateado: {int(df_resultado['tiene_flete_real'].sum())}")
    print(f"  Con estimacion 5%: {int((~df_resultado['tiene_flete_real']).sum())}")
    # Control: la suma de los divisores tiene que dar los renglones leidos al
    # principio. Si no da, alguna linea quedo sin representar y el flete que
    # muestre el tablero va a estar corto.
    representados = int(df_resultado["lineas_venta"].sum())
    print(f"  Renglones de venta representados: {representados} de {len(fact)}")
    anuladas = int((df_resultado["flete_prorrateado"] == 0).sum())
    print(f"  Lineas con flete 0 (anuladas o sin costo): {anuladas}")
    if representados != len(fact):
        print(f"  OJO: faltan {len(fact) - representados} renglones sin flete asignado")

    # --- 8) Guardar en gold.fact_ventas_flete (tabla aparte, no toca fact_ventas) ---
    with engine.begin() as con:
        con.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS gold.fact_ventas_flete (
                nro_orden text,
                sku text,
                clave_fila text,
                flete_prorrateado numeric,
                tiene_flete_real boolean,
                lineas_venta integer,
                fecha_calculo timestamptz DEFAULT now()
            );
        """)
        # La tabla ya existe en produccion sin esta columna, y mas abajo se
        # escribe con to_sql(if_exists="append"), que no crea columnas: sin el
        # ALTER el INSERT falla.
        con.exec_driver_sql("""
            ALTER TABLE gold.fact_ventas_flete
            ADD COLUMN IF NOT EXISTS lineas_venta integer;
        """)

    # DELETE + APPEND en una sola transaccion (no `replace`, que hace DROP y
    # rompe si alguien crea una vista encima).
    #
    # Las dos juntas para que gold.fact_ventas_flete no quede vacia en el medio:
    # la lee el tablero de Logistica en vivo.
    with engine.begin() as con:
        try:
            con.exec_driver_sql("DELETE FROM gold.fact_ventas_flete;")
        except Exception as e:
            print(f"  No se pudo vaciar (¿tabla recien creada?): {e}")

        df_resultado.to_sql(
            "fact_ventas_flete", con, schema="gold", if_exists="append", index=False
        )
    print("Guardado: gold.fact_ventas_flete")


if __name__ == "__main__":
    construir_fact_ventas_flete()
    print("\n=== LISTO ===")