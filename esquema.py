"""Tablas del medidor de elasticidad de precios. DDL explicito e idempotente.

POR QUE EXISTE ESTE ARCHIVO Y NO SE DEJA QUE PANDAS CREE LAS TABLAS

`to_sql(if_exists="append")` crea la tabla si no existe, y por eso el resto del
pipeline nunca necesito un DDL. Para las tablas que solo CRECEN eso no alcanza,
por dos motivos que ya se pagaron:

1. Sin clave primaria no hay idempotencia real. `guardar_foto_stock` promete en
   su docstring que correr el catalogo dos veces el mismo dia no duplica el dia,
   y lo hace borrando antes de insertar -- pero si el DELETE falla (bloqueo,
   permiso, tabla que no existe todavia) el except reintenta el INSERT SOLO, y
   ahi el dia queda duplicado en silencio. Con la PK puesta, el segundo insert
   explota en vez de mentir.

2. El tipo lo termina eligiendo el primer DataFrame que entra. Si una corrida
   trae `available_quantity` todo en nulo, pandas crea la columna como TEXT y la
   corrida siguiente empieza a fallar por una conversion. Con el DDL escrito, el
   tipo lo decidimos nosotros una vez.

Todo es `if not exists`: se puede correr las veces que haga falta.

    python esquema.py
"""

import os
import re

from dotenv import load_dotenv
from sqlalchemy import text
from conexion import crear_engine as crear_engine_base

load_dotenv()


# ---------------------------------------------------------------------------
#  BRONZE - lo que se OBSERVO, sin interpretar
# ---------------------------------------------------------------------------
#
# COMO SE GUARDA EL PULSO, Y POR QUE NO ES UNA FILA POR PUBLICACION POR PULSO
#
# Lo obvio seria guardar las 8.509 publicaciones en cada pulso. Con un pulso
# cada 2 horas eso da 102.000 filas por dia y 37 millones al ano, para un dato
# que casi nunca cambia: entre dos pulsos, el 99% de las publicaciones esta
# exactamente igual que antes.
#
# Por eso se guardan TRAMOS y no fotos: una fila nueva solo cuando el estado
# cambia de verdad (SCD tipo 2). Un articulo que estuvo vendible tres semanas
# es UNA fila con `desde` y `hasta`, no 250 fotos identicas.
#
# El precio va aparte de la disponibilidad a proposito. Son dos preguntas
# distintas y cambian a ritmos distintos: la disponibilidad se mueve pocas veces
# por mes, el precio lo mueve el repricer varias veces por dia. Juntos en la
# misma tabla, cada cambio de precio abriria tambien un tramo de disponibilidad
# y la tabla que tenia que ser chica se fragmentaria hasta volver al problema
# que veniamos a evitar.

