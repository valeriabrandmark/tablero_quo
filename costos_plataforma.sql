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
-- UNA FILA POR MES, y la ultima sigue vigente hasta que se cargue otra. Asi si
-- alguien se olvida de cargar el mes que viene, el tablero arrastra el ultimo
-- abono conocido en vez de mostrar cero -- que se leeria como que el plan dejo
-- de costar. En un pais donde el abono sube todos los meses, arrastrar el
-- anterior se queda corto; mostrar cero miente.
create table if not exists bronze.costos_plataforma_tn (
  vigente_desde date primary key,
  plan          text not null,
  -- Nullable a proposito: mientras no este la factura de ese mes, el tablero
  -- muestra lo que genera la operacion y avisa que falta el dato, en vez de
  -- dar por sentado que el plan es gratis.
  abono_mensual numeric,
  nota          text
);

comment on table bronze.costos_plataforma_tn is
  'Abono mensual del plan de Tienda Nube, versionado por fecha. El costo por transaccion NO va aca: es por cobro y vive en bronze.comisiones_pasarela.cpt_pct.';

-- LOS IMPORTES NO SE VERSIONAN ACA. Este repositorio es publico y lo que se
-- paga de abono es informacion comercial nuestra, no una lista de precios: los
-- aranceles de pasarela si estan commiteados porque son publicos, esto no.
--
-- Los valores viven cargados en la base. Para agregar el mes que viene:
--
--   insert into bronze.costos_plataforma_tn (vigente_desde, plan, abono_mensual, nota)
--   values ('2026-09-01', 'Escala', <lo que dice la factura>, 'Abono de factura');
--
-- La fila semilla queda con el abono en null para que un despliegue limpio
-- arranque avisando que falta el dato, y no inventando uno.
insert into bronze.costos_plataforma_tn (vigente_desde, plan, abono_mensual, nota)
values ('2026-05-01', 'Escala', null,
        'Semilla. El abono real se carga en la base, no se versiona.')
on conflict (vigente_desde) do nothing;
