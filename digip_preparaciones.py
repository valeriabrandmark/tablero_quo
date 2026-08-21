import os
import json
import time
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine

from errores_bd import es_tabla_inexistente

load_dotenv()

BASE = os.getenv("DIGIP_URL_BASE")
API_KEY = os.getenv("DIGIP_API_KEY")
headers = {"X-API-Key": API_KEY}

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
    connect_args={"client_encoding": "utf8"}
)

FECHA_CORTE = date(2026, 5, 6)
WINDOW_DAYS = 7


def extraer_preparaciones():
    hoy = date.today()
    cutoff = max(FECHA_CORTE, hoy - timedelta(days=WINDOW_DAYS))
    print(f"=== Extrayendo preparaciones (ventana: {cutoff.isoformat()} a {hoy.isoformat()}) ===")

    # ANTES: se consultaba TODO bronze.digip_pedidos completo (una llamada a la API
    # por pedido) -> por eso cada corrida tardaba mas que la anterior, para siempre.
    # AHORA: solo los pedidos de la distri (codigo numerico) DENTRO de la ventana movil.
    pedidos = pd.read_sql(
        """
        SELECT codigo FROM bronze.digip_pedidos
        WHERE codigo ~ '^[0-9]+$'
          AND "fecha"::date >= %(cutoff)s
        """,
        engine, params={"cutoff": cutoff}
    )
    codigos = pedidos["codigo"].astype(str).tolist()
    print(f"Pedidos a consultar (dentro de la ventana): {len(codigos)}")

    filas = []
    errores = 0

    for i, cod in enumerate(codigos, 1):
        try:
            resp = requests.get(f"{BASE}Preparaciones/{cod}", headers=headers, timeout=30)
            if resp.status_code != 200:
                errores += 1
                continue
            prep = resp.json()
            prep_id = prep.get("id")

            transporte = prep.get("despachoDescripcion") or prep.get("despachoCodigo")

            contenedores = prep.get("contenedores") or []
            bultos = sum((c.get("cantidadBulto") or 0) for c in contenedores)

            items = prep.get("items") or []
            volumen_total = sum((it.get("volumen") or 0) for it in items)
            peso_gramos = sum((it.get("peso") or 0) for it in items)
            peso_kg = round(peso_gramos / 1000.0, 3)

            tipo = "Bultos"
            for c in contenedores:
                desc = json.dumps(c, ensure_ascii=False).lower()
                if "pallet" in desc or "pale" in desc:
                    tipo = "Pallets"
                    break

            pedidos_en_prep = [p.get("codigo") for p in (prep.get("pedidos") or [])]

            for ped_cod in pedidos_en_prep:
                filas.append({
                    "preparacion_id": prep_id,
                    "pedido_codigo": str(ped_cod),
                    "transporte": transporte,
                    "bultos_preparacion": bultos,
                    "volumen_preparacion": volumen_total,
                    "kg_preparacion": peso_kg,
                    "tipo_preparacion": tipo,
                    "cant_pedidos_en_prep": len(pedidos_en_prep),
                })

        except Exception:
            errores += 1

        if i % 25 == 0:
            print(f"  {i}/{len(codigos)} pedidos consultados...")
        time.sleep(0.1)

    df = pd.DataFrame(filas).drop_duplicates(subset=["preparacion_id", "pedido_codigo"])
    print(f"\nFilas (preparacion-pedido): {len(df)}")
    if len(df) > 0:
        print(f"Preparaciones unicas: {df['preparacion_id'].nunique()}")
    print(f"Errores: {errores}")

    if len(df) > 0:
        consolidadas = df.groupby("preparacion_id")["pedido_codigo"].nunique()
        print(f"Preparaciones consolidadas: {(consolidadas > 1).sum()}")
        print("\nEjemplo de datos (primeras 3 filas):")
        print(df[["preparacion_id", "pedido_codigo", "transporte", "bultos_preparacion",
                  "volumen_preparacion", "kg_preparacion", "tipo_preparacion"]].head(3).to_string())

    # Reemplazo SOLO de las preparaciones de los pedidos que estan dentro de la ventana
    # actual. NOTA: digip_preparaciones no tiene columna de fecha propia -- se identifica
    # que filas pertenecen a la ventana por su pedido_codigo (los mismos que se acaban
    # de volver a consultar), no por fecha directamente.
    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze;")

    # SI NO HAY NADA CON QUE REEMPLAZAR, NO SE BORRA. Y el chequeo va ACA, antes
    # del DELETE, que es donde estaba el problema.
    #
    # Este script se traga los errores de la API: cuando una llamada no devuelve
    # 200 hace `errores += 1; continue`. Con Digip caido, entonces, las ~28
    # llamadas de la ventana fallan, `df` queda vacio... y la version anterior
    # igual borraba los 7 dias, no insertaba nada, imprimia "sin preparaciones
    # nuevas" y terminaba EN VERDE. Una semana de logistica perdida sin que
    # nada avise.
    #
    # Es la misma trampa que dejo a Tienda Nube sin datos desde el 12/06 y la
    # que duplico 2.548 ordenes el 21/08: borrar primero y despues no tener con
    # que reemplazar. Ver errores_bd.py.
    #
    # Quedarse con lo de la corrida anterior es estrictamente mejor: el dato
    # queda viejo una corrida en vez de desaparecer, y la que viene lo arregla.
    if df.empty:
        print("  (sin preparaciones en esta ventana: NO se toca lo que ya estaba)")
        return

    # BORRAR E INSERTAR EN UNA SOLA TRANSACCION, igual que en digip.py. En dos
    # transacciones separadas hay un instante en el que la ventana no existe, y
    # el tablero de Logistica lee esta tabla en vivo.
    try:
        with engine.begin() as con:
            resultado = con.exec_driver_sql(
                "DELETE FROM bronze.digip_preparaciones WHERE pedido_codigo = ANY(%(codigos)s)",
                {"codigos": codigos},
            )
            print(f"  Filas borradas dentro de la ventana (se van a reemplazar): {resultado.rowcount}")
            df.to_sql("digip_preparaciones", con, schema="bronze", if_exists="append", index=False)
        print("\nGuardado (ventana) en bronze.digip_preparaciones")
        return
    except Exception as e:
        # SOLO se tolera que la tabla no exista (primera corrida en base limpia).
        # Cualquier otro error explota: si el borrado no se hizo, insertar igual
        # duplica la ventana entera.
        if not es_tabla_inexistente(e):
            raise
        print("  bronze.digip_preparaciones no existe todavia -> la crea el to_sql.")

    df.to_sql("digip_preparaciones", engine, schema="bronze", if_exists="append", index=False)
    print("\nGuardado (ventana) en bronze.digip_preparaciones")


if __name__ == "__main__":
    extraer_preparaciones()
    print("\n=== LISTO ===")