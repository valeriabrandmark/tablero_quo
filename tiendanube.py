import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

STORE_ID = os.getenv("TN_STORE_ID")
TOKEN = os.getenv("TN_TOKEN", "")
USER_AGENT = os.getenv("TN_USER_AGENT", "TableroQuo (correo@ejemplo.com)")

URL_BASE = f"https://api.tiendanube.com/v1/{STORE_ID}/"
HEADERS = {
    "Authentication": f"bearer {TOKEN}",
    "User-Agent": USER_AGENT,
}

PAUSA = 0.6


def llamar_tn_paginado(endpoint, params=None):
    """Trae todos los registros de un endpoint paginando.
       Tienda Nube usa page / per_page (max 200) y avisa el fin con lista vacia."""
    if params is None:
        params = {}
    params["per_page"] = 200
    todos = []
    pagina = 1
    while True:
        params["page"] = pagina
        r = requests.get(URL_BASE + endpoint, headers=HEADERS, params=params)
        if r.status_code == 429:          # demasiadas peticiones
            print("  429: esperando 2s...")
            time.sleep(2)
            continue
        r.raise_for_status()
        datos = r.json()
        if not datos:                     # lista vacia = no hay mas
            break
        todos.extend(datos)
        print(f"  Pagina {pagina}: {len(datos)} registros (acumulado: {len(todos)})")
        if len(datos) < 200:              # ultima pagina
            break
        pagina += 1
        time.sleep(PAUSA)
    return todos


def guardar_en_bd(df, tabla, modo="replace"):
    if df.empty:
        print(f"  (sin datos para {tabla})")
        return
    import json as _json
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
            df[col] = df[col].apply(
                lambda x: _json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x
            )
    engine = create_engine(
        f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")
    df.to_sql(tabla, engine, schema="bronze", if_exists=modo, index=False)
    print(f"  Guardado ({modo}): bronze.{tabla} ({len(df)} filas)")


# ============================================================
#  EXTRACCIONES
# ============================================================

def extraer_pedidos():
    """Pedidos = ventas del ecommerce Tienda Nube. Desde inicio de 2026."""
    print("\n=== PEDIDOS TIENDA NUBE (ventas) ===")
    datos = llamar_tn_paginado("orders", {"created_at_min": "2026-01-01T00:00:00+00:00"})
    df = pd.json_normalize(datos)
    print(f"  Total: {len(df)} pedidos")
    guardar_en_bd(df, "tn_pedidos", modo="replace")

def extraer_pedidos_items():
    """Desglosa los pedidos: una fila por cada producto de cada pedido.
       Esto deja las ventas de Tienda Nube en formato analizable (como sigma_ventas)."""
    print("\n=== PEDIDOS ITEMS (una fila por producto vendido) ===")
    pedidos = llamar_tn_paginado("orders", {"created_at_min": "2026-01-01T00:00:00+00:00"})
    print(f"  {len(pedidos)} pedidos a desglosar")

    filas = []
    for pedido in pedidos:
        # Datos que queremos repetir en cada linea del pedido
        pedido_id = pedido.get("id")
        numero = pedido.get("number")
        fecha = pedido.get("created_at")
        estado = pedido.get("status")
        estado_pago = pedido.get("payment_status")
        total_pedido = pedido.get("total")
        # cliente puede venir anidado
        cliente = pedido.get("customer") or {}
        cliente_nombre = cliente.get("name")
        cliente_id = cliente.get("id")

        # La lista de productos del pedido
        for prod in pedido.get("products", []):
            filas.append({
                "pedido_id": pedido_id,
                "pedido_numero": numero,
                "fecha": fecha,
                "estado": estado,
                "estado_pago": estado_pago,
                "cliente_id": cliente_id,
                "cliente_nombre": cliente_nombre,
                "producto_id": prod.get("product_id"),
                "variant_id": prod.get("variant_id"),
                "sku": prod.get("sku"),
                "nombre": prod.get("name"),
                "cantidad": prod.get("quantity"),
                "precio": prod.get("price"),
                "costo": prod.get("cost"),
                "total_linea": prod.get("total"),
                "total_pedido": total_pedido,
            })

    df = pd.DataFrame(filas)
    print(f"  Total: {len(df)} lineas de producto (de {len(pedidos)} pedidos)")
    guardar_en_bd(df, "tn_pedidos_items", modo="replace")


def extraer_productos():
    print("\n=== PRODUCTOS TIENDA NUBE ===")
    datos = llamar_tn_paginado("products")
    df = pd.json_normalize(datos)
    print(f"  Total: {len(df)} productos")
    guardar_en_bd(df, "tn_productos", modo="replace")


def extraer_clientes():
    print("\n=== CLIENTES TIENDA NUBE ===")
    datos = llamar_tn_paginado("customers")
    df = pd.json_normalize(datos)
    print(f"  Total: {len(df)} clientes")
    guardar_en_bd(df, "tn_clientes", modo="replace")


# ============================================================
#  EJECUCION
# ============================================================

if __name__ == "__main__":
    print("URL base:", URL_BASE)
    print("Token cargado:", "SI" if TOKEN else "NO")

    # PRUEBA: empezamos solo por pedidos (las ventas)
    #extraer_pedidos()
    extraer_pedidos_items()
    #extraer_productos()   # activar despues
    #extraer_clientes()    # activar despues

    print("\n=== LISTO. Revisa las tablas tn_* en Supabase. ===")