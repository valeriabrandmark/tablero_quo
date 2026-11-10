-- Los candidatos a objetivo de producto de cada vendedor. La lee
-- proponer_objetivos.py; la eleccion final (cuantos, que marcas, cuanto stock
-- queda para el otro vendedor) esta en propuesta_objetivos.py, que es puro.
--
-- Parametros:
--   :vendedores     text[]  nombres, como en gold.fact_ventas ('SILVIO', ...)
--   :codigos        text[]  sus codigos de SIGMA, en el mismo orden ('006', ...)
--   :meses          text[]  los ultimos 3 meses comerciales, del mas viejo al mas nuevo
--   :mes            text    el mes que se propone (para el sell in del proveedor)
--   :patron         text    regex de los articulos de temporada, sobre la descripcion
--   :verano_desde   text    primer mes calendario del ultimo verano medido ('2026-02')
--   :verano_hasta   text    ultimo mes calendario ('2026-04')
--   :ritmo          float8  que parte del ritmo del verano se espera este mes
--   :limite         int     cuantos candidatos por vendedor y por tipo
--
-- DOS TIPOS DE CANDIDATO, porque se miden con historias distintas:
--
--   nucleo     lo que el vendedor vende todos los meses. La base es el PEOR de
--              los ultimos 3 meses. Probado contra lo que paso: con el peor mes
--              + 10 % el objetivo se cumplio el 39 % (2026-09) y el 49 %
--              (2026-10) de las veces; con la mediana, sin desafio, solo el 24 %
--              y el 37 %. La venta mayorista por SKU es a los saltos -- una caja
--              un mes y nada el siguiente -- y la mediana la sobreestima.
--
--   temporada  lo que se vende en verano. Los ultimos 3 meses son invierno y
--              no dicen nada: la base es el ritmo del verano pasado por :ritmo,
--              o lo del ultimo mes si fue mas. El verano sale de
--              bronze.sigma_ventas porque gold.fact_ventas arranca en mayo.
--              En 2026-10 las espirales X5 tuvieron objetivo de 480 con 1.945
--              en stock y no se vendio ninguna: el ritmo de marzo no sirve
--              para el arranque.
with vend as (
    select v as vendedor, c as codigo
      from unnest(cast(:vendedores as text[]), cast(:codigos as text[])) as t(v, c)
),
ultimos as (
    select fv.vendedor, fv.sku,
           coalesce(sum(fv.cantidad) filter (where fv.mes_comercial = (cast(:meses as text[]))[1]), 0) as u1,
           coalesce(sum(fv.cantidad) filter (where fv.mes_comercial = (cast(:meses as text[]))[2]), 0) as u2,
           coalesce(sum(fv.cantidad) filter (where fv.mes_comercial = (cast(:meses as text[]))[3]), 0) as u3,
           coalesce(sum(fv.precio_neto * fv.cantidad), 0) as pesos,
           coalesce(sum(fv.margen_total), 0) as margen
      from gold.fact_ventas fv
      join vend on vend.vendedor = fv.vendedor
     where fv.canal = 'Mayorista'
       and fv.mes_comercial = any(cast(:meses as text[]))
       and fv.sku is not null
     group by fv.vendedor, fv.sku
),
verano as (
    select vend.vendedor, sv."itemArticuloId"::text as sku,
           sum(sv."itemCantidad"::numeric) as unidades,
           sum(sv."itemCantidad"::numeric * sv."itemPrecioUnitario"::numeric) as pesos
      from bronze.sigma_ventas sv
      join vend on vend.codigo = sv.vendedor
     where to_char(sv.fecha::date, 'YYYY-MM') between :verano_desde and :verano_hasta
     group by vend.vendedor, sv."itemArticuloId"::text
),
-- Sumado por codigo: Digip tiene algunos codigos en dos filas (5 en noviembre
-- de 2026) y sin esto el SKU saldria dos veces.
stock as (
    select codigo as sku, sum("stock.disponible") as disponible
      from bronze.digip_stock group by codigo
),
sell_in as (
    select sku, max(descuento_pct) as pct
      from bronze.sell_in where mes_comercial = :mes group by sku
),
-- Un objetivo de un SKU solo que ya existe se propone con SU nombre: con otro,
-- la carga lo tomaria como un grupo nuevo con los mismos SKUs.
grupo_existente as (
    select min(i.valor) as sku, min(g.grupo) as grupo
      from gold.objetivos_grupo g
      join gold.objetivos_grupo_item i on i.grupo = g.grupo
     where g.criterio = 'sku'
     group by g.grupo
    having count(*) = 1
),
nucleo as (
    select 'nucleo' as tipo, u.vendedor, u.sku,
           u.u1, u.u2, u.u3,
           least(u.u1, u.u2, u.u3) as base,
           u.pesos / 3 as pesos_mes,
           u.margen / nullif(u.pesos, 0) as margen_pct,
           -- Que tan parejo se vende: el peor mes contra el promedio. Con 1 el
           -- objetivo es lo de todos los meses; con 0,1 es un regalo en un mes
           -- normal y una pared en uno flojo.
           least(u.u1, u.u2, u.u3) / ((u.u1 + u.u2 + u.u3) / 3.0) as parejo,
           null::float8 as verano_mes
      from ultimos u
     where u.u1 > 0 and u.u2 > 0 and u.u3 > 0
       and least(u.u1, u.u2, u.u3) >= 12
       and u.pesos > 0
),
temporada as (
    select 'temporada' as tipo, v.vendedor, v.sku,
           coalesce(u.u1, 0) as u1, coalesce(u.u2, 0) as u2, coalesce(u.u3, 0) as u3,
           greatest(v.unidades / 3 * :ritmo, coalesce(u.u3, 0)) as base,
           v.pesos / 3 as pesos_mes,
           u.margen / nullif(u.pesos, 0) as margen_pct,
           1.0 as parejo,
           (v.unidades / 3)::float8 as verano_mes
      from verano v
      left join ultimos u on u.vendedor = v.vendedor and u.sku = v.sku
      left join bronze.sigma_articulos a on a.id::text = v.sku
     where upper(a.descripcion) ~ :patron
       -- Dos docenas en tres meses: menos que eso no es una temporada, es una
       -- venta suelta.
       and v.unidades >= 24
),
candidatos as (
    select c.*, a.descripcion, a."attributes.marca" as marca,
           coalesce(s.disponible, 0) as stock,
           coalesce(si.pct, 0) as sell_in_pct,
           ge.grupo as grupo_existente
      from (select * from nucleo union all select * from temporada) c
      left join bronze.sigma_articulos a on a.id::text = c.sku
      left join stock s on s.sku = c.sku
      left join sell_in si on si.sku = c.sku
      left join grupo_existente ge on ge.sku = c.sku
     where coalesce(s.disponible, 0) > 0
       -- El nucleo no repite lo de temporada: esos se miden con el verano.
       and (c.tipo = 'temporada' or upper(a.descripcion) !~ :patron)
       -- Un articulo que se vende a perdida no se empuja. Sin margen medido
       -- (temporada sin ventas recientes) se deja pasar.
       and (c.margen_pct is null or c.margen_pct >= 0.05)
),
puntaje as (
    select c.*,
           -- Plata que mueve x que tan parejo se vende x el empujon del
           -- proveedor x el margen. Temporada no usa "parejo": se elige por lo
           -- que movio en verano.
           c.pesos_mes * c.parejo * (1 + c.sell_in_pct / 50.0)
               * (1 + coalesce(greatest(c.margen_pct, 0), 0.2)) as puntaje
      from candidatos c
)
select tipo, vendedor, sku, descripcion, marca,
       u1::float8, u2::float8, u3::float8, base::float8, verano_mes,
       pesos_mes::float8, margen_pct::float8, parejo::float8,
       stock::float8, sell_in_pct::float8, grupo_existente, puntaje::float8
  from (select p.*, row_number() over (partition by tipo, vendedor order by puntaje desc) as r
          from puntaje p) x
 where r <= :limite
 order by vendedor, tipo, puntaje desc
