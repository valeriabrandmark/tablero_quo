"""Como se guarda una ventana movil en bronze. UN SOLO LUGAR.

============================================================================
 POR QUE ESTE ARCHIVO EXISTE: LA MISMA FALLA, DOS VECES
============================================================================

`sigma.py` y `mercadolibre.py` tenian CADA UNO su propia copia de
`guardar_ventana_en_bd`. Eran casi iguales, y esa palabra --casi-- costo dos
incidentes de duplicados:

  * 21/08/2026, Mercado Libre: 2.548 ordenes duplicadas. El DELETE de la
    ventana se paso del statement_timeout de Supabase, la transaccion hizo
    rollback, y un `except Exception` que atrapaba cualquier cosa dejo que la
    insercion siguiera igual. El paso reporto OK.

  * 14/08/2026, SIGMA: 31 lineas duplicadas (comprobantes 4343 a 4346,
    2.046 unidades de mas en el mes comercial 2026-08). Se descubrio el
    22/09, mes y medio despues, porque un sell out calculado a mano no
    cerraba: el SKU PR02007 daba 48 unidades cuando las reales eran 36.

La copia de Mercado Libre se blindo en agosto. La de SIGMA no, porque nadie
tenia por que acordarse de que existia. Un arreglo que hay que aplicar dos
veces se aplica una.

Ahora hay una sola. Si manana aparece un tercer origen con ventana movil,
usa esta o no usa ninguna.

============================================================================
 LAS CUATRO COSAS QUE HACE BIEN, Y POR QUE CADA UNA
============================================================================

1. BORRAR E INSERTAR EN UNA SOLA TRANSACCION.
   Separadas, entre el commit del DELETE y el del INSERT la tabla se queda
   SIN la ventana, y el tablero la lee en vivo. Y si el INSERT falla ahi
   quedo: una semana borrada. En una sola, quien consulta sigue viendo la
   version anterior completa hasta que la nueva esta entera.

2. SOLO SE TOLERA UN ERROR: QUE LA TABLA NO EXISTA.
   Es el `except` del 21/08. Cualquier otra cosa explota, el orquestador la
   ve, la reintenta y queda en el log. Guardar de menos es peor que fallar:
   fallar deja los datos como estaban.

3. EL DELETE PUEDE USAR INDICE.
   `"fecha"::date >= cutoff` es un cast, y ningun indice sirve a un cast: el
   borrado recorria la tabla entera. Eso es lo que se paso del timeout el
   21/08. El pre-filtro de texto `>= piso` es redundante con el `::date` y
   esta puesto solo para que el indice entre; el filtro exacto sigue
   decidiendo que se borra.

4. TAMBIEN SE BORRA POR CLAVE NATURAL.
   Las filas que se estan por insertar se borran por su clave, ademas de por
   ventana. Eso es exacto por definicion --la clave que se borra es la misma
   que se agrega-- y no depende de husos, formatos ni bordes de dia. En ML
   hacia falta de verdad: `date_created` es texto con offset -04:00 y
   Postgres resuelve ese `::date` en UTC, asi que una venta de las 21 de aca
   ya era del dia siguiente alla y quedaba fuera del borrado por ventana.
"""

from contextlib import nullcontext
from datetime import timedelta

from sqlalchemy.engine import Connection

from errores_bd import es_tabla_inexistente


def listas_a_texto(df):
    """Pasa a texto las columnas que traen listas o diccionarios.

    Postgres no tiene donde poner un dict de Python. Se guardan como JSON en
    una columna de texto: bronze es la zona de aterrizaje y su trabajo es
    aguantar lo que el origen mande, no discutirlo.
    """
    import json as _json

    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
            df[col] = df[col].apply(
                lambda x: _json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x
            )
    return df


