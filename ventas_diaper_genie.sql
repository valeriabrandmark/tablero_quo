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
CREATE OR REPLACE VIEW public.ventas_diaper_genie
    WITH (security_invoker = on) AS
    SELECT * FROM gold.fact_ventas_previo
     WHERE upper(trim(coalesce(marca, ''))) = 'DIAPER GENIE'
    UNION ALL
    SELECT * FROM gold.fact_ventas
     WHERE upper(trim(coalesce(marca, ''))) = 'DIAPER GENIE';

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
-- Para otra marca no hace falta tocar nada de esto: es la misma vista con otro
-- nombre y otro literal.
