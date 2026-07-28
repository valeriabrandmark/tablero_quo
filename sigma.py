import os
import time
import json
import calendar
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

# --- URL base de Sigma ---
URL_BASE = (
    f"https://{os.getenv('SIGMA_URL_CLIENTE')}"
    f"/{os.getenv('SIGMA_BASEALIAS')}"
    f"/{os.getenv('SIGMA_ID_CLIENTE')}"
    f"/sigma/api/v10/"
)
TOKEN = os.getenv("SIGMA_TOKEN", "")
HEADERS = {"X-Auth-Token": TOKEN}

# Archivo donde recordamos hasta cuando trajimos datos
ARCHIVO_ESTADO = "estado_sigma.json"

# Pausa base entre llamadas (segundos). Subila si te bloquean seguido.
PAUSA = 1.0


def cargar_estado():
    """Lee la fecha de la ultima corrida. Si no existe, devuelve {}."""
    if os.path.exists(ARCHIVO_ESTADO):
        with open(ARCHIVO_ESTADO) as f:
            return json.load(f)
    return {}


def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, "w") as f:
        json.dump(estado, f, indent=2)


def llamar_sigma(endpoint, params=None):
    """Llama a un endpoint respetando limites (429 y 403 por saturacion)."""
    url = URL_BASE + endpoint
    intentos_403 = 0
    while True:
        r = requests.get(url, headers=HEADERS, params=params)

        if r.status_code == 429:
            espera_ms = int(r.headers.get("X-Retry-After-ms", 1000))
            print(f"  429: esperando {espera_ms} ms...")
            time.sleep(espera_ms / 1000)
            continue

        if r.status_code == 403:
            intentos_403 += 1
            if intentos_403 > 5:
                print("  403 persistente. El servidor sigue bloqueando.")
                r.raise_for_status()
            espera = 60 * intentos_403   # 60s, 120s, 180s...
            print(f"  403 (saturacion?). Esperando {espera}s y reintentando ({intentos_403}/5)...")
            time.sleep(espera)
            continue

        r.raise_for_status()
        time.sleep(PAUSA)                # pausa corta entre llamadas exitosas
        return r.json()


def guardar_en_bd(df, tabla, modo="replace"):
    if df.empty:
        print(f"  (sin datos nuevos para {tabla})")
        return
    # Convertir a texto cualquier columna que contenga listas o diccionarios
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

    if modo == "replace":
        # TRUNCATE + APPEND: vacia la tabla sin borrarla -> no rompe las vistas
        try:
            with engine.begin() as con:
                con.exec_driver_sql(f'TRUNCATE TABLE bronze."{tabla}";')
            df.to_sql(tabla, engine, schema="bronze", if_exists="append", index=False)
            print(f"  Guardado (truncate+append): bronze.{tabla} ({len(df)} filas)")
            return
        except Exception as e:
            print(f"  (no se pudo truncate: {str(e)[:80]}... -> creando tabla)")

    df.to_sql(tabla, engine, schema="bronze", if_exists=modo, index=False)
    print(f"  Guardado ({modo}): bronze.{tabla} ({len(df)} filas)")
# ============================================================
#  EXTRACCIONES
# ============================================================

def extraer_articulos():
    """Catalogo completo. Se reemplaza entero (corre 1 vez al dia)."""
    print("\n=== ARTICULOS (catalogo con costos) ===")
    datos = llamar_sigma("ExportArticulos")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} articulos")
    guardar_en_bd(df, "sigma_articulos", modo="replace")


def extraer_clientes():
    """Catalogo de clientes (datos completos)."""
    print("\n=== CLIENTES ===")
    datos = llamar_sigma("ExportClientes")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} clientes")
    guardar_en_bd(df, "sigma_clientes", modo="replace")


def extraer_cuentas_corrientes():
    """Saldos de deuda por cliente (cuenta corriente)."""
    print("\n=== CUENTAS CORRIENTES (deudas) ===")
    datos = llamar_sigma("ExportClientesCtaCte")
    df = pd.json_normalize(datos)
    print(f"  Recibidos {len(df)} saldos de cliente")
    guardar_en_bd(df, "sigma_cuentas_corrientes", modo="replace")

