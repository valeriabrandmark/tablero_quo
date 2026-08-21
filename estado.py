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

import json
import os

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

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
    return create_engine(
        f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
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
