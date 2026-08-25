"""Un solo lugar donde se arma la conexion a Postgres.

POR QUE EXISTE ESTE ARCHIVO

Supabase atiende por un pooler con un tope de 15 clientes en modo sesion, y ese
tope lo comparten TODOS: la web de Vercel y cada script de este repo.

SQLAlchemy, si no se le dice nada, abre hasta 15 conexiones por proceso
(pool_size 5 + max_overflow 10). O sea que un solo script puede quedarse con el
cupo entero y dejar afuera al resto. La corrida de las 13:14 del 25/08/2026
murio asi:

    FATAL: (EMAXCONNSESSION) max clients reached in session mode
           - max clients are limited to pool_size: 15
    FALLO DEFINITIVO: clasificar_clientes.py no pudo completarse tras 2 intentos

Los scripts de este repo son secuenciales: leen, transforman y escriben, de a
una consulta por vez. Con dos conexiones alcanza y sobra.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

# Dos y sin extras. Un script secuencial no usa mas de una a la vez; la segunda
# es para que pandas pueda leer y escribir sin esperarse a si mismo.
POOL_TAMANO = 2
POOL_EXTRA = 0

# Una conexion que el pooler cerro de su lado no se nota hasta que se usa, y
# ahi el script muere con "server closed the connection unexpectedly". Con
# pre_ping SQLAlchemy la prueba antes de entregarla y la reemplaza si esta
# muerta. Cuesta un SELECT 1.
POOL_PRE_PING = True

# Ninguna conexion se queda ocupando cupo mas de cinco minutos.
POOL_RECICLAR = 300


def url():
    """La URL de conexion, siempre desde variables de entorno."""
    return (
        f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )


def crear_engine(**extra):
    """El engine que usan todos los scripts. `extra` va tal cual a create_engine
       (por ejemplo connect_args), asi cada script puede seguir pidiendo lo suyo
       sin volver a escribir la URL ni el pool."""
    return create_engine(
        url(),
        pool_size=POOL_TAMANO,
        max_overflow=POOL_EXTRA,
        pool_pre_ping=POOL_PRE_PING,
        pool_recycle=POOL_RECICLAR,
        **extra,
    )
