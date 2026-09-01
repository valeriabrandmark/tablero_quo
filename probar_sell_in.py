"""Pruebas del lector de sell in, sin red ni base.

QUE SE PRUEBA. Lo unico que puede salir mal en silencio: entender mal un
encabezado y guardar la oferta de agosto como si fuera de enero, o leer un
"7,69%" como 0,0769. Las dos cosas terminan en la columna FDESCU1 de una orden
de compra que alguien importa a Sigma sin volver a mirarla.

Ya nos paso una vez con esta misma planilla: leerla por posicion desalineo
4.368 valores y nada aviso.

    python probar_sell_in.py
"""

from sell_in import filas_de_la_planilla, interpretar_encabezado, limpiar_pct

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


# --- El encabezado: de que oferta es esta columna --------------------------

revisar("mes suelto", interpretar_encabezado("1/8/2026"), ("2026-08", ""))
revisar("mes con cero adelante", interpretar_encabezado("01/08/2026"), ("2026-08", ""))
revisar("mes con guiones", interpretar_encabezado("1-8-2026"), ("2026-08", ""))
revisar("mes con espacios", interpretar_encabezado("  1/8/2026  "), ("2026-08", ""))
revisar("diciembre", interpretar_encabezado("1/12/2026"), ("2026-12", ""))

# Un aclarador entre parentesis es un corte DENTRO de ese mes, no otro mes.
revisar("mes con aclaracion", interpretar_encabezado("1/7/2026 (Glade)"), ("2026-07", "Glade"))
revisar("mes con aclaracion pegada", interpretar_encabezado("1/7/2026(Glade)"), ("2026-07", "Glade"))

# Un evento no se lleva un mes inventado, aunque este al lado de uno.
revisar("evento sin fecha", interpretar_encabezado("HOT SALE"), ("", "HOT SALE"))
revisar("evento con año", interpretar_encabezado("Hotsale 2026"), ("", "Hotsale 2026"))

# EL DIA TIENE QUE SER 1. Con "3/8/2026" no se sabe si es 3 de agosto o 8 de
# marzo, asi que cae como evento en vez de elegir un mes al azar.
revisar("dia distinto de 1 no es mes", interpretar_encabezado("3/8/2026"), ("", "3/8/2026"))
revisar("mes 13 no es mes", interpretar_encabezado("1/13/2026"), ("", "1/13/2026"))

# Las columnas del articulo y las vacias no son ofertas.
for texto in ["PROVEEDOR", "MARCA", "COD PROV", "SKU", "EAN", "DESCRIPCIÓN",
              "descripcion", " sku ", "", "   ", None]:
    revisar(f"no es oferta: {texto!r}", interpretar_encabezado(texto), None)


# --- El valor: de "7,69%" a 7.69 ------------------------------------------

revisar("porcentaje con coma", limpiar_pct("7,69%"), 7.69)
revisar("porcentaje redondo", limpiar_pct("20,00%"), 20.0)
revisar("cero", limpiar_pct("0,00%"), 0.0)
revisar("sin simbolo", limpiar_pct("15"), 15.0)
revisar("con punto decimal", limpiar_pct("7.69"), 7.69)
revisar("con separador de miles", limpiar_pct("1.234,56"), 1234.56)
revisar("con espacio antes del simbolo", limpiar_pct("7,69 %"), 7.69)
revisar("vacio", limpiar_pct(""), None)
revisar("guion", limpiar_pct("-"), None)
revisar("texto cualquiera", limpiar_pct("s/d"), None)
revisar("None", limpiar_pct(None), None)

# Si la celda llega como numero con formato porcentaje.
revisar("fraccion", limpiar_pct(0.0769), 7.69)
revisar("numero entero", limpiar_pct(15), 15.0)
# EL BORDE QUE IMPORTA: 1 es 1 %, no 100 %. Ver la nota en limpiar_pct.
revisar("el uno es un uno por ciento", limpiar_pct(1), 1.0)
revisar("apenas menos que uno", limpiar_pct(0.99), 99.0)


