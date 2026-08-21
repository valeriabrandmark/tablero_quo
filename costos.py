import argparse
import hashlib
import json
import estado
import os
import glob
import re
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

CARPETA_COSTOS = "costos_mensuales"

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)


def limpiar_pct(valor):
    """Convierte el descuento a numero porcentual (ej 50.0).
       Maneja texto '50,00%' Y numero de Excel (0.5 = 50%, formato porcentaje)."""
    if valor is None or pd.isna(valor):
        return 0.0
    # Si Excel lo guardo como NUMERO (formato porcentaje interno: 0.5 = 50%)
    if isinstance(valor, (int, float)):
        v = float(valor)
        # Si es <= 1, es fraccion (0.5 -> 50). Si es mayor, ya es porcentaje (50 -> 50)
        return v * 100 if abs(v) <= 1 else v
    # Si es texto tipo "50,00%"
    s = str(valor).replace("%", "").strip()
    if s == "" or s.lower() == "nan":
        return 0.0
    # formato argentino: punto miles, coma decimal
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0
    
def limpiar_numero(valor):
    """Convierte costos en cualquier formato a numero:
       - '2.460,85' (texto formato argentino) -> 2460.85
       - 2460.85 (numero puro) -> 2460.85
       - '2460.85' (texto con punto decimal) -> 2460.85"""
    if valor is None or pd.isna(valor):
        return None
    # Si YA es un numero (int/float), lo devolvemos tal cual
    if isinstance(valor, (int, float)):
        return float(valor)
    # Si es texto, detectamos el formato
    s = str(valor).strip()
    if s == "" or s.lower() == "nan":
        return None
    # Caso formato argentino: tiene coma como decimal (ej "2.460,85")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    # Si no tiene coma, asumimos que el punto ya es decimal (ej "2460.85") -> no tocamos
    try:
        return float(s)
    except ValueError:
        return None
    

def leer_hoja_flexible(archivo, hoja, columnas_necesarias, max_filas_prueba=5):
    """Lee una hoja probando distintas filas de encabezado hasta encontrar
       una donde existan las columnas necesarias. Evita fallar si el export
       cambia la cantidad de filas de titulo."""
    for h in range(max_filas_prueba):
        try:
            df = pd.read_excel(archivo, sheet_name=hoja, header=h)
            if all(col in df.columns for col in columnas_necesarias):
                if h != 2:
                    print(f"    (encabezados detectados en fila {h+1})")
                return df
        except Exception:
            continue
    # Si no encontro, lee normal y deja que falle con mensaje claro
    raise ValueError(f"No se encontraron las columnas {columnas_necesarias} "
                     f"en la hoja '{hoja}' de {archivo}")




def huella_de_los_excel():
    """Una firma de los .xlsx: cambia si cambia cualquiera de ellos.

    Se usan tamano + fecha de modificacion y no el contenido entero porque los
    cuatro archivos pesan 11 MB juntos: leerlos para hashearlos costaria casi lo
    mismo que procesarlos, que es lo que se quiere evitar.
    """
    h = hashlib.sha256()
    for archivo in sorted(glob.glob(os.path.join(CARPETA_COSTOS, "*.xlsx"))):
        st = os.stat(archivo)
        h.update(f"{os.path.basename(archivo)}:{st.st_size}:{int(st.st_mtime)}|".encode())
    return h.hexdigest()


def huella_guardada():
    """La huella del Excel de costos de la ultima carga. Vive en Postgres.

    Si no se pudo leer se devuelve None, que significa "recarga igual": perder
    dos minutos recargando es mucho mejor que saltearse un cambio de costos y
    dejar el margen mal calculado.
    """
    try:
        return (estado.leer("costos", {}) or {}).get("huella")
    except Exception as e:
        print(f"  (aviso: no se pudo leer la huella: {str(e)[:60]}) -> recarga")
        return None


def guardar_huella(huella):
    estado.guardar("costos", {"huella": huella})


def meses_disponibles():
    """Los meses que hay en la carpeta, por nombre de archivo."""
    return sorted(
        os.path.splitext(os.path.basename(a))[0]
        for a in glob.glob(os.path.join(CARPETA_COSTOS, "*.xlsx"))
    )


