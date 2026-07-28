import os
import json
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

# Buscamos ordenes que SI tengan shipping_cost (sin sumar, solo mirar)
df = pd.read_sql("""
    SELECT id, shipping_cost, total_amount
    FROM bronze.ml_ventas
    WHERE status = 'paid' AND shipping_cost IS NOT NULL
    LIMIT 10
""", engine)

print("=== Ordenes CON shipping_cost ===")
print(df)
print("\nTipo de dato de shipping_cost:", df["shipping_cost"].dtype)

# Contar cuantas tienen envio (sin sumar)
conteo = pd.read_sql("""
    SELECT 
        COUNT(*) AS total,
        COUNT(shipping_cost) AS con_envio
    FROM bronze.ml_ventas
    WHERE status = 'paid'
""", engine)
print("\n=== Cuantas tienen envio ===")
print(conteo)