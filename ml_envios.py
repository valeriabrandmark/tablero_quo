import os
import json
import time
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine
from mercadolibre import renovar_access_token

load_dotenv()

# `statement_timeout` propio, mas largo que el de la base.
#
# Supabase corta cualquier consulta a los 2 minutos, y esta bien que lo haga:
# es la defensa contra una consulta suelta que cuelgue el tablero. Pero esto es
# un proceso de fondo que corre solo, no una pagina esperando a un usuario, y
# tenerlo cortado por el limite pensado para paginas no protege nada -- el
# techo real de este paso lo pone el orquestador, que lo mata a los 45 minutos.
ENGINE_OPCIONES = {"options": "-c statement_timeout=300000"}   # 5 min

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
    connect_args=ENGINE_OPCIONES,
)

MI_USER_ID = int(os.getenv("ML_USER_ID"))
FECHA_CORTE = date(2026, 5, 6)


def to_date(valor):
    ts = pd.to_datetime(valor, errors="coerce", utc=True)
    return None if pd.isna(ts) else ts.date()


def extraer_envios():
    print("=== Extrayendo costos de envio de ML (retomando) ===")

    # 1) Los envios que FALTAN, resueltos en SQL y no en pandas.
    #
    # Antes esta consulta se traia las 38.000 ordenes pagadas desde mayo, con
    # sus tres columnas, y recien despues filtraba en pandas por fecha y por
    # cuales ya estaban bajadas. Traia 38.000 filas para quedarse con veinte.
    #
    # Eso reventaba contra el `statement_timeout`: `bronze.ml_ventas` pesa
    # 108 MB y no tiene indices, asi que la consulta es un recorrido completo de
    # la tabla. Con el cache frio -- justo lo que pasa despues de que
    # mercadolibre.py la reescribe entera -- no entra en dos minutos y la corrida
    # se cae. Paso el 21/08.
    #
    # Ahora el filtro va en la base: la fecha, y el `NOT EXISTS` contra lo que
    # ya esta bajado. La consulta devuelve solo los que hay que pedirle a la API.
    #
    # El piso de fecha se compara como TEXTO, que es como esta guardada
    # `date_created` (ISO-8601, que ordena igual como texto que como fecha), y va
    # un dia antes del corte a proposito: el filtro fino sigue siendo el de
    # pandas, que resuelve bien el huso. Sin ese dia de más, una orden del 5 de
    # mayo a las 23 hs -- que en UTC ya es 6 de mayo -- se perderia.
    piso = (FECHA_CORTE - timedelta(days=1)).isoformat()
    ship = 'v."shipping.id"::bigint::text'

    # `bronze.ml_envios` no existe en una instalacion nueva: la crea el primer
    # to_sql. Si todavia no esta, no hay nada bajado que saltear.
    with engine.connect() as con:
        hay_envios = con.exec_driver_sql(
            "select to_regclass('bronze.ml_envios') is not null").scalar()

    filtro_ya_bajados = f"""
          AND NOT EXISTS (SELECT 1 FROM bronze.ml_envios e
                           WHERE e.shipping_id = {ship})""" if hay_envios else ""

    # El parametro va como %(piso)s y NO como :piso.
    #
    # `pd.read_sql` con un Engine y el SQL como STRING pelado despacha por
    # `exec_driver_sql`, que le pasa la consulta derecho a psycopg2 -- y psycopg2
    # usa `pyformat`. Con `:piso` la base recibe los dos puntos literales y tira
    # "syntax error at or near :". Es el mismo estilo que usa mercadolibre.py.
    ordenes = pd.read_sql(f"""
        SELECT DISTINCT ON ({ship})
               v.id, {ship} AS shipping_id_str, v.date_created
        FROM bronze.ml_ventas v
        WHERE v.status = 'paid'
          AND v."shipping.id" IS NOT NULL
          AND v.date_created >= %(piso)s{filtro_ya_bajados}
        ORDER BY {ship}
    """, engine, params={"piso": piso})

    ordenes["fecha"] = ordenes["date_created"].apply(to_date)
    faltan = ordenes[ordenes["fecha"].notna() & (ordenes["fecha"] >= FECHA_CORTE)]
    print(f"Faltan {len(faltan)} envios por pedirle a la API.")

    if len(faltan) == 0:
        print("Ya estan todos! Nada que hacer.")
        return

    token = renovar_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    errores = 0

    # Se guarda AGREGANDO al final de la tabla, no reescribiendola.
    #
    # Antes cada checkpoint hacia to_sql(..., if_exists="replace") sobre la lista
    # entera: eso borra la tabla y la vuelve a crear. Con 17.000 envios por pedir
    # son ~170 reescrituras de una tabla que crece hasta 34.000 filas, y sobre
    # todo: si el proceso se cortaba justo durante una de esas reescrituras, la
    # tabla quedaba vacia y la corrida siguiente tenia que volver a pedirle los
    # 34.000 a la API desde cero. En una tanda de dos horas eso es la diferencia
    # entre poder cortarla tranquilo y no poder.
    #
    # Agregando, lo que ya se bajo queda guardado pase lo que pase, y cortar la
    # corrida (Ctrl+C, se corta la luz, lo que sea) no cuesta mas que los ultimos
    # envios del buffer.
    nuevos = []

    def guardar_pendientes():
        if not nuevos:
            return
        pd.DataFrame(nuevos).to_sql("ml_envios", engine, schema="bronze",
                                    if_exists="append", index=False)
        nuevos.clear()

    for i, (_, r) in enumerate(faltan.iterrows(), 1):
        ship_id = r["shipping_id_str"]
        try:
            url = f"https://api.mercadolibre.com/shipments/{ship_id}/costs"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 401:
                token = renovar_access_token()
                headers = {"Authorization": f"Bearer {token}"}
                resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                costo_envio = 0
                for s in data.get("senders", []):
                    if s.get("user_id") == MI_USER_ID:
                        costo_envio = s.get("cost", 0) or 0
                        break
                nuevos.append({
                    "shipping_id": ship_id,
                    "order_id": str(r["id"]),
                    "costo_envio": costo_envio,
                })
            else:
                errores += 1
        except Exception:
            errores += 1

        if i % 100 == 0:
            print(f"  {i}/{len(faltan)} pedidos a la API ({errores} con error)...")
            guardar_pendientes()
        time.sleep(0.1)

    guardar_pendientes()

    # El resumen se lee de la tabla y no de una lista en memoria: asi cuenta
    # tambien lo que quedo de corridas anteriores, que es el numero que importa.
    final = pd.read_sql("SELECT costo_envio FROM bronze.ml_envios", engine)
    print(f"\nGuardado: bronze.ml_envios ({len(final)} envios totales)")
    print(f"Errores en esta corrida: {errores} (se reintentan en la proxima)")
    print(f"Envios con costo a tu cargo: {(final['costo_envio'] > 0).sum()}")
    print(f"Costo total de envios: ${final['costo_envio'].sum():,.2f}")


if __name__ == "__main__":
    extraer_envios()
    print("\n=== LISTO ===")