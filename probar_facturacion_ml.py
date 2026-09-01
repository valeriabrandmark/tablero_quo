"""Sondeo: si Mercado Libre nos dice CUANTO nos cobra por antiguedad.

============================================================================
 POR QUE
============================================================================

`ml_antiguedad.py` calcula DIAS EN DEPOSITO y los corta en 30/60/90/120. Eso
no es lo mismo que el cargo: Mercado Libre cobra el almacenamiento prolongado
con un umbral QUE DEPENDE DE LA CATEGORIA -- un perfume puede entrar a los 60
dias y una crema a los 120. Con un corte fijo, lo nuestro es una aproximacion.

La API de facturacion tiene un tipo de cargo "almacenamiento prolongado". Si
contesta, deja de hacer falta adivinar el umbral: da PESOS efectivamente
cobrados, por periodo y por articulo. Ademas seria un dato que hoy no esta en
ningun tablero -- cuanto pagamos por tener la mercaderia parada.

============================================================================
 QUE SE PRUEBA
============================================================================

Primero los periodos (para sacar la `key`), despues el detalle de cada uno.
Las rutas salen de la documentacion de Reportes de Facturacion, que no coincide
entre paises, asi que se prueban las variantes que aparecen.

LO IMPORTANTE ES QUE IMPRIME EL CUERPO DE LA RESPUESTA, pase lo que pase. Dos
veces en este proyecto un 400 llego sin cuerpo y hubo que adivinar; el cuerpo
decia exactamente que faltaba. No se repite.

NO ESCRIBE NADA EN LA BASE.

    python probar_facturacion_ml.py
"""

import json
import os

import requests
from dotenv import load_dotenv

from mercadolibre import renovar_access_token

load_dotenv()
USER_ID = os.getenv("ML_USER_ID")
BASE = "https://api.mercadolibre.com"


def llamar(ruta, params, token, mostrar=900):
    url = BASE + ruta
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    print(f"\n  {r.status_code}  {ruta}")
    if params:
        print(f"     params: {params}")
    print(f"     cuerpo: {r.text[:mostrar]}")
    return r.json() if r.status_code == 200 else None


def buscar_periodos(token):
    """La `key` del periodo, que es lo que piden todas las rutas de detalle."""
    print("=" * 74)
    print("PASO 1 · LOS PERIODOS DE FACTURACION")
    print("=" * 74)

    for ruta, params in [
        ("/billing/integration/periods",
         {"group": "ML", "document_type": "BILL", "limit": 6}),
        (f"/billing/integration/{USER_ID}/periods",
         {"group": "ML", "document_type": "BILL", "limit": 6}),
        ("/billing/integration/periods", {"group": "ML"}),
    ]:
        datos = llamar(ruta, params, token)
        if not datos:
            continue
        # La estructura no esta documentada igual en todos lados: se busca
        # cualquier cosa que parezca una clave de periodo.
        candidatos = datos.get("results") or datos.get("periods") or []
        claves = [p.get("key") or p.get("period") for p in candidatos
                  if isinstance(p, dict)]
        claves = [k for k in claves if k]
        if claves:
            print(f"\n     >>> periodos encontrados: {claves}")
            return claves
    return []


def detalle(clave, token):
    print("\n" + "=" * 74)
    print(f"PASO 2 · EL DETALLE DEL PERIODO {clave}")
    print("=" * 74)

    for ruta, params in [
        (f"/billing/integration/periods/key/{clave}/group/ML/full/details",
         {"document_type": "BILL", "limit": 30}),
        (f"/billing/integration/periods/key/{clave}/group/ML/summary/details",
         {"document_type": "BILL"}),
    ]:
        datos = llamar(ruta, params, token, mostrar=1800)
        if not datos:
            continue

        filas = datos.get("results") or []
        if not filas:
            continue

        # LO QUE VENIMOS A BUSCAR: si entre los cargos aparece el de
        # almacenamiento, y si trae con que identificar el articulo.
        conceptos = sorted({str(f.get("detail") or f.get("charge_type")
                                or f.get("concept") or "?") for f in filas})
        print(f"\n     >>> conceptos de cargo en el periodo: {conceptos}")

        claves_fila = sorted(filas[0].keys()) if filas else []
        print(f"     >>> campos de cada fila: {claves_fila}")

        almacenamiento = [f for f in filas
                          if "almacen" in json.dumps(f, ensure_ascii=False).lower()
                          or "storage" in json.dumps(f).lower()]
        if almacenamiento:
            print(f"\n     >>> HAY CARGOS DE ALMACENAMIENTO ({len(almacenamiento)}):")
            print(json.dumps(almacenamiento[:3], indent=2, ensure_ascii=False)[:1500])
        else:
            print("\n     >>> sin cargos de almacenamiento en esta muestra")


def main():
    token = renovar_access_token()
    print("ML User ID:", USER_ID)

    claves = buscar_periodos(token)
    if not claves:
        print("\n" + "=" * 74)
        print("NINGUNA RUTA DE PERIODOS CONTESTO")
        print("=" * 74)
        print("Mirar los cuerpos de arriba: un 403 significa que la aplicacion no")
        print("tiene el permiso de facturacion y hay que habilitarlo en el panel de")
        print("desarrolladores. Un 404 significa que la ruta es otra.")
        return

    for clave in claves[:2]:
        detalle(clave, token)

    print("\n" + "=" * 74)
    print("QUE MIRAR")
    print("=" * 74)
    print("1. Si aparece un concepto de almacenamiento / storage.")
    print("2. Si la fila trae item_id, inventory_id o SKU para poder cruzarla.")
    print("3. El monto: eso es lo que se paga de verdad, sin adivinar umbrales.")


if __name__ == "__main__":
    main()
