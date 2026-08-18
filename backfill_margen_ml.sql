-- Backfill de margen_total en Mercado Libre
-- ==========================================
--
-- POR QUE HACE FALTA
-- `modelo.py` calculaba el margen de Mercado Libre restando la comision una
-- sola vez por linea, cuando `sale_fee` de la API es la comision de UNA unidad.
-- En las lineas de mas de una unidad el margen quedaba inflado.
--
-- El arreglo ya esta hecho en modelo.py, pero ese script solo reconstruye los
-- ULTIMOS 7 DIAS de gold.fact_ventas (ventana movil), asi que toda la historia
-- anterior sigue con el numero viejo. Esto la corrige de una.
--
-- ES SEGURO Y SE PUEDE REPETIR: margen_total es una columna DERIVADA. No se
-- inventa nada, se recalcula desde precio_neto, costo_unitario, comision y
-- envio, que estan bien. Correrlo dos veces da el mismo resultado.
--
-- COMO CORRERLO: Supabase -> SQL Editor -> pegar y ejecutar los tres bloques
-- en orden. El 1 y el 3 solo miran; el 2 es el unico que escribe.
--
-- ATAJO O CAMINO LARGO
-- Esto arregla el margen y nada mas, en dos segundos. La otra opcion es
-- `python modelo.py --todo`, que reconstruye los cuatro meses enteros: tarda
-- bastante mas, pero ademas mete en gold el costo de envio que ml_envios.py
-- haya recuperado de los meses viejos, cosa que este SQL no puede hacer porque
-- ese dato todavia no esta en fact_ventas.
--
-- Si vas a correr `modelo.py --todo` igual, este script no hace falta.

-- ---------------------------------------------------------------------------
-- 1) ANTES: cuanto se esta corrigiendo
-- ---------------------------------------------------------------------------
select count(*)                                                    as filas_a_corregir,
       round(sum(margen_total)::numeric)                           as margen_hoy,
       round(sum((precio_neto - costo_unitario - coalesce(comision, 0)) * cantidad
                 - coalesce(envio, 0))::numeric)                   as margen_corregido,
       round(sum(comision * (cantidad - 1))::numeric)              as diferencia
from gold.fact_ventas
where canal = 'Mercado Libre'
  and costo_unitario is not null
  and coalesce(comision, 0) <> 0
  and cantidad > 1;

-- Esperado al 18/08/2026: ~6.800 filas y una diferencia de ~21,5 M.
-- Solo se tocan las lineas de MAS DE UNA UNIDAD: con cantidad = 1,
-- comision * 1 = comision, asi que esas ya estaban bien.

-- ---------------------------------------------------------------------------
-- 2) LA CORRECCION
-- ---------------------------------------------------------------------------
update gold.fact_ventas
set margen_total = (precio_neto - costo_unitario - coalesce(comision, 0)) * cantidad
                   - coalesce(envio, 0)
where canal = 'Mercado Libre'
  and costo_unitario is not null
  and coalesce(comision, 0) <> 0
  and cantidad > 1;

-- Las lineas sin costo_unitario quedan con margen_total en null, que es lo que
-- ya hacia modelo.py: sin costo no hay margen que calcular, y poner 0 seria
-- decir "no ganamos nada", que es otra cosa.

-- ---------------------------------------------------------------------------
-- 3) DESPUES: que no quede ninguna fila mal, en ningun canal
-- ---------------------------------------------------------------------------
select canal,
       count(*)                                                    as lineas,
       count(*) filter (
         where margen_total is not null
           and abs(margen_total - ((precio_neto - costo_unitario - coalesce(comision, 0)) * cantidad
                                   - coalesce(envio, 0))) > 0.01
       )                                                           as filas_mal
from gold.fact_ventas
group by canal
order by canal;

-- `filas_mal` tiene que dar 0 en los tres canales.
