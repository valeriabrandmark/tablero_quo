import argparse
import os
import json
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
    connect_args={"client_encoding": "utf8"}
)

# Fecha de corte ABSOLUTA: nunca se procesa nada anterior a esto (piso historico)
FECHA_CORTE = date(2026, 5, 6)

# Ventana movil: en cada corrida, se re-procesan y reemplazan solo los ultimos N dias.
# Todo lo anterior a esa ventana en gold.fact_ventas queda intacto (no se vuelve a tocar).
WINDOW_DAYS = 7
CUTOFF = max(FECHA_CORTE, date.today() - timedelta(days=WINDOW_DAYS))


# Mapeo de codigo de vendedor a nombre (tabla de Sigma, no se extrae por API)
VENDEDORES = {
    "001": "CASA CENTRAL",
    "002": "AGENCIA",
    "003": "ECOMMERCE",
    "004": "IGNACIO",
    "005": "IVANA",
    "006": "SILVIO",
    "007": "RAMON",
    "008": "PABLO",
    "009": "MELI",
    "010": "ALEJANDRO",
    "011": "TRADE",
    "012": "BTL",
    "013": "PROYECTOS ESPECIALES",
    "014": "RICARDO",
    "WEB": "VENDEDOR WEB",
}


EMPRESAS = {
    "0001": "Quo Marketing SRL",
    "0002": "Noa Comercial SRL",
    "0003": "Presupuesto QUO",
    "0004": "Presupuesto Noa",
}


def empresa_de(codigo):
    if codigo is None:
        return None
    return EMPRESAS.get(str(codigo).strip(), f"Empresa {codigo}")


def vendedor_de(codigo):
    if codigo is None:
        return None
    return VENDEDORES.get(str(codigo).strip(), f"Vendedor {codigo}")


def mes_comercial(fecha):
    """Devuelve el mes comercial 'AAAA-MM' segun la regla del 6 al 5.
       Dia >= 6 -> mes actual. Dia < 6 -> mes anterior."""
    if fecha is None:
        return None
    if fecha.day >= 6:
        anio, mes = fecha.year, fecha.month
    else:
        # mes anterior
        if fecha.month == 1:
            anio, mes = fecha.year - 1, 12
        else:
            anio, mes = fecha.year, fecha.month - 1
    return f"{anio:04d}-{mes:02d}"


