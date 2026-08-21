import os
import json
import estado
import requests
from dotenv import load_dotenv

# Cuanto se espera una respuesta de la API: (CONECTAR, LEER), en segundos.
#
# No es una optimizacion: sin `timeout`, `requests` espera PARA SIEMPRE si el
# servidor acepta la conexion y despues no contesta. El proceso no falla ni
# reintenta -- se queda colgado, el orquestador se cuelga con el, y el
# Programador de tareas de Windows saltea en silencio todas las corridas
# siguientes porque para el la tarea "todavia esta ejecutandose".
#
# SON DOS NUMEROS Y NO UNO, y la diferencia se nota cuando el servidor del otro
# lado esta caido. Con un solo valor, `timeout=120` es tambien el de conexion:
# tres intentos contra un host que no contesta se van SEIS MINUTOS antes de
# rendirse. Separados, el intento muere en 10 segundos si no hay con quien
# hablar, y sigue teniendo su tiempo largo para una consulta pesada que si
# arranco.
#
# El de conexion es corto a proposito: un servidor sano acepta la conexion en
# milisegundos. Si tarda diez segundos, no es que este pensando -- no esta.
TIMEOUT_HTTP = (10, 30)

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
    timeout=TIMEOUT_HTTP,
)

print("Status:", r.status_code)
data = r.json()
print(json.dumps(data, indent=2))

if "access_token" in data:
    # Se guarda en la BASE y no en un archivo. ML entrega un refresh_token nuevo
    # en cada renovacion y anula el anterior, asi que con el token en un archivo
    # local dos maquinas se pisan: la que renueva deja a la otra afuera. Ademas
    # es lo que permite que el orquestador corra en GitHub Actions, donde el
    # disco arranca limpio en cada corrida. Ver estado.py.
    estado.guardar("ml_tokens", data)
    print("\n=== TOKENS GUARDADOS en la base (ops.estado) ===")
    print("access_token:", data["access_token"][:20], "...")
    print("refresh_token:", data["refresh_token"][:20], "...")
    print("user_id (seller):", data.get("user_id"))
else:
    print("\n=== ERROR: no se obtuvo el token. Revisar el code o las credenciales. ===")