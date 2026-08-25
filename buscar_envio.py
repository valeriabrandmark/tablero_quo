import os
import json
import pandas as pd
from dotenv import load_dotenv
from conexion import crear_engine

load_dotenv()
engine = crear_engine()

# Primero veamos como esta guardado el id (buscamos parecidos)
print("=== Buscando la orden ===")
df = pd.read_sql("""
    SELECT * FROM bronze.ml_ventas 
    WHERE CAST(id AS TEXT) LIKE '%2000016882431140%'
""", engine)
print(f"Filas encontradas: {len(df)}")

if df.empty:
    # Probamos traer cualquier orden de junio para ver el formato del id
    print("\nNo se encontro. Veamos el formato de los id:")
    muestra = pd.read_sql("SELECT id FROM bronze.ml_ventas LIMIT 5", engine)
    print(muestra)
    print("Tipo de id:", muestra['id'].dtype)
else:
    row = df.iloc[0]
    print("\n=== Buscando valores cercanos a 6600 ===")
    for col in df.columns:
        val = row[col]
        try:
            num = float(val)
            if 6000 <= num <= 7500:
                print(f"  [COLUMNA] {col}: {num}")
        except (ValueError, TypeError):
            pass
        if isinstance(val, (str, list, dict)):
            texto = str(val)
            if "6600" in texto:
                print(f"  [JSON en {col}] contiene '6600'")

    print("\n=== order_items ===")
    items = row["order_items"]
    if isinstance(items, str):
        items = json.loads(items)
    print(json.dumps(items, indent=2, ensure_ascii=False)[:2500])

    print("\n=== payments ===")
    pays = row.get("payments")
    if isinstance(pays, str):
        try:
            pays = json.loads(pays)
            print(json.dumps(pays, indent=2, ensure_ascii=False)[:2500])
        except:
            print(str(pays)[:1500])
    else:
        print(str(pays)[:1500])