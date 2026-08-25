import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from conexion import crear_engine

load_dotenv()

engine = crear_engine()

SQL_CLASIFICACION = """
DROP TABLE IF EXISTS gold.clientes_clasificados;

CREATE TABLE gold.clientes_clasificados AS
WITH tickets_dia AS (
    SELECT "clienteId", fecha::date AS dia,
        SUM("itemPrecioUnitario" * "itemCantidad") AS ticket_dia
    FROM bronze.sigma_ventas
    WHERE empresa IN ('0001','0003') AND "clienteId" IS NOT NULL
    GROUP BY "clienteId", fecha::date
),
resumen_cliente AS (
    SELECT "clienteId",
        ROUND(AVG(ticket_dia)::numeric, 2) AS ticket_promedio,
        ROUND(SUM(ticket_dia)::numeric, 2) AS monto_total,
        COUNT(*) AS cantidad_tickets
    FROM tickets_dia GROUP BY "clienteId"
),
empresas_cliente AS (
    SELECT "clienteId",
        BOOL_OR(empresa = '0001') AS compra_0001,
        BOOL_OR(empresa = '0003') AS compra_0003
    FROM bronze.sigma_ventas
    WHERE empresa IN ('0001','0003') AND "clienteId" IS NOT NULL
    GROUP BY "clienteId"
)
SELECT 
    c.id AS cliente_id,
    c.nombre,
    c."rubroDescripcion" AS rubro,
    c.provincia,
    c.localidad,
    c.zona,
    c."eMail" AS email,
    COALESCE(r.ticket_promedio, 0) AS ticket_promedio,
    COALESCE(r.monto_total, 0) AS monto_total,
    COALESCE(r.cantidad_tickets, 0) AS cantidad_tickets,
    COALESCE(e.compra_0001, false) AS compra_0001,
    COALESCE(e.compra_0003, false) AS compra_0003,
    CASE
        WHEN e."clienteId" IS NULL THEN 'C'
        WHEN e.compra_0001 AND e.compra_0003 THEN 'B1'
        WHEN e.compra_0001 AND NOT e.compra_0003 AND r.ticket_promedio > 900000 THEN 'A'
        WHEN e.compra_0001 AND NOT e.compra_0003 THEN 'B1'
        WHEN e.compra_0003 AND NOT e.compra_0001 AND r.ticket_promedio > 300000 THEN 'B2'
        WHEN e.compra_0003 AND NOT e.compra_0001 THEN 'C'
        ELSE 'C'
    END AS categoria
FROM bronze.sigma_clientes c
LEFT JOIN empresas_cliente e ON e."clienteId" = c.id
LEFT JOIN resumen_cliente r ON r."clienteId" = c.id;
"""


def clasificar_clientes():
    print("=== Clasificando clientes ===")
    with engine.begin() as con:
        con.execute(text(SQL_CLASIFICACION))
    # Mostrar la distribución resultante
    with engine.connect() as con:
        result = con.execute(text("""
            SELECT categoria, COUNT(*) AS cantidad
            FROM gold.clientes_clasificados
            GROUP BY categoria ORDER BY categoria
        """))
        print("\nDistribución por categoría:")
        for fila in result:
            print(f"  {fila[0]}: {fila[1]} clientes")
    print("\nTabla gold.clientes_clasificados actualizada.")


if __name__ == "__main__":
    clasificar_clientes()
    print("\n=== LISTO ===")
    