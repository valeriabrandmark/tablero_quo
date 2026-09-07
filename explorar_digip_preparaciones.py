"""Sondeo de la API de Digip para la trazabilidad del stock en Full.

NO ESCRIBE NADA. Sólo pregunta e imprime, para poder decidir el modelo con
nombres de campo reales en vez de adivinados.

POR QUE EXISTE
--------------
Se quiere reconstruir, dia por dia, cuanto stock mandamos a Full y compararlo
con lo que Mercado Libre declara: cuando ML "elimina" unidades sin avisar, hoy
no hay forma de darse cuenta. Para eso hace falta la pata de los INGRESOS, que
son las preparaciones despachadas a Full.

Al ir a buscarla aparecieron tres cosas que este script viene a contestar:

  1. `digip_preparaciones.py` ya pide `Preparaciones/{codigo}`, y esa respuesta
     trae un array `items` -- pero el script sólo le suma `volumen` y `peso` y
     tira el resto. Las cantidades por articulo (las "satisfechas") estan ahi y
     se descartan. FALTA SABER COMO SE LLAMAN LOS CAMPOS.

  2. Los dos despachos que van a Full son "ETIQUETADO MELI QUO" y
     "ANDREANI BRANDMARK". En la base hay CERO del primero y UNA del segundo,
     asi que el flujo no se esta capturando.

  3. `digip_pedidos.py` pide sólo `PedidoEstado=RemitidoExterno`. Si los
     despachos a Full quedan en otro estado, ese filtro los tapa a todos.

Se corre a mano desde Actions (workflow "Sondeo Digip"). Es de lectura y con un
tope de llamadas: no toca la ventana movil ni el orquestador.
"""

import argparse
import json
import os
from collections import Counter
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("DIGIP_URL_BASE")
API_KEY = os.getenv("DIGIP_API_KEY")
HEADERS = {"X-API-Key": API_KEY}

# Los dos despachos que van al deposito de Mercado Libre. Se comparan en
# mayusculas y sin espacios de sobra: en la base ya hay un "CONSUMO INTERNO "
# con espacio al final, asi que el dato viene como viene.
DESPACHOS_A_FULL = {"ETIQUETADO MELI QUO", "ANDREANI BRANDMARK"}

# Tope de llamadas del sondeo. Es una exploracion, no una carga: con esto
# alcanza para ver la forma de la respuesta sin castigar a la API de nadie.
TOPE_PEDIDOS = 60


def pedir(endpoint, params=None):
    """Una llamada. Devuelve (status, json o None). NUNCA levanta."""
    try:
        r = requests.get(f"{BASE}{endpoint}", headers=HEADERS, params=params, timeout=30)
    except Exception as e:
        return None, {"error": str(e)}
    if r.status_code != 200:
        return r.status_code, None
    try:
        return 200, r.json()
    except Exception:
        return 200, None


def titulo(t):
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def preg_1_estados(desde, hasta):
    """Que estados de pedido existen, SIN el filtro de RemitidoExterno."""
    titulo("1. Estados de pedido que devuelve la API (sin filtrar por estado)")

    params = {
        "FechaPedidoDesde": f"{desde}T00:00:00",
        "FechaPedidoHasta": f"{hasta}T23:59:59",
        "Page": 1, "PerPage": 500, "OrderCriteria": "CodigoPedido",
    }
    status, pedidos = pedir("Pedidos", params)
    if status != 200 or not pedidos:
        print(f"  Sin datos (HTTP {status}). Puede ser que el estado sea obligatorio.")
        return []

    estados = Counter(p.get("estado") for p in pedidos)
    print(f"  {len(pedidos)} pedidos entre {desde} y {hasta}:")
    for est, n in estados.most_common():
        marca = "   <-- el unico que se ingesta hoy" if est == "RemitidoExterno" else ""
        print(f"    {str(est):<28} {n:>5}{marca}")
    if len(estados) > 1:
        print("\n  OJO: hay mas de un estado. El filtro de digip_pedidos.py deja")
        print("  afuera todo lo que no sea RemitidoExterno.")
    return pedidos


