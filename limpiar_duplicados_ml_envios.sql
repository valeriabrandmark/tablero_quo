-- Limpieza de envios duplicados en bronze.ml_envios
-- ==================================================
--
-- POR QUE PASA
-- ml_envios.py guarda AGREGANDO, y antes de saltear un envio consulta que
-- shipping_id ya estan en la tabla. Con una sola corrida por vez eso alcanza:
-- nunca agrega algo que ya este.
--
-- Pero si dos procesos escriben a la vez -- por ejemplo, quedo corriendo la
-- version vieja del script (que reescribia la tabla entera desde su lista en
-- memoria) y se arranco la nueva despues de un git pull -- los dos leen la
-- misma foto de "lo que ya hay" y los dos escriben lo mismo.
--
-- ES SEGURO
-- La fila que se borra es una copia identica: se verifico que ningun
-- shipping_id repetido tenga dos costos distintos, ni dos order_id distintos.
-- No se elige entre dos versiones, se tira una copia.
--
-- Y NO ROMPE NADA AGUAS ABAJO: modelo.py ya no usa el order_id de esta tabla,
-- cruza por shipping_id contra bronze.ml_ventas.
--
-- COMO CORRERLO: Supabase -> SQL Editor -> los tres bloques en orden.
-- El 1 y el 3 solo miran; el 2 es el unico que escribe.

-- ---------------------------------------------------------------------------
-- 1) ANTES: cuanto sobra, y confirmar que los duplicados no se contradicen
-- ---------------------------------------------------------------------------
select count(*)                                       as filas,
       count(distinct shipping_id)                    as envios_distintos,
       count(*) - count(distinct shipping_id)         as filas_de_mas,
       (select count(*) from (
          select shipping_id from bronze.ml_envios
          group by 1 having count(distinct costo_envio) > 1) x)  as costos_contradictorios,
       (select count(*) from (
          select shipping_id from bronze.ml_envios
          group by 1 having count(distinct order_id) > 1) y)     as ordenes_contradictorias
from bronze.ml_envios;

-- `costos_contradictorios` y `ordenes_contradictorias` TIENEN que dar 0.
-- Si alguno da distinto de 0, NO sigas: avisá, porque entonces no son copias
-- sino dos respuestas distintas de la API para el mismo envio, y ahi hay que
-- decidir cual vale en vez de tirar una al azar.

-- ---------------------------------------------------------------------------
-- 2) LA LIMPIEZA: deja una fila por shipping_id
-- ---------------------------------------------------------------------------
delete from bronze.ml_envios a
using bronze.ml_envios b
where a.shipping_id = b.shipping_id
  and a.ctid > b.ctid;

-- `ctid` es el identificador fisico de la fila que trae Postgres. Se usa porque
-- esta tabla no tiene clave primaria: sin el no habria forma de decir "borra
-- una de estas dos filas iguales y deja la otra".

-- ---------------------------------------------------------------------------
-- 3) DESPUES: no tiene que quedar ningun repetido
-- ---------------------------------------------------------------------------
select count(*)                               as filas,
       count(distinct shipping_id)            as envios_distintos,
       count(*) - count(distinct shipping_id) as filas_de_mas,
       count(*) filter (where costo_envio > 0) as con_costo,
       round(sum(costo_envio)::numeric)        as total_bruto
from bronze.ml_envios;

-- `filas_de_mas` tiene que dar 0, y `total_bruto` tiene que BAJAR respecto del
-- bloque 1 (se fue la plata que estaba contada dos veces). `envios_distintos`
-- y `con_costo` NO tienen que cambiar: no se pierde ningun envio.


-- ---------------------------------------------------------------------------
-- OPCIONAL: que no pueda volver a pasar
-- ---------------------------------------------------------------------------
-- Un indice unico hace que un segundo proceso falle con un error en vez de
-- duplicar en silencio. Es a proposito: un script que se corta a la vista se
-- arregla; una tabla que se duplica sin avisar se descubre meses despues.
--
-- El costo es que si alguna vez se corren dos a la vez, el segundo muere a
-- mitad de camino. Como ml_envios.py es incremental, la corrida siguiente
-- retoma sola.
--
-- Correr SOLO despues de la limpieza de arriba (con duplicados presentes falla):
--
--   create unique index if not exists ml_envios_shipping_id_uniq
--     on bronze.ml_envios (shipping_id);
