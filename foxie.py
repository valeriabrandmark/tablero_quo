"""Lo que Foxie sabe de nuestras publicaciones de Mercado Libre.

QUE ES FOXIE

El repricer que pagamos: con un piso y un techo de margen por estrategia, mira
a la competencia y mueve el precio de la publicacion para ganar la caja.

QUE TRAE ESTE SCRIPT, Y QUE NO

La API publica de Foxie (https://app.thefoxie.io/api-docs) expone:

  - publicaciones de catalogo y tradicionales, con el COSTO que Foxie tiene
    cargado y el envio que calcula;
  - las estrategias, con su margen minimo y maximo (el piso y el techo);
  - los valores de referencia de impuestos.

NO expone los competidores ni el precio para ganar. Eso sale de Mercado Libre
directo, en ml_competencia.py.

PARA QUE SIRVE ENTONCES

Para controlar que Foxie trabaje con el MISMO costo que el tablero. Foxie
calcula el piso sobre el costo que tiene cargado: si ese costo quedo viejo, el
piso esta mal y Foxie baja a un precio que pierde plata (o no baja cuando
podria). El tablero compara este costo contra `bronze.costos_historicos`.

QUE ESCRIBE (foto actual, se reemplaza entera)

  bronze.foxie_publicaciones  una fila por publicacion, con `tipo` catalogo o
                              tradicional.
  bronze.foxie_estrategias    las estrategias con su piso y techo.
  bronze.foxie_impuestos      los impuestos configurados en Foxie.

SOLO LEE. El endpoint de actualizar costo existe, pero escribir en Foxie es una
decision aparte: aca no se toca.

LIMITE: 1.000 llamadas por minuto. Con 100 por pagina son ~40 llamadas en total.

    python foxie.py
"""

import os
import sys
import time

import pandas as pd
import requests
from dotenv import load_dotenv

from mercadolibre import TIMEOUT_HTTP, guardar_en_bd

load_dotenv()

URL_BASE = "https://app.thefoxie.io/api/v1"
POR_PAGINA = 100


def llamar_foxie(endpoint, token, params=None, intentos=3):
    """GET a Foxie con reintento en 429 y 5xx. Devuelve `data` de la respuesta."""
    headers = {"access-token": token, "accept": "application/json"}
    for intento in range(1, intentos + 1):
        r = requests.get(URL_BASE + endpoint, headers=headers, params=params,
                         timeout=TIMEOUT_HTTP)
        if r.status_code == 429 or r.status_code >= 500:
            espera = int(r.headers.get("Retry-After", 0)) or 5 * intento
            print(f"  {r.status_code}: esperando {espera}s (intento {intento}/{intentos})")
            time.sleep(espera)
            continue
        if r.status_code == 401:
            # No se reintenta: un token revocado no vuelve solo.
            sys.exit("Foxie rechazo el token (401). Revisar FOXIE_TOKEN: el token "
                     "es por usuario y deja de valer si alguien hizo sign_out.")
        r.raise_for_status()
        return r.json().get("data")
    r.raise_for_status()


def listar_todo(endpoint, token):
    """Todas las paginas de un listado de Foxie."""
    filas, pagina = [], 1
    while pagina:
        datos = llamar_foxie(endpoint, token,
                             params={"page": pagina, "per_page": POR_PAGINA}) or {}
        filas += datos.get("list") or []
        pagina = (datos.get("pagination") or {}).get("next_page")
    return filas


def publicaciones(token):
    """Catalogo y tradicionales juntas, aplanando `marketplace_data`."""
    todas = []
    for tipo, endpoint in (("catalogo", "/catalog_publications"),
                           ("tradicional", "/normal_publications")):
        filas = listar_todo(endpoint, token)
        print(f"  {tipo}: {len(filas)} publicaciones")
        for f in filas:
            ml = f.get("marketplace_data") or {}
            todas.append({
                "foxie_id": f.get("id"),
                "tipo": tipo,
                "item_id": f.get("marketplace_id") or ml.get("marketplace_id"),
                "sku": f.get("meli_sku") or ml.get("meli_sku"),
                "nombre": f.get("meli_name"),
                "sync_status": f.get("sync_status"),
                "modo_costo": f.get("cost_mode"),
                "costo_con_iva": f.get("cost_entry"),
                "costo_sin_iva": f.get("cost_without_tax_entry"),
                "estado_ml": ml.get("meli_status"),
                "envio_gratis": ml.get("meli_free_shipping"),
                "logistica": ml.get("meli_shipping_logistic_type"),
                "costo_envio": ml.get("meli_shipping_cost"),
                "peso_facturable": ml.get("meli_billable_weight"),
                "categoria": ml.get("meli_category_id"),
            })
    df = pd.DataFrame(todas)
    # Vienen como texto en algunos casos ("580.0"): se pasan a numero para que
    # la columna no se cree como TEXT.
    for col in ("costo_con_iva", "costo_sin_iva", "costo_envio", "peso_facturable"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def estrategias(token):
    df = pd.DataFrame(listar_todo("/strategies", token))
    for col in ("min_margin", "max_margin"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def impuestos(token):
    datos = llamar_foxie("/reference_values", token) or {}
    filas = []
    for grupo, lista in (datos.get("publication_taxes") or {}).items():
        for t in lista or []:
            filas.append({"grupo": grupo, "id": t.get("id"), "nombre": t.get("name"),
                          "valor_pct": pd.to_numeric(t.get("value"), errors="coerce")})
    return pd.DataFrame(filas)


def main():
    token = os.getenv("FOXIE_TOKEN")
    if not token:
        sys.exit("Falta FOXIE_TOKEN. Es el access token del usuario de integracion "
                 "de Foxie (pantalla de informacion del usuario).")

    print("=== FOXIE ===")
    guardar_en_bd(publicaciones(token), "foxie_publicaciones", modo="replace")
    guardar_en_bd(estrategias(token), "foxie_estrategias", modo="replace")
    guardar_en_bd(impuestos(token), "foxie_impuestos", modo="replace")
    print("=== LISTO ===")


if __name__ == "__main__":
    main()
