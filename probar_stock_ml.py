import json
import requests
from mercadolibre import token_ml
from conexion import crear_engine

print("=== Stock ML Full (Fulfillment) ===")
token = token_ml()
headers = {"Authorization": f"Bearer {token}"}

# Primero necesitamos los inventory_id de tus publicaciones Full
# Los sacamos de ml_stock_full que ya tenemos, o de las publicaciones
import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
load_dotenv()
engine = crear_engine()

# Traemos unos inventory_id de ejemplo de lo que ya tenemos guardado
df = pd.read_sql("SELECT * FROM bronze.ml_stock_full LIMIT 5", engine)
print("Columnas de ml_stock_full:", list(df.columns))
print(df.to_string())