def preg_2_listar_preparaciones(desde, hasta):
    """Si `Preparaciones` se puede listar por fecha, sin caminar pedido por pedido."""
    titulo("2. Se pueden listar las preparaciones directo, por fecha?")
    print("  Si esto anda, no hace falta recorrer los pedidos uno por uno --que")
    print("  es lo que hoy obliga a la ventana de 7 dias y al filtro de estado.")

    for params in (
        {"FechaDesde": f"{desde}T00:00:00", "FechaHasta": f"{hasta}T23:59:59",
         "Page": 1, "PerPage": 100},
        {"Page": 1, "PerPage": 100},
        None,
    ):
        status, datos = pedir("Preparaciones", params)
        print(f"\n  params={params if params else '(ninguno)'} -> HTTP {status}")
        if status == 200 and datos:
            n = len(datos) if isinstance(datos, list) else 1
            print(f"    SI: devolvio {n} preparaciones.")
            if isinstance(datos, list) and datos:
                print(f"    Campos: {', '.join(sorted(datos[0].keys()))}")
            return datos
    print("\n  NO se puede listar: hay que seguir yendo pedido por pedido.")
    return None


def preg_3_forma_de_items(pedidos):
    """Los nombres reales de los campos de `items`, que es donde estan las cantidades."""
    titulo("3. Que trae `items` de una preparacion (LA PREGUNTA IMPORTANTE)")

    codigos = [str(p.get("codigo")) for p in pedidos if p.get("codigo")][:TOPE_PEDIDOS]
    if not codigos:
        print("  Sin pedidos con los que probar.")
        return

    print(f"  Probando hasta {len(codigos)} pedidos...")
    despachos = Counter()
    mostrada = False
    a_full = []

    for cod in codigos:
        status, prep = pedir(f"Preparaciones/{cod}")
        if status != 200 or not prep:
            continue

        despacho = prep.get("despachoDescripcion") or prep.get("despachoCodigo") or "(sin despacho)"
        despachos[despacho] += 1
        if str(despacho).strip().upper() in DESPACHOS_A_FULL:
            a_full.append((cod, prep))

        items = prep.get("items") or []
        if items and not mostrada:
            mostrada = True
            print(f"\n  --- Preparacion del pedido {cod}, despacho '{despacho}' ---")
            print(f"  Campos de la preparacion: {', '.join(sorted(prep.keys()))}")
            print(f"  {len(items)} items. Campos del primero:")
            for k, v in sorted(items[0].items()):
                print(f"    {k:<28} = {json.dumps(v, ensure_ascii=False)[:70]}")
            print("\n  Item completo, crudo:")
            print("  " + json.dumps(items[0], ensure_ascii=False, indent=2).replace("\n", "\n  "))

    print(f"\n  --- Despachos vistos en los {len(codigos)} pedidos probados ---")
    for d, n in despachos.most_common():
        marca = "   <-- VA A FULL" if str(d).strip().upper() in DESPACHOS_A_FULL else ""
        print(f"    {str(d):<32} {n:>4}{marca}")

    if not a_full:
        print("\n  No aparecio ninguno de los dos despachos de Full en esta muestra.")
        print("  Puede ser la ventana de fechas, o que esos pedidos esten en otro estado.")
        return

    titulo("4. Las preparaciones que SI van a Full")
    for cod, prep in a_full[:3]:
        items = prep.get("items") or []
        print(f"\n  Pedido {cod} -- {prep.get('despachoDescripcion')} -- {len(items)} items")
        for it in items[:8]:
            # Se imprime el item entero porque justamente no sabemos todavia
            # cual de sus campos es "la cantidad satisfecha".
            print(f"    {json.dumps(it, ensure_ascii=False)[:160]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias", type=int, default=30,
                    help="Cuantos dias para atras mirar (default 30)")
    args = ap.parse_args()

    if not BASE or not API_KEY:
        raise SystemExit("Faltan DIGIP_URL_BASE o DIGIP_API_KEY en el entorno.")

    hasta = date.today()
    desde = hasta - timedelta(days=args.dias)
    print(f"Sondeo de Digip, del {desde} al {hasta}. NO ESCRIBE NADA EN LA BASE.")

    pedidos = preg_1_estados(desde, hasta)
    preg_2_listar_preparaciones(desde, hasta)
    preg_3_forma_de_items(pedidos)

    titulo("Fin")
    print("  Con los nombres de campo de `items` ya se puede definir la tabla")
    print("  bronze.digip_preparacion_items y arrancar a guardar los ingresos.")


if __name__ == "__main__":
    main()
