"""Pruebas de la bajada de ventas de ML, sin tocar la red.

QUE SE PRUEBA Y POR QUE. La bajada tiene dos formas de traer de menos SIN
DAR ERROR, y las dos son peligrosas por lo que viene despues: el guardado de
la corrida diaria BORRA la ventana antes de insertar lo que se junto. Traer
de menos no deja un dato viejo -- borra el bueno.

  1. Una pagina vacia a destiempo. `/orders/search` contesta 200 con
     `results: []` cuando hipa, sin haber llegado al final.

  2. El tope de offset. La API no devuelve NADA pasado el offset 10.000: no
     da error, se corta. Un rango con mas ordenes que eso se baja recortado.

El (1) casi siempre se arregla solo a la corrida siguiente. Lo que no se
arregla nunca es el dia que se cae del borde de los 7 dias antes de la
proxima corrida buena: ahi el hueco es permanente. Asi se formo el del
06/08/2026 --622 minutos sin una sola venta, ~250 ordenes, ~$5 M-- que hubo
que rellenar a mano.

El (2) importa justo cuando se usa el relleno: toda la historia son ~56.000
ordenes, y pedidas de una la API contestaria las primeras 10.000 y el relleno
diria que termino bien habiendo mirado menos de un quinto.

    python probar_pagina_vacia_ml.py
"""

from datetime import date, datetime, timedelta

import mercadolibre
import rellenar_ventas_ml as rellenar

FALLOS = []


def revisar(nombre, ok, detalle=""):
    if ok:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}  {detalle}")
        FALLOS.append(nombre)


def api_falsa(por_dia, cortar_en=None):
    """Simula `/orders/search` sobre un mundo de `por_dia` ordenes por dia.

    Filtra por el rango pedido igual que la API de verdad, asi que sirve para
    probar que partir un tramo al medio cubre los mismos dias sin repetir.

    Con `cortar_en` en un offset, esa pagina vuelve vacia -- la hipada.
    """
    def responder(endpoint, access_token, params=None, **kwargs):
        desde = datetime.strptime(params["order.date_created.from"][:10], "%Y-%m-%d").date()
        hasta = datetime.strptime(params["order.date_created.to"][:10], "%Y-%m-%d").date()

        ids = []
        dia = desde
        while dia <= hasta:
            ids += [f"{dia}#{i}" for i in range(por_dia.get(dia, 0))]
            dia += timedelta(days=1)

        offset, limit = params["offset"], params["limit"]
        if cortar_en is not None and offset == cortar_en:
            return {"paging": {"total": len(ids)}, "results": []}
        return {
            "paging": {"total": len(ids)},
            "results": [{"id": i, "last_updated": "2026-08-06"}
                        for i in ids[offset:offset + limit]],
        }
    return responder


def correr(por_dia, cortar_en, funcion):
    """Devuelve ('ok', lo que devolvio) o ('error', el mensaje)."""
    original = mercadolibre.llamar_ml
    mercadolibre.llamar_ml = api_falsa(por_dia, cortar_en)
    try:
        return "ok", funcion()
    except RuntimeError as e:
        return "error", str(e)
    finally:
        mercadolibre.llamar_ml = original


def mundo(desde, hasta, por_dia):
    d, salida = desde, {}
    while d <= hasta:
        salida[d] = por_dia
        d += timedelta(days=1)
    return salida


DESDE = date(2026, 8, 1)
HASTA = date(2026, 8, 6)


def main():
    pedir = rellenar._pedir_tramo

    # --- 1. El caso normal ----------------------------------------------
    estado, r = correr(mundo(DESDE, HASTA, 50), None,
                       lambda: pedir("tok", DESDE, HASTA))
    revisar("6 dias x 50 ordenes: baja las 300",
            estado == "ok" and len(r) == 300,
            f"-> {estado} {r if estado == 'error' else len(r)}")

    # Un total que no es multiplo del tamaño de pagina: el bucle tiene que
    # cortar solo, sin pedir una pagina de mas.
    estado, r = correr({DESDE: 130}, None, lambda: pedir("tok", DESDE, DESDE))
    revisar("130 ordenes (no es multiplo de 50): baja las 130",
            estado == "ok" and len(r) == 130,
            f"-> {estado} {r if estado == 'error' else len(r)}")

    # --- 2. La pagina vacia a destiempo ----------------------------------
    estado, r = correr(mundo(DESDE, HASTA, 50), 100,
                       lambda: pedir("tok", DESDE, HASTA))
    revisar("pagina vacia en el offset 100 de 300: corta con error",
            estado == "error", f"-> {estado}, devolvio {r if estado == 'ok' else ''}")
    if estado == "error":
        revisar("  el error dice donde se corto", "offset 100" in r, f"-> {r}")

    # --- 3. Un rango sin ventas no es un error ---------------------------
    estado, r = correr({}, None, lambda: pedir("tok", DESDE, HASTA))
    revisar("rango sin ventas: no explota y devuelve 0",
            estado == "ok" and len(r) == 0, f"-> {estado} {r}")

    # --- 4. El tope de offset: el tramo se parte solo --------------------
    #
    # 20 dias x 500 son 10.000, por encima del tope seguro. Tiene que partirse
    # hasta que cada pedazo entre, y traer las 10.000 igual: ni una de menos
    # (se perderian ventas) ni una repetida (los bordes se solaparian).
    veinte = mundo(date(2026, 8, 1), date(2026, 8, 20), 500)
    estado, r = correr(veinte, None,
                       lambda: pedir("tok", date(2026, 8, 1), date(2026, 8, 20)))
    revisar("20 dias x 500: parte el tramo y baja las 10.000",
            estado == "ok" and len(r) == 10000,
            f"-> {estado} {r if estado == 'error' else len(r)}")
    if estado == "ok":
        ids = [o["id"] for o in r]
        revisar("  sin repetir ninguna al partir", len(set(ids)) == len(ids),
                f"-> {len(ids) - len(set(ids))} repetidas")
        revisar("  y sin saltearse ningun dia",
                len({i.split('#')[0] for i in ids}) == 20,
                f"-> {len({i.split('#')[0] for i in ids})} dias de 20")

    # --- 5. Un solo dia que no entra no se puede partir mas --------------
    estado, r = correr({DESDE: 12000}, None, lambda: pedir("tok", DESDE, DESDE))
    revisar("un solo dia por encima del tope: corta con error",
            estado == "error", f"-> {estado}")
    if estado == "error":
        revisar("  el error dice que hay que partir por hora",
                "por hora" in r, f"-> {r}")

    # --- 6. Los tramos cubren el rango entero ----------------------------
    tramos = list(rellenar._tramos(date(2026, 5, 6), date(2026, 9, 21), 15))
    dias = []
    for a, b in tramos:
        d = a
        while d <= b:
            dias.append(d)
            d += timedelta(days=1)
    esperados = (date(2026, 9, 21) - date(2026, 5, 6)).days + 1
    revisar(f"los tramos de 15 dias cubren los {esperados} dias sin huecos",
            len(dias) == esperados and len(set(dias)) == esperados,
            f"-> {len(dias)} dias, {len(set(dias))} distintos")
    revisar("  y el ultimo tramo termina justo en la fecha pedida",
            tramos[-1][1] == date(2026, 9, 21), f"-> {tramos[-1][1]}")

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) MAL: {', '.join(FALLOS)}")
        raise SystemExit(1)
    print("Todas las pruebas pasaron.")


if __name__ == "__main__":
    main()
