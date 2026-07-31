import os
import json
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


def extraer_pedidos():
    print("=== Extrayendo pedidos RemitidoExterno de junio 2026 ===")
    todos = []
    page = 1
    while True:
        params = {
            "PedidoEstado": "RemitidoExterno",
            "FechaPedidoDesde": "2026-06-01T00:00:00",
            "FechaPedidoHasta": "2026-07-31T23:59:59",
            "Page": page,
            "PerPage": 500,
            "OrderCriteria": "CodigoPedido",
        }
        resp = requests.get(f"{BASE}Pedidos", headers=headers, params=params, timeout=60)
        if resp.status_code == 204:
            break
        if resp.status_code != 200:
            print(f"  Error pagina {page}: {resp.status_code} {resp.text[:200]}")
            break
        pedidos = resp.json()
        if not pedidos:
            break
        print(f"  Pagina {page}: {len(pedidos)} pedidos")

        for p in pedidos:
            cu = p.get("clienteUbicacion") or {}
            cli = cu.get("cliente") or {}
            todos.append({
                "codigo": p.get("codigo"),
                "fecha": p.get("fecha"),
                "estado": p.get("estado"),
                "importe": p.get("importe"),
                "cliente_codigo": cli.get("codigo"),
                "cliente_nombre": cli.get("descripcion"),
                "provincia": cu.get("provincia"),
                "localidad": cu.get("localidad"),
                "direccion": cu.get("direccion"),
                "codigo_despacho": p.get("codigoDespacho"),
                "servicio_envio": p.get("servicioDeEnvioTipo"),
                "orden_preparacion": p.get("ordenPreparacion"),
            })

        if len(pedidos) < 500:
            break
        page += 1

    df = pd.DataFrame(todos)
    print(f"\nTotal pedidos: {len(df)}")
    if len(df) > 0:
        # Solo los de la distri (codigo numerico) para verificar
        distri = df[df["codigo"].astype(str).str.isdigit()]
        print(f"  De distribuidora (codigo numerico): {len(distri)}")
        print(f"  Provincias: {distri['provincia'].value_counts().to_dict()}")
        # TRUNCATE + APPEND para no romper las vistas
    # TRUNCATE + APPEND para no romper las vistas
    try:
        with engine.begin() as con:
            con.exec_driver_sql('TRUNCATE TABLE bronze.digip_pedidos;')
        df.to_sql("digip_pedidos", engine, schema="bronze", if_exists="append", index=False)
        print("Guardado (truncate+append) en bronze.digip_pedidos")
    except Exception as e:
        print(f"(truncate falló: {str(e)[:80]} -> creando tabla)")
        df.to_sql("digip_pedidos", engine, schema="bronze", if_exists="replace", index=False)
        print("Guardado (replace) en bronze.digip_pedidos")


if __name__ == "__main__":
    extraer_pedidos()
    print("\n=== LISTO ===")