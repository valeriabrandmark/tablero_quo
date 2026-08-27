-- ============================================================================
--  EL DESPERTADOR: que el orquestador no dependa del scheduler de GitHub.
--
--  POR QUE EXISTE
--
--  El workflow tiene `cron: '20 * * * *'`, pero el `schedule` de GitHub no da
--  ninguna garantia: es best-effort y lo dice la documentacion. Medido en este
--  repo el 26/08/2026: atraso mediano de 38 minutos, maximo 54, y ~2 slots
--  perdidos por dia. El 27/08 se cayo del todo -- 8 horas seguidas sin disparar
--  ni una sola vez, con el workflow `active`, el repo publico y las corridas
--  manuales andando perfecto. O sea que no habia nada roto de nuestro lado y
--  aun asi el tablero se quedaba viejo.
--
--  Este script agrega un SEGUNDO disparador que no pasa por el scheduler de
--  GitHub: un cron dentro de Supabase que, si el orquestador lleva demasiado
--  sin correr, le pega a la API de GitHub para forzar una corrida.
--
--  ES UNA RED, NO UN REEMPLAZO. El cron del workflow se queda como esta. Este
--  solo actua cuando el otro fallo, y por eso el umbral (75 min) esta POR
--  ENCIMA del intervalo normal (60 min) mas el atraso tipico: una corrida que
--  llega tarde pero llega no dispara nada.
--
--  POR QUE EN SUPABASE Y NO EN VERCEL
--
--  El cron de Vercel en plan Hobby solo permite una corrida por dia. Y ademas
--  asi el disparo no depende de que el tablero este levantado: la base es lo
--  unico que tiene que estar viva, y si no lo esta no hay tablero que mostrar.
--
--  EL TOKEN NO ESTA ACA. Vive en Vault, cifrado, bajo el nombre
--  `github_token_orquestador`. Este archivo solo lo lee. Ver el README para
--  como crearlo y rotarlo.
-- ============================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

create schema if not exists ops;

-- El registro de lo que hizo el despertador. Sin esto no hay forma de saber si
-- esta funcionando: un despertador que nunca dispara y uno que esta roto se ven
-- exactamente igual desde afuera.
create table if not exists ops.despertador (
    id                 bigserial   primary key,
    momento            timestamptz not null default now(),
    minutos_sin_correr numeric,
    disparo            boolean     not null,
    request_id         bigint,
    detalle            text
);

comment on table ops.despertador is
  'Una fila por chequeo del despertador. `disparo` = si forzo una corrida.';

-- Para poder mirar "que paso en las ultimas horas" sin escanear la tabla entera
-- el dia que tenga miles de filas (96 por dia).
create index if not exists despertador_momento_idx
    on ops.despertador (momento desc);


create or replace function ops.despertar_orquestador(umbral_minutos int default 75)
returns text
language plpgsql
security definer
set search_path = ops, public, vault
as $$
declare
    ultimo  timestamptz;
    minutos numeric;
    token   text;
    rid     bigint;
begin
    -- `ops.estado` con clave 'pasos' lo escribe el orquestador cada vez que
    -- termina un paso, asi que su `actualizado` es "cuando corrio por ultima
    -- vez". Se usa ESO y no la API de GitHub a proposito: mide lo que
    -- importa -- si los datos estan frescos -- y no si el workflow se disparo.
    -- Una corrida que arranca y muere sin hacer nada no cuenta como corrida.
    select actualizado into ultimo from ops.estado where clave = 'pasos';

    -- Sin fila todavia (base nueva) se toma como infinito y dispara: es el
    -- comportamiento correcto, hay que arrancar el pipeline.
    minutos := extract(epoch from (now() - coalesce(ultimo, '-infinity'::timestamptz))) / 60;

    if minutos < umbral_minutos then
        insert into ops.despertador (minutos_sin_correr, disparo, detalle)
        values (minutos, false, 'al dia');
        return format('ok: corrio hace %s min', round(minutos));
    end if;

    select decrypted_secret into token
    from vault.decrypted_secrets
    where name = 'github_token_orquestador';

    -- Sin token no se puede hacer nada, pero queda registrado. Es el modo de
    -- falla mas probable a futuro: los tokens vencen.
    if token is null then
        insert into ops.despertador (minutos_sin_correr, disparo, detalle)
        values (minutos, false, 'FALTA el secreto github_token_orquestador en Vault');
        return 'ERROR: falta el token en Vault';
    end if;

    -- El User-Agent no es opcional: la API de GitHub rechaza los pedidos que no
    -- lo mandan. El X-GitHub-Api-Version fija la version para que un cambio del
    -- lado de ellos no rompa esto en silencio.
    select net.http_post(
        url     := 'https://api.github.com/repos/valeriabrandmark/tablero_quo/actions/workflows/orquestador.yml/dispatches',
        body    := '{"ref":"main"}'::jsonb,
        headers := jsonb_build_object(
            'Authorization',        'Bearer ' || token,
            'Accept',               'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent',           'tablero-quo-despertador',
            'Content-Type',         'application/json'
        )
    ) into rid;

    insert into ops.despertador (minutos_sin_correr, disparo, request_id, detalle)
    values (minutos, true, rid, 'disparado');

    return format('DISPARADO: hacia %s min que no corria', round(minutos));
end;
$$;

comment on function ops.despertar_orquestador(int) is
  'Fuerza una corrida del orquestador si lleva mas de `umbral_minutos` sin correr.';

-- La funcion es `security definer` porque tiene que leer Vault. Que nadie mas
-- que el dueño de la base la pueda ejecutar: quien la corra puede disparar
-- corridas del pipeline a voluntad.
revoke all on function ops.despertar_orquestador(int) from public;

-- Cada 15 minutos. No cada 60: si el chequeo cayera justo antes de que se
-- cumpla el umbral, habria que esperar una hora entera para el siguiente. Con
-- 15 el atraso maximo del tablero queda en 75 + 15 = 90 minutos.
--
-- `schedule` con el mismo nombre reemplaza el job anterior, asi que este script
-- se puede volver a correr entero sin duplicar nada.
select cron.schedule(
    'despertar-orquestador',
    '*/15 * * * *',
    $cron$ select ops.despertar_orquestador(); $cron$
);
