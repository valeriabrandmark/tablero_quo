-- El ABONO DEL PLAN de Tienda Nube. Nada mas.
--
-- OJO CON LO QUE **NO** VA ACA: el costo por transaccion de la plataforma
-- (0,7%) NO se guarda en esta tabla. Vive en bronze.comisiones_pasarela, en la
-- columna `cpt_pct`, y esta ahi desde que se armo esa tabla.
--
-- La razon es que NO ES UN COSTO DEL CANAL SINO DE CADA COBRO: depende de con
-- que pasarela se pago. Pago Nube lo bonifica al 0% y Nave lo cobra entero, asi
-- que la tarifa de Nave son 2,8780% = 2,1780% de Nave + 0,7% de Tienda Nube.
-- Ponerlo tambien aca abriria dos fuentes de verdad para el mismo numero, y la
-- primera version de este archivo hizo exactamente eso: sumaba el 0,7% otra vez
-- encima de lo que la tarifa de Nave ya incluia.
--
-- Lo que si es del canal es el abono: se paga todos los meses, se venda o no,
-- y no cambia con la pasarela. Por eso tiene tabla propia.
create table if not exists bronze.costos_plataforma_tn (
  vigente_desde date primary key,
  plan          text not null,
  -- Nullable a proposito: mientras no tengamos la factura, el tablero muestra
  -- lo que genera la operacion y avisa que falta el dato, en vez de dar por
  -- sentado que el plan es gratis.
  abono_mensual numeric,
  nota          text
);

comment on table bronze.costos_plataforma_tn is
  'Abono mensual del plan de Tienda Nube, versionado por fecha. El costo por transaccion NO va aca: es por cobro y vive en bronze.comisiones_pasarela.cpt_pct.';

insert into bronze.costos_plataforma_tn (vigente_desde, plan, abono_mensual, nota)
values ('2026-05-01', 'Escala', null,
        'Abono pendiente de confirmar contra la factura.')
on conflict (vigente_desde) do nothing;
