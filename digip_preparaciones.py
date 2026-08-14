import os
import json
import time
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

BASE = os.getenv("DIGIP_URL_BASE")
API_KEY = os.getenv("DIGIP_API_KEY")
headers = {"X-API-Key": API_KEY}

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
    connect_args={"client_encoding": "utf8"}
)

FECHA_CORTE = date(2026, 5, 6)
WINDOW_DAYS = 7


def extraer_preparaciones():
    hoy = date.today()
    cutoff = max(FECHA_CORTE, hoy - timedelta(days=WINDOW_DAYS))
    print(f"=== Extrayendo preparaciones (ventana: {cutoff.isoformat()} a {hoy.isoformat()}) ===")

    # ANTES: se consultaba TODO bronze.digip_pedidos completo (una llamada a la API
    # por pedido) -> por eso cada corrida tardaba mas que la anterior, para siempre.
    # AHORA: solo los pedidos de la distri (codigo numerico) DENTRO de la ventana movil.
    pedidos = pd.read_sql(
        """
        SELECT codigo FROM bronze.digip_pedidos
        WHERE codigo ~ '^[0-9]+$'
          AND "fecha"::date >= %(cutoff)s
        """,
        engine, params={"cutoff": cutoff}
    )
    codigos = pedidos["codigo"].astype(str).tolist()
    print(f"Pedidos a consultar (dentro de la ventana): {len(codigos)}")

    filas = []
    errores = 0

    for i, cod in enumerate(codigos, 1):
        try:
            resp = requests.get(f"{BASE}Preparaciones/{cod}", headers=headers, timeout=30)
            if resp.status_code != 200:
                errores += 1
                continue
            prep = resp.json()
            prep_id = prep.get("id")

            transporte = prep.get("despachoDescripcion") or prep.get("despachoCodigo")

            contenedores = prep.get("contenedores") or []
            bultos = sum((c.get("cantidadBulto") or 0) for c in contenedores)

            items = prep.get("items") or []
            volumen_total = sum((it.get("volumen") or 0) for it in items)
            peso_gramos = sum((it.get("peso") or 0) for it in items)
            peso_kg = round(peso_gramos / 1000.0, 3)

            tipo = "Bultos"
            for c in contenedores:
                desc = json.dumps(c, ensure_ascii=False).lower()
                if "pallet" in desc or "pale" in desc:
                    tipo = "Pallets"
                    break

            pedidos_en_prep = [p.get("codigo") for p in (prep.get("pedidos") or [])]

            for ped_cod in pedidos_en_prep:
                filas.append({
                    "preparacion_id": prep_id,
                    "pedido_codigo": str(ped_cod),
                    "transporte": transporte,
                    "bultos_preparacion": bultos,
                    "volumen_preparacion": volumen_total,
                    "kg_preparacion": peso_kg,
                    "tipo_preparacion": tipo,
                    "cant_pedidos_en_prep": len(pedidos_en_prep),
                })

        except Exception:
            errores += 1

        if i % 25 == 0:
            print(f"  {i}/{len(codigos)} pedidos consultados...")
        time.sleep(0.1)

    df = pd.DataFrame(filas).drop_duplicates(subset=["preparacion_id", "pedido_codigo"])
    print(f"\nFilas (preparacion-pedido): {len(df)}")
    if len(df) > 0:
        print(f"Preparaciones unicas: {df['preparacion_id'].nunique()}")
    print(f"Errores: {errores}")

    if len(df) > 0:
        consolidadas = df.groupby("preparacion_id")["pedido_codigo"].nunique()
        print(f"Preparaciones consolidadas: {(consolidadas > 1).sum()}")
        print("\nEjemplo de datos (primeras 3 filas):")
        print(df[["preparacion_id", "pedido_codigo", "transporte", "bultos_preparacion",
                  "volumen_preparacion", "kg_preparacion", "tipo_preparacion"]].head(3).to_string())

    # Reemplazo SOLO de las preparaciones de los pedidos que estan dentro de la ventana
    # actual. NOTA: digip_preparaciones no tiene columna de fecha propia -- se identifica
    # que filas pertenecen a la ventana por su pedido_codigo (los mismos que se acaban
    # de volver a consultar), no por fecha directamente.
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")

    if codigos:
        try:
            with engine.begin() as con:
                resultado = con.exec_driver_sql(
                    "DELETE FROM bronze.digip_preparaciones WHERE pedido_codigo = ANY(%(codigos)s)",
                    {"codigos": codigos}
                )
                print(f"  Filas borradas dentro de la ventana (se van a reemplazar): {resultado.rowcount}")
        except Exception:
            print("  (tabla bronze.digip_preparaciones no existe todavia, se va a crear)")

    if df.empty:
        print("  (sin preparaciones nuevas en esta ventana)")
        return

    df.to_sql("digip_preparaciones", engine, schema="bronze", if_exists="append", index=False)
    print("\nGuardado (ventana) en bronze.digip_preparaciones")


if __name__ == "__main__":
    extraer_preparaciones()
    print("\n=== LISTO ===")