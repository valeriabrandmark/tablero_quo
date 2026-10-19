"""Sondeo de la API de Tienda Nube: el costo real de la etiqueta de Envio Nube.

NO ESCRIBE NADA. Solo pregunta e imprime, para decidir con valores reales si
vale la pena sumar el campo a tiendanube.py.

POR QUE EXISTE
--------------
Lo que trae hoy el pedido (`shipping_cost_owner`) es el precio COTIZADO del
envio, no lo que nos factura Envio Nube por la etiqueta. En el pedido #160 la
etiqueta vale $17.522 y el campo dice $19.222. La diferencia es el recargo que
cubre impuestos, y en los pedidos con envio gratis importa.

Segun la documentacion, hay un endpoint que no usamos:

    GET /orders/{id}/fulfillment-orders  ->  shipping.merchant_cost
                                             shipping.consumer_cost

Lo que este script contesta:

  1. Si nuestro token tiene permiso para leerlo. Pide el scope
     `read_fulfillment_orders`, que es aparte de `read_orders`; sin el, la API
     contesta 401/403.
  2. Si `merchant_cost` es la etiqueta REAL ($17.522 en el #160) o vuelve a ser
     el precio cotizado ($19.222). Si es lo segundo, no aporta nada.
  3. En que unidad viene (pesos o centavos) y con que forma.

Se imprime solo el bloque `shipping` y el estado, no la direccion del
destinatario: son datos personales y no hacen falta para decidir.

Uso:
    python explorar_tn_envio_nube.py                 # los pedidos de referencia
    python explorar_tn_envio_nube.py 2054692871 ...  # otros, por ID (no numero)
"""

import json
import sys

import requests

# URL_BASE, HEADERS y el timeout se toman de tiendanube.py para que el sondeo
# pegue exactamente igual que la extraccion de verdad.
from tiendanube import HEADERS, TIMEOUT_HTTP, URL_BASE

# ID de la API -> lo que dicen hoy los dos importes de la cabecera.
REFERENCIA = {
    2054692871: "#160 Envio Nube    | cotizado $19.222 | etiqueta segun panel $17.522",
    2095117787: "#189 Envio Nube    | cotizado $7.213  | etiqueta: mirar en el panel",
    1980552241: "#115 Andreani      | cotizado $16.461 | envio gratis, cliente pago $0",
}


def mostrar(titulo, r):
    print(f"\n--- {titulo}: HTTP {r.status_code}")
    if r.status_code in (401, 403):
        print("  SIN PERMISO. Al token le falta el scope `read_fulfillment_orders`:")
        print("  hay que sumarlo en la app de Tienda Nube y reinstalarla para que")
        print("  emita un token nuevo.")
        print(" ", r.text[:300])
        return
    if not r.ok:
        print(" ", r.text[:500])
        return
    return r.json()


def sondear(pedido_id):
    print("\n" + "=" * 70)
    print(f"PEDIDO {pedido_id}   {REFERENCIA.get(pedido_id, '')}")
    print("=" * 70)

    r = requests.get(f"{URL_BASE}orders/{pedido_id}/fulfillment-orders",
                     headers=HEADERS, timeout=TIMEOUT_HTTP)
    datos = mostrar("fulfillment-orders", r)
    if datos is not None:
        for fo in datos if isinstance(datos, list) else [datos]:
            print("  campos:", ", ".join(sorted(fo)))
            print("  status:", fo.get("status"))
            print("  shipping:", json.dumps(fo.get("shipping"), ensure_ascii=False, indent=2))

    # Por si el endpoint de arriba no existe con ese nombre: la misma info
    # deberia venir agregada al pedido.
    r = requests.get(f"{URL_BASE}orders/{pedido_id}",
                     params={"aggregates": "fulfillment_orders"},
                     headers=HEADERS, timeout=TIMEOUT_HTTP)
    pedido = mostrar("orders?aggregates=fulfillment_orders", r)
    if pedido is not None:
        fos = pedido.get("fulfillment_orders") or pedido.get("fulfillments") or []
        for fo in fos:
            if isinstance(fo, dict):
                print("  shipping:", json.dumps(fo.get("shipping"), ensure_ascii=False, indent=2))
            else:
                print("  (solo el ID, sin detalle):", fo)


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:]] or list(REFERENCIA)
    for pedido_id in ids:
        sondear(pedido_id)
