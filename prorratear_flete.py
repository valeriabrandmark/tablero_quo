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
        SELECT nro_orden, sku, unidad, costo_unitario, cantidad, precio_neto
        FROM gold.fact_ventas
        WHERE canal = 'Mayorista'
          AND unidad IN ('Quo', 'Noa')
          AND nro_orden IS NOT NULL
    """, engine)
    print(f"  Lineas de distri: {len(fact)}")

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
          AND neto_cobrado_transporte <> 0
    """, engine)
    flete_map = dict(zip(fletes["clave_fila"], fletes["neto_cobrado_transporte"]))
    print(f"  Claves con flete real cargado: {len(flete_map)}")

    # --- 4) Unir fact con preparaciones (nro_orden = pedido_codigo) ---
    fact["nro_orden_str"] = fact["nro_orden"].astype("Int64").astype(str)
    merged = fact.merge(
        prep, left_on="nro_orden_str", right_on="pedido_codigo", how="left"
    )

    # Si un pedido se partio en varias preparaciones (envio parcial), DIGIP no nos dice
    # que parte de cada SKU fue en cada preparacion. Repartimos la linea por IGUAL entre
    # las N preparaciones asociadas, prorrateamos cada porcion, y al final se vuelve a
    # unir en una sola fila por linea (ver paso 7).
    cant_preparaciones = merged.groupby(["nro_orden", "sku"])["preparacion_id"].transform(
        lambda s: s.notna().sum() if s.notna().sum() > 0 else 1
    )
    dup = merged.groupby(["nro_orden", "sku"]).size()
    duplicados = dup[dup > 1]
    if len(duplicados) > 0:
        print(f"  ATENCION: {len(duplicados)} lineas con envio partido (mas de una preparacion). "
              f"Se reparten por igual entre preparaciones y se reunen en una sola fila al final.")

    # --- 5) Calcular clave_fila por porcion (misma logica que la vista) ---
    merged["clave_fila"] = merged.apply(calcular_clave_fila, axis=1)
    merged["costo_neto_porcion"] = (
        merged["costo_unitario"].fillna(0) * merged["cantidad"].fillna(0) / cant_preparaciones
    )
    merged["flete_total_clave"] = merged["clave_fila"].map(flete_map)

    # Costo neto total de la preparacion (sumando todas las porciones que caen ahi),
    # para prorratear proporcionalmente
    merged["costo_neto_total_clave"] = merged.groupby("clave_fila")["costo_neto_porcion"].transform("sum")

    # --- 6) Prorrateo por porcion (real donde hay dato, estimado 5% donde no) ---
    porciones = []
    for _, row in merged.iterrows():
        clave = row["clave_fila"]
        flete_total = row["flete_total_clave"]
        costo_total_clave = row["costo_neto_total_clave"]

        porcion_real = (
            clave is not None
            and pd.notna(flete_total)
            and flete_total != 0
            and costo_total_clave and costo_total_clave > 0
        )

        if porcion_real:
            flete_porcion = flete_total * (row["costo_neto_porcion"] / costo_total_clave)
        else:
            flete_porcion = None  # se recalcula como estimado al reunir, si hace falta

        porciones.append({
            "nro_orden": row["nro_orden"],
            "sku": row["sku"],
            "cantidad": row["cantidad"],
            "precio_neto": row["precio_neto"],
            "clave_fila": clave,
            "flete_porcion_real": flete_porcion,
            "porcion_real": porcion_real,
        })

    df_porciones = pd.DataFrame(porciones)

    # --- 7) Reunir porciones en UNA fila por linea (nro_orden + sku) ---
    resultado = []
    for (nro_orden, sku), grupo in df_porciones.groupby(["nro_orden", "sku"], dropna=False):
        todas_reales = grupo["porcion_real"].all()
        if todas_reales:
            flete_linea = grupo["flete_porcion_real"].sum()
            tiene_real = True
        else:
            # si alguna porcion no tuvo flete real, la linea entera cae a estimado
            # (evita mezclar "un poco real + un poco estimado" en la misma linea)
            cant_total = grupo["cantidad"].iloc[0]
            precio_neto = grupo["precio_neto"].iloc[0]
            flete_linea = (precio_neto or 0) * (cant_total or 0) * 0.05
            tiene_real = False

        clave_repr = ", ".join(sorted(set(c for c in grupo["clave_fila"] if c is not None))) or None
        resultado.append({
            "nro_orden": nro_orden,
            "sku": sku,
            "clave_fila": clave_repr,
            "flete_prorrateado": round(float(flete_linea), 2),
            "tiene_flete_real": bool(tiene_real),
        })

    df_resultado = pd.DataFrame(resultado)
    print(f"\nTotal lineas procesadas: {len(df_resultado)}")
    print(f"  Con flete real prorrateado: {int(df_resultado['tiene_flete_real'].sum())}")
    print(f"  Con estimacion 5%: {int((~df_resultado['tiene_flete_real']).sum())}")

    # --- 8) Guardar en gold.fact_ventas_flete (tabla aparte, no toca fact_ventas) ---
    with engine.begin() as con:
        con.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS gold.fact_ventas_flete (
                nro_orden text,
                sku text,
                clave_fila text,
                flete_prorrateado numeric,
                tiene_flete_real boolean,
                fecha_calculo timestamptz DEFAULT now()
            );
        """)

    # TRUNCATE + APPEND (mismo patron que tus otros scripts, no rompe vistas si algo la referencia)
    with engine.begin() as con:
        try:
            con.exec_driver_sql("TRUNCATE TABLE gold.fact_ventas_flete;")
        except Exception as e:
            print(f"  No se pudo truncar (¿tabla recien creada?): {e}")

    df_resultado.to_sql(
        "fact_ventas_flete", engine, schema="gold", if_exists="append", index=False
    )
    print("Guardado: gold.fact_ventas_flete")


if __name__ == "__main__":
    construir_fact_ventas_flete()
    print("\n=== LISTO ===")