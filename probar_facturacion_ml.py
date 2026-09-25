"""Sondeo: que cargos nos informa Mercado Libre en su facturacion.

============================================================================
 POR QUE
============================================================================

Hay costos de Mercado Libre que NO vienen en la orden. La orden trae la
comision (`sale_fee`) y, con los envios, el flete. Todo lo demas vive en la
facturacion mensual, que hoy no leemos. Dos que ya sabemos que faltan:

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

============================================================================
 LAS RUTAS, Y COMO SE LLEGO A ESTAS
============================================================================

La primera version de este sondeo pidio /billing/integration/periods y se
llevo tres 404. El detalle que lo resolvio fue el tercer intento: pidiendo
ESA MISMA ruta con menos parametros, contesto 422 "Missing required parameter
<document_type>" en vez de 404. O sea que del otro lado hay algo que valida
parametros -- la ruta de periodos es otra, pero la familia existe.

Segun la documentacion de Reportes de Facturacion:

    /billing/monthly/periods                              los periodos
    /billing/integration/periods/key/{KEY}/group/ML/details   el detalle
    /billing/integration/periods/key/{KEY}/group/ML/summary   el resumen
    /billing/integration/periods/key/{KEY}/documents          las facturas

y la `KEY` de un periodo es EL PRIMER DIA DEL MES ('2026-08-01'). Eso ultimo
es lo que hace que este sondeo ya no dependa de adivinar la ruta de periodos:
si no contesta ninguna, las claves se arman solas con los ultimos meses. Un
404 en el paso 1 deja de ser el final del camino.

NO ESCRIBE NADA EN LA BASE. Pide y muestra, nada mas.

    python probar_facturacion_ml.py
"""

import json
import os
from datetime import date

import requests
from dotenv import load_dotenv

from mercadolibre import token_ml

load_dotenv()
USER_ID = os.getenv("ML_USER_ID")
BASE = "https://api.mercadolibre.com"

# Cuantos meses probar cuando hay que armar las claves a mano. El mes en curso
# no se factura hasta que termina, asi que se empieza por el anterior.
MESES_A_PROBAR = 3

# Las palabras con las que se reconoce cada cargo. Van en minuscula y sin
# acento del lado de la busqueda, pero el texto de ML puede traerlos: por eso
# se buscan las dos formas de "interes".
CARGOS_BUSCADOS = (
    ("CUOTAS / FINANCIACION",
     ("cuota", "financ", "interes", "interés", "installment")),
    ("ALMACENAMIENTO", ("almacen", "storage")),
)

# Los nombres con los que la API puede mandar el importe de una fila. Cambian
# entre paises y entre versiones de la documentacion.
NOMBRES_DE_IMPORTE = ("amount", "charge_amount", "total_amount", "value",
                      "detail_amount", "amount_with_taxes")

# Con que se podria colgar un cargo de una venta.
CAMPOS_PARA_CRUZAR = ("order", "operation", "item", "sku", "shipment", "pack")


