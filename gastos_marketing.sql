-- Lo que se gasta en traer gente a la tienda: agencia, pauta, influencers.
--
-- ES EL DIVIDENDO DEL COSTO DE ADQUISICION, y no sale de ninguna API. Ni la de
-- Tienda Nube ni la de Google Analytics saben cuanto se paga por la pauta: GA
-- mide GENTE, no plata que sale. El divisor --los clientes nuevos-- si lo
-- tenemos en gold.fact_ventas, asi que con esta tabla el costo por cliente se
-- puede calcular sin esperar a que Analytics junte un solo dato.
--
-- POR QUE IMPORTA EN ESTE CANAL EN PARTICULAR: de treinta y un compradores en
-- toda la historia de la tienda, volvio uno. Donde hay recompra se puede pagar
-- caro por entrar y recuperarlo en la segunda o tercera venta; aca cada venta
-- tiene que pagarse sola, asi que el costo de traer al cliente tiene que ser
-- menor que lo que deja esa unica compra.
--
-- UNA FILA POR MES Y CONCEPTO, para poder ver en que se fue cada peso. El mes
-- se guarda como el dia 1, igual que en costos_plataforma_tn.
create table if not exists bronze.gastos_marketing (
  mes      date not null,
  concepto text not null,
  monto    numeric not null,
  nota     text,
  primary key (mes, concepto)
);

comment on table bronze.gastos_marketing is
  'Inversion en marketing por mes y concepto (agencia, pauta, influencers). Carga manual: no viene de ninguna API.';
comment on column bronze.gastos_marketing.concepto is
  'Texto libre, pero conviene mantenerlo estable entre meses para poder comparar.';

-- LOS IMPORTES NO SE VERSIONAN ACA, por lo mismo que el abono del plan: este
-- repositorio es publico y lo que se paga de marketing es informacion nuestra.
-- Se cargan en la base, asi:
--
--   insert into bronze.gastos_marketing (mes, concepto, monto, nota) values
--     ('2026-08-01', 'Agencia',     000000, 'Honorarios del mes'),
--     ('2026-08-01', 'Pauta Meta',  000000, 'Segun Ads Manager'),
--     ('2026-08-01', 'Influencers', 000000, 'Canjes y pagos');
--
-- El tablero prorratea por dia, asi que un rango de medio mes toma la mitad.
