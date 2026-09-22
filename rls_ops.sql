-- ============================================================================
--  RLS EN ops: UN CANDADO MAS, NO EL PRIMERO
--
--  El linter de Supabase marca `ops.estado` y `ops.despertador` porque no
--  tienen RLS, y ahi viven los tokens de Mercado Libre (clave `ml_tokens`).
--  Dicho asi suena a que estan a la vista con la anon key. NO LO ESTAN, y
--  conviene tener claro por que antes de tocar nada.
--
--  RLS ES LA SEGUNDA PUERTA. La primera son los permisos, y esa ya esta
--  cerrada. Medido el 22/09/2026:
--
--      has_schema_privilege('anon','ops','usage')           -> false
--      has_schema_privilege('authenticated','ops','usage')  -> false
--      has_table_privilege('anon','ops.estado','select')    -> false
--
--  Sin USAGE sobre el esquema, PostgREST no llega a la tabla por mas que RLS
--  este apagado: el esquema `ops` no esta expuesto y nadie le dio permiso a
--  ningun rol de la API. El linter no mira eso, mira solo RLS.
--
--  ENTONCES POR QUE PONERLO IGUAL. Porque la proteccion de hoy depende de que
--  nadie escriba nunca un `grant usage on schema ops to anon` ni un
--  `grant select on all tables ...` de apuro. El dia que eso pase --y ese tipo
--  de comando se escribe apurado, siempre-- con RLS puesto no se abre nada.
--  Sin RLS, se abre todo de una.
--
--  NO ROMPE EL ORQUESTADOR, y esto es lo que hay que verificar antes de
--  correrlo en cualquier otra tabla. Medido:
--
--      dueño de las dos tablas            -> postgres
--      relforcerowsecurity                -> false
--      rolbypassrls de postgres           -> true
--
--  O sea que postgres saltea RLS por partida doble: es el dueño (y sin
--  FORCE ROW LEVEL SECURITY el dueño no queda sujeto) y ademas tiene
--  BYPASSRLS. El orquestador, el despertador (pg_cron corre como postgres) y
--  el tablero se conectan todos con ese rol, asi que para ellos no cambia
--  nada. Por eso NO hacen falta politicas: activar RLS sin ninguna politica
--  deja la tabla cerrada para todos MENOS para quien la usa.
--
--  Si algun dia la app pasa a conectarse con un rol que no sea el dueño, esto
--  SI la deja afuera y hay que escribirle una politica. Queda dicho.
-- ============================================================================

alter table ops.estado      enable row level security;
alter table ops.despertador enable row level security;

-- Y el cinturon del cinturon: que quede escrito que los roles de la API no
-- tienen nada que hacer aca, en vez de depender de que nunca se les haya dado.
revoke all on schema ops                from anon, authenticated;
revoke all on all tables in schema ops  from anon, authenticated;


-- COMO COMPROBAR QUE QUEDO BIEN (tiene que dar todo false menos rls_activo):
--
--   select c.relname,
--          c.relrowsecurity                                   as rls_activo,
--          has_schema_privilege('anon','ops','usage')          as anon_entra,
--          has_table_privilege('anon','ops.'||c.relname,'select') as anon_lee
--     from pg_class c join pg_namespace n on n.oid = c.relnamespace
--    where n.nspname = 'ops' and c.relkind = 'r';
--
-- Y que el pipeline sigue andando: correr el orquestador a mano una vez, o
-- apretar "Actualizar ahora" en el tablero.
