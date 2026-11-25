"""Pruebas del plan de ml_stock_fixy.py: sin base, sin red y sin escribir.

    python probar_ml_stock_fixy.py
"""

from ml_stock_fixy import armar_plan, cantidad_objetivo, demasiados_ceros

FALLOS = []


def revisar(que, real, esperado):
    ok = real == esperado
    print(f"{'OK ' if ok else 'FALLA'}  {que}" + ("" if ok else f"  (dio {real!r}, se esperaba {esperado!r})"))
    if not ok:
        FALLOS.append(que)


PUBS = [
    # Diaper Genie en Flex, dos publicaciones del mismo user product.
    {"item_id": "MLA1", "user_product_id": "UP1", "sku": "AC01004", "logistica": "self_service", "status": "active"},
    {"item_id": "MLA2", "user_product_id": "UP1", "sku": "AC01004", "logistica": "self_service", "status": "paused"},
    # El mismo SKU en Full: NUNCA se toca.
    {"item_id": "MLA3", "user_product_id": "UP9", "sku": "AC01004", "logistica": "fulfillment", "status": "active"},
    # Colecta desde Tucuman: NUNCA se toca.
    {"item_id": "MLA4", "user_product_id": "UP4", "sku": "AC01007", "logistica": "cross_docking", "status": "paused"},
    # Flex de un SKU que FIXY no tiene: va a CERO (todo Flex sale de FIXY).
    {"item_id": "MLA5", "user_product_id": "UP5", "sku": "SS06001", "logistica": "self_service", "status": "active"},
    # Flex sin SKU: no se puede buscar en FIXY, no entra.
    {"item_id": "MLA7", "user_product_id": "UP7", "sku": None, "logistica": "self_service", "status": "active"},
    # Flex de un SKU que estuvo en FIXY y se agoto: va a cero.
    {"item_id": "MLA6", "user_product_id": "UP6", "sku": "AC01003", "logistica": "self_service", "status": "active"},
]
STOCK = {"AC01004": 192.0, "AC01007": 290.0}

plan = armar_plan(PUBS, STOCK)
revisar("todo Flex entra; lo que FIXY no tiene va a cero",
        [(f["user_product_id"], f["objetivo"]) for f in plan],
        [("UP6", 0), ("UP1", 192), ("UP5", 0)])
revisar("un user product se escribe una vez con todas sus publicaciones",
        next(f["items"] for f in plan if f["user_product_id"] == "UP1"), ["MLA1", "MLA2"])
revisar("Full no entra", any("MLA3" in f["items"] for f in plan), False)
revisar("colecta desde Tucuman no entra", any("MLA4" in f["items"] for f in plan), False)
revisar("Flex sin SKU no entra", any("MLA7" in f["items"] for f in plan), False)

revisar("la reserva se descuenta", cantidad_objetivo(192, 5), 187)
revisar("nunca negativo", cantidad_objetivo(3, 5), 0)
revisar("sin dato es cero", cantidad_objetivo(None), 0)
revisar("se trunca a entero", cantidad_objetivo(7.9), 7)

ceros = [{"user_product_id": f"UP{i}", "items": [f"M{i}"], "objetivo": 0} for i in range(12)]
revisar("apagar 12 de golpe frena", demasiados_ceros(ceros, {f"UP{i}": 5 for i in range(12)}), True)
revisar("12 que ya estaban en cero no frenan", demasiados_ceros(ceros, {f"UP{i}": 0 for i in range(12)}), False)
revisar("apagar 3 no frena", demasiados_ceros(ceros[:3], {f"UP{i}": 5 for i in range(3)}), False)

print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLAS")
raise SystemExit(1 if FALLOS else 0)
