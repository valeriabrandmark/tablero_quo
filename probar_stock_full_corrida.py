"""El stock de Full en cada corrida: un inventario que ML no contesta no
puede pasar a valer cero. Sin base ni red.

Desde el 27/11/2026 el stock de Full se pide en cada pasada del orquestador. Una
fila con `error` no trae unidades y el tablero la lee como CERO: con una
corrida por hora, un 429 suelto haria aparecer y desaparecer stock de Full en
Compras de una hora a la otra.
"""

from mercadolibre import heredar_fallados

FALLOS = []


def revisar(nombre, obtenido, esperado):
    ok = obtenido == esperado
    print(f"OK  {nombre}" if ok else
          f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
    if not ok:
        FALLOS.append(nombre)


anteriores = {
    "HCYA59313": {"inventory_id": "HCYA59313", "total": 24, "available_quantity": 24,
                  "not_available_quantity": 0},
}

filas = [
    {"inventory_id": "HCYA59313", "error": "429 Too Many Requests"},
    {"inventory_id": "YUZF71836", "total": 4, "available_quantity": 4,
     "not_available_quantity": 0},
    {"inventory_id": "NUEVO1", "error": "500"},
]

salida, heredados = heredar_fallados(filas, anteriores)
por_id = {f["inventory_id"]: f for f in salida}

revisar("se heredo uno", heredados, 1)
revisar("el que fallo conserva sus 24 u. de la corrida anterior",
        por_id["HCYA59313"]["available_quantity"], 24)
revisar("  y sigue marcado con el error (para contarlo y que no entre a la foto)",
        por_id["HCYA59313"]["error"], "429 Too Many Requests")
revisar("  y como heredado", por_id["HCYA59313"]["heredado"], True)
revisar("el que contesto queda como vino", por_id["YUZF71836"]["available_quantity"], 4)
revisar("  sin marca de heredado", por_id["YUZF71836"]["heredado"], False)
revisar("sin dato anterior no se inventa nada: queda con su error",
        (por_id["NUEVO1"].get("available_quantity"), por_id["NUEVO1"]["error"]), (None, "500"))
revisar("no se pierde ni se duplica ningun inventario", len(salida), 3)

# Un error en la corrida anterior no se hereda: solo el ultimo dato BUENO.
salida, heredados = heredar_fallados([{"inventory_id": "X", "error": "500"}], {})
revisar("sin dato bueno anterior, nada que heredar", heredados, 0)

print(f"\n{len(FALLOS)} pruebas fallaron" if FALLOS else "\nTodo OK")
raise SystemExit(1 if FALLOS else 0)
