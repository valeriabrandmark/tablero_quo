"""Sondeo: que cargos nos informa Mercado Libre en su facturacion.

============================================================================
 POR QUE
============================================================================

Hay costos de Mercado Libre que NO vienen en la orden. La orden trae la
comision (`sale_fee`) y, con los envios, el flete. Todo lo demas --lo que ML
nos cobra por otras cosas-- vive en la facturacion mensual, que hoy no
leemos. Dos que ya sabemos que faltan:

  EL COSTO DE LAS CUOTAS. En la orden, `installments` y la diferencia entre
  `total_paid_amount` y `transaction_amount` son lo que paga EL COMPRADOR.
  Medido sobre junio en adelante: de 903 pagos en cuotas, 610 no le costaron
  un peso de interes al comprador. Esa financiacion la paga alguien --ML con
  una promo suya, o nosotros-- y la orden no lo dice. Si la pagamos nosotros,
  es un costo por venta que el tablero hoy ignora.

  EL ALMACENAMIENTO PROLONGADO. `ml_antiguedad.py` calcula DIAS EN DEPOSITO y
  los corta en 30/60/90/120, pero el cargo real usa un umbral QUE DEPENDE DE
  LA CATEGORIA: un perfume puede entrar a los 60 dias y una crema a los 120.
  Con un corte fijo, lo nuestro es una aproximacion.

La facturacion da PESOS efectivamente cobrados. Si ademas cada fila trae con
que cruzarla --una orden, un articulo-- el costo deja de ser un total mensual
y pasa a poder colgarse de cada venta.

============================================================================
 QUE SE PRUEBA
============================================================================

Primero los periodos (para sacar la `key`), despues el detalle de cada uno, y
de ahi la lista COMPLETA de conceptos de cargo con lo que suma cada uno: la
pregunta de fondo no es "esta tal cargo" sino "que nos estan cobrando".
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

from mercadolibre import token_ml

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


def _monto(fila):
    """El importe de una fila de facturacion, se llame como se llame.

    La API no usa el mismo nombre en todos lados y este sondeo justamente no
    sabe cual va a venir. Si no encuentra ninguno devuelve 0: el total queda
    corto y se nota, que es mejor que romper el sondeo entero por un nombre.
    """
    for clave in ("amount", "charge_amount", "total_amount", "value",
                  "detail_amount", "amount_with_taxes"):
        valor = fila.get(clave)
        if isinstance(valor, (int, float)):
            return float(valor)
    return 0.0


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

        # 1) TODOS LOS CONCEPTOS, CON CUANTO SUMA CADA UNO.
        #
        #    Antes esto listaba los nombres y listo. El nombre solo no alcanza
        #    para decidir nada: un cargo de $200 al mes no justifica una
        #    ingesta y uno de $2 M cambia el margen de un canal entero.
        por_concepto = {}
        for f in filas:
            nombre = str(f.get("detail") or f.get("charge_type")
                         or f.get("concept") or "?")
            cuenta, suma = por_concepto.get(nombre, (0, 0.0))
            por_concepto[nombre] = (cuenta + 1, suma + _monto(f))

        print("\n     >>> conceptos de cargo en el periodo:")
        for nombre, (cuenta, suma) in sorted(por_concepto.items(),
                                             key=lambda x: -abs(x[1][1])):
            print(f"         {suma:14,.2f}   x{cuenta:<5} {nombre}")

        claves_fila = sorted(filas[0].keys()) if filas else []
        print(f"\n     >>> campos de cada fila: {claves_fila}")

        # 2) SE PUEDE COLGAR DE UNA VENTA, O ES SOLO UN TOTAL DEL MES?
        #
        #    Es la diferencia entre "Mercado Libre nos cobro $X de cuotas en
        #    agosto" y "esta venta nos costo $X de cuotas". Lo segundo entra al
        #    margen por linea; lo primero es un gasto del canal y nada mas.
        cruces = sorted({c for f in filas for c in f
                         if any(p in c.lower() for p in
                                ("order", "operation", "item", "sku", "shipment", "pack"))})
        print(f"     >>> campos para cruzar con una venta: {cruces or 'NINGUNO'}")

        # 3) LOS DOS CARGOS QUE ESTAMOS BUSCANDO.
        for titulo, palabras in (
            ("CUOTAS / FINANCIACION",
             ("cuota", "financ", "interes", "interés", "installment")),
            ("ALMACENAMIENTO", ("almacen", "storage")),
        ):
            encontrados = [f for f in filas
                           if any(p in json.dumps(f, ensure_ascii=False).lower()
                                  for p in palabras)]
            if encontrados:
                total = sum(_monto(f) for f in encontrados)
                print(f"\n     >>> HAY CARGOS DE {titulo}: {len(encontrados)} filas, "
                      f"{total:,.2f} en total")
                print(json.dumps(encontrados[:2], indent=2, ensure_ascii=False)[:1200])
            else:
                print(f"\n     >>> sin cargos de {titulo} en esta muestra")


def main():
    token = token_ml()
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
    print("1. La lista de conceptos con sus totales: que nos cobran, y cuanto.")
    print("   Es lo que decide si vale la pena ingerir algo o no.")
    print("2. Si hay un concepto de cuotas / financiacion. Si existe, es un")
    print("   costo por venta que hoy no esta en ningun lado.")
    print("3. Los campos para cruzar. Con order_id o pack_id el cargo se cuelga")
    print("   de la venta; sin eso, es un gasto mensual del canal y nada mas.")
    print("4. Un 403 es permiso de la aplicacion, no es que el dato no exista.")


if __name__ == "__main__":
    main()
