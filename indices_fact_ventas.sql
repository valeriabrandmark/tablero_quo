-- ===========================================================================
--  gold.fact_ventas NO TENIA UN SOLO INDICE
-- ===========================================================================
--
--  APLICADA EN PRODUCCION el 05/10/2026.
--
--  La tabla la lee TODO el tablero y no tenia indices: ni los de filtro, ni
--  clave primaria. Cada consulta la recorria entera. Medido ese dia, en una
--  ventana de 3,3 horas: 45 escaneos secuenciales completos sobre 68.830
--  filas, y en la acumulada desde el arranque anterior iban 46.684 escaneos
--  con 2.447.962.175 filas leidas.
--
--  Lo que lo hace caro no es el tamanio -- son 28 MB -- sino la frecuencia:
--  en `mi-tablero-app` las rutas son `force-dynamic`, asi que no hay cache y
--  cada pantalla que alguien abre vuelve a barrer la tabla entera.
--
--  El 04/10 llego el aviso de Supabase de que el proyecto se estaba quedando
--  sin Disk IO Budget. Ese dia no hubo ningun pico: 20 corridas del
--  orquestador contra 19 y 21 los dias previos, y 96 tareas del cron igual
--  que siempre. No fue un pico, fue el gasto de siempre llegando al limite.
--
--  ---------------------------------------------------------------------
--
--  POR QUE `canal` PRIMERO Y POR QUE NO ALCANZA SOLA.
--
--  `canal` sola no filtra nada: Mercado Libre es el 79,3% de la tabla
--  (54.589 de 68.830 filas), asi que un indice solo por canal no se usaria
--  nunca -- Postgres prefiere el barrido antes que un indice que devuelve
--  cuatro de cada cinco filas.
--
--  Va primero igual porque `whereBase()` en lib/queries-meli.ts SIEMPRE
--  arranca con `canal = $1` y recien despues agrega el rango de fechas. Lo
--  que selecciona de verdad es la combinacion: un mes de los cinco que hay.
--
--  Medido con EXPLAIN (ANALYZE, BUFFERS) sobre el ultimo mes de Mercado
--  Libre, que es la consulta que mas corre:
--
--    sin indice:  Seq Scan    3.594 buffers   21,6 ms   59.106 filas descartadas
--    con indice:  Index Scan    385 buffers    4,3 ms        0 filas descartadas
--
--  ---------------------------------------------------------------------
--
--  EL SEGUNDO INDICE es para los combos de filtro, que se llenan con
--  `select distinct ... where canal = $1` en cada carga de pagina. Ese no
--  puede evitar recorrer las 54.397 filas del canal -- hay que mirarlas
--  todas para saber que hay 253 marcas -- pero las lee del indice y no de la
--  tabla (Index Only Scan):
--
--    sin indice:  2.175 buffers   37,0 ms
--    con indice:  1.808 buffers   13,2 ms
--
--  La mejora es mas modesta y se justifica solo porque esas consultas corren
--  en cada carga. EL ARREGLO DE VERDAD es que el tablero no pregunte las
--  marcas en cada pantalla: cambian una vez por dia. Eso es en la app, no
--  aca.
--
--  ---------------------------------------------------------------------
--
--  LO QUE CUESTAN. `modelo.py` hace DELETE + INSERT, no DROP TABLE, asi que
--  los indices sobreviven a cada corrida y no hay que recrearlos. En 3,3
--  horas la tabla recibio 6.973 inserts (~50.000 por dia): mantener cada
--  indice son ~1 MB diario de WAL contra los ~9 GB diarios de lectura que
--  evitan. Pesan 512 kB y 520 kB sobre una tabla de 28 MB.
--
--  CONCURRENTLY para no bloquear al orquestador si justo esta escribiendo.
--  No corre dentro de una transaccion: va suelto.
-- ===========================================================================

create index concurrently if not exists ix_fact_ventas_canal_fecha
  on gold.fact_ventas (canal, fecha);

create index concurrently if not exists ix_fact_ventas_canal_marca
  on gold.fact_ventas (canal, marca);

analyze gold.fact_ventas;

-- Para volver atras:
--   drop index concurrently if exists gold.ix_fact_ventas_canal_fecha;
--   drop index concurrently if exists gold.ix_fact_ventas_canal_marca;