# --- La hoja entera, armada como la de verdad ------------------------------

HOJA = [
    ["PROVEEDOR", "MARCA", "COD PROV", "SKU", "EAN", "DESCRIPCIÓN",
     "1/5/2026", "HOT SALE", "1/6/2026", "1/7/2026 (Glade)", "1/8/2026"],
    ["ALGABO S. A.", "ALGABO BABY", "3367234", "AL01001", "7791274001567",
     "Algabo Baby aceite 200", "0,00%", "0,00%", "0,00%", "0,00%", "0,00%"],
    ["ALGABO S. A.", "ALGABO BABY", "3369030", "AL01012", "7791274200670",
     "ALGABO BABY HISOPOS BOX 24", "7,69%", "0,00%", "7,69%", "7,69%", "0,00%"],
    ["ALGABO S. A.", "ALGABO BABY", "3369031", "AL01013", "7791274201011",
     "Algabo Baby hisopos papel eco box 24", "20,00%", "12,00%", "0,00%", "0,00%", "0,00%"],
    # Una fila sin SKU: pasa en las planillas con subtotales o separadores.
    ["", "", "", "", "", "TOTAL", "", "", "", "", ""],
    # Un descuento imposible, como el 973,08 % que ya existe en costos.
    ["OTRO S.A.", "OTRA", "999", "XX01", "779", "Articulo raro",
     "973,08%", "", "", "", "150%"],
]

filas, resumen = filas_de_la_planilla(HOJA)

revisar("columnas de oferta detectadas", resumen["columnas_oferta"], 5)
revisar("filas sin SKU salteadas", resumen["sin_sku"], 1)
revisar("descuentos imposibles salteados", resumen["fuera_de_rango"], 2)
revisar("ninguna columna del articulo se toma por oferta", resumen["ignoradas"], [])

# Los ceros NO se guardan: "sin oferta" y "0 %" son lo mismo para comprar, y
# guardarlos llenaria la tabla de filas que no dicen nada.
revisar("solo se guardan los descuentos > 0", len(filas), 5)

def buscar(sku, mes, evento=""):
    return [f for f in filas if f["sku"] == sku and f["mes_comercial"] == mes
            and f["evento"] == evento]

revisar("AL01012 en mayo", [f["descuento_pct"] for f in buscar("AL01012", "2026-05")], [7.69])
revisar("AL01012 en junio", [f["descuento_pct"] for f in buscar("AL01012", "2026-06")], [7.69])
revisar("AL01012 en agosto no tiene oferta", buscar("AL01012", "2026-08"), [])
revisar("AL01013 en el Hot Sale",
        [f["descuento_pct"] for f in buscar("AL01013", "", "HOT SALE")], [12.0])
revisar("el evento no se guarda como mes", buscar("AL01013", "2026-05", "HOT SALE"), [])
revisar("el corte de julio va con su evento",
        [f["descuento_pct"] for f in buscar("AL01012", "2026-07", "Glade")], [7.69])
revisar("y NO como la oferta del mes de julio", buscar("AL01012", "2026-07"), [])
revisar("el proveedor viaja con la fila",
        {f["proveedor"] for f in filas if f["sku"].startswith("AL")}, {"ALGABO S. A."})
revisar("el encabezado original queda guardado",
        buscar("AL01013", "", "HOT SALE")[0]["encabezado"], "HOT SALE")

# Una hoja sin SKU tiene que explotar y no devolver cero filas en silencio: sin
# esto, un cambio de encabezado vaciaria la tabla sin que nadie se entere.
try:
    filas_de_la_planilla([["PROVEEDOR", "MARCA", "1/8/2026"], ["X", "Y", "5%"]])
    revisar("hoja sin columna SKU: avisa", "no aviso", "RuntimeError")
except RuntimeError as e:
    revisar("hoja sin columna SKU: avisa", "SKU" in str(e), True)

revisar("hoja vacia no rompe", filas_de_la_planilla([]), ([], {
    "columnas_oferta": 0, "ignoradas": [], "sin_sku": 0, "fuera_de_rango": 0}))


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
