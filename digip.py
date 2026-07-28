import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

URL_BASE = os.getenv("DIGIP_URL_BASE", "https://api.v2.digipwms.com/api/v2/")
API_KEY = os.getenv("DIGIP_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}

PAUSA = 1.0


def llamar_digip(endpoint, params=None):
    """Llama a un endpoint de DIGIP."""
    url = URL_BASE + endpoint
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code == 204:        # No Content = sin datos
        return []
    r.raise_for_status()
    time.sleep(PAUSA)
    return r.json()


def llamar_digip_paginado(endpoint, params=None):
    """Para endpoints que usan Page/PerPage. Pide pagina por pagina."""
    if params is None:
        params = {}
    params["PerPage"] = 2000        # maximo permitido
    todos = []
    pagina = 1
    while True:
        params["Page"] = pagina
        datos = llamar_digip(endpoint, params)
        if not datos:
            break
        todos.extend(datos)
        print(f"  Pagina {pagina}: {len(datos)} registros (acumulado: {len(todos)})")
        if len(datos) < 2000:       # ultima pagina (vino incompleta)
            break
        pagina += 1
        time.sleep(0.3)
    return todos


def guardar_en_bd(df, tabla, modo="replace"):
    if df.empty:
        print(f"  (sin datos para {tabla})")
        return
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

def extraer_stock_tipo():
    """Stock resumido por articulo (disponible, bloqueado, vencidos, etc).
       Este es el ideal para el tablero."""
    print("\n=== STOCK por TIPO (resumen por articulo) ===")
    datos = llamar_digip("Stock/Tipo")
    # El stock viene anidado: aplanamos para tener columnas planas
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} articulos con stock")
    guardar_en_bd(df, "digip_stock", modo="replace")

def extraer_stock_detalle():
    """Stock detallado por ubicacion/lote, CON fechas de vencimiento.
       Es lo que sirve para ver que articulos estan por vencer."""
    print("\n=== STOCK DETALLE (con vencimientos) ===")
    datos = llamar_digip("Stock/Detalle")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} registros de stock detallado")
    # Cuantos tienen fecha de vencimiento cargada
    if "fechaVencimiento" in df.columns:
        con_venc = df["fechaVencimiento"].notna().sum()
        print(f"  De esos, {con_venc} tienen fecha de vencimiento")
    guardar_en_bd(df, "digip_stock_detalle", modo="replace")


def extraer_articulos_digip():
    """Catalogo de articulos en DIGIP (por si sirve para mapear)."""
    print("\n=== ARTICULOS DIGIP ===")
    datos = llamar_digip_paginado("Articulos")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} articulos DIGIP")
    guardar_en_bd(df, "digip_articulos", modo="replace")


# ============================================================
#  EJECUCION
# ============================================================

if __name__ == "__main__":
    print("URL base:", URL_BASE)
    print("API Key cargada:", "SI" if API_KEY else "NO")

    extraer_stock_tipo()
    extraer_stock_detalle()
    # extraer_articulos_digip()   # activar despues si lo necesitas

    print("\n=== LISTO. Revisa la tabla digip_stock en Supabase. ===")