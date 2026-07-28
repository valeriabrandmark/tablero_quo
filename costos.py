import os
import glob
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

CARPETA_COSTOS = "costos_mensuales"

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)


def limpiar_pct(valor):
    """Convierte el descuento a numero porcentual (ej 50.0).
       Maneja texto '50,00%' Y numero de Excel (0.5 = 50%, formato porcentaje)."""
    if valor is None or pd.isna(valor):
        return 0.0
    # Si Excel lo guardo como NUMERO (formato porcentaje interno: 0.5 = 50%)
    if isinstance(valor, (int, float)):
        v = float(valor)
        # Si es <= 1, es fraccion (0.5 -> 50). Si es mayor, ya es porcentaje (50 -> 50)
        return v * 100 if abs(v) <= 1 else v
    # Si es texto tipo "50,00%"
    s = str(valor).replace("%", "").strip()
    if s == "" or s.lower() == "nan":
        return 0.0
    # formato argentino: punto miles, coma decimal
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0
    
def limpiar_numero(valor):
    """Convierte costos en cualquier formato a numero:
       - '2.460,85' (texto formato argentino) -> 2460.85
       - 2460.85 (numero puro) -> 2460.85
       - '2460.85' (texto con punto decimal) -> 2460.85"""
    if valor is None or pd.isna(valor):
        return None
    # Si YA es un numero (int/float), lo devolvemos tal cual
    if isinstance(valor, (int, float)):
        return float(valor)
    # Si es texto, detectamos el formato
    s = str(valor).strip()
    if s == "" or s.lower() == "nan":
        return None
    # Caso formato argentino: tiene coma como decimal (ej "2.460,85")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    # Si no tiene coma, asumimos que el punto ya es decimal (ej "2460.85") -> no tocamos
    try:
        return float(s)
    except ValueError:
        return None
    

def leer_hoja_flexible(archivo, hoja, columnas_necesarias, max_filas_prueba=5):
    """Lee una hoja probando distintas filas de encabezado hasta encontrar
       una donde existan las columnas necesarias. Evita fallar si el export
       cambia la cantidad de filas de titulo."""
    for h in range(max_filas_prueba):
        try:
            df = pd.read_excel(archivo, sheet_name=hoja, header=h)
            if all(col in df.columns for col in columnas_necesarias):
                if h != 2:
                    print(f"    (encabezados detectados en fila {h+1})")
                return df
        except Exception:
            continue
    # Si no encontro, lee normal y deja que falle con mensaje claro
    raise ValueError(f"No se encontraron las columnas {columnas_necesarias} "
                     f"en la hoja '{hoja}' de {archivo}")


def cargar_costos():
    print("=== Cargando costos historicos con ofertas ===")
    archivos = glob.glob(os.path.join(CARPETA_COSTOS, "*.xlsx"))
    if not archivos:
        print(f"  No hay archivos .xlsx en {CARPETA_COSTOS}/")
        return

    todos = []
    for archivo in archivos:
        nombre = os.path.splitext(os.path.basename(archivo))[0]   # ej "2026-06"
        print(f"\n  === Mes comercial: {nombre} ===")

        # --- Pestaña COSTOS: encabezados en fila 3 (header=2) ---
        # Columna B = Codigo, Columna AQ = Costo Teorico
        dfc = leer_hoja_flexible(archivo, "Costos", ["Codigo", "Costo Teorico"])
        costos = dfc[["Codigo", "Costo Teorico"]].copy()
        costos.columns = ["sku", "costo_teorico"]
        costos["sku"] = costos["sku"].astype(str).str.strip()
        # Sigma a veces trae el codigo como numero (1.0) -> limpiamos el .0
        costos["sku"] = costos["sku"].str.replace(r"\.0$", "", regex=True)
        costos["costo_teorico"] = costos["costo_teorico"].apply(limpiar_numero)
        costos = costos.dropna(subset=["sku"])
        costos = costos[(costos["sku"] != "") & (costos["sku"].str.lower() != "nan")]
        print(f"    Costos: {len(costos)} SKUs")

        # --- Pestaña OFERTAS: encabezados en fila 1 (header=0) ---
        # Columna D = SKU, Columna J = DESCUENTO TOTAL PROVEEDOR
        dfo = leer_hoja_flexible(archivo, "Ofertas", ["SKU", "DESCUENTO TOTAL PROVEEDOR"])
        ofertas = dfo[["SKU", "DESCUENTO TOTAL PROVEEDOR"]].copy()
        ofertas.columns = ["sku", "oferta_pct"]
        ofertas["sku"] = ofertas["sku"].astype(str).str.strip()
        ofertas["sku"] = ofertas["sku"].str.replace(r"\.0$", "", regex=True)
        ofertas["oferta_pct"] = ofertas["oferta_pct"].apply(limpiar_pct)
        ofertas = ofertas.dropna(subset=["sku"])
        ofertas = ofertas[(ofertas["sku"] != "") & (ofertas["sku"].str.lower() != "nan")]
        ofertas = ofertas.drop_duplicates(subset=["sku"], keep="last")
        print(f"    Ofertas: {len(ofertas)} SKUs (con o sin descuento)")

        # --- Combinar: costo_real = costo_teorico * (1 - oferta%/100) ---
        costos = costos.merge(ofertas, on="sku", how="left")
        costos["oferta_pct"] = costos["oferta_pct"].fillna(0)
        costos["costo_real"] = costos["costo_teorico"] * (1 - costos["oferta_pct"] / 100)
        costos["mes_comercial"] = nombre
        todos.append(costos)

    if not todos:
        print("\n  No se cargo nada.")
        return

    final = pd.concat(todos, ignore_index=True)
    final = final.drop_duplicates(subset=["sku", "mes_comercial"], keep="last")
    final = final[["sku", "mes_comercial", "costo_teorico", "oferta_pct", "costo_real"]]

    final.to_sql("costos_historicos", engine, schema="bronze",
                 if_exists="replace", index=False)
    print(f"\n  Guardado: bronze.costos_historicos ({len(final)} filas)")
    print(f"  Meses: {sorted(final['mes_comercial'].unique())}")
    # Muestra de control
    print("\n  Ejemplo (primeras 5 con oferta > 0):")
    print(final[final['oferta_pct'] > 0].head(5).to_string(index=False))


if __name__ == "__main__":
    cargar_costos()
    print("\n=== LISTO ===")