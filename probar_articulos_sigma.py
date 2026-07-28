import os
import time
import requests
from dotenv import load_dotenv
load_dotenv()

URL_BASE = (
    f"https://{os.getenv('SIGMA_URL_CLIENTE')}"
    f"/{os.getenv('SIGMA_BASEALIAS')}"
    f"/{os.getenv('SIGMA_ID_CLIENTE')}"
    f"/sigma/api/v10/"
)
HEADERS = {"X-Auth-Token": os.getenv("SIGMA_TOKEN")}

def probar(nombre, params):
    print(f"\n=== {nombre} ===")
    t0 = time.time()
    try:
        r = requests.get(URL_BASE + "ExportArticulos", headers=HEADERS, params=params, timeout=120)
        dur = time.time() - t0
        print(f"Status: {r.status_code} | Duracion: {dur:.1f}s")
        if r.status_code == 200:
            print(f"Articulos: {len(r.json())}")
    except Exception as e:
        print(f"Error: {e}")

probar("Sin filtro (todo)", {})
probar("Por proveedor 00007", {"proveedorCodigo": "00007"})
probar("Por rubro 0099", {"rubroCodigo": "0099"})
probar("Paginado", {"page": 1, "perPage": 500})
probar("Solo activos", {"desactivado": "false"})