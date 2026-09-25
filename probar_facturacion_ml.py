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
 LO QUE YA CONTESTO (corrida del 25/09/2026, periodo 2026-08-01)
============================================================================

La ruta de detalle anda y devuelve 27.737 filas para un mes. Entre los
cargos esta el que se estaba buscando:

    transaction_detail  "Costo por ofrecer cuotas"
    detail_sub_type     CVFN
    detail_amount       1468.95
    sales_info[0]       order_id, operation_id, sale_date_time, ...

ASI QUE EL COSTO DE LAS CUOTAS SE PUEDE COLGAR DE CADA VENTA: la fila del
cargo trae la orden adentro. No es un total del mes.

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

============================================================================
 LA FORMA DE UNA FILA
============================================================================

No es plana: cada fila es un cargo con sus partes separadas.

    charge_info     que cargo es y cuanto        transaction_detail,
                                                 detail_sub_type, detail_amount
    sales_info      LA VENTA: order_id, operation_id, fecha, importe
    items_info      el articulo: item_id, titulo, categoria
    shipping_info   shipping_id, pack_id
    discount_info   bonificaciones, applied_percentage

La primera version de este sondeo las leia como si fueran planas y por eso
dijo "? x50, 0.00": ningun campo estaba donde miraba. Es el riesgo de un
sondeo que resume -- si no entiende la forma, no falla, MIENTE. Por eso
ahora, cuando no encuentra el importe donde deberia estar, lo dice.

NO ESCRIBE NADA EN LA BASE. Pide y muestra, nada mas.

    python probar_facturacion_ml.py
    python probar_facturacion_ml.py --periodo 2026-08-01 --paginas 60
