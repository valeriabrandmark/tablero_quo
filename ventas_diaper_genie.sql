-- ===========================================================================
--  LO QUE SE RELLENA HACIA ATRAS NO VA EN LA TABLA QUE LEE EL TABLERO
-- ===========================================================================
--
-- gold.fact_ventas arranca el 06/05/2026 y el tablero cuenta con eso: todos
-- sus paneles comparan meses, sacan promedios y arman ritmos sobre lo que
-- encuentran ahi.
--
-- El relleno de Diaper Genie metio 14 lineas de abril. Son correctas, pero son
-- LA UNICA MARCA de ese mes: cualquier panel que mire abril veria un abril de
-- 14 lineas y $1,2 M, y no tiene forma de saber que eso no es abril entero.
-- No da error; da un numero equivocado, que es peor.
--
-- Asi que lo anterior al corte vive aparte:
--
--   gold.fact_ventas            exactamente como estaba: del 06/05 en
--                               adelante, todas las marcas. La lee el tablero.
--   gold.fact_ventas_previo     lo que se rellena hacia atras. Misma forma,
--                               otra tabla. El tablero no la mira.
--   public.ventas_diaper_genie  las dos juntas, filtradas por marca. Es la que
--                               lee el archivo de afuera.
--
-- LA VISTA VA EN public Y NO EN gold PORQUE SE LEE POR LA API. PostgREST
-- --lo que contesta en /rest/v1/-- solo sirve los esquemas que estan en la
-- lista de expuestos del proyecto, y `public` es el unico que esta siempre.
-- Una vista en gold da 404 aunque los permisos esten bien.
--
-- La vista se mantiene sola: una venta nueva entra a fact_ventas por la
-- corrida de todos los dias y aparece ahi sin que nadie copie nada.
--
-- Se corre una sola vez, y despues modelo.py --relleno escribe solo en
-- fact_ventas_previo.
-- ===========================================================================

BEGIN;

-- 1) La tabla de lo viejo, con la forma exacta de la que ya existe.
--
--    Con LIKE y no con un CREATE TABLE AS: asi las columnas quedan con los
--    MISMOS tipos, que es lo que hace que la vista de abajo pueda unirlas. Una
--    columna que en una sea numeric y en la otra text rompe el UNION.
CREATE TABLE IF NOT EXISTS gold.fact_ventas_previo (
    LIKE gold.fact_ventas INCLUDING DEFAULTS
);

-- 2) Mudar lo que ya se relleno. El DELETE y el INSERT van en la misma
--    transaccion: o se mueve todo o no se mueve nada.
WITH movidas AS (
    DELETE FROM gold.fact_ventas
     WHERE fecha < DATE '2026-05-06'
    RETURNING *
)
INSERT INTO gold.fact_ventas_previo SELECT * FROM movidas;

