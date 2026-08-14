import os
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

# Fecha de corte ABSOLUTA (piso historico, nunca se pide nada anterior a esto)
FECHA_CORTE = date(2026, 5, 6)

# Ventana movil: cada corrida solo re-pide (y reemplaza) los ultimos N dias.
# El resto del historial en bronze.digip_pedidos queda intacto.
WINDOW_DAYS = 7


def extraer_pedidos():
    hoy = date.today()
    cutoff = max(FECHA_CORTE, hoy - timedelta(days=WINDOW_DAYS))
    print(f"=== Extrayendo pedidos RemitidoExterno (ventana: {cutoff.isoformat()} a {hoy.isoformat()}) ===")

    todos = []
    page = 1
    while True:
        params = {
            "PedidoEstado": "RemitidoExterno",
            "FechaPedidoDesde": f"{cutoff.isoformat()}T00:00:00",
            "FechaPedidoHasta": f"{hoy.isoformat()}T23:59:59",
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
    print(f"\nTotal pedidos en la ventana: {len(df)}")
    if len(df) > 0:
        distri = df[df["codigo"].astype(str).str.isdigit()]
        print(f"  De distribuidora (codigo numerico): {len(distri)}")
        print(f"  Provincias: {distri['provincia'].value_counts().to_dict()}")

    # Reemplazo SOLO de la ventana movil (deja el resto de la historia intacta)
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")

    try:
        with engine.begin() as con:
            resultado = con.exec_driver_sql(
                'DELETE FROM bronze.digip_pedidos WHERE "fecha"::date >= %(cutoff)s',
                {"cutoff": cutoff}
            )
            print(f"  Filas borradas dentro de la ventana (se van a reemplazar): {resultado.rowcount}")
    except Exception:
        print("  (tabla bronze.digip_pedidos no existe todavia, se va a crear)")

    if df.empty:
        print("  (sin pedidos nuevos en esta ventana)")
        return

    df.to_sql("digip_pedidos", engine, schema="bronze", if_exists="append", index=False)
    print("Guardado (ventana) en bronze.digip_pedidos")


if __name__ == "__main__":
    extraer_pedidos()
    print("\n=== LISTO ===")