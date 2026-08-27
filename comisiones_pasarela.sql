-- ============================================================================
--  ARANCELES DE LAS PASARELAS DE PAGO DE TIENDA NUBE
--
--  Tienda Nube NO informa la comision en el pedido: no hay ningun campo con el
--  monto ni con el neto liquidado. Lo que si manda es la LLAVE para calcularla
--  -- que pasarela y con que medio se pago -- y de ahi sale esta tabla.
--
--  Los valores salen de la pantalla "Medios de pago" del panel de la tienda
--  (27/08/2026). Cuando cambien, se agrega una fila nueva con otro
--  `vigente_desde`: NO se pisa la vieja, porque las ventas de meses anteriores
--  se liquidaron con el arancel de su momento. Es el mismo criterio que
--  `costos_historicos`.
--
--  LA TASA VA CON IVA. Las pantallas publican "2,99% + IVA" y el IVA de la
--  comision no se toma como credito fiscal, asi que el costo real es
--  2,99 x 1,21. Por eso se guarda la tasa publicada y el IVA por separado: asi
--  la tabla se puede contrastar contra la pantalla sin hacer cuentas.
--
--  DOS SUPUESTOS, los dos anotados para poder revisarlos:
--
--  1) DEBITO SE COBRA COMO CREDITO. La API manda `credit_card` para los dos y
--     no hay forma de distinguirlos. En Pago Nube da igual (misma tasa), pero
--     en Nave el debito es 1,20% y el credito 1,80%. Se usa el de credito, que
--     es el caro: preferible pasarse de prudente que subestimar el costo.
--
--  2) EL CPT NO LLEVA IVA. En la pantalla de Nave el "+ IVA" figura sobre la
--     columna Tasas y el CPT esta en una columna aparte, sin esa aclaracion. Si
--     resultara que tambien lleva IVA, se corrige poniendo cpt_pct = 0,847.
-- ============================================================================

create schema if not exists bronze;

create table if not exists bronze.comisiones_pasarela (
    -- `gateway` y `metodo` son los valores CRUDOS de la API, no los nombres
    -- lindos: son los que llegan en el pedido y con los que hay que cruzar.
    gateway       text    not null,
    metodo        text    not null,
    vigente_desde date    not null,
    tasa_pct      numeric not null,
    iva_pct       numeric not null default 21,
    -- Costo por transaccion de Nave. Pago Nube lo tiene en "gratis!".
    cpt_pct       numeric not null default 0,
    nota          text,
    primary key (gateway, metodo, vigente_desde)
);

comment on table bronze.comisiones_pasarela is
  'Aranceles por pasarela y medio de pago. Se agrega una fila por cambio de tarifa, nunca se pisa.';

-- El `on conflict` deja correr este archivo entero las veces que haga falta.
insert into bronze.comisiones_pasarela
    (gateway, metodo, vigente_desde, tasa_pct, iva_pct, cpt_pct, nota)
values
    -- PAGO NUBE, plazo de acreditacion 14 dias.
    ('pago-nube', 'credit_card',   date '2026-05-01', 2.99, 21, 0,
     'Tarjeta de debito y credito, 14 dias. El debito no se puede distinguir.'),
    ('pago-nube', 'wallet',        date '2026-05-01', 2.99, 21, 0,
     'Billetera virtual (MODO), 14 dias.'),
    ('pago-nube', 'wire_transfer', date '2026-05-01', 0.85, 21, 0,
     'Transferencia bancaria. Unico plazo: 1 dia.'),

    -- NAVE, plazo de acreditacion 8 dias. Todas suman 0,7% de CPT.
    ('nave', 'credit_card', date '2026-05-01', 1.80, 21, 0.7,
     'Tarjeta de credito, 8 dias. El debito (1,20%) no se puede distinguir.'),
    ('nave', 'wallet',      date '2026-05-01', 0.80, 21, 0.7,
     'Billetera virtual, en el momento.'),
    ('nave', 'redirect',    date '2026-05-01', 0.80, 21, 0.7,
     'MODO por redireccion. Se cobra como billetera virtual.'),

    -- Un pedido 100% bonificado no pasa por ninguna pasarela: no hay que
    -- cobrarle comision a una transaccion que nunca existio.
    ('free', 'ninguno', date '2026-05-01', 0, 0, 0,
     'Pedido con 100% de descuento. No hubo cobro.')
on conflict (gateway, metodo, vigente_desde) do update
    set tasa_pct = excluded.tasa_pct,
        iva_pct  = excluded.iva_pct,
        cpt_pct  = excluded.cpt_pct,
        nota     = excluded.nota;

-- Para mirar de un vistazo lo que termina costando cada medio.
create or replace view bronze.comisiones_pasarela_efectiva as
select gateway, metodo, vigente_desde,
       tasa_pct, iva_pct, cpt_pct,
       round(tasa_pct * (1 + iva_pct/100) + cpt_pct, 4) as efectiva_pct,
       nota
from bronze.comisiones_pasarela;
