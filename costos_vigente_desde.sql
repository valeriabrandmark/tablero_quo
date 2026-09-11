-- ============================================================================
--  bronze.costos_historicos: el costo pasa a regir DESDE UNA FECHA
--  Ya aplicada el 11/09/2026. Queda por escrito para saber que se hizo.
-- ============================================================================
--
--  POR QUE
--
--  La tabla tenia exactamente una fila por (sku, mes_comercial): el costo valia
--  el mes comercial entero. Cuando un proveedor manda lista nueva a mitad de
--  mes --que pasa seguido-- eso obliga a elegir entre dos cosas que no son
--  ciertas: dejar el costo viejo hasta el 5, o pisarlo y recostear hacia atras
--  ventas que se hicieron con el precio anterior.
--
--  Con `vigente_desde` conviven varios tramos del mismo mes y cada venta se
--  costea con el que regia ESE DIA. Sirve igual para corregir: se vuelve a
--  cargar el archivo de esa vigencia y modelo.py recalcula solo los dias que
--  dependian de ella.
--
--  EL BACKFILL NO CAMBIA NINGUN NUMERO. A cada una de las 39.689 filas que ya
--  estaban se le pone el primer dia de su propio mes comercial, que es justo el
--  periodo que hoy cubre.
--
--  OJO CON EL 2026-09. El mes comercial va del 6 al 5, pero agosto se estiro
--  hasta el 06/09 (CIERRES_EXCEPCION en calendario.py), asi que septiembre
--  arranca el 7 y no el 6. Poner el 6 habria dejado un dia de septiembre
--  costeado con la lista que todavia no regia.

alter table bronze.costos_historicos
  add column if not exists vigente_desde date;

update bronze.costos_historicos
   set vigente_desde = case
         when mes_comercial = '2026-09' then date '2026-09-07'
         else make_date(
                (split_part(mes_comercial, '-', 1))::int,
                (split_part(mes_comercial, '-', 2))::int,
                6)
       end
 where vigente_desde is null;

-- Sin DEFAULT a proposito: una fila sin vigencia no se sabe desde cuando rige,
-- y adivinarla es peor que no cargarla. costos.py siempre la escribe.
alter table bronze.costos_historicos
  alter column vigente_desde set not null;

-- La tabla no tenia ningun indice. Estos son los dos accesos que hay:
-- el tablero pide "el tramo de tal SKU en tal mes" y modelo.py la lee entera.
create index if not exists costos_historicos_sku_mes_vig
  on bronze.costos_historicos (sku, mes_comercial, vigente_desde);

-- Como quedo:
--   mes_comercial | vigente_desde | filas
--   2026-05       | 2026-05-06    | 7585
--   2026-06       | 2026-06-06    | 7706
--   2026-07       | 2026-07-06    | 7993
--   2026-08       | 2026-08-06    | 8162
--   2026-09       | 2026-09-07    | 8243
