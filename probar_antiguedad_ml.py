"""Sondeo: por donde puede venir la ANTIGUEDAD del stock en Full.

POR QUE ESTE SCRIPT. El reporte de Full que se baja a mano tiene la columna
"Unidades que afectan la metrica Con antiguedad", y el tablero de Stock la
necesita. Buscamos ese campo en el endpoint que ya usamos y NO esta:

    GET /inventories/{inventory_id}/stock/fulfillment
    -> total, available_quantity, not_available_quantity,
       not_available_detail (notSupported, withdrawal, damaged...),
       external_references

Ni una fecha ni un dia de permanencia. Verificado tambien contra lo que hay
guardado en bronze.ml_stock_full: ninguna columna dice cuando entro la unidad.

LA HIPOTESIS QUE ESTE SCRIPT PRUEBA es que la antiguedad no se pide, se DERIVA
del libro de movimientos del inventario:

    GET /stock/fulfillment/operations/search
        ?inventory_id=...&date_from=...&date_to=...

Cada `inbound_reception` dice cuando entraron unidades. Con eso y las salidas
(sale_confirmation, withdrawal_*) se reconstruye, por FIFO, hace cuanto esta en
el deposito cada unidad que todavia queda. Que es exactamente lo que Mercado
Libre cobra: el cargo por stock antiguo arranca a los 120 dias.

NO ESCRIBE NADA EN LA BASE. Imprime lo que contesta la API para poder decidir
con el dato a la vista en vez de con la documentacion, que para este endpoint
esta incompleta.

    python probar_antiguedad_ml.py            # 3 inventarios con stock
    python probar_antiguedad_ml.py XBIE01052  # uno puntual
"""

import json
import sys
from datetime import date, timedelta

import pandas as pd

from conexion import crear_engine
from mercadolibre import llamar_ml, renovar_access_token

# El rango maximo que acepta el endpoint por llamada. Si se pide mas, contesta
# error: para cubrir los 120 dias del cargo por antiguedad hay que encadenar
# tres ventanas, y eso es parte de lo que hay que confirmar aca.
DIAS_POR_LLAMADA = 60


def inventarios_de_prueba(n=3):
    """Inventarios que HOY tienen stock. Con cero unidades no se ve nada."""
    engine = crear_engine()
    df = pd.read_sql(
        """
        select inventory_id, available_quantity
        from bronze.ml_stock_full
        where available_quantity > 0
        order by available_quantity desc
        limit %(n)s
        """,
        engine,
        params={"n": n},
    )
    return list(df.itertuples(index=False))


def mostrar(titulo, dato):
    print(f"\n--- {titulo} ---")
    print(json.dumps(dato, indent=2, ensure_ascii=False)[:2500])


def sondear(inv_id, token):
    print("=" * 70)
    print(f"INVENTARIO {inv_id}")
    print("=" * 70)

    # 1) Lo que ya usamos, para tenerlo al lado y comparar.
    try:
        mostrar(
            "stock/fulfillment (el que ya usamos)",
            llamar_ml(f"/inventories/{inv_id}/stock/fulfillment", token),
        )
    except Exception as e:
        print(f"  FALLO: {e}")

    hoy = date.today()
    desde = hoy - timedelta(days=DIAS_POR_LLAMADA)

    # 2) La apuesta: el libro de movimientos.
    #
    # Se prueban las DOS rutas que aparecen en la documentacion. La de
    # /marketplace/ figura en la doc de Global Selling y la otra en la de MLA;
    # cual anda para esta cuenta es justamente lo que no sabemos.
    for ruta in (
        "/stock/fulfillment/operations/search",
        "/marketplace/stock/fulfillment/operations/search",
    ):
        try:
            datos = llamar_ml(
                ruta,
                token,
                params={
                    "inventory_id": inv_id,
                    "date_from": desde.isoformat(),
                    "date_to": hoy.isoformat(),
                    "limit": 50,
                },
            )
            mostrar(f"{ruta}  ({desde} a {hoy})", datos)

            # Lo que de verdad importa: si hay inbound_reception con fecha, la
            # antiguedad se puede calcular. Si no, no.
            filas = datos.get("results", datos if isinstance(datos, list) else [])
            tipos = sorted({f.get("type") for f in filas if isinstance(f, dict)})
            print(f"  tipos de operacion vistos: {tipos or 'ninguno'}")
        except Exception as e:
            print(f"\n--- {ruta} ---\n  FALLO: {e}")


def main():
    token = renovar_access_token()

    if len(sys.argv) > 1:
        for inv_id in sys.argv[1:]:
            sondear(inv_id, token)
        return

    for fila in inventarios_de_prueba():
        print(f"\n(stock disponible hoy: {fila.available_quantity})")
        sondear(fila.inventory_id, token)

    print("\n" + "=" * 70)
    print("QUE MIRAR EN LO DE ARRIBA")
    print("=" * 70)
    print("1. Si alguna de las dos rutas de operations contesta 200.")
    print("2. Si entre los tipos aparece 'inbound_reception' CON fecha.")
    print("3. Si la fecha mas vieja alcanza para los 120 dias del cargo por")
    print("   antiguedad, o si hay que encadenar tres ventanas de 60 dias.")


if __name__ == "__main__":
    main()
