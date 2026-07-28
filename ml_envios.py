import os
import json
import time
import requests
import pandas as pd
from datetime import date
from dotenv import load_dotenv
from sqlalchemy import create_engine
from mercadolibre import renovar_access_token

load_dotenv()

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

MI_USER_ID = int(os.getenv("ML_USER_ID"))
FECHA_CORTE = date(2026, 5, 6)


def to_date(valor):
    ts = pd.to_datetime(valor, errors="coerce", utc=True)
    return None if pd.isna(ts) else ts.date()


def extraer_envios():
    print("=== Extrayendo costos de envio de ML (retomando) ===")

    # 1) Ordenes con envio desde el corte
    ordenes = pd.read_sql("""
        SELECT id, "shipping.id" AS shipping_id, date_created
        FROM bronze.ml_ventas
        WHERE status = 'paid' AND "shipping.id" IS NOT NULL
    """, engine)
    ordenes["fecha"] = ordenes["date_created"].apply(to_date)
    ordenes = ordenes[ordenes["fecha"].notna() & (ordenes["fecha"] >= FECHA_CORTE)]
    ordenes = ordenes.drop_duplicates(subset=["shipping_id"])

    # 2) Ver cuales YA tenemos guardados (para saltearlos)
    try:
        ya = pd.read_sql("SELECT shipping_id FROM bronze.ml_envios", engine)
        ya_hechos = set(ya["shipping_id"].astype(str))
        print(f"Ya guardados: {len(ya_hechos)}")
        # Cargamos los resultados existentes para no perderlos
        resultados = pd.read_sql("SELECT * FROM bronze.ml_envios", engine).to_dict("records")
    except Exception:
        ya_hechos = set()
        resultados = []

    # 3) Filtrar los que faltan
    ordenes["shipping_id_str"] = ordenes["shipping_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    faltan = ordenes[~ordenes["shipping_id_str"].isin(ya_hechos)]
    print(f"Total envios: {len(ordenes)} | Faltan: {len(faltan)}")

    if len(faltan) == 0:
        print("Ya estan todos! Nada que hacer.")
        return

    token = renovar_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    errores = 0

    for i, (_, r) in enumerate(faltan.iterrows(), 1):
        ship_id = r["shipping_id_str"]
        try:
            url = f"https://api.mercadolibre.com/shipments/{ship_id}/costs"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 401:
                token = renovar_access_token()
                headers = {"Authorization": f"Bearer {token}"}
                resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                costo_envio = 0
                for s in data.get("senders", []):
                    if s.get("user_id") == MI_USER_ID:
                        costo_envio = s.get("cost", 0) or 0
                        break
                resultados.append({
                    "shipping_id": ship_id,
                    "order_id": str(r["id"]),
                    "costo_envio": costo_envio,
                })
            else:
                errores += 1
        except Exception:
            errores += 1

        if i % 100 == 0:
            print(f"  {i}/{len(faltan)} nuevos procesados (total {len(resultados)})...")
            pd.DataFrame(resultados).to_sql("ml_envios", engine, schema="bronze",
                                            if_exists="replace", index=False)
        time.sleep(0.1)

    final = pd.DataFrame(resultados)
    final.to_sql("ml_envios", engine, schema="bronze", if_exists="replace", index=False)
    print(f"\nGuardado: bronze.ml_envios ({len(final)} envios totales)")
    print(f"Errores en esta corrida: {errores}")
    con_costo = (final["costo_envio"] > 0).sum()
    print(f"Envios con costo a tu cargo: {con_costo}")
    print(f"Costo total de envios: ${final['costo_envio'].sum():,.2f}")


if __name__ == "__main__":
    extraer_envios()
    print("\n=== LISTO ===")