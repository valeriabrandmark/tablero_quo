-- ============================================================================
--  INDICE PARA bronze.sigma_ventas
--  Correr en el SQL Editor de Supabase.
-- ============================================================================
--
--  POR QUE
--
--  modelo.py levanta la ventana movil de sigma_ventas en cada corrida:
--
--      WHERE empresa IN ('0001','0002','0003','0004')
--        AND fecha >= '<piso>'
--
--  La tabla son 13 MB y 17.291 filas, y NO tiene ningun indice sobre `fecha`,
--  asi que esa consulta la recorre entera. Trece megas no matan a nadie de a
--  uno, pero el orquestador corre CADA HORA: son ~312 MB de lectura por dia
--  para traer las ~860 filas de la ventana.
--
--  Es el mismo problema que el 22/08 tumbo el pipeline en ml_ventas, en chico.
--  Alla el arreglo grande fue sacar el `left(fecha, 10)` del WHERE, que impedia
--  usar el indice que ya existia. Aca el `left()` tambien se saco (ver
--  modelo.py), pero no habia indice que usar. Este lo crea.
--
--  El indice ocupa unos 400 kB.
--
--  OJO CON `CONCURRENTLY`: no se puede correr adentro de una transaccion. Si el
--  editor de Supabase se queja, sacale la palabra -- con 17.000 filas el indice
--  se arma en un par de segundos.
-- ============================================================================


-- 1) EL INDICE
--
-- `fecha` primero porque es el filtro selectivo: de 17.291 filas la ventana
-- deja 864. `empresa` va segundo y de arrastre: son cuatro valores sobre casi
-- todas las filas, asi que sola no descarta nada, pero estando en el indice
-- Postgres filtra sin volver a la tabla.
create index concurrently if not exists sigma_ventas_fecha_idx
    on bronze.sigma_ventas (fecha, empresa);


-- 2) PARA VERIFICAR QUE QUEDO (correr despues, aparte)
--
-- Tiene que decir "Index Scan using sigma_ventas_fecha_idx". Si dice
-- "Seq Scan", el indice no se creo o el planificador lo descarto.
--
-- explain (analyze, buffers, costs off)
-- select id, fecha, empresa
--   from bronze.sigma_ventas
--  where empresa in ('0001','0002','0003','0004')
--    and fecha >= '2026-08-14';


-- 3) EL TAMANO, para confirmar que no es caro
--
-- select pg_size_pretty(pg_relation_size('bronze.sigma_ventas_fecha_idx'));
