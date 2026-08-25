import os, json, requests, pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from conexion import crear_engine
load_dotenv()
BASE = os.getenv("DIGIP_URL_BASE"); API_KEY = os.getenv("DIGIP_API_KEY")
headers = {"X-API-Key": API_KEY}
engine = crear_engine()

resp = requests.get(f"{BASE}Preparaciones/3520", headers=headers, timeout=30)
prep = resp.json()

# Ver las claves de nivel superior
print("=== CLAVES PRINCIPALES ===")
print(list(prep.keys()))

# Ver contenedores si existen
print("\n=== CONTENEDORES ===")
print(json.dumps(prep.get("contenedores", "NO HAY"), indent=2, ensure_ascii=False))

# Sumar volumen y peso de items
items = prep.get("items", [])
vol_total = sum(i.get("volumen", 0) or 0 for i in items)
peso_total = sum(i.get("peso", 0) or 0 for i in items)
print(f"\n=== TOTALES ===")
print(f"Items: {len(items)}")
print(f"Volumen total: {vol_total}")
print(f"Peso total: {peso_total} (¿gramos? → {peso_total/1000} kg)")