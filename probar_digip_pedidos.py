import os
import requests
from dotenv import load_dotenv
load_dotenv()

BASE = os.getenv("DIGIP_URL_BASE")
API_KEY = os.getenv("DIGIP_API_KEY")
headers = {"X-API-Key": API_KEY}

for estado in ["RemitidoExterno", "Remitido"]:
    print(f"=== Estado: {estado} (junio 2026) ===")
    url = f"{BASE}Pedidos"
    params = {
        "PedidoEstado": estado,
        "FechaPedidoDesde": "2026-06-01T00:00:00",
        "FechaPedidoHasta": "2026-06-30T23:59:59",
        "Page": 1, "PerPage": 50,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        pedidos = resp.json()
        print(f"Pedidos en esta pagina: {len(pedidos)}")
        # Cuantos tienen codigo numerico limpio (distribuidora)
        distri = [p for p in pedidos if str(p.get("codigo") or "").isdigit()]
        print(f"  De esos, con codigo numerico (distri): {len(distri)}")
        for p in pedidos[:8]:
            cu = p.get("clienteUbicacion") or {}
            print("  codigo:", p.get("codigo"),
                  "| cliente:", (cu.get("cliente") or {}).get("descripcion"),
                  "| prov:", cu.get("provincia"),
                  "| localidad:", cu.get("localidad"),
                  "| transporte:", p.get("servicioDeEnvioTipo"),
                  "| despacho:", p.get("codigoDespacho"))
    elif resp.status_code == 204:
        print("  Sin contenido (no hay pedidos con ese estado)")
    else:
        print("  ", resp.text[:300])
    print()