import json
import requests
from mercadolibre import token_ml

# Usamos tu funcion que ya refresca y guarda el token
print("=== Refrescando token con tu funcion ===")
access_token = token_ml()

headers = {"Authorization": f"Bearer {access_token}"}

# --- Consultar el envio de la orden que vimos ---
shipping_id = "47273545551"

print(f"\n=== Consultando envio {shipping_id} ===")
url = f"https://api.mercadolibre.com/shipments/{shipping_id}"
resp = requests.get(url, headers=headers)
print(f"Status: {resp.status_code}\n")
if resp.status_code == 200:
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False)[:3000])
else:
    print("Respuesta:", resp.text[:1000])

print(f"\n=== Consultando costos del envio ({shipping_id}/costs) ===")
url2 = f"https://api.mercadolibre.com/shipments/{shipping_id}/costs"
resp2 = requests.get(url2, headers=headers)
print(f"Status: {resp2.status_code}\n")
if resp2.status_code == 200:
    print(json.dumps(resp2.json(), indent=2, ensure_ascii=False)[:2000])
else:
    print("Respuesta:", resp2.text[:500])