DDL = """
create schema if not exists bronze;
create schema if not exists gold;

-- Una fila por PULSO (no por publicacion): deja ver los huecos del pipeline.
--
-- Es la tabla que hace honesta a todas las demas. Un tramo de disponibilidad
-- dice "estuvo vendible desde el lunes hasta el jueves", pero si el miercoles
-- el orquestador no corrio, ese dia no lo vimos: lo estamos suponiendo. Sin
-- este registro no hay forma de distinguir "estuvo vendible" de "no miramos",
-- y las dos cosas se leen igual en un promedio.
create table if not exists bronze.ml_pulso_corrida (
    momento       timestamptz  not null primary key,
    items         integer,
    vendibles     integer,
    sin_stock     integer,
    duracion_seg  double precision,
    origen        text
);

-- Tramos de DISPONIBILIDAD por publicacion (SCD tipo 2).
--
-- `hasta` en null = el tramo sigue abierto.
--
-- `visto_hasta` es el ultimo pulso que confirmo este estado, y NO es lo mismo
-- que `hasta`. Si el pipeline estuvo caido dos dias, el tramo abierto sigue
-- diciendo "vendible" pero `visto_hasta` quedo dos dias atras: esas 48 horas
-- son horas SIN DATO, no horas vendibles, y quien haga la cuenta tiene que
-- poder distinguirlas. Contarlas como vendibles infla el denominador del
-- experimento justo cuando el pipeline fallo.
create table if not exists bronze.ml_estado_item (
    item_id       text         not null,
    sku           text,
    inventory_id  text,
    desde         timestamptz  not null,
    hasta         timestamptz,
    visto_hasta   timestamptz  not null,
    vendible      boolean      not null,
    status        text,
    sub_status    text,
    motivo        text         not null,
    unidades      integer,
    primary key (item_id, desde)
);

create index if not exists ix_estado_item_abierto
    on bronze.ml_estado_item (item_id) where hasta is null;
create index if not exists ix_estado_item_sku
    on bronze.ml_estado_item (sku, desde);
create index if not exists ix_estado_item_ventana
    on bronze.ml_estado_item (desde, hasta);

-- Tramos de PRECIO por publicacion. Misma forma que el anterior.
--
-- Existe porque el experimento asigna una BANDA de markup (10-18%), no un
-- precio: el repricer se mueve con el competidor, asi que el markup realmente
-- aplicado varia dentro de la banda y dentro del dia. La elasticidad se calcula
-- contra el precio que estuvo puesto, no contra la banda que quisimos poner.
create table if not exists bronze.ml_precio_item (
    item_id       text         not null,
    sku           text,
    desde         timestamptz  not null,
    hasta         timestamptz,
    visto_hasta   timestamptz  not null,
    precio        double precision,
    primary key (item_id, desde)
);

create index if not exists ix_precio_item_abierto
    on bronze.ml_precio_item (item_id) where hasta is null;
create index if not exists ix_precio_item_ventana
    on bronze.ml_precio_item (sku, desde, hasta);

-- Tramos de BUY BOX (solo publicaciones de catalogo del experimento).
--
-- POR QUE ES IMPRESCINDIBLE Y NO UN LUJO
-- 4.047 de 8.509 publicaciones son de catalogo. En catalogo, el que no gana la
-- caja vende cerca de cero sin importar su precio. Y como la estrategia del
-- experimento es "ponerse apenas debajo del competidor", perder la caja esta
-- CORRELACIONADO con el tratamiento: la semana de markup alto es justo la
-- semana en la que mas se pierde. Sin esta tabla, "vendio menos porque estaba
-- caro" y "vendio menos porque le sacaron la caja" son indistinguibles, y las
-- dos empujan para el mismo lado: la elasticidad medida sale exagerada.
create table if not exists bronze.ml_buybox_item (
    item_id        text         not null,
    desde          timestamptz  not null,
    hasta          timestamptz,
    visto_hasta    timestamptz  not null,
    ganando        boolean,
    estado         text,
    precio_ganador double precision,
    primary key (item_id, desde)
);

create index if not exists ix_buybox_abierto
    on bronze.ml_buybox_item (item_id) where hasta is null;

-- `bronze.ml_stock_full_historico` NO esta aca, aunque parezca que corresponde.
--
-- La escribe `mercadolibre.py` (guardar_foto_stock) y la lee el tablero de
-- Stock Full: es del pipeline de ventas, no del experimento. Estuvo un tiempo
-- en este DDL, y eso obligo a `mercadolibre.py` a importar `asegurar_tablas` de
-- aca -- o sea que el pipeline de ventas pasaba a depender del experimento.
--
-- El 21/08/2026 eso costo caro: un `%` en un comentario de este archivo rompio
-- `mercadolibre.py --catalogo`, que no tiene nada que ver con elasticidad.
-- Cada uno crea lo suyo.


-- ---------------------------------------------------------------------------
--  GOLD - lo que se INTERPRETO
-- ---------------------------------------------------------------------------

-- La asignacion del experimento: que banda de markup le toca a cada SKU en cada
-- semana. La escribe `experimento.py --asignar` una sola vez y despues NO se
-- toca: si la asignacion se pudiera reescribir a mitad del experimento, las
-- semanas ya medidas quedarian atribuidas a una banda que no fue la que
-- estuvo puesta.
create table if not exists gold.experimento_markup (
    experimento  text         not null,
    sku          text         not null,
    grupo        smallint     not null,
    semana       smallint     not null,
    banda        text         not null,
    markup_min   double precision not null,
    markup_max   double precision not null,
    desde        timestamptz  not null,
    hasta        timestamptz  not null,
    primary key (experimento, sku, semana)
);

create index if not exists ix_experimento_ventana
    on gold.experimento_markup (experimento, desde, hasta);

-- El resultado: una fila por SKU y semana del experimento.
--
-- LA COLUMNA QUE IMPORTA ES `uds_por_dia_vendible`, no `unidades`.
--
-- Comparar unidades entre semanas es lo que arruina el experimento. Si un SKU
-- quebro stock el martes de la semana de markup 25-35%, esa semana muestra
-- menos unidades y la conclusion que sale es "con markup alto vende menos",
-- cuando en realidad no vendio porque no estaba a la venta. Con 56% del
-- catalogo hoy quebrado, ese sesgo no es un detalle: es el efecto dominante.
--
-- Por eso el denominador no es "la semana" sino las horas que el articulo
-- realmente estuvo a la venta.
create table if not exists gold.fact_experimento (
    experimento       text        not null,
    sku               text        not null,
    semana            smallint    not null,
    grupo             smallint,
    banda             text,
    desde             timestamptz,
    hasta             timestamptz,

    -- Reparto de la ventana. Las cuatro suman `horas_ventana`, siempre.
    horas_ventana     double precision,
    horas_vendible    double precision,
    horas_sin_stock   double precision,
    horas_pausada     double precision,
    horas_sin_dato    double precision,

    -- Buy box (null si el SKU no tiene publicaciones de catalogo).
    horas_ganando_bb  double precision,

    unidades          double precision,
    ordenes           integer,
    facturacion       double precision,
    costo             double precision,
    comision          double precision,
    envio             double precision,
    -- Margen de CONTRIBUCION: facturacion - costo - comision - envio. No
    -- descuenta IIBB, impuesto al cheque ni municipal: esas alicuotas son de la
    -- empresa y no del articulo, viven en `lib/impuestos.ts` del tablero y se
    -- aplican ahi. Duplicarlas aca garantizaria que un dia digan cosas
    -- distintas.
    margen            double precision,

    -- Precio promedio de lo VENDIDO (null si no vendio nada esa semana).
    precio_prom       double precision,
    -- Precio promedio PUBLICADO, ponderado por las horas que estuvo puesto.
    -- Existe para las semanas sin ventas: son las mas informativas del
    -- experimento -- "a este precio no vendio nada" es un dato -- y con solo el
    -- promedio de lo vendido quedarian en null y fuera del analisis.
    precio_publicado  double precision,
    -- Markup realmente aplicado sobre el costo, ponderado por horas. Es lo que
    -- hay que usar como variable explicativa, NO la banda: el repricer sigue al
    -- competidor, asi que dentro de la banda 25-35% el markup real se mueve.
    markup_realizado  double precision,

    -- Margen por unidad vendida. Es el criterio de DESEMPATE, y sale de una
    -- preferencia del negocio que no es obvia leyendo solo los totales.
    --
    -- Entre dos bandas que dejan lo mismo por dia conviene la de markup mas
    -- alto, porque vende menos unidades para ganar la misma plata. Y eso
    -- importa por dos motivos concretos. El stock dura mas, asi que el articulo
    -- no se queda sin mercaderia a mitad de semana. Y cada unidad movida cuesta
    -- trabajo -- preparar, despachar, atender-- que no depende de a cuanto se
    -- vendio. Vender 50 unidades marcando 10 por ciento y vender 20 marcando 30
    -- no son equivalentes aunque den el mismo total.
    --
    -- Null cuando no se vendio nada, que no es lo mismo que cero.
    margen_por_unidad double precision,

    -- unidades / (horas_vendible / 24). Null si no hubo horas vendibles: cero
    -- ventas sin exposicion no es un cero, es un dato que no existe, y ponerle
    -- 0 lo mete en los promedios como si el articulo hubiera fracasado.
    uds_por_dia_vendible double precision,

    primary key (experimento, sku, semana)
);


-- ---------------------------------------------------------------------------
--  EVOLUCION - columnas agregadas DESPUES de que la tabla ya existia
-- ---------------------------------------------------------------------------
--
-- POR QUE HACE FALTA ESTA SECCION, Y POR QUE ES UNA TRAMPA SILENCIOSA
--
-- Todo lo de arriba es `create table if not exists`. Eso alcanza la primera
-- vez, pero NO agrega una columna nueva a una tabla que YA existe. El
-- `if not exists` ve la tabla, no hace nada, y `asegurar_tablas` termina
-- diciendo "listo" -- o sea que el dia que se agrega una columna al DDL, quien
-- ya tenia la tabla se queda sin ella y NADA lo avisa.
--
-- Paso el 21/08/2026 con `margen_por_unidad`. Se corrio `python esquema.py`,
-- dijo "listo", y la columna no estaba. El consolidado habria fallado despues,
-- lejos de la causa.
--
-- `add column if not exists` es idempotente, asi que estas lineas se dejan para
-- siempre. REGLA: toda columna nueva va en DOS lugares, arriba en su
-- `create table` (para quien arranca de cero) y aca abajo (para quien ya tenia
-- la tabla). Si alguien se olvida de la segunda, lo levanta el chequeo de
-- `_verificar_columnas`.
alter table gold.fact_experimento
    add column if not exists margen_por_unidad double precision;
"""


