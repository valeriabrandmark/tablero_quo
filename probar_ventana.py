"""Pruebas de la ventana que se estira sola. Sin base ni red.

QUE SE PRUEBA Y POR QUE. La ventana era una constante --`hoy - 7 dias`-- y eso
alcanza mientras el pipeline corra. Falla justo cuando no corre: si un paso
esta caido ocho dias, la primera corrida que vuelve a funcionar pide siete y
EL DIA 1 YA NO ENTRA. Nadie lo vuelve a pedir nunca, porque la ventana
siguiente esta todavia mas adelante.

Ese agujero no da error. Es el mismo tipo de falla que dejo 622 minutos sin
ventas de ML el 06/08 y no se vio hasta seis semanas despues.

Lo que se fija aca es que la ventana SOLO SE ESTIRA, nunca se achica: ante
cualquier duda --no se puede leer el estado, el paso nunca corrio, la fecha
guardada es ilegible-- se pide de mas, que es barato, en vez de dejar un
hueco.

    python probar_ventana.py
"""

import datetime

import ventana

FALLOS = []


def revisar(nombre, obtenido, esperado):
    ok = obtenido == esperado
    print(f"OK  {nombre}" if ok else f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
    if not ok:
        FALLOS.append(nombre)


HOY = datetime.date(2026, 9, 22)
PISO = datetime.date(2026, 5, 6)
DIAS = 7
PASO = "mercadolibre.py --ventas"


def pasos_con(ok):
    """El estado tal como lo guarda el orquestador."""
    return {PASO: {"ok": ok, "fallos": 0, "ultimo_fallo": None, "error": None}}


def cutoff(pasos):
    return ventana.cutoff(PASO, DIAS, PISO, HOY, pasos=pasos)


def main():
    # --- El caso normal: el paso corrio recien, ventana de siempre -----------
    revisar(
        "corrio hace un rato: la ventana normal de 7 dias",
        cutoff(pasos_con("2026-09-22T09:56:22")),
        datetime.date(2026, 9, 15),
    )
    revisar(
        "corrio ayer: sigue siendo la normal",
        cutoff(pasos_con("2026-09-21T23:10:00")),
        datetime.date(2026, 9, 15),
    )

    # --- EL CASO QUE IMPORTA: el paso estuvo caido --------------------------
    #
    # Caido desde el 10/09. La ventana normal (15/09) dejaria afuera del 10 al
    # 14: cinco dias que nadie volveria a pedir nunca.
    revisar(
        "caido 12 dias: la ventana se estira hasta cubrirlos",
        cutoff(pasos_con("2026-09-10T04:00:00")),
        datetime.date(2026, 9, 9),  # el 10 menos el dia de colchon
    )
    revisar(
        "caido dos meses: se estira dos meses",
        cutoff(pasos_con("2026-07-20T04:00:00")),
        datetime.date(2026, 7, 19),
    )

    # --- El piso historico manda por encima de todo -------------------------
    #
    # Sin esto, un `ok` viejisimo --o corrupto-- haria pedir desde el principio
    # de los tiempos a una API que no tiene nada de antes.
    revisar(
        "caido desde antes del piso: no se pide nada anterior al piso",
        cutoff(pasos_con("2026-01-15T04:00:00")),
        PISO,
    )

    # --- Ante la duda, la ventana normal: NUNCA menos -----------------------
    revisar("el paso nunca corrio", cutoff({}), datetime.date(2026, 9, 15))
    revisar(
        "la fecha guardada es ilegible",
        cutoff(pasos_con("el martes pasado")),
        datetime.date(2026, 9, 15),
    )
    revisar("el paso esta pero sin ok", cutoff({PASO: {"ok": None}}),
            datetime.date(2026, 9, 15))

    # Formato viejo: antes se guardaba solo la fecha, como texto suelto.
    revisar(
        "el formato viejo (texto suelto) se sigue entendiendo",
        cutoff({PASO: "2026-09-10T04:00:00"}),
        datetime.date(2026, 9, 9),
    )

    # --- Un paso no mira el estado de otro ----------------------------------
    #
    # Si `cutoff` se equivocara de clave, un paso sano heredaria la ventana
    # estirada de uno caido -- y peor, uno caido usaria la normal de uno sano.
    revisar(
        "el estado de OTRO paso no lo afecta",
        cutoff({"sigma.py --ventas": {"ok": "2026-07-01T00:00:00"}}),
        datetime.date(2026, 9, 15),
    )

    # --- Y las funciones que el orquestador comparte ------------------------
    revisar(
        "ultima_corrida lee el formato nuevo",
        ventana.ultima_corrida(pasos_con("2026-09-10T04:00:00"), PASO),
        datetime.datetime(2026, 9, 10, 4, 0, 0),
    )
    revisar(
        "y el viejo",
        ventana.ultima_corrida({PASO: "2026-09-10T04:00:00"}, PASO),
        datetime.datetime(2026, 9, 10, 4, 0, 0),
    )
    revisar("sin estado devuelve None", ventana.ultima_corrida({}, PASO), None)
    revisar(
        "registro normaliza el formato viejo",
        ventana.registro({PASO: "2026-09-10T04:00:00"}, PASO),
        {"ok": "2026-09-10T04:00:00", "fallos": 0, "ultimo_fallo": None, "error": None},
    )

    print()
    if FALLOS:
        print(f"{len(FALLOS)} prueba(s) MAL: {', '.join(FALLOS)}")
        raise SystemExit(1)
    print("Todas las pruebas pasaron.")


if __name__ == "__main__":
    main()
