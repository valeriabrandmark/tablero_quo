"""Prueba que una pagina vacia a destiempo CORTE la bajada de ventas.

QUE SE PRUEBA Y POR QUE. `/orders/search` a veces contesta 200 con
`results: []` sin haber llegado al final. Antes eso terminaba la paginacion en
silencio, y el paso seguia hasta `guardar_ventana_en_bd`, que BORRA la ventana
entera antes de insertar lo que se junto. Con media ventana en la mano, el
borrado se lleva puestas las ordenes que no se volvieron a bajar y el paso
reporta OK.

Casi siempre se arregla solo a la corrida siguiente. Lo que no se arregla nunca
es el dia que se cae del borde de los 7 dias antes de la proxima corrida buena:
ahi el hueco es permanente. Asi se formo el del 06/08/2026 --622 minutos sin una
sola venta, ~250 ordenes, ~$5 M-- que hubo que rellenar a mano.

    python probar_pagina_vacia_ml.py
"""

from datetime import date

import mercadolibre
import rellenar_ventas_ml

# Cualquier dia sirve: la API esta simulada y el rango no se usa para filtrar.
DIA = date(2026, 8, 6)

FALLOS = []


def revisar(nombre, ok, detalle=""):
    if ok:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}  {detalle}")
        FALLOS.append(nombre)


def paginas_falsas(total, cortar_en=None):
    """Simula `/orders/search`: devuelve `total` ordenes de a 50.

    Con `cortar_en` en un offset, esa pagina vuelve vacia -- la hipada de la
    API que hay que detectar.
    """
    def responder(endpoint, access_token, params=None, **kwargs):
        offset = params["offset"]
        limit = params["limit"]
        if cortar_en is not None and offset == cortar_en:
            return {"paging": {"total": total}, "results": []}
        ids = range(offset, min(offset + limit, total))
        return {
            "paging": {"total": total},
            "results": [{"id": 9000 + i, "last_updated": "2026-08-06"} for i in ids],
        }
    return responder


def correr(total, cortar_en, funcion):
    """Devuelve ('ok', cuantas) o ('error', mensaje)."""
    original = mercadolibre.llamar_ml
    mercadolibre.llamar_ml = paginas_falsas(total, cortar_en)
    try:
        return "ok", funcion()
    except RuntimeError as e:
        return "error", str(e)
    finally:
        mercadolibre.llamar_ml = original


def main():
    # --- El extractor de todos los dias ---------------------------------
    #
    # Se prueba el bucle de paginacion de `extraer_ventas_ml` a traves del
    # de `rellenar_ventas_ml`, que es el mismo bucle: los dos cortan con
    # RuntimeError cuando la pagina vacia llega antes de tiempo.
    pedir = rellenar_ventas_ml._pedir_ordenes

    # 1. Sin hipadas: baja las 300 y no se queja.
    estado, r = correr(300, None, lambda: pedir("tok", DIA, DIA))
    revisar("300 ordenes sin cortes: las baja todas",
            estado == "ok" and len(r[0]) == 300,
            f"-> {estado} {r if estado == 'error' else len(r[0])}")

    # 2. La pagina vacia en el medio TIENE que explotar, no devolver 100.
    estado, r = correr(300, 100, lambda: pedir("tok", DIA, DIA))
    revisar("pagina vacia en el offset 100 de 300: corta con error",
            estado == "error", f"-> {estado}, devolvio {r if estado == 'ok' else ''}")
    if estado == "error":
        revisar("  el error dice donde se corto", "offset 100" in r, f"-> {r}")

    # 3. La pagina vacia JUSTO AL FINAL es el final de verdad, no un error.
    #    Con 100 ordenes exactas el offset llega a 100 y corta por cuenta
    #    propia; el caso a cubrir es el de un total que no es multiplo de 50.
    estado, r = correr(100, 100, lambda: pedir("tok", DIA, DIA))
    revisar("pagina vacia al llegar al total: termina normal",
            estado == "ok" and len(r[0]) == 100,
            f"-> {estado} {r if estado == 'error' else len(r[0])}")

    # 4. Cero ordenes en el rango no es un error: es un rango sin ventas.
    estado, r = correr(0, 0, lambda: pedir("tok", DIA, DIA))
    revisar("rango sin ventas: no explota",
            estado == "ok" and len(r[0]) == 0,
            f"-> {estado} {r}")

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) MAL: {', '.join(FALLOS)}")
        raise SystemExit(1)
    print("Todas las pruebas pasaron.")


if __name__ == "__main__":
    main()