def crear_engine():
    # El nombre se mantiene porque lo importan ml_pulso.py y experimento.py.
    # El pool sale de conexion.py; aca solo se agrega el encoding.
    return crear_engine_base(connect_args={"client_encoding": "utf8"})


# La misma regex con la que SQLAlchemy detecta un parametro en `text()`.
_PARAMETRO = re.compile(r"(?<![:\w\$]):(\w+)")


def _verificar_sin_parametros(ddl):
    """Explota si el DDL tiene algo que `text()` tomaria como parametro.

    Sin esto, agregar un `:algo` en un comentario del DDL hace fallar la
    creacion del esquema entera con un error que no menciona ni el comentario ni
    el archivo. Es el mismo tipo de trampa que ya costo una corrida con los `%`.
    """
    encontrados = _PARAMETRO.findall(ddl)
    if encontrados:
        raise ValueError(
            f"El DDL tiene {encontrados}, que `text()` va a tomar como parametros "
            "y hacen fallar la creacion del esquema. Ojo que agregarle un espacio "
            "adelante NO alcanza (`( :sku)` liga igual): hay que sacar los dos "
            "puntos, o duplicarlos si de verdad va un cast."
        )


# Las columnas que cada `create table` del DDL declara.
_TABLA = re.compile(
    r"create table if not exists\s+(\w+)\.(\w+)\s*\((.*?)\n\);", re.S | re.I
)


