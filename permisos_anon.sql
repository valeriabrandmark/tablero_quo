-- ===========================================================================
--  LA CLAVE DEL NAVEGADOR NO PUEDE ESCRIBIR EN LA BASE
-- ===========================================================================
--
-- Supabase da dos claves. La `anon` viaja al navegador: esta en el codigo de
-- cualquier pagina del tablero, asi que la tiene cualquiera que sepa mirar. La
-- `service_role` vive en un servidor y no se publica.
--
-- En Settings -> API estaban expuestos cuatro esquemas: public, graphql_public
-- y ademas bronze y gold. Eso hace que todo lo que `anon` tenga permitido en
-- esos esquemas se pueda hacer POR INTERNET, sin credencial propia.
--
-- Y `anon` tenia INSERT, UPDATE y DELETE en siete lugares.
--
-- ===========================================================================
--  LO MAS GRAVE NO ERA LO QUE PARECIA
-- ===========================================================================
--
-- A simple vista el problema eran las cinco tablas de cuentas corrientes. Pero
-- lo peor estaba en una vista:
--
--   public.v_elasticidad_ml es un SELECT simple sobre gold.fact_ventas, o sea
--   AUTO-ACTUALIZABLE para Postgres. Y no tiene security_invoker, asi que
--   corre con los permisos de su dueño (postgres), no con los de quien
--   pregunta.
--
--   Las dos cosas juntas: escribir en la vista escribe en gold.fact_ventas,
--   con permisos de superusuario, desde una clave publica. Un DELETE ahi se
--   llevaba las ventas del tablero entero.
--
-- No hay indicios de que haya pasado. Pero la puerta estaba abierta.
--
-- ===========================================================================
--  QUE SE SACO Y QUE NO
-- ===========================================================================
--
-- Se sacan INSERT, UPDATE, DELETE y TRUNCATE de `anon` y de `authenticated`.
--
-- EL SELECT NO SE TOCA, a proposito: las planillas de logistica leen
-- public.reporte_logistica con la clave anonima. Sacarles la lectura las
-- rompe, y lo urgente era que nadie pueda borrar.
--
-- Nada de lo que escribe hoy usa la clave anonima: las planillas escriben con
-- la service key, y el orquestador va por conexion directa. Por eso esto no
-- rompe nada.
-- ===========================================================================

BEGIN;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON
    bronze.cuentas_corrientes_aging,
    bronze.cuentas_corrientes_cancelaciones,
    bronze.cuentas_corrientes_historial_diario,
    bronze.cuentas_corrientes_historial_scoring,
    bronze.cuentas_corrientes_scoring
FROM anon, authenticated;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON
    public.v_elasticidad_ml,
    public.reporte_logistica
FROM anon, authenticated;

COMMIT;

-- Para comprobar que quedo limpio (tiene que devolver cero filas):
--
--     select g.table_schema, g.table_name, g.grantee, g.privilege_type
--     from information_schema.role_table_grants g
--     where g.grantee in ('anon','authenticated')
--       and g.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
--       and g.table_schema in ('bronze','gold','public');

-- ===========================================================================
--  LO QUE QUEDA PENDIENTE, Y NO SE HIZO ACA
-- ===========================================================================
--
-- Con la clave del navegador todavia SE PUEDE LEER:
--
--     gold.fact_ventas            todas las ventas: precios, costos, clientes
--     gold.reporte_logistica      y las dos vistas de public que salen de ahi
--     bronze.cuentas_corrientes_* el scoring y la deuda de cada cliente
--
-- Eso no se toco porque no se puede hacer a ciegas: hay planillas leyendo con
-- esa clave y sacarles la lectura las deja mudas. Lo que corresponde es ir una
-- por una, ver con que clave lee cada consumidor y pasarlas a la service key
-- --o a una vista acotada-- antes de revocar.
--
-- Mientras tanto conviene saber que esos datos son publicos para cualquiera
-- que tenga la clave anonima, que esta en el codigo del tablero.
