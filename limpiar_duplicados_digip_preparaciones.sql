-- Limpieza de filas repetidas en bronze.digip_preparaciones
-- ==========================================================
--
-- POR QUE PASA
-- digip_preparaciones.py guarda una fila por (preparacion, pedido), con TODOS
-- los pedidos de cada preparacion. Pero solo consulta los pedidos de la
-- ventana de 7 dias, y hasta el arreglo de este commit borraba solo esos. Si
-- una preparacion juntaba un pedido de adentro con uno de afuera (mas viejo, de
-- Tienda Nube, o que no esta en digip_pedidos), la fila del de afuera se
-- agregaba de nuevo en cada corrida y no se borraba nunca.
--
-- Al 29/10/2026: 5 pares repetidos, 477 filas de mas.
--
--     14092763 / 4287          x64   (SALVATIERRA VIVIANA)
--     14264926 / 4378          x144
--     14528736 / 4396          x139
--     14629832 / 2087200542TN  x120
--     13842998 / 2043656989TN  x15
--
-- Y NO ERA INOFENSIVO: gold.reporte_logistica cruza por pedido y suma el neto
-- una vez por fila. La 14092763-Quo aparecia en la planilla de logistica con
-- $14.079.115 en vez de $390.793, y de ahi a la hoja de ruta de Sebastian y al
-- tablero.
--
-- ES SEGURO
-- Las copias son identicas: se verifico que ningun par repetido tenga dos
-- versiones distintas de transporte, bultos, volumen, kg o tipo, y que ninguna
-- tenga correcciones manuales. No se elige entre dos versiones, se tiran copias.
--
-- COMO CORRERLO: Supabase -> SQL Editor -> los bloques en orden.
-- El 1 y el 3 solo miran; el 2 es el unico que escribe. El 4 es la red.
-- Conviene correrlo DESPUES de mergear el arreglo, si no la corrida siguiente
-- los vuelve a crear.

-- ---------------------------------------------------------------------------
-- 1) ANTES: cuanto sobra, y confirmar que las copias no se contradicen
-- ---------------------------------------------------------------------------
select preparacion_id, pedido_codigo, count(*) as filas,
       count(distinct (transporte, bultos_preparacion, volumen_preparacion,
                       kg_preparacion, tipo_preparacion, cant_pedidos_en_prep)) as versiones,
       count(distinct (kg_preparacion_manual, volumen_preparacion_manual)) as versiones_manuales
from bronze.digip_preparaciones
group by 1, 2
having count(*) > 1
order by filas desc;

-- `versiones` y `versiones_manuales` TIENEN que dar 1 en todas las filas.
-- Si alguna da mas, NO sigas: no son copias, son datos distintos y hay que
-- mirar cual es el bueno.

-- ---------------------------------------------------------------------------
-- 2) LIMPIEZA: deja una fila por (preparacion, pedido)
-- ---------------------------------------------------------------------------
delete from bronze.digip_preparaciones d
using (
    select ctid,
           row_number() over (partition by preparacion_id, pedido_codigo order by ctid) as n
    from bronze.digip_preparaciones
) r
where d.ctid = r.ctid
  and r.n > 1;

-- ---------------------------------------------------------------------------
-- 3) DESPUES: tiene que dar 0, y Salvatierra tiene que volver a $390.793
-- ---------------------------------------------------------------------------
select count(*) - (select count(*) from (
           select 1 from bronze.digip_preparaciones group by preparacion_id, pedido_codigo) g
       ) as filas_de_mas
from bronze.digip_preparaciones;

select clave_fila, quo_prueba, monto_total
from gold.reporte_logistica
where clave_fila = '14092763-Quo';

-- ---------------------------------------------------------------------------
-- 4) LA RED: que repetir un par sea un error y no un numero inflado
-- ---------------------------------------------------------------------------
-- Tambien esta en claves_unicas_bronze.sql. Con esto, si el guardado vuelve a
-- duplicar por otro camino, el INSERT falla y el orquestador lo muestra.
create unique index if not exists digip_preparaciones_prep_pedido_uidx
    on bronze.digip_preparaciones (preparacion_id, pedido_codigo);
