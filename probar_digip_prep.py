import os
import json
import requests
from dotenv import load_dotenv
load_dotenv()

BASE = os.getenv("DIGIP_URL_BASE")
API_KEY = os.getenv("DIGIP_API_KEY")
headers = {"X-API-Key": API_KEY}

for pedido in ["3333", "3343"]:
    print(f"=== Preparacion del pedido {pedido} ===")
    resp = requests.get(f"{BASE}Preparaciones/{pedido}", headers=headers, timeout=30)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        prep = resp.json()
        print("ID preparacion:", prep.get("id"))
        print("Despacho (transporte):", prep.get("despachoCodigo"), "-", prep.get("despachoDescripcion"))
        print("PEDIDOS en esta preparacion:")
        for ped in (prep.get("pedidos") or []):
            print("  - codigo:", ped.get("codigo"),
                  "| cliente:", ped.get("clienteDescripcion"),
                  "| estado:", ped.get("estado"))
        print("CONTENEDORES (bultos):")
        for c in (prep.get("contenedores") or []):
            print("  - numero:", c.get("numero"), "| bultos:", c.get("cantidadBulto"))
    else:
        print("Respuesta:", resp.text[:300])
    print()