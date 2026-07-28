import os
import json
import time
import requests
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

ARCHIVO_TOKENS = "ml_tokens.json"
USER_ID = os.getenv("ML_USER_ID")
PAUSA = 0.5


def cargar_tokens():
    with open(ARCHIVO_TOKENS) as f:
        return json.load(f)


def guardar_tokens(tokens):
    with open(ARCHIVO_TOKENS, "w") as f:
        json.dump(tokens, f, indent=2)


def renovar_access_token():
    """Usa el refresh_token para obtener un access_token nuevo.
       OJO: ML devuelve un refresh_token NUEVO cada vez, hay que guardarlo."""
    tokens = cargar_tokens()
    r = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "client_id": os.getenv("ML_CLIENT_ID"),
            "client_secret": os.getenv("ML_CLIENT_SECRET"),
            "refresh_token": tokens["refresh_token"],
        },
    )
    r.raise_for_status()
    nuevos = r.json()
    guardar_tokens(nuevos)        # guardamos el refresh_token nuevo
    print("  Token renovado OK")
    return nuevos["access_token"]


def llamar_ml(endpoint, access_token, params=None):
    """Llama a la API de ML con el access_token."""
    url = "https://api.mercadolibre.com" + endpoint
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(url, headers=headers, params=params)
    if r.status_code == 401:      # token vencido: renovar y reintentar
        print("  401: renovando token...")
        access_token = renovar_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    time.sleep(PAUSA)
    return r.json()


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

def extraer_ventas_ml():
    """Ordenes de ML por QUINCENAS (para no topar el limite de offset 10.000)."""
    from datetime import date, timedelta
    print("\n=== VENTAS MERCADO LIBRE (por quincenas) ===")

    access_token = renovar_access_token()

    hoy = date.today()
    inicio = date(2026, 1, 1)
    todas = []

    # Generamos tramos de ~15 dias
    tramo_inicio = inicio
    while tramo_inicio <= hoy:
        tramo_fin = tramo_inicio + timedelta(days=14)   # 15 dias en total
        if tramo_fin > hoy:
            tramo_fin = hoy

        desde = f"{tramo_inicio.isoformat()}T00:00:00.000-00:00"
        hasta = f"{tramo_fin.isoformat()}T23:59:59.000-00:00"
        print(f"\n  --- {tramo_inicio} a {tramo_fin} ---")

        offset = 0
        limit = 50
        tramo_ordenes = []
        while True:
            datos = llamar_ml(
                "/orders/search",
                access_token,
                params={
                    "seller": USER_ID,
                    "order.date_created.from": desde,
                    "order.date_created.to": hasta,
                    "offset": offset,
                    "limit": limit,
                },
            )
            resultados = datos.get("results", [])
            if not resultados:
                break
            tramo_ordenes.extend(resultados)
            total = datos.get("paging", {}).get("total", 0)
            offset += limit
            if offset >= total or offset >= 10000:
                break

        print(f"  Tramo: {len(tramo_ordenes)} ordenes")
        if len(tramo_ordenes) >= 9999:
            print(f"  ATENCION: este tramo llego al limite. Habria que partirlo mas fino.")
        todas.extend(tramo_ordenes)

        tramo_inicio = tramo_fin + timedelta(days=1)

    df = pd.json_normalize(todas)
    print(f"\n  TOTAL ventas ML 2026: {len(df)} ordenes")
    guardar_en_bd(df, "ml_ventas", modo="replace")


def obtener_ids_publicaciones(access_token):
    """Trae TODOS los IDs de publicaciones usando scan (sin limite de offset)."""
    print("  Obteniendo IDs de publicaciones (scan)...")
    ids = []
    scroll_id = None
    while True:
        params = {"search_type": "scan", "limit": 100}
        if scroll_id:
            params["scroll_id"] = scroll_id
        datos = llamar_ml(f"/users/{USER_ID}/items/search", access_token, params=params)
        resultados = datos.get("results", [])
        if not resultados:
            break
        ids.extend(resultados)
        scroll_id = datos.get("scroll_id")
        print(f"    IDs acumulados: {len(ids)}")
        if not scroll_id:
            break
    return ids


def extraer_publicaciones_ml():
    """Trae el detalle de TODAS las publicaciones de ML (activas, pausadas, cerradas)."""
    print("\n=== PUBLICACIONES MERCADO LIBRE ===")
    access_token = renovar_access_token()

    ids = obtener_ids_publicaciones(access_token)
    print(f"  Total de publicaciones: {len(ids)}")

    # Traer detalle de a 20 (multiget)
    detalles = []
    for i in range(0, len(ids), 20):
        lote = ids[i:i + 20]
        datos = llamar_ml("/items", access_token,
                          params={"ids": ",".join(lote)})
        # El multiget devuelve una lista de {code, body}
        for item in datos:
            if item.get("code") == 200:
                detalles.append(item["body"])
        if i % 200 == 0:
            print(f"    Detalles traidos: {len(detalles)} de {len(ids)}")

    df = pd.json_normalize(detalles)
    print(f"  Total con detalle: {len(df)} publicaciones")
    guardar_en_bd(df, "ml_publicaciones", modo="replace")

def extraer_stock_full():
    """Trae el stock real en Full (fulfillment) por cada inventory_id.
       Lee los inventory_id desde la tabla ml_publicaciones ya guardada."""
    print("\n=== STOCK FULL (fulfillment) ===")
    access_token = renovar_access_token()

    # Leer los inventory_id unicos de las publicaciones que estan en Full
    engine = create_engine(
        f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    query = """
        SELECT DISTINCT inventory_id
        FROM bronze.ml_publicaciones
        WHERE "shipping.logistic_type" = 'fulfillment'
          AND inventory_id IS NOT NULL
    """
    df_inv = pd.read_sql(query, engine)
    inventory_ids = df_inv["inventory_id"].tolist()
    print(f"  {len(inventory_ids)} inventory_id unicos a consultar")

    filas = []
    for i, inv_id in enumerate(inventory_ids):
        try:
            datos = llamar_ml(f"/inventories/{inv_id}/stock/fulfillment", access_token)
            datos["inventory_id"] = inv_id   # aseguramos guardar el id
            filas.append(datos)
        except Exception as e:
            # Si alguno falla (no disponible, etc.), lo registramos y seguimos
            filas.append({"inventory_id": inv_id, "error": str(e)})
        if i % 200 == 0:
            print(f"    Consultados: {i} de {len(inventory_ids)}")

    df = pd.json_normalize(filas)
    print(f"  Total: {len(df)} registros de stock full")
    guardar_en_bd(df, "ml_stock_full", modo="replace")

# ============================================================
#  EJECUCION
# ============================================================

if __name__ == "__main__":
    print("ML User ID:", USER_ID)
    #extraer_ventas_ml()
    #extraer_publicaciones_ml()
    extraer_stock_full()
    print("\n=== LISTO. Revisa la tabla ml_ventas en Supabase. ===")