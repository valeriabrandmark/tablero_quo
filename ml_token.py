import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# Pegá acá el code que te dio Mercado Libre (dura pocos minutos):
CODE="TG-6a2c5e343b794a00019aeb76-270905522"

r = requests.post(
    "https://api.mercadolibre.com/oauth/token",
    headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    data={
        "grant_type": "authorization_code",
        "client_id": os.getenv("ML_CLIENT_ID"),
        "client_secret": os.getenv("ML_CLIENT_SECRET"),
        "code": CODE,
        "redirect_uri": os.getenv("ML_REDIRECT_URI"),
    },
)

print("Status:", r.status_code)
data = r.json()
print(json.dumps(data, indent=2))

if "access_token" in data:
    # Guardamos los tokens en un archivo para usarlos despues
    with open("ml_tokens.json", "w") as f:
        json.dump(data, f, indent=2)
    print("\n=== TOKENS GUARDADOS en ml_tokens.json ===")
    print("access_token:", data["access_token"][:20], "...")
    print("refresh_token:", data["refresh_token"][:20], "...")
    print("user_id (seller):", data.get("user_id"))
else:
    print("\n=== ERROR: no se obtuvo el token. Revisar el code o las credenciales. ===")