def cargar_costos(mes=None):
    """Carga los costos de todos los meses, o de uno solo si se pasa `mes`.

    Con `mes` NO se reescribe la tabla entera: se borra unicamente ese mes y se
    vuelve a insertar. Reescribir todo con un solo mes cargado se llevaria
    puestos los demas, que es justo lo que uno NO quiere cuando corrige el
    Excel de un mes suelto.
    """
    print("=== Cargando costos historicos con ofertas ===")

    if mes:
        archivo = os.path.join(CARPETA_COSTOS, f"{mes}.xlsx")
        if not os.path.exists(archivo):
            print(f"  No existe {archivo}")
            print(f"  Meses disponibles: {', '.join(meses_disponibles()) or '(ninguno)'}")
            return
        archivos = [archivo]
        print(f"  Solo el mes {mes} (los demas quedan como estan)")
    else:
        archivos = glob.glob(os.path.join(CARPETA_COSTOS, "*.xlsx"))

    if not archivos:
        print(f"  No hay archivos .xlsx en {CARPETA_COSTOS}/")
        return

    todos = []
    for archivo in archivos:
        nombre = os.path.splitext(os.path.basename(archivo))[0]   # ej "2026-06"
        print(f"\n  === Mes comercial: {nombre} ===")

        # --- Pestaña COSTOS: encabezados en fila 3 (header=2) ---
        # Columna B = Codigo, Columna AQ = Costo Teorico
        dfc = leer_hoja_flexible(archivo, "Costos", ["Codigo", "Costo Teorico"])
        costos = dfc[["Codigo", "Costo Teorico"]].copy()
        costos.columns = ["sku", "costo_teorico"]
        costos["sku"] = costos["sku"].astype(str).str.strip()
        # Sigma a veces trae el codigo como numero (1.0) -> limpiamos el .0
        costos["sku"] = costos["sku"].str.replace(r"\.0$", "", regex=True)
        costos["costo_teorico"] = costos["costo_teorico"].apply(limpiar_numero)
        costos = costos.dropna(subset=["sku"])
        costos = costos[(costos["sku"] != "") & (costos["sku"].str.lower() != "nan")]
        print(f"    Costos: {len(costos)} SKUs")

        # --- Pestaña OFERTAS: encabezados en fila 1 (header=0) ---
        # Columna D = SKU, Columna J = DESCUENTO TOTAL PROVEEDOR
        dfo = leer_hoja_flexible(archivo, "Ofertas", ["SKU", "DESCUENTO TOTAL PROVEEDOR"])
        ofertas = dfo[["SKU", "DESCUENTO TOTAL PROVEEDOR"]].copy()
        ofertas.columns = ["sku", "oferta_pct"]
        ofertas["sku"] = ofertas["sku"].astype(str).str.strip()
        ofertas["sku"] = ofertas["sku"].str.replace(r"\.0$", "", regex=True)
        ofertas["oferta_pct"] = ofertas["oferta_pct"].apply(limpiar_pct)
        ofertas = ofertas.dropna(subset=["sku"])
        ofertas = ofertas[(ofertas["sku"] != "") & (ofertas["sku"].str.lower() != "nan")]
        ofertas = ofertas.drop_duplicates(subset=["sku"], keep="last")
        print(f"    Ofertas: {len(ofertas)} SKUs (con o sin descuento)")

        # --- Combinar: costo_real = costo_teorico * (1 - oferta%/100) ---
        costos = costos.merge(ofertas, on="sku", how="left")
        costos["oferta_pct"] = costos["oferta_pct"].fillna(0)
        costos["costo_real"] = costos["costo_teorico"] * (1 - costos["oferta_pct"] / 100)
        costos["mes_comercial"] = nombre
        todos.append(costos)

    if not todos:
        print("\n  No se cargo nada.")
        return

    final = pd.concat(todos, ignore_index=True)
    final = final.drop_duplicates(subset=["sku", "mes_comercial"], keep="last")
    final = final[["sku", "mes_comercial", "costo_teorico", "oferta_pct", "costo_real"]]

    if mes:
        # Borrar + agregar, para no tocar los otros meses. Si la tabla todavia
        # no existe, el borrado no aplica y el append la crea.
        with engine.begin() as con:
            existe = con.exec_driver_sql("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'bronze' AND table_name = 'costos_historicos'
                )
            """).scalar()
            if existe:
                borradas = con.exec_driver_sql(
                    "DELETE FROM bronze.costos_historicos WHERE mes_comercial = %(mes)s",
                    {"mes": mes},
                ).rowcount
                print(f"\n  Filas viejas de {mes} borradas: {borradas}")
            # Dentro del `with`: borrar y reinsertar tienen que ser una sola
            # transaccion, o entre las dos ese mes queda sin costos y modelo.py
            # -- si corre justo ahi -- calcula margenes sin costo.
            final.to_sql("costos_historicos", con, schema="bronze",
                         if_exists="append", index=False)
    else:
        # DELETE + APPEND EN UNA SOLA TRANSACCION, y no `if_exists="replace"`:
        # replace hace DROP, y el DROP falla si alguien crea una vista encima de
        # la tabla. (Ver tiendanube.py, que estuvo dos meses roto por esto.)
        #
        # Y las dos operaciones juntas para que la tabla no quede vacia en el
        # medio: modelo.py lee los costos de aca, y si corre justo en ese hueco
        # arma gold.fact_ventas entero sin margenes.
        with engine.begin() as con:
            existe = con.exec_driver_sql("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'bronze' AND table_name = 'costos_historicos'
                )
            """).scalar()
            if existe:
                con.exec_driver_sql("DELETE FROM bronze.costos_historicos;")
            final.to_sql("costos_historicos", con, schema="bronze",
                         if_exists="append", index=False)

    print(f"\n  Guardado: bronze.costos_historicos ({len(final)} filas de esta corrida)")
    print(f"  Meses cargados ahora: {sorted(final['mes_comercial'].unique())}")

    # Estado de la tabla entera, no solo de lo que se acaba de escribir: con
    # --mes es el unico numero que dice si los otros meses siguen ahi.
    resumen = pd.read_sql(
        "SELECT mes_comercial, count(*) AS skus FROM bronze.costos_historicos "
        "GROUP BY 1 ORDER BY 1", engine)
    print("\n  Tabla completa:")
    print(resumen.to_string(index=False))
    # Muestra de control
    print("\n  Ejemplo (primeras 5 con oferta > 0):")
    print(final[final['oferta_pct'] > 0].head(5).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(
        description="Carga los costos de costos_mensuales/*.xlsx a bronze.costos_historicos.",
        epilog="Ejemplos:\n"
               "  python costos.py            todos los meses\n"
               "  python costos.py 2026-08    solo agosto\n"
               "  python costos.py --listar   que meses hay en la carpeta",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("mes", nargs="?",
                        help="Mes comercial AAAA-MM. Sin esto carga todos.")
    parser.add_argument("--listar", action="store_true",
                        help="Muestra los meses que hay en la carpeta y sale")
    parser.add_argument("--si-cambio", action="store_true",
                        help="No hace nada si ningun .xlsx cambio desde la ultima vez")
    args = parser.parse_args()

    if args.listar:
        disponibles = meses_disponibles()
        print("Meses en " + CARPETA_COSTOS + "/: " + (", ".join(disponibles) or "(ninguno)"))
        return

    # Se valida el formato antes de tocar nada: un mes mal escrito no encuentra
    # el archivo y sin este chequeo el mensaje seria "no existe", que hace
    # pensar que falta el Excel cuando lo que esta mal es lo que se tipeo.
    if args.mes and not re.fullmatch(r"\d{4}-\d{2}", args.mes):
        parser.error(f"'{args.mes}' no tiene el formato AAAA-MM (ej: 2026-08)")

    # Los Excel viven en el disco y solo cambian cuando alguien los edita, asi
    # que reprocesarlos en cada corrida del orquestador es trabajo al pedo: son
    # 31.446 filas reescritas cada dos horas para que quede exactamente lo
    # mismo. Con --si-cambio el orquestador lo llama siempre y el script decide.
    if args.si_cambio:
        huella = huella_de_los_excel()
        if huella == huella_guardada():
            print("Ningun Excel de costos cambio desde la ultima carga. No hay nada que hacer.")
            return
        print("Cambio algun Excel de costos: se recarga.")

    cargar_costos(args.mes)

    # La huella se guarda DESPUES de cargar bien: si la carga falla, la proxima
    # corrida tiene que volver a intentarlo y no darlo por hecho.
    if args.si_cambio:
        guardar_huella(huella_de_los_excel())

    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()