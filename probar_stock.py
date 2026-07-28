import os
import requests
from dotenv import load_dotenv
load_dotenv()

BASE = os.getenv("DIGIP_URL_BASE")
API_KEY = os.getenv("DIGIP_API_KEY")
headers = {"X-API-Key": API_KEY}

# Stock por tipo en DIGIP - probamos con unos SKU
print("=== Stock DIGIP (Disponible) ===")
url = f"{BASE}Stock/Tipo"
# Probamos sin filtro para ver la estructura (o con unos codigos)
resp = requests.get(url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    datos = resp.json()
    print(f"Articulos con stock: {len(datos)}")
    # Mostrar los primeros 5 con su disponible
    for art in datos[:5]:
        stock = art.get("stock") or {}
        print(f"  SKU: {art.get('codigo')} | {art.get('descripcion')[:30]} | "
              f"Disponible: {stock.get('disponible')}")
else:
    print(resp.text[:300])