def _columnas_declaradas(ddl):
    """{(schema, tabla): {columnas}} segun el DDL."""
    declaradas = {}
    for schema, tabla, cuerpo in _TABLA.findall(ddl):
        columnas = set()
        for linea in cuerpo.splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("--"):
                continue
            if linea.lower().startswith(("primary key", "unique", "foreign key", "constraint")):
                continue
            nombre = re.match(r"(\w+)\s", linea)
            if nombre:
                columnas.add(nombre.group(1).lower())
        declaradas[(schema.lower(), tabla.lower())] = columnas
    return declaradas


def _verificar_columnas(con, ddl):
    """Explota si una tabla existe pero le falta alguna columna del DDL.

    Es la red que cierra la trampa del `create table if not exists`. Sin esto,
    agregar una columna al DDL y olvidarse del `alter` de la seccion EVOLUCION
    no da ningun error: la creacion "funciona", y el problema aparece mucho mas
    tarde y en otro lado.
    """
    faltantes = []
    for (schema, tabla), columnas in _columnas_declaradas(ddl).items():
        reales = {
            fila[0].lower()
            for fila in con.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_schema = :s and table_name = :t"
                ),
                {"s": schema, "t": tabla},
            )
        }
        if not reales:
            continue                      # la tabla no existe todavia; se acaba de crear
        for columna in sorted(columnas - reales):
            faltantes.append(f"{schema}.{tabla}.{columna}")

    if faltantes:
        raise RuntimeError(
            "Estas columnas estan en el DDL pero no en la base: "
            + ", ".join(faltantes)
            + ". La tabla ya existia, asi que `create table if not exists` no las "
            "agrego. Sumalas a la seccion EVOLUCION del DDL con "
            "`alter table ... add column if not exists`."
        )


