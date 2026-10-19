"""Sondeo: con el token nuevo, ¿la API trae los costos reales de Pago Nube?

NO ESCRIBE NADA. Pregunta, busca e imprime.

Los importes a buscar salen del exportable de ventas del panel, pedido #189:
procesamiento 1372.84, impuestos 151.77, interes por cuotas 6428 y neto
29989.97. Ninguno esta en lo que hoy guardamos de `orders`; este script prueba
los endpoints que podrian tenerlos y dice en cual aparece cada uno.

Se imprime el estado HTTP de cada endpoint, que importes encontro y, completa,
la respuesta de `transactions`, que es la candidata. El resto no se imprime
entero porque trae datos personales del comprador.

Uso:
    python explorar_tn_costos.py
"""

import json

import requests

from tiendanube import HEADERS, TIMEOUT_HTTP, URL_BASE

PEDIDO = 2095117787   # el #189

BUSCADOS = {
    "procesamiento": ["1372.84", "137284"],
    "impuestos":     ["151.77", "15177"],
    "interes":       ["6428"],
    "neto":          ["29989.97", "2998997"],
}

ENDPOINTS = [
    f"orders/{PEDIDO}/transactions",
    f"orders/{PEDIDO}/fulfillment-orders",
    f"orders/{PEDIDO}",
    f"orders/{PEDIDO}?aggregates=fulfillment_orders",
    "payment_providers",
]


def main():
    for ep in ENDPOINTS:
        r = requests.get(URL_BASE + ep, headers=HEADERS, timeout=TIMEOUT_HTTP)
        texto = r.text
        print("\n" + "=" * 70)
        print(f"{ep}  ->  HTTP {r.status_code}")
        if not r.ok:
            print(" ", texto[:300])
            continue
        encontrados = [nombre for nombre, valores in BUSCADOS.items()
                       if any(v in texto for v in valores)]
        print("  importes encontrados:", ", ".join(encontrados) or "NINGUNO")
        if "transactions" in ep or "payment_providers" in ep:
            print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:6000])


if __name__ == "__main__":
    main()
