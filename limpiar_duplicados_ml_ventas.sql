-- Limpieza de ordenes duplicadas en bronze.ml_ventas
-- ===================================================
--
-- POR QUE PASA
-- `guardar_ventana_en_bd` borraba la ventana movil con
--
--     DELETE ... WHERE date_created::date >= cutoff
--
-- `date_created` es TEXTO con offset ("2026-08-19T21:30:00.000-04:00") y
-- Postgres resuelve ese `::date` en la zona del SERVIDOR, que es UTC. Argentina
-- es UTC-3, asi que una venta de las 21 de aca ya es del dia siguiente en UTC:
-- quedaba fuera del borrado, la API la volvia a traer, y entraba de nuevo.
--
-- El patron no deja lugar a dudas: de 1.195 ordenes duplicadas, 1.191 estaban
-- entre las 21 y la medianoche.
--
-- YA ESTA ARREGLADO EN EL CODIGO: ahora se borran los ID que se estan por
-- insertar, que es exacto por definicion y no depende de ningun huso. Este
-- script limpia lo que se acumulo antes del arreglo.
--
-- QUE IMPACTO TENIA
-- 790 filas de mas en bronze -> 404 lineas de mas en gold.fact_ventas ->
-- $8,5 M contados DOS VECES en el tablero.
--
-- COMO CORRERLO: Supabase -> SQL Editor -> los bloques en orden.
-- El 1 y el 4 solo miran; el 2 y el 3 son los que escriben.

-- ---------------------------------------------------------------------------
-- 1) ANTES: cuanto sobra, y si las copias se contradicen
-- ---------------------------------------------------------------------------
select count(*)                                          as filas,
       count(distinct id)                                as ordenes,
       count(*) - count(distinct id)                     as filas_de_mas,
       (select count(*) from (
          select id from bronze.ml_ventas
          group by 1 having count(distinct status) > 1) x)  as estados_contradictorios
from bronze.ml_ventas;

-- `estados_contradictorios` son ordenes que aparecen con DOS estados distintos
-- (por ejemplo una que estaba `paid` y despues se cancelo). No es un error: es
-- la misma orden en dos momentos. El bloque 2 se queda con la version MAS
-- NUEVA, que es la que vale.

-- ---------------------------------------------------------------------------
-- 2) LA LIMPIEZA: una fila por orden, la ultima que entro
-- ---------------------------------------------------------------------------
delete from bronze.ml_ventas a
using bronze.ml_ventas b
where a.id = b.id
  and a.ctid < b.ctid;

-- `ctid` es el identificador fisico de la fila que trae Postgres. Se usa porque
-- esta tabla no tiene clave primaria. Y se borra la de ctid MENOR --al reves
-- que en ml_envios-- porque aca las copias PUEDEN diferir: la que se inserto
-- despues es la que refleja el estado actual de la orden.

-- ---------------------------------------------------------------------------
-- 3) RECONSTRUIR gold: es lo unico que arregla los numeros del tablero
-- ---------------------------------------------------------------------------
-- Limpiar bronze NO alcanza. `gold.fact_ventas` ya tiene las lineas duplicadas
-- adentro, y ahi es donde el tablero las suma. Hay que reconstruirlo, y eso NO
-- se hace con SQL sino desde la maquina del orquestador:
--
--     python modelo.py --todo
--
-- Tarda unos minutos y reconstruye los cuatro meses leyendo bronze ya limpio.
-- Sin este paso, el tablero sigue mostrando los $8,5 M de mas.

-- ---------------------------------------------------------------------------
-- 4) DESPUES: que no quede ningun repetido, ni en bronze ni en gold
-- ---------------------------------------------------------------------------
select 'bronze.ml_ventas' as tabla,
       count(*) - count(distinct id) as filas_de_mas
from bronze.ml_ventas
union all
select 'gold.fact_ventas (orden+sku repetido)',
       coalesce(sum(veces - 1), 0)
from (
  select count(*) as veces
  from gold.fact_ventas
  where canal = 'Mercado Libre'
  group by nro_orden, sku
  having count(*) > 1
) r;

-- Los dos tienen que dar 0. El de gold recien va a dar 0 despues de correr
-- `python modelo.py --todo`.


-- ---------------------------------------------------------------------------
-- OPCIONAL: que no pueda volver a pasar
-- ---------------------------------------------------------------------------
-- Un indice unico hace que un segundo insert falle con un error en vez de
-- duplicar en silencio. Es a proposito: un script que se corta a la vista se
-- arregla; una tabla que se duplica sin avisar se descubre tres meses despues,
-- que es exactamente lo que paso aca.
--
-- OJO: solo tiene sentido DESPUES del arreglo de guardar_ventana_en_bd, porque
-- con el codigo viejo cada corrida moriria en la primera orden de la noche.
--
--   create unique index if not exists ml_ventas_id_uniq
--     on bronze.ml_ventas (id);