def sincronizar_columnas(destino, tabla, df):
    """Agrega a la tabla las columnas que el origen empezo a mandar y no estan.

    POR QUE EXISTE. El 31/08/2026 SIGMA sumo el campo `operacionId` a
    ExportArticulosVendidos. `to_sql(if_exists="append")` no lo tolera: falla
    con "Unconsumed column names" y se cae la extraccion ENTERA. Las ventas
    mayoristas dejaron de actualizarse por una columna nueva que ni siquiera
    usamos.

    SE AGREGA Y NO SE DESCARTA. Tirar las columnas desconocidas tambien
    evitaria el error, pero en silencio: el dia que el origen mande algo que SI
    importa, nadie se enteraria hasta necesitarlo.

    Todo como `text`, que acepta cualquier cosa que venga. Convertirlo despues
    es barato; perder el dato no.

    `destino` PUEDE SER UN ENGINE O UNA CONEXION YA ABIERTA. Cuando el llamador
    esta dentro de una transaccion hay que pasarle ESA conexion: abrir otra por
    dentro pediria un ACCESS EXCLUSIVE sobre una tabla que la de afuera ya tiene
    tomada, y las dos se quedarian esperando para siempre.
    """
    ctx = nullcontext(destino) if isinstance(destino, Connection) else destino.begin()
    faltantes = []
    with ctx as con:
        existentes = {
            f[0]
            for f in con.exec_driver_sql(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'bronze' AND table_name = %(t)s""",
                {"t": tabla},
            ).fetchall()
        }
        if not existentes:
            return  # la tabla no existe todavia: la crea el to_sql
        for col in df.columns:
            if col not in existentes:
                faltantes.append(col)
                con.exec_driver_sql(
                    f'ALTER TABLE bronze."{tabla}" ADD COLUMN IF NOT EXISTS "{col}" text'
                )
    if faltantes:
        print(f"  COLUMNAS NUEVAS en bronze.{tabla}: {', '.join(faltantes)}")
        print("  (agregadas como text: el origen cambio y quedo registrado)")


def _borrar_por_clave(con, tabla, clave, df):
    """Borra las filas que se estan por insertar, por su clave natural.

    LAS COLUMNAS DE LA CLAVE TIENEN QUE SER ENTERAS. Hoy lo son en los tres
    usos (`id` en ml_ventas y sigma_compras, `(id, item)` en sigma_ventas) y
    el `::bigint[]` es a proposito: comparar `id::text` contra un `text[]`
    tambien anda, pero el cast impide usar el indice -- medido con 3 ids,
    Index Scan de costo 5,35 contra un Seq Scan de 13.871.
    """
    columnas = ", ".join(f'"{c}"' for c in clave)
    valores = {f"k{i}": [int(v) for v in df[c].tolist()] for i, c in enumerate(clave)}
    arrays = ", ".join(f"%({k})s::bigint[]" for k in valores)

    if len(clave) == 1:
        sql = f'DELETE FROM bronze."{tabla}" WHERE {columnas} = ANY({arrays})'
    else:
        sql = (
            f'DELETE FROM bronze."{tabla}" '
            f"WHERE ({columnas}) IN (SELECT * FROM unnest({arrays}))"
        )
    return con.exec_driver_sql(sql, valores).rowcount


def guardar_ventana(df, tabla, col_fecha, cutoff, clave, engine=None):
    """Reemplaza las filas de la ventana movil. Lo anterior a `cutoff` no se toca.

    `clave` son las columnas que identifican una fila: `("id",)` para las
    ordenes de Mercado Libre y las facturas de compra, `("id", "item")` para
    las lineas de venta de SIGMA, donde un comprobante tiene varios renglones.

    `engine` lo pasa el llamador QUE PIDE ALGO ESPECIAL en la conexion. `sigma.py`
    fuerza `client_encoding=utf8`, que viene de cuando esto corria en una PC con
    Windows. Hoy en el runner de GitHub el cliente ya negocia UTF-8 solo y
    probablemente no cambie nada -- pero "probablemente no cambia nada" es
    exactamente el razonamiento que hay que evitar en este archivo, asi que la
    conexion de cada script se respeta tal cual estaba.

    Arriba de todo del archivo esta el por que de cada una de las cuatro cosas
    que hace. Ninguna es opcional: cada una salio de un incidente.
    """
    if engine is None:
        from conexion import crear_engine

        engine = crear_engine()
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")

    if df.empty:
        # NO SE BORRA NADA SI NO HAY CON QUE REEMPLAZAR. Una API que contesta
        # una lista vacia --porque se cayo, o porque cambio un parametro-- no
        # es "no hubo ventas esta semana": borrar la ventana ahi seria creerle
        # a un silencio.
        print(f"  (sin datos nuevos para {tabla} en esta ventana: no se toca nada)")
        return

    df = listas_a_texto(df)

    # LA MISMA FILA PUEDE VENIR DOS VECES EN LA MISMA TANDA, y entonces el
    # INSERT choca contra el indice unico. Pasa de verdad: el 26/08/2026 la
    # orden 2000018121647354 vino repetida en la paginacion de ML y tumbo el
    # orquestador dos corridas seguidas.
    #
    # Deduplicar aca y no en la base es a proposito: el indice unico es la red
    # que descubrio esto y tiene que seguir siendo un error si alguna vez se
    # cuela un duplicado por otra via. Lo que se arregla es la causa.
    antes = len(df)
    if "last_updated" in df.columns:
        df = df.sort_values("last_updated", na_position="first")
    df = df.drop_duplicates(subset=list(clave), keep="last")
    if len(df) < antes:
        print(f"  El origen mando {antes - len(df)} fila(s) repetida(s): se dejo la mas nueva")

    try:
        with engine.begin() as con:
            piso = (cutoff - timedelta(days=1)).isoformat()
            por_ventana = con.exec_driver_sql(
                f'DELETE FROM bronze."{tabla}" '
                f'WHERE "{col_fecha}" >= %(piso)s '
                f'  AND "{col_fecha}"::date >= %(cutoff)s',
                {"piso": piso, "cutoff": cutoff},
            ).rowcount
            por_clave = _borrar_por_clave(con, tabla, clave, df)
            print(f"  Borradas antes de reinsertar: {por_ventana} por ventana + {por_clave} por clave")

            sincronizar_columnas(con, tabla, df)
            df.to_sql(tabla, con, schema="bronze", if_exists="append", index=False)
    except Exception as e:
        # EL UNICO ERROR QUE SE TOLERA: que la tabla no exista, o sea la
        # primera corrida sobre una base limpia, donde no hay nada que
        # duplicar. Todo lo demas explota. Ver el punto 2 de arriba.
        if not es_tabla_inexistente(e):
            raise
        print(f"  bronze.{tabla} no existe todavia -> la crea el to_sql.")
        sincronizar_columnas(engine, tabla, df)
        df.to_sql(tabla, engine, schema="bronze", if_exists="append", index=False)

    print(f"  Guardado (ventana): bronze.{tabla} ({len(df)} filas)")