def asegurar_tablas(engine=None):
    """Crea lo que falte. Barato y sin efecto si ya esta todo: se llama al
    principio de cada script que escribe estas tablas, asi que un despliegue
    nuevo no necesita que nadie se acuerde de correr un DDL a mano."""
    engine = engine or crear_engine()
    with engine.begin() as con:
        # `text(DDL)` y no `exec_driver_sql(DDL)`.
        #
        # `exec_driver_sql` manda la consulta CRUDA a psycopg2, que usa el
        # estilo `pyformat`: cualquier `%` en el texto lo toma como marcador de
        # parametro. Este DDL tiene cuatro, todos dentro de comentarios
        # ("markup 10-18%", "56% del competidor"), y con eso alcanzaba para que
        # la creacion del esquema muriera con
        #
        #   TypeError: immutabledict is not a sequence
        #
        # Rompio el paso `mercadolibre.py --catalogo` el 21/08/2026, que llama
        # a esta funcion desde `guardar_foto_stock`.
        #
        # `text()` compila el SQL para el dialecto y escapa los `%` como `%%`,
        # que psycopg2 devuelve como un `%` literal. Verificado: los cuatro
        # quedan escapados y el comentario llega intacto a Postgres.
        #
        # OJO CON LA OTRA MITAD DE `text()`, QUE ES LO QUE PUEDE ROMPERLO.
        #
        # `text()` interpreta `:nombre` como parametro. El DDL tiene 16 dos
        # puntos -- todos dentro de comentarios -- y funciona igual, asi que la
        # regla NO es "no usar dos puntos": eso seria imposible de sostener
        # escribiendo comentarios en castellano.
        #
        # Lo que liga es un `:` seguido de una palabra Y precedido por algo que
        # no sea letra, `:` ni `$` (la regex de SQLAlchemy es
        # `(?<![:\w\$]):(\w+)`). O sea:
        #
        #     "-- una fila por pulso: deja ver los huecos"  -> seguido de espacio, no liga
        #     "-- nota:importante"                          -> precedido por letra, no liga
        #     "select a::text"                              -> el `::` lo excluye, no liga
        #     "-- ojo con :sku"                             -> LIGA, y rompe la corrida
        #
        # Como es una invariante facil de romper sin darse cuenta, se chequea en
        # vez de confiar en que alguien lea este comentario.
        _verificar_sin_parametros(DDL)
        con.execute(text(DDL))
        _verificar_columnas(con, DDL)
    return engine


if __name__ == "__main__":
    asegurar_tablas()
    print("Esquema del experimento de elasticidad: listo.")
