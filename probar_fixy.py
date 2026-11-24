"""Pruebas de `normalizar` en fixy.py: sin base y sin red.

La respuesta de consultarStock no esta documentada, asi que se prueban las
formas que podria tener. Si la primera corrida real trae otra, se agrega aca.

    python probar_fixy.py
"""

from fixy import normalizar

FALLOS = []


def revisar(que, real, esperado):
    ok = real == esperado
    print(f"{'OK ' if ok else 'FALLA'}  {que}" + ("" if ok else f"  (dio {real!r}, se esperaba {esperado!r})"))
    if not ok:
        FALLOS.append(que)


def cantidades(r):
    return {k: v[0] for k, v in normalizar(r).items()}


# La respuesta REAL de FIXY (24/11/2026). Es la que importa: las de abajo son
# por si algun dia cambia de forma.
REAL = {"status": "ok", "data": [
    {"articulo_sku": "AC01004", "articulo": "Diaper Genie Repuesto Cuadrado",
     "cliente_nombre": "QUO MARKETING SRL", "stock_actual": 192,
     "stock_armado": 0, "stock_pendiente": 0, "stock_total": 192},
    {"articulo_sku": "AC01005", "articulo": "Diaper Genie Repuesto Signature",
     "cliente_nombre": "QUO MARKETING SRL", "stock_actual": 48,
     "stock_armado": 2, "stock_pendiente": 1, "stock_total": 51},
]}
revisar("respuesta real: SKU de articulo_sku y cantidad de stock_actual",
        cantidades(REAL), {"AC01004": 192.0, "AC01005": 48.0})
revisar("la descripcion (`articulo`) nunca se toma como SKU",
        cantidades([{"articulo": "Diaper Genie", "stock_actual": 3}]), {})
revisar("rechazo de FIXY no da filas",
        cantidades({"status": "error", "message": "skus requerido"}), {})

revisar("lista con sku y stock",
        cantidades([{"sku": "A1", "stock": 10}, {"sku": "B2", "stock": "3"}]),
        {"A1": 10.0, "B2": 3.0})
revisar("disponible gana sobre stock",
        cantidades([{"sku": "A1", "stock": 10, "disponible": 7}]), {"A1": 7.0})
revisar("envuelta en status/message",
        cantidades({"status": "ok", "message": [{"codigo": "A1", "cantidad": 4}]}), {"A1": 4.0})
revisar("envuelta en data",
        cantidades({"data": [{"sku": " A1 ", "total": 2}]}), {"A1": 2.0})
revisar("dict por sku con numero",
        cantidades({"A1": 5, "B2": 0}), {"A1": 5.0, "B2": 0.0})
revisar("dict por sku con objeto",
        cantidades({"A1": {"stock": 8}}), {"A1": 8.0})
revisar("sku sin cantidad reconocible queda en None (y se guarda el crudo)",
        cantidades([{"sku": "A1", "raro": 8}]), {"A1": None})
revisar("item sin sku se ignora",
        cantidades([{"stock": 8}]), {})
revisar("respuesta vacia", cantidades([]), {})
revisar("respuesta null", cantidades(None), {})
revisar("el crudo se conserva",
        normalizar([{"sku": "A1", "stock": 1, "lote": "X"}])["A1"][1],
        {"sku": "A1", "stock": 1, "lote": "X"})

print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLAS")
raise SystemExit(1 if FALLOS else 0)