def llamar(ruta, params, token, mostrar=900):
    r = requests.get(BASE + ruta, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    print(f"\n  {r.status_code}  {ruta}")
    if params:
        print(f"     params: {params}")
    print(f"     cuerpo: {r.text[:mostrar]}")
    return r.json() if r.status_code == 200 else None


def _monto(fila):
    """El importe de una fila, se llame como se llame.

    Si no reconoce ningun nombre devuelve 0: el total queda corto y se nota,
    que es mejor que romper el sondeo entero por un nombre nuevo.
    """
    for clave in NOMBRES_DE_IMPORTE:
        valor = fila.get(clave)
        if isinstance(valor, (int, float)):
            return float(valor)
    return 0.0


def _filas(datos):
    """Las filas de una respuesta, que no siempre se llaman igual."""
    if isinstance(datos, list):
        return [f for f in datos if isinstance(f, dict)]
    for clave in ("results", "details", "data", "items"):
        valor = (datos or {}).get(clave)
        if isinstance(valor, list):
            return [f for f in valor if isinstance(f, dict)]
    return []


def claves_de_los_ultimos_meses(cuantos=MESES_A_PROBAR):
    """'2026-08-01', '2026-07-01', ... El mes en curso no se factura todavia."""
    hoy = date.today()
    claves = []
    anio, mes = hoy.year, hoy.month
    for _ in range(cuantos):
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
        claves.append(date(anio, mes, 1).isoformat())
    return claves


def buscar_periodos(token):
    print("=" * 74)
    print("PASO 1 · LOS PERIODOS DE FACTURACION")
    print("=" * 74)

    for ruta, params in [
        ("/billing/monthly/periods", {"group": "ML", "document_type": "BILL", "limit": 6}),
        ("/billing/monthly/periods", {"group": "ML", "limit": 6}),
        ("/billing/monthly/periods", {"document_type": "BILL"}),
        # La de la primera version. Se deja porque su 422 fue la pista de que
        # la familia de rutas existe, y porque no cuesta nada.
        ("/billing/integration/periods", {"group": "ML", "document_type": "BILL", "limit": 6}),
    ]:
        datos = llamar(ruta, params, token)
        candidatos = _filas(datos) or (datos or {}).get("periods") or []
        claves = [p.get("key") or p.get("period") or p.get("date_from")
                  for p in candidatos if isinstance(p, dict)]
        claves = [k for k in claves if k]
        if claves:
            print(f"\n     >>> periodos encontrados: {claves}")
            return claves

    # NO SE ABANDONA ACA. La clave de un periodo es el primer dia del mes, asi
    # que se arman solas y el paso 2 corre igual.
    claves = claves_de_los_ultimos_meses()
    print("\n     >>> ninguna ruta de periodos contesto.")
    print(f"     >>> se prueban las claves armadas a mano: {claves}")
    return claves


def analizar(filas):
    """Lo que se vino a ver: que cargos hay, cuanto suman, y si se pueden cruzar."""
    por_concepto = {}
    for f in filas:
        nombre = str(f.get("detail") or f.get("detail_type") or f.get("charge_type")
                     or f.get("concept") or "?")
        sub = f.get("detail_sub_type") or f.get("sub_type")
        if sub:
            nombre = f"{nombre} / {sub}"
        cuenta, suma = por_concepto.get(nombre, (0, 0.0))
        por_concepto[nombre] = (cuenta + 1, suma + _monto(f))

    print("\n     >>> conceptos de cargo en el periodo:")
    for nombre, (cuenta, suma) in sorted(por_concepto.items(), key=lambda x: -abs(x[1][1])):
        print(f"         {suma:14,.2f}   x{cuenta:<5} {nombre}")

    print(f"\n     >>> campos de cada fila: {sorted(filas[0].keys())}")

    # LA PREGUNTA QUE DECIDE TODO: es un costo POR VENTA o un gasto del mes.
    # Con order_id el cargo entra al margen de esa linea; sin eso, es un gasto
    # del canal y nada mas.
    cruces = sorted({c for f in filas for c in f
                     if any(p in c.lower() for p in CAMPOS_PARA_CRUZAR)})
    print(f"     >>> campos para cruzar con una venta: {cruces or 'NINGUNO'}")

    for titulo, palabras in CARGOS_BUSCADOS:
        texto = [(f, json.dumps(f, ensure_ascii=False).lower()) for f in filas]
        encontrados = [f for f, t in texto if any(p in t for p in palabras)]
        if encontrados:
            total = sum(_monto(f) for f in encontrados)
            print(f"\n     >>> HAY CARGOS DE {titulo}: {len(encontrados)} filas, "
                  f"{total:,.2f} en total")
            print(json.dumps(encontrados[:2], indent=2, ensure_ascii=False)[:1200])
        else:
            print(f"\n     >>> sin cargos de {titulo} en esta muestra")


def detalle(clave, token):
    print("\n" + "=" * 74)
    print(f"PASO 2 · EL DETALLE DEL PERIODO {clave}")
    print("=" * 74)

    for ruta, params in [
        (f"/billing/integration/periods/key/{clave}/group/ML/details",
         {"document_type": "BILL", "limit": 50}),
        (f"/billing/integration/periods/key/{clave}/group/ML/summary",
         {"document_type": "BILL"}),
        (f"/billing/integration/periods/key/{clave}/documents",
         {"group": "ML", "document_type": "BILL", "limit": 5}),
    ]:
        datos = llamar(ruta, params, token, mostrar=1800)
        filas = _filas(datos)
        if filas:
            analizar(filas)
            return True
    return False


def main():
    token = token_ml()
    print("ML User ID:", USER_ID)

    hubo_detalle = False
    for clave in buscar_periodos(token)[:MESES_A_PROBAR]:
        if detalle(clave, token):
            hubo_detalle = True
            break

    print("\n" + "=" * 74)
    print("QUE MIRAR")
    print("=" * 74)
    if not hubo_detalle:
        print("Ninguna ruta devolvio filas. Mirar los cuerpos de arriba:")
        print("  403  la aplicacion no tiene el permiso de facturacion. Se")
        print("       habilita en el panel de desarrolladores, y NO significa")
        print("       que el dato no exista.")
        print("  404  la ruta es otra en este pais.")
        print("  422  el parametro es otro: el cuerpo dice cual falta.")
        return
    print("1. La lista de conceptos con sus totales: que nos cobran, y cuanto.")
    print("   Es lo que decide si vale la pena ingerir algo o no.")
    print("2. Si hay un concepto de cuotas / financiacion. Si existe, es un")
    print("   costo por venta que hoy no esta en ningun lado.")
    print("3. Los campos para cruzar. Con order_id o pack_id el cargo se cuelga")
    print("   de la venta; sin eso, es un gasto mensual del canal y nada mas.")


if __name__ == "__main__":
    main()
