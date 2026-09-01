"""El estado del pipeline, en Postgres en vez de archivos sueltos.

POR QUE

Habia tres archivos JSON al lado de los scripts, todos en .gitignore:

    ml_tokens.json     el token de Mercado Libre
    estado_pasos.json  cuando corrio bien cada paso
    estado_costos.json la huella del Excel de costos

Eso ataba el orquestador a UNA maquina, y de dos formas distintas:

1. No se puede mover a ningun lado. Un runner de GitHub Actions arranca con el
   disco limpio en cada corrida: sin `estado_pasos` correria TODO siempre, y sin
   `ml_tokens` no podria ni autenticarse.

2. Dos maquinas se pisan. Mercado Libre entrega un refresh_token NUEVO en cada
   renovacion y anula el anterior, asi que la maquina que renueva deja a la otra
   afuera. Paso el 20/08/2026: correr el orquestador desde la notebook dejo la
   PC de la oficina con un token muerto.

Con el estado en la base los dos problemas desaparecen juntos: cualquier maquina
lee y escribe el mismo estado, y el token vive en un solo lugar.

LA MIGRACION ES SOLA. `leer()` busca primero en la base; si no esta y todavia
existe el archivo viejo al lado del script, lo importa, lo guarda en la base y
lo devuelve. Nadie tiene que acordarse de correr nada, y la primera corrida
despues de este cambio se lleva el estado que ya habia.
"""

import hashlib
import json
import os
from contextlib import contextmanager

from sqlalchemy import text
from dotenv import load_dotenv
from conexion import crear_engine

load_dotenv()

# La carpeta del script, no la del que lo invoco: el Programador de tareas de
# Windows arranca los procesos parado en C:\Windows\System32.
CARPETA = os.path.dirname(os.path.abspath(__file__))

DDL = """
create schema if not exists ops;

-- Una fila por cosa que el pipeline necesita recordar entre corridas.
--
-- `jsonb` y no columnas: lo que se guarda cambia con el tiempo (el token de ML
-- trae los campos que le da la gana, los pasos van cambiando de nombre), y
-- migrar el esquema cada vez que eso pasa no aporta nada. Lo unico que hace
-- falta es guardar y recuperar.
create table if not exists ops.estado (
    clave       text        primary key,
    valor       jsonb       not null,
    actualizado timestamptz not null default now()
);
"""

# Los archivos que se importan solos la primera vez.
LEGADO = {
    "ml_tokens": "ml_tokens.json",
    "pasos": "estado_pasos.json",
    "costos": "estado_costos.json",
}


def _engine():
    return crear_engine(
        # Este es un proceso de fondo, no una pagina esperando: el limite de 2
        # minutos de Supabase esta pensado para lo segundo.
        connect_args={"options": "-c statement_timeout=120000"},
    )


def _asegurar(con):
    con.execute(text(DDL))


def leer(clave, por_defecto=None):
    """El valor guardado, o `por_defecto` si nunca se guardo nada.

    Si la clave no esta en la base pero SI existe el archivo viejo al lado del
    script, lo importa y lo guarda. Asi la primera corrida despues de migrar no
    empieza de cero: se lleva el estado que ya habia en esa maquina.
    """
    with _engine().begin() as con:
        _asegurar(con)
        fila = con.execute(
            text("select valor from ops.estado where clave = :c"), {"c": clave}
        ).scalar()
        if fila is not None:
            return fila

        archivo = LEGADO.get(clave)
        if archivo:
            ruta = os.path.join(CARPETA, archivo)
            if os.path.exists(ruta):
                try:
                    with open(ruta, encoding="utf-8") as f:
                        valor = json.load(f)
                except (OSError, json.JSONDecodeError):
                    print(f"  (aviso: {archivo} ilegible, se ignora)")
                else:
                    print(f"  Migrando {archivo} -> ops.estado['{clave}']")
                    _escribir(con, clave, valor)
                    return valor

    return por_defecto


def guardar(clave, valor):
    with _engine().begin() as con:
        _asegurar(con)
        _escribir(con, clave, valor)


def _escribir(con, clave, valor):
    con.execute(
        text("""
            insert into ops.estado (clave, valor, actualizado)
                 values (:c, cast(:v as jsonb), now())
            on conflict (clave) do update
                    set valor = excluded.valor,
                        actualizado = now()
        """),
        {"c": clave, "v": json.dumps(valor, ensure_ascii=False)},
    )


# ---------------------------------------------------------------------------
#  CANDADO ENTRE PROCESOS
# ---------------------------------------------------------------------------


def _numero_de_candado(clave):
    """El numero con el que Postgres identifica el candado.

    `pg_advisory_lock` no mira textos, mira un bigint, asi que hay que convertir
    la clave. Se usa blake2b y NO `hash()` de Python: desde 3.3 `hash()` de un
    texto lleva una semilla aleatoria por proceso, o sea que dos runners
    calcularian numeros distintos para la misma clave y cada uno tomaria un
    candado distinto -- que es exactamente no tener candado.
    """
    return int.from_bytes(
        hashlib.blake2b(clave.encode("utf-8"), digest_size=8).digest(),
        "big",
        signed=True,
    )


class _Sesion:
    """Leer y guardar DENTRO de la transaccion que tiene el candado tomado.

    Existe porque `leer` y `guardar` sueltos abren cada uno su conexion: usados
    adentro del candado, leerian y escribirian por afuera de la transaccion que
    lo sostiene, y el candado no estaria protegiendo nada.
    """

    def __init__(self, con):
        self._con = con

    def leer(self, clave, por_defecto=None):
        fila = self._con.execute(
            text("select valor from ops.estado where clave = :c"), {"c": clave}
        ).scalar()
        return por_defecto if fila is None else fila

    def guardar(self, clave, valor):
        _escribir(self._con, clave, valor)


@contextmanager
def con_candado(clave):
    """Un candado de Postgres tomado por `clave`, con su leer/guardar adentro.

    PARA QUE. Hay estado que no se puede leer-modificar-escribir de a dos a la
    vez, y el caso que duele es el token de Mercado Libre: ML entrega un
    refresh_token nuevo en cada renovacion y ANULA el anterior, asi que dos
    procesos que renueven juntos dejan guardado uno que ya no vale -- y ahi hay
    que reautorizar a mano. Paso el 20/08/2026 entre dos maquinas.

    POR QUE NO ALCANZA UN `threading.Lock`. Ese candado vive dentro de UN
    proceso. El workflow de antiguedad y el del orquestador son dos runners
    distintos, en dos maquinas distintas: comparten la base y nada mas. El
    unico lugar donde los dos se pueden poner de acuerdo es Postgres.

    ES `xact` Y NO A SECAS: el candado se suelta solo al cerrar la transaccion,
    tambien si el bloque explota en el medio. Con `pg_advisory_lock` pelado, un
    proceso que muere con el candado tomado deja a los demas esperando hasta que
    el pooler cierre la conexion.

    CUANTO SE PUEDE ESPERAR. El que espera lo hace adentro de un
    `statement_timeout` de 120 segundos (ver `_engine`), asi que si el que tiene
    el candado se cuelga, el otro muere con un error en vez de esperar para
    siempre. Da holgura: lo mas largo que se hace con el candado tomado es la
    renovacion del token, con un timeout de lectura de 60 segundos.
    """
    with _engine().begin() as con:
        _asegurar(con)
        con.execute(
            text("select pg_advisory_xact_lock(:k)"),
            {"k": _numero_de_candado(clave)},
        )
        yield _Sesion(con)
