import os
import requests
from dotenv import load_dotenv

load_dotenv()

URL_BASE = (
    f"https://{os.getenv('SIGMA_URL_CLIENTE')}"
    f"/{os.getenv('SIGMA_BASEALIAS')}"
    f"/{os.getenv('SIGMA_ID_CLIENTE')}"
    f"/sigma/api/v10/"
)
TOKEN = os.getenv("SIGMA_TOKEN", "")
print("Token que se va a usar:", repr(TOKEN))
HEADERS = {"X-Auth-Token": TOKEN}

# Prueba 1: el endpoint que SI funciona (control)
print("=== PRUEBA 1: ExportArticulos (deberia funcionar) ===")
r = requests.get(URL_BASE + "ExportArticulos", headers=HEADERS)
print("Status:", r.status_code)
print("Primeros 300 caracteres:", r.text[:300])

# Prueba 2: ventas SIN parametro de pagina, un solo dia
print("\n=== PRUEBA 2: ExportArticulosVendidos un dia, sin paginar ===")
r = requests.get(
    URL_BASE + "ExportArticulosVendidos",
    headers=HEADERS,
    params={"dde": "2026-06-01", "hta": "2026-06-01"}
)
print("Status:", r.status_code)
print("Respuesta completa:", r.text[:800])