"""

import argparse
import json
import os
from datetime import date

import requests
from dotenv import load_dotenv

import mercadolibre as ml
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
    if mostrar:
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


def _aplanar(fila):
    """Una fila de cargo, con lo que sirve a la vista.

    Devuelve tambien `sin_importe` para poder contar cuantas no se entendieron:
    un total que se arma ignorando filas en silencio es peor que no tenerlo.
    """
    cargo = fila.get("charge_info") or {}
    ventas = fila.get("sales_info") or []
    items = fila.get("items_info") or []
    envio = fila.get("shipping_info") or {}

    monto = cargo.get("detail_amount")
    if not isinstance(monto, (int, float)):
        monto = _monto(fila)
        sin_importe = True
    else:
        sin_importe = False

    return {
        "concepto": (cargo.get("transaction_detail") or "?").strip(),
        "sub_tipo": cargo.get("detail_sub_type"),
        "tipo": cargo.get("detail_type"),
        "monto": float(monto or 0),
        "sin_importe": sin_importe,
        # UNA FILA PUEDE CUBRIR VARIAS VENTAS (un pack). Se guardan todas: de
        # eso depende si el cargo se puede repartir o no.
        "ordenes": [v.get("order_id") for v in ventas if v.get("order_id")],
        "items": [i.get("item_id") for i in items if i.get("item_id")],
        "shipping_id": envio.get("shipping_id"),
        "pack_id": envio.get("pack_id"),
    }


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


def analizar(filas, cuantas_hay=None, corte=None):
    """Que cargos hay, cuanto suman, y si se pueden colgar de una venta."""
    planas = [_aplanar(f) for f in filas]

    # ESTO PRIMERO, ANTES DE CUALQUIER NUMERO.
    #
    # La corrida del 25/09 leyo 400 filas de 27.737 --murio en un 429-- e
    # imprimio la tabla de conceptos como si fuera el mes. Un total que sale
    # del 1,4 % de las filas y no lo aclara no es un dato incompleto: es un
    # dato falso, y encima creible.
    if corte or (cuantas_hay and len(planas) < cuantas_hay):
        porcion = f" ({len(planas) / cuantas_hay:.1%})" if cuantas_hay else ""
        print(f"\n     !!! MUESTRA PARCIAL: {len(planas)} filas"
              + (f" de {cuantas_hay}{porcion}" if cuantas_hay else "")
              + (f" · {corte}" if corte else ""))
        print("     !!! Los totales de abajo son de ESA MUESTRA, no del mes.")

    por_concepto = {}
    for f in planas:
        nombre = f["concepto"] + (f" [{f['sub_tipo']}]" if f["sub_tipo"] else "")
        cuenta, suma, con_orden = por_concepto.get(nombre, (0, 0.0, 0))
        por_concepto[nombre] = (cuenta + 1, suma + f["monto"],
                                con_orden + (1 if f["ordenes"] else 0))

    print(f"\n     >>> {len(planas)} filas leidas. Conceptos de cargo:")
    print(f"         {'IMPORTE':>16}  {'FILAS':>7}  {'C/ORDEN':>7}  CONCEPTO")
    for nombre, (cuenta, suma, con_orden) in sorted(por_concepto.items(),
                                                    key=lambda x: -abs(x[1][1])):
        print(f"         {suma:16,.2f}  {cuenta:>7}  {con_orden:>7}  {nombre}")

    # LAS ANULACIONES NO SE SUMAN, SE RESTAN.
    #
    # Vienen con el importe en positivo y el tipo dice que son otra cosa
    # (CHARGE vs lo demas). Sumandolas, una anulacion de $6.594 aparecia como
    # $6.594 mas de gasto en vez de $6.594 menos: el error es del doble del
    # importe, y siempre para el lado de creer que gastamos mas.
    cargos = sum(f["monto"] for f in planas if f["tipo"] == "CHARGE")
    otros = sum(f["monto"] for f in planas if f["tipo"] != "CHARGE")
    print(f"\n         {cargos:16,.2f}  cargos")
    print(f"         {-otros:16,.2f}  anulaciones y bonificaciones")
    print(f"         {cargos - otros:16,.2f}  NETO"
          + (" de la muestra" if corte or (cuantas_hay and len(planas) < cuantas_hay)
             else ""))
    tipos = sorted({str(f["tipo"]) for f in planas})
    print(f"         (tipos vistos: {', '.join(tipos)})")

    ciegas = sum(1 for f in planas if f["sin_importe"])
    if ciegas:
        print(f"\n     !!! {ciegas} filas sin importe reconocible: el total esta corto.")

    # LA PREGUNTA QUE DECIDE TODO: por venta o total del mes.
    con_orden = [f for f in planas if f["ordenes"]]
    varias = [f for f in con_orden if len(f["ordenes"]) > 1]
    print(f"\n     >>> con order_id: {len(con_orden)} de {len(planas)}"
          f" · con mas de una orden en la misma fila: {len(varias)}")
    print(f"     >>> con item_id: {sum(1 for f in planas if f['items'])}"
          f" · con pack_id: {sum(1 for f in planas if f['pack_id'])}")

    for titulo, palabras in CARGOS_BUSCADOS:
        encontrados = [f for f in planas
                       if any(p in (f["concepto"] or "").lower() for p in palabras)]
        if encontrados:
            total = sum(f["monto"] for f in encontrados)
            colgables = sum(1 for f in encontrados if f["ordenes"])
            print(f"\n     >>> CARGOS DE {titulo}: {len(encontrados)} filas, "
                  f"{total:,.2f} en total, {colgables} con orden")
            for f in encontrados[:3]:
                print(f"         {f['monto']:12,.2f}  {f['concepto']}"
                      f"  orden={f['ordenes'][:1] or '-'}")
        else:
            print(f"\n     >>> sin cargos de {titulo} en esta muestra")


RUTA_DETALLE = "/billing/integration/periods/key/{clave}/group/ML/details"


def pedir_paginas(clave, token, paginas, limite):
    """El detalle del periodo. Devuelve (filas, cuantas hay, por que corto).

    UN MES SON ~28.000 FILAS y se piden de a `limite`. Lo que devuelve NO es
    necesariamente el mes entero, y por eso devuelve tambien el motivo del
    corte: quien lo muestre tiene que poder decir "esto es una muestra".

    LAS LLAMADAS VAN POR `llamar_ml` Y NO POR requests.
    La corrida del 25/09 murio en un 429 a la tercera pagina --400 filas de
    27.737-- porque esto pedia directo con requests y un 429 lo dejaba sin
    respuesta. `llamar_ml` ya sabe esperar lo que la API pide y reintentar, y
    ademas renueva el token si vence a mitad de camino, que con 139 paginas es
    perfectamente posible.
    """
    filas, desde, offset, modo = [], None, 0, "from_id"
    cuantas_hay = None
    corte = None

    for pagina in range(1, paginas + 1):
        params = {"document_type": "BILL", "limit": limite}
        if modo == "from_id" and desde is not None:
            params["from_id"] = desde
        elif modo == "offset":
            params["offset"] = offset

        ruta = RUTA_DETALLE.format(clave=clave)
        try:
            datos = ml.llamar_ml(ruta, token, params)
        except Exception as e:
            # Se rindio despues de los reintentos. No se pierde lo leido: se
            # dice hasta donde se llego y se analiza eso.
            print(f"\n  la pagina {pagina} no volvio: {e}")
            corte = f"la API corto en la pagina {pagina}"
            break

        if pagina == 1:
            print(f"\n  200  {ruta}")
            print(f"     params: {params}")
            # `total` es LO QUE FALTA, no el tamaño del mes: baja en cada
            # pagina (27.737, despues 27.537...). El del primer pedido es el
            # unico que sirve de denominador.
            cuantas_hay = (datos or {}).get("total")

        nuevas = _filas(datos)
        if not nuevas:
            break

        filas.extend(nuevas)
        print(f"     pagina {pagina}: {len(nuevas)} filas · acumuladas {len(filas)}"
              + (f" de {cuantas_hay}" if cuantas_hay else ""))

        ultimo = (datos or {}).get("last_id")
        if modo == "from_id" and (ultimo is None or ultimo == desde):
            print("     (from_id no avanza: se pasa a offset)")
            modo = "offset"
        desde = ultimo
        offset += len(nuevas)

        if cuantas_hay and len(filas) >= cuantas_hay:
            break
    else:
        if cuantas_hay and len(filas) < cuantas_hay:
            corte = f"se acabaron las {paginas} paginas pedidas"

    return filas, cuantas_hay, corte


def detalle(clave, token, paginas, limite):
    print("\n" + "=" * 74)
    print(f"PASO 2 · EL DETALLE DEL PERIODO {clave}")
    print("=" * 74)

    filas, cuantas_hay, corte = pedir_paginas(clave, token, paginas, limite)
    if filas:
        analizar(filas, cuantas_hay, corte)
        return True

    # Si el detalle no contesto, se prueban las otras dos por si el periodo
    # existe con otro nombre o el permiso alcanza solo para el resumen.
    for ruta, params in [
        (f"/billing/integration/periods/key/{clave}/group/ML/summary",
         {"document_type": "BILL"}),
        (f"/billing/integration/periods/key/{clave}/documents",
         {"group": "ML", "document_type": "BILL", "limit": 5}),
    ]:
        datos = llamar(ruta, params, token, mostrar=1800)
        otras = _filas(datos)
        if otras:
            analizar(otras, None, None)
            return True
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Sondeo de la facturacion de ML. No escribe nada.")
    parser.add_argument("--periodo",
                        help="Clave del periodo (el primer dia del mes, "
                             "'2026-08-01'). Por defecto los ultimos meses.")
    parser.add_argument("--paginas", type=int, default=5,
                        help="Cuantas paginas de detalle pedir (por defecto 5). "
                             "Un mes entero son ~28.000 filas.")
    parser.add_argument("--limite", type=int, default=200,
                        help="Filas por pagina (por defecto 200)")
    args = parser.parse_args()

    token = token_ml()
    print("ML User ID:", USER_ID)

    claves = [args.periodo] if args.periodo else buscar_periodos(token)[:MESES_A_PROBAR]

    hubo_detalle = False
    for clave in claves:
        if detalle(clave, token, args.paginas, args.limite):
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
    print("3. La columna C/ORDEN: cuantas filas de ese concepto traen la venta")
    print("   adentro. Esas se pueden colgar del margen de cada linea; el")
    print("   resto es un gasto mensual del canal y nada mas.")
    print("4. Si arriba dice MUESTRA PARCIAL, los totales son de esa muestra.")
    print("   Para el mes entero hacen falta ~139 paginas de 200.")
    print("\nPara los totales de un mes entero:")
    print("   python probar_facturacion_ml.py --periodo 2026-08-01 --paginas 60")


if __name__ == "__main__":
    main()