def to_date(valor):
    """Convierte cualquier formato de fecha a date (o None)."""
    ts = pd.to_datetime(valor, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return ts.date()


def construir_fact_ventas():
    print(f"=== Construyendo fact_ventas (ventana movil: {CUTOFF} en adelante) ===")

    # --- Catalogos auxiliares ---
    print("Leyendo costos historicos y IVA...")
    # Costo real por (sku, mes_comercial)
    cost = pd.read_sql("SELECT sku, mes_comercial, costo_real FROM bronze.costos_historicos", engine)
    costo_idx = {(r["sku"], r["mes_comercial"]): r["costo_real"] for _, r in cost.iterrows()}

    # IVA por sku (de sigma_articulos)
    art = pd.read_sql('SELECT id, "ivaPorcentual" FROM bronze.sigma_articulos', engine)
    iva_por_sku = dict(zip(art["id"].astype(str), art["ivaPorcentual"]))

    # Proveedor y marca por sku (de sigma_articulos)
    # NOTA: la columna "marca" plana viene vacia desde la API de Sigma.
    # El dato real esta anidado en "attributes.marca".
    prov = pd.read_sql(
        'SELECT id, "proveedorNombre", "attributes.marca" AS marca FROM bronze.sigma_articulos',
        engine
    )
    proveedor_por_sku = {str(k).strip(): v for k, v in zip(prov["id"], prov["proveedorNombre"])}
    marca_por_sku = {str(k).strip(): v for k, v in zip(prov["id"], prov["marca"])}

    def proveedor_de(sku):
        return proveedor_por_sku.get(str(sku).strip())

    def marca_de(sku):
        return marca_por_sku.get(str(sku).strip())

    # Costo de envio por orden (de ml_envios)
    env = pd.read_sql("SELECT order_id, costo_envio FROM bronze.ml_envios", engine)
    envio_por_orden = dict(zip(env["order_id"].astype(str), env["costo_envio"]))

    def costo_de(sku, mc):
        return costo_idx.get((str(sku), mc))

    def iva_de(sku):
        v = iva_por_sku.get(str(sku))
        return float(v) if v is not None else 21.0   # default 21 si no se encuentra

    filas = []

    # --- 1) SIGMA ---
    print("Procesando Sigma...")
    sigma = pd.read_sql("""
        SELECT empresa, surcursal, fecha, "itemArticuloId", "itemDescripcion",
               "itemCantidad", "itemPrecioUnitario", "clienteNombre",
               "itemDescuento", "itemDescuentoGlobal", "itemDescuentoFinanciero",
               "itemPedidoId", vendedor, "comprobanteCodigo", "comprobanteNumero",
               "comprobanteTipo"
        FROM bronze.sigma_ventas
        WHERE empresa IN ('0001','0002','0003','0004')
    """, engine)

    for _, r in sigma.iterrows():
        f = to_date(r["fecha"])
        if f is None or f < CUTOFF:
            continue
        mc = mes_comercial(f)
        sku = r["itemArticuloId"]
        if r["empresa"] == "0001" and r["surcursal"] == "0001":
            unidad = "Quo Agencia"
        elif r["empresa"] in ("0001", "0003"):
            unidad = "Quo"
        else:
            unidad = "Noa"
        tipo = "Fiscal" if r["empresa"] in ("0001", "0002") else "No fiscal"
        cant = r["itemCantidad"] or 0
        precio_lista = r["itemPrecioUnitario"] or 0
        # Aplicar descuentos por artículo (Sigma da precio de lista + % de descuento)
        desc = (r["itemDescuento"] or 0) / 100
        desc_g = (r["itemDescuentoGlobal"] or 0) / 100
        desc_f = (r["itemDescuentoFinanciero"] or 0) / 100
        precio = precio_lista * (1 - desc) * (1 - desc_g) * (1 - desc_f)   # precio real de venta
        # % de descuento/oferta combinado (los 3 descuentos de Sigma juntos, como un solo %)
        oferta_pct = round((1 - (1 - desc) * (1 - desc_g) * (1 - desc_f)) * 100, 2)
        iva = iva_de(sku)
        precio_neto = precio                          # Sigma ya viene SIN IVA (y ahora con descuento)
        precio_con_iva = precio * (1 + iva / 100)     # para el % de rentabilidad
        costo = costo_de(sku, mc)
        margen = None if costo is None else (precio_neto - costo) * cant
        filas.append({
            "canal": "Mayorista", "unidad": unidad, "tipo": tipo, "nro_orden": r["itemPedidoId"],
            "fecha": f, "mes_comercial": mc, "sku": sku, "producto": r["itemDescripcion"],
            "cantidad": cant, "precio_unitario": precio, "precio_neto": precio_neto,
            "iva_pct": iva, "costo_unitario": costo, "comision": 0,
            "total_linea": cant * precio_con_iva, "margen_total": margen,
            "proveedor": proveedor_de(sku),
            "marca": marca_de(sku),
            "cliente": r["clienteNombre"],
            "vendedor": vendedor_de(r["vendedor"]),
            "comprobante": f"{r['comprobanteTipo']}-{r['comprobanteCodigo']}-{r['comprobanteNumero']}" if r["comprobanteCodigo"] else None,
            "oferta_pct": oferta_pct,
            "empresa": empresa_de(r["empresa"]),
        })

    # --- 2) TIENDA NUBE ---
    print("Procesando Tienda Nube...")
    tn = pd.read_sql("""
        SELECT fecha, sku, nombre, cantidad, precio, cliente_nombre
        FROM bronze.tn_pedidos_items
        WHERE estado != 'cancelled'
    """, engine)

    for _, r in tn.iterrows():
        f = to_date(r["fecha"])
        if f is None or f < CUTOFF:
            continue
        mc = mes_comercial(f)
        sku = r["sku"]
        cant = r["cantidad"] or 0
        precio = float(r["precio"]) if r["precio"] else 0
        iva = iva_de(sku)
        precio_neto = precio / (1 + iva / 100)
        costo = costo_de(sku, mc)
        margen = None if costo is None else (precio_neto - costo) * cant
        filas.append({
            "canal": "Tienda Nube", "unidad": "Quo", "tipo": "Fiscal", "nro_orden": None,
            "fecha": f, "mes_comercial": mc, "sku": sku, "producto": r["nombre"],
            "cantidad": cant, "precio_unitario": precio, "precio_neto": precio_neto,
            "iva_pct": iva, "costo_unitario": costo, "comision": 0,
            "total_linea": cant * precio, "margen_total": margen,
            "proveedor": proveedor_de(sku),
            "marca": marca_de(sku),
            "cliente": r["cliente_nombre"],
        })

# --- 3) MERCADO LIBRE (en lotes; reparte envio proporcional al precio) ---
    print("Procesando Mercado Libre (puede tardar)...")
    LOTE = 5000
    offset = 0
    while True:
        ml = pd.read_sql(f"""
            SELECT id, date_created, order_items, "buyer.nickname"
            FROM bronze.ml_ventas
            WHERE status = 'paid'
            ORDER BY id
            LIMIT {LOTE} OFFSET {offset}
        """, engine)
        if ml.empty:
            break
        print(f"  ML lote desde {offset}: {len(ml)} ordenes")

        for _, r in ml.iterrows():
            f = to_date(r["date_created"])
            if f is None or f < CUTOFF:
                continue
            mc = mes_comercial(f)
            items = json.loads(r["order_items"]) if isinstance(r["order_items"], str) else r["order_items"]
            items = items or []

            # Total de la orden (precio con IVA x cantidad) para repartir el envio
            total_orden = sum((it.get("unit_price") or 0) * (it.get("quantity") or 0) for it in items)
            # Envio de esta orden, ya NETO (le sacamos el IVA)
            envio_bruto = envio_por_orden.get(str(r["id"]), 0) or 0
            envio_neto = envio_bruto / 1.21

            for it in items:
                sku = (it.get("item") or {}).get("seller_sku")
                cant = it.get("quantity") or 0
                precio = it.get("unit_price") or 0          # ML viene CON IVA
                # OJO: sale_fee es la comision de UNA unidad, no la del item entero.
                # Se guarda por unidad (igual que precio_neto y costo_unitario), asi
                # que el que la use tiene que multiplicarla por la cantidad.
                comision_con_iva = it.get("sale_fee") or 0
                comision = comision_con_iva / 1.21
                iva = iva_de(sku)
                precio_neto = precio / (1 + iva / 100)
                costo = costo_de(sku, mc)

                # Reparto del envio: proporcional al valor del item en la orden
                valor_item = precio * cant
                if total_orden > 0:
                    envio_item = envio_neto * (valor_item / total_orden)
                else:
                    envio_item = 0

                # La comision va multiplicada por la cantidad: es por unidad.
                # Sin el * cant, una linea de 10 unidades descontaba una sola
                # comision y la ganancia de Mercado Libre quedaba inflada
                # (21,5 M de mas sobre el historico, un 13%).
                # El envio NO se multiplica: envio_item ya es la parte de esta
                # linea del envio total de la orden.
                margen = (
                    None if costo is None
                    else (precio_neto - costo - comision) * cant - envio_item
                )
                filas.append({
                    "canal": "Mercado Libre", "unidad": "Quo", "tipo": "Fiscal", "nro_orden": r["id"],
                    "fecha": f, "mes_comercial": mc, "sku": sku,
                    "producto": (it.get("item") or {}).get("title"),
                    "cantidad": cant, "precio_unitario": precio, "precio_neto": precio_neto,
                    "iva_pct": iva, "costo_unitario": costo, "comision": comision,
                    "envio": round(envio_item, 2),
                    "total_linea": cant * precio, "margen_total": margen,
                    "proveedor": proveedor_de(sku),
                    "marca": marca_de(sku),
                    "cliente": r["buyer.nickname"],
                })

        offset += LOTE


    print(f"Total de lineas (ventana de {WINDOW_DAYS} dias, desde {CUTOFF}): {len(filas)}")
    df = pd.DataFrame(filas)

    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS gold;")

        existe = con.exec_driver_sql("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'gold' AND table_name = 'fact_ventas'
            )
        """).scalar()

        if existe:
            # Solo se borra (y se va a re-insertar) la ventana movil. Todo lo anterior
            # a CUTOFF en gold.fact_ventas queda intacto -- no se vuelve a tocar ni reprocesar.
            resultado = con.exec_driver_sql(
                "DELETE FROM gold.fact_ventas WHERE fecha >= %(cutoff)s",
                {"cutoff": CUTOFF}
            )
            print(f"  Filas viejas borradas dentro de la ventana (se van a reemplazar): {resultado.rowcount}")

    df.to_sql("fact_ventas", engine, schema="gold", if_exists="append", index=False)
    print("Guardado: gold.fact_ventas")


def main():
    parser = argparse.ArgumentParser(
        description="Arma gold.fact_ventas desde las tablas de bronze."
    )
    parser.add_argument(
        "--dias", type=int, default=WINDOW_DAYS,
        help=f"Cuantos dias hacia atras reconstruir (por defecto {WINDOW_DAYS})",
    )
    parser.add_argument(
        "--todo", action="store_true",
        help=f"Reconstruye TODO desde {FECHA_CORTE}. Tarda, pero es la unica forma "
             f"de que un dato que llego tarde a bronze (por ejemplo el costo de "
             f"envio de meses viejos) entre a gold.",
    )
    args = parser.parse_args()

    # La ventana movil es lo correcto para la corrida de todos los dias: reprocesar
    # cuatro meses cada hora seria tirar tiempo. Pero deja un agujero: si una tabla
    # de bronze se rellena hacia atras -- que es exactamente lo que pasa cuando
    # ml_envios.py se pone al dia despues de meses sin correr -- ese dato nunca
    # entra a gold, porque gold ya no vuelve a mirar esas fechas. Para eso esta
    # --todo, que se corre a mano una vez y despues no se toca mas.
    global CUTOFF
    if args.todo:
        CUTOFF = FECHA_CORTE
    else:
        CUTOFF = max(FECHA_CORTE, date.today() - timedelta(days=args.dias))

    print(f"Ventana a reconstruir: desde {CUTOFF} (hoy es {date.today()})")
    construir_fact_ventas()
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()