-- ============================================================================
--  INDICES PARA bronze.ml_ventas
--  Correr en el SQL Editor de Supabase, UNA SENTENCIA POR VEZ.
-- ============================================================================
--
--  POR QUE
--
--  La tabla pesa 108 MB con 46.000 filas y NO TIENE NINGUN INDICE. Toda
--  consulta que la toca la recorre entera. Con el cache frio eso son varios
--  segundos, y el 21/08 ml_envios.py se cayo dos veces seguidas contra el
--  `statement_timeout` de 2 minutos de Supabase.
--
--  No es solo el orquestador: el tablero cruza contra esta tabla en el filtro
--  por hora, en el grafico de facturacion por hora y en las canceladas. Cada
--  una de esas paga el mismo recorrido completo.
--
--  Los tres indices juntos ocupan unos 5 MB contra los 108 MB de la tabla.
--
--  OJO CON `CONCURRENTLY`: no se puede correr adentro de una transaccion. Si el
--  editor de Supabase se queja, sacale la palabra `concurrently` -- con 46.000
--  filas el indice se arma en segundos y solo bloquea las escrituras ese rato.
--  El orquestador escribe cada 2 horas, asi que la ventana es amplia.
-- ============================================================================


-- 1) EL QUE RESUELVE ml_envios.py
--
-- Cubre las tres columnas que pide la consulta, asi que Postgres la contesta
-- SIN TOCAR LA TABLA (index-only scan): lee 3 MB de indice en vez de 108 MB.
-- Es parcial -- solo las ordenes con envio -- porque son las unicas que se
-- consultan.
create index concurrently if not exists ml_ventas_envios_idx
  on bronze.ml_ventas (status, date_created)
  include (id, "shipping.id")
  where "shipping.id" is not null;


-- 2) EL DEL TABLERO
--
-- El filtro por hora, el grafico de facturacion por hora y el corte del periodo
-- anterior cruzan gold.fact_ventas contra esta tabla por `id`.
create index concurrently if not exists ml_ventas_id_idx
  on bronze.ml_ventas (id);


-- 3) EL DE LAS CANCELADAS
--
-- El panel de canceladas y el grafico apilado filtran por estado. Son el 11 %
-- de la tabla, asi que el indice evita recorrer el 89 % restante.
create index concurrently if not exists ml_ventas_status_idx
  on bronze.ml_ventas (status);


-- 4) DESPUES DE CREARLOS
--
-- El index-only scan del punto 1 necesita el mapa de visibilidad al dia, y ese
-- lo arma el vacuum. Sin esto el indice existe pero Postgres igual va a la
-- tabla a chequear cada fila, que es justo lo que queremos evitar.
--
-- Tampoco corre adentro de una transaccion: va solo.
vacuum (analyze) bronze.ml_ventas;


-- ============================================================================
--  PARA VERIFICAR QUE QUEDARON BIEN
-- ============================================================================
-- select indexname, pg_size_pretty(pg_relation_size(indexname::regclass)) as tamano
--   from pg_indexes where schemaname='bronze' and tablename='ml_ventas';
--
-- Y que la consulta de ml_envios ahora use el indice (tiene que decir
-- "Index Only Scan using ml_ventas_envios_idx"):
--
-- explain analyze
-- select distinct on (v."shipping.id"::bigint::text)
--        v.id, v."shipping.id"::bigint::text, v.date_created
--   from bronze.ml_ventas v
--  where v.status = 'paid' and v."shipping.id" is not null
--    and v.date_created >= '2026-05-05'
--    and not exists (select 1 from bronze.ml_envios e
--                     where e.shipping_id = v."shipping.id"::bigint::text)
--  order by v."shipping.id"::bigint::text;
