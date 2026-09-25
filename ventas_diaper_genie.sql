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
--   gold.fact_ventas          exactamente como estaba: del 06/05 en adelante,
--                             todas las marcas. Es la que lee el tablero.
--   gold.fact_ventas_previo   lo que se rellena hacia atras. Misma forma, otra
--                             tabla. El tablero no la mira.
--   gold.ventas_diaper_genie  las dos juntas, filtradas por marca. Es la que
--                             lee el archivo de afuera.
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
CREATE OR REPLACE VIEW gold.ventas_diaper_genie AS
    SELECT * FROM gold.fact_ventas_previo
     WHERE upper(trim(coalesce(marca, ''))) = 'DIAPER GENIE'
    UNION ALL
    SELECT * FROM gold.fact_ventas
     WHERE upper(trim(coalesce(marca, ''))) = 'DIAPER GENIE';

COMMIT;

-- Para otra marca no hace falta tocar nada de esto: es la misma vista con otro
-- nombre y otro literal, o directamente
--
--     SELECT * FROM gold.fact_ventas_previo WHERE marca = '...'
--     UNION ALL
--     SELECT * FROM gold.fact_ventas        WHERE marca = '...';
