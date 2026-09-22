-- ============================================================================
--  LA RED QUE CONVIERTE UN DUPLICADO EN UN ERROR
--
--  El codigo ya no deberia duplicar --ver `guardado.py` y sus pruebas-- pero
--  "no deberia" es exactamente lo que se creia el 21/08 y el 14/08. Estos
--  indices son la diferencia entre enterarse en la corrida siguiente y
--  enterarse mes y medio despues porque un sell out a mano no cierra.
--
--  Un indice unico no es una optimizacion: es una afirmacion sobre el negocio.
--  Con el puesto, duplicar deja de ser posible en silencio -- el INSERT falla,
--  el orquestador lo ve y queda en el log.
--
--  ES SEGURO CORRERLO VARIAS VECES: los tres usan IF NOT EXISTS.
--
--  ANTES DE CORRERLO conviene confirmar que no haya duplicados, porque si los
--  hay el CREATE UNIQUE INDEX falla (y esta bien que falle: hay que limpiarlos
--  primero). Al 22/09/2026 las tres tablas estaban limpias:
--
--      select count(*) - count(distinct id) from bronze.ml_ventas;      -- 0
--      select count(*) - count(distinct id) from bronze.sigma_compras;  -- 0
--      select count(*) - (select count(*) from (
--               select 1 from bronze.sigma_ventas group by id, item) g)
--        from bronze.sigma_ventas;                                      -- 0
-- ============================================================================


-- UNA LINEA DE VENTA ES (COMPROBANTE, RENGLON), no solo el comprobante: un
-- mismo `id` tiene un `item` por cada articulo facturado. Ese es justo el caso
-- que hace que NO se pueda deduplicar a ciegas mirando las columnas de negocio:
-- en FA9-00000916 el articulo SS06007 esta en el item 18 con 240 unidades y en
-- el item 21 con 168, las dos a $935,79. Son dos renglones reales del mismo
-- comprobante, no una fila repetida.
--
-- (Este ya estaba creado a mano el 22/09, junto con la limpieza de las 31
-- lineas duplicadas del 14/08. Queda escrito aca para que exista en el repo y
-- no solo en la base.)
create unique index if not exists sigma_ventas_id_item_uidx
    on bronze.sigma_ventas (id, item);

-- Una fila por factura de compra, asi que alcanza con el id.
create unique index if not exists sigma_compras_id_uidx
    on bronze.sigma_compras (id);

-- Una fila por orden de Mercado Libre. Este ya existia (`ml_ventas_id_uniq`) y
-- fue el que descubrio, el 26/08, que la paginacion de ML devuelve la misma
-- orden dos veces cuando se actualiza mientras se recorren las paginas.
create unique index if not exists ml_ventas_id_uniq
    on bronze.ml_ventas (id);


-- ============================================================================
--  Y LOS INDICES QUE HACEN QUE EL BORRADO POR VENTANA NO SE CUELGUE
--
--  `guardar_ventana` borra con `"fecha" >= piso AND "fecha"::date >= cutoff`.
--  El segundo filtro es el que decide; el primero esta solo para que Postgres
--  pueda usar un indice, porque NINGUN indice sirve a un cast. Sin el, el
--  DELETE recorre la tabla entera -- y eso es lo que se paso del
--  statement_timeout de Supabase el 21/08 y empezo el incidente de los 2.548
--  duplicados.
--
--  `sigma_ventas` ya tiene el suyo (`sigma_ventas_fecha_idx`) y `ml_ventas`
--  tambien (`ml_ventas_date_created_idx`). `sigma_compras` no tenia ninguno.
-- ============================================================================

create index if not exists sigma_compras_fecha_idx
    on bronze.sigma_compras ("fechaFactura");
