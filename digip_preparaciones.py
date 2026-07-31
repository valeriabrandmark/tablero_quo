import os
import json
import time
import requests
import pandas as pd
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


def extraer_preparaciones():
    print("=== Extrayendo preparaciones (con volumen y peso) ===")

    # Pedidos de la distri (codigo numerico) que ya tenemos
    pedidos = pd.read_sql("""
        SELECT codigo FROM bronze.digip_pedidos
        WHERE codigo ~ '^[0-9]+$'
    """, engine)
    codigos = pedidos["codigo"].astype(str).tolist()
    print(f"Pedidos a consultar: {len(codigos)}")

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

            # Transporte (una vez por preparacion)
            transporte = prep.get("despachoDescripcion") or prep.get("despachoCodigo")

            # Bultos: de los contenedores
            contenedores = prep.get("contenedores") or []
            bultos = sum((c.get("cantidadBulto") or 0) for c in contenedores)

            # Volumen y peso: sumar de todos los items
            # (peso viene en gramos -> lo pasamos a kg dividiendo /1000)
            items = prep.get("items") or []
            volumen_total = sum((it.get("volumen") or 0) for it in items)
            peso_gramos = sum((it.get("peso") or 0) for it in items)
            peso_kg = round(peso_gramos / 1000.0, 3)

            # Tipo (bultos/pallets): heuristica.
            # Si hay contenedores con info de pallet, lo marcamos; si no, "Bultos".
            # (ajustable cuando confirmemos el campo exacto de DIGIP)
            tipo = "Bultos"
            for c in contenedores:
                desc = json.dumps(c, ensure_ascii=False).lower()
                if "pallet" in desc or "pale" in desc:
                    tipo = "Pallets"
                    break

            # Lista de pedidos que van en esta preparacion (consolidacion)
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

        except Exception as e:
            errores += 1

        if i % 25 == 0:
            print(f"  {i}/{len(codigos)} pedidos consultados...")
        time.sleep(0.1)

    df = pd.DataFrame(filas).drop_duplicates(subset=["preparacion_id", "pedido_codigo"])
    print(f"\nFilas (preparacion-pedido): {len(df)}")
    print(f"Preparaciones unicas: {df['preparacion_id'].nunique()}")
    print(f"Errores: {errores}")

    consolidadas = df.groupby("preparacion_id")["pedido_codigo"].nunique()
    print(f"Preparaciones consolidadas: {(consolidadas > 1).sum()}")

    # Muestra de control
    print("\nEjemplo de datos (primeras 3 filas):")
    print(df[["preparacion_id", "pedido_codigo", "transporte", "bultos_preparacion",
              "volumen_preparacion", "kg_preparacion", "tipo_preparacion"]].head(3).to_string())

    # TRUNCATE + APPEND para no romper las vistas
   # TRUNCATE + APPEND para no romper las vistas
    try:
        with engine.begin() as con:
            con.exec_driver_sql('TRUNCATE TABLE bronze.digip_preparaciones;')
        df.to_sql("digip_preparaciones", engine, schema="bronze", if_exists="append", index=False)
        print("\nGuardado (truncate+append) en bronze.digip_preparaciones")
    except Exception as e:
        print(f"\n(truncate falló: {str(e)[:80]} -> creando tabla)")
        df.to_sql("digip_preparaciones", engine, schema="bronze", if_exists="replace", index=False)
        print("Guardado (replace) en bronze.digip_preparaciones")


if __name__ == "__main__":
    extraer_preparaciones()
    print("\n=== LISTO ===")