-- 3) La vista que lee el archivo de afuera.
--
--    UNION ALL y no UNION: las dos tablas no se pisan --una termina donde
--    empieza la otra-- asi que buscar duplicados seria pagar un ordenamiento
--    entero para no encontrar ninguno.
--    LA MARCA SOLA NO ALCANZA: FALTAN LOS KITS.
--
--    Los packs armados para Mercado Libre --AC01001C, AC01002C: el cesto con
--    tres repuestos-- no existen en el maestro de Sigma. Son una publicacion
--    de Meli, no un articulo. Y la marca de cada linea sale del maestro, asi
--    que esas lineas llegan a gold con marca en NULL y una vista que filtra
--    por marca las deja afuera, aunque sean Diaper Genie de punta a punta.
--
--    Por eso el corte es "la marca O el codigo": todos los articulos de la
--    marca empiezan con AC01, y los kits son ese mismo codigo con una C al
--    final. Las dos condiciones se pisan en los cinco articulos del maestro
--    --que cumplen las dos-- y eso esta bien: es un OR, no los duplica.
--    LA COMISION SE SIRVE DE LAS DOS FORMAS, Y CON EL NOMBRE PUESTO.
--
--    `comision` es POR UNIDAD y SIN IVA, igual que precio_unitario,
--    precio_neto y costo_unitario. El reporte que se baja de Mercado Libre
--    muestra otra cosa --"Cargo por venta e impuestos", que es por LINEA y
--    CON IVA-- y quien las cruzo leyo una como si fuera la otra: en 64 de
--    259 lineas de ML faltaban $450.324, un 7,4% de la comision del canal.
--
--    NO SE CAMBIA `comision`. Multiplicarla ahi dejaria una fila con el
--    precio por unidad y la comision por linea, y el que la lea despues se
--    equivoca al reves; ademas la vista dejaria de coincidir con
--    gold.fact_ventas, que es de donde sale.
--
--    OJO AL MEZCLAR: en la misma fila, precio_unitario, precio_neto,
--    costo_unitario y comision son POR UNIDAD, y total_linea, envio y
--    margen_total son POR LINEA. El envio de ML es la parte que le toca a
--    esa linea del flete del paquete, no un costo unitario.
CREATE OR REPLACE VIEW public.ventas_diaper_genie
    WITH (security_invoker = on) AS
    SELECT *,
           comision * cantidad          AS comision_linea,
           -- El sale_fee tal cual lo manda ML: es el mismo 1,21 con el que
           -- modelo.py lo neteo, asi que esto lo devuelve entero.
           comision * cantidad * 1.21   AS comision_linea_con_iva
      FROM gold.fact_ventas_previo
     WHERE upper(trim(coalesce(marca, ''))) = 'DIAPER GENIE'
        OR upper(trim(coalesce(sku, ''))) LIKE 'AC01%'
    UNION ALL
    SELECT *,
           comision * cantidad          AS comision_linea,
           comision * cantidad * 1.21   AS comision_linea_con_iva
      FROM gold.fact_ventas
     WHERE upper(trim(coalesce(marca, ''))) = 'DIAPER GENIE'
        OR upper(trim(coalesce(sku, ''))) LIKE 'AC01%';

-- 4) Quien puede leerla.
--
--    SOLO service_role, Y NO anon. Son las dos claves que da Supabase: la
--    `anon` es la que viaja al navegador --cualquiera que abra el tablero la
--    tiene-- y la `service_role` vive en un servidor y no se publica. Esto son
--    ventas con precios y comisiones, asi que va con la segunda.
--
--    El archivo que lea esta vista tiene que usar la clave de servicio. Si
--    alguna vez hace falta abrirla mas, es una linea mas aca, a conciencia.
--
--    security_invoker = on (arriba) hace que la vista lea las tablas CON LOS
--    PERMISOS DE QUIEN PREGUNTA y no con los del dueno. Por eso hacen falta
--    los permisos sobre gold de aca abajo: sin esto, la vista le daria a
--    cualquiera una ventana a gold que sus permisos no le dan.
GRANT USAGE ON SCHEMA gold TO service_role;
GRANT SELECT ON gold.fact_ventas, gold.fact_ventas_previo TO service_role;
GRANT SELECT ON public.ventas_diaper_genie TO service_role;
REVOKE ALL ON public.ventas_diaper_genie FROM anon, authenticated;

COMMIT;

-- Como se lee desde afuera:
--
--     GET {SUPABASE_URL}/rest/v1/ventas_diaper_genie?select=*&order=fecha
--         apikey: <clave service_role>
--         Authorization: Bearer <clave service_role>
--
-- OJO CON LOS KITS SI SE SACAN CUENTAS: no estan en el maestro, asi que esas
-- lineas vienen sin costo, sin proveedor y sin marca. Precio, comision, envio
-- e IVA si. Un margen calculado sobre ellas da todo el importe como ganancia.

-- Para otra marca no hace falta tocar nada de esto: es la misma vista con otro
-- nombre y otro literal.