def extraer_ofertas():
    """Politicas de descuento vigentes: % de oferta y su periodo de vigencia."""
    print("\n=== OFERTAS / POLITICAS DE DESCUENTO ===")
    datos = llamar_sigma("ExportPoliticasDescuento")
    df = pd.json_normalize(datos)
    print(f"  Recibidas {len(df)} politicas de descuento")
    # Cuantas estan vigentes hoy (informativo)
    if "vigenciaHasta" in df.columns:
        hoy = date.today().isoformat()
        vigentes = df[df["vigenciaHasta"] >= hoy] if len(df) else df
        print(f"  De esas, {len(vigentes)} con vigencia que llega a hoy o despues")
    guardar_en_bd(df, "sigma_ofertas", modo="replace")


def extraer_ventas(estado):
    """Ventas por TRAMOS MENSUALES (evita el problema de paginacion).
       Trae mes por mes desde inicio de 2026 hasta hoy."""
    print("\n=== VENTAS (articulos vendidos) por meses ===")

    hoy = date.today()
    anio = 2026
    todas = []

    for mes in range(1, hoy.month + 1):
        primer_dia = date(anio, mes, 1)
        ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
        ultimo_dia = date(anio, mes, ultimo_dia_mes)
        if ultimo_dia > hoy:
            ultimo_dia = hoy

        dde = primer_dia.isoformat()
        hta = ultimo_dia.isoformat()
        print(f"\n  --- Mes {mes:02d} ({dde} a {hta}) ---")

        datos_mes = llamar_sigma(
            "ExportArticulosVendidos",
            {"dde": dde, "hta": hta}
        )
        cant = len(datos_mes)
        print(f"  Mes {mes:02d}: {cant} lineas de venta")

        if cant >= 28000:
            print(f"  ATENCION: el mes {mes:02d} trae {cant} registros, cerca del tope.")
            print(f"  Podria estar incompleto. Avisar para partir este mes por quincenas.")

        todas.extend(datos_mes)
        time.sleep(10)   # pausa entre meses para no gatillar el 429

    df = pd.json_normalize(todas)
    print(f"\n  TOTAL ventas 2026: {len(df)} lineas")
    guardar_en_bd(df, "sigma_ventas", modo="replace")

    estado["ventas_hasta"] = hoy.isoformat()
    return estado


def extraer_compras(estado):
    """Facturas de compra (a proveedores) por meses, igual que ventas."""
    print("\n=== COMPRAS (facturas de compra) por meses ===")
    hoy = date.today()
    anio = 2026
    todas = []
    for mes in range(1, hoy.month + 1):
        primer_dia = date(anio, mes, 1)
        ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
        ultimo_dia = date(anio, mes, ultimo_dia_mes)
        if ultimo_dia > hoy:
            ultimo_dia = hoy
        dde = primer_dia.isoformat()
        hta = ultimo_dia.isoformat()
        print(f"  --- Mes {mes:02d} ({dde} a {hta}) ---")
        datos_mes = llamar_sigma("ExportFacturasCompra", {"dde": dde, "hta": hta})
        print(f"  Mes {mes:02d}: {len(datos_mes)} facturas de compra")
        todas.extend(datos_mes)
        time.sleep(10)
    df = pd.json_normalize(todas)
    print(f"  TOTAL compras 2026: {len(df)} registros")
    guardar_en_bd(df, "sigma_compras", modo="replace")
    return estado


# ============================================================
#  EJECUCION
# ============================================================

if __name__ == "__main__":
    print("URL base:", URL_BASE)
    print("Token cargado:", "SI" if TOKEN else "NO")

    estado = cargar_estado()

    # --- PRUEBA DE AHORA: clientes + cuentas corrientes ---
    #extraer_clientes()
    #extraer_cuentas_corrientes()
   # extraer_ofertas()

    # --- Otras extracciones (comentadas; activar cuando corresponda) ---
    estado = extraer_ventas(estado)
    #estado = extraer_compras(estado)
    # extraer_articulos()
    # extraer_stock()  -> el stock ahora viene de DIGIP, no de Sigma
    

    guardar_estado(estado)
    print("\n=== LISTO. Revisa las tablas en Supabase (esquema bronze). ===")