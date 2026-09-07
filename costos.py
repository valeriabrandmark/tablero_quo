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
from conexion import crear_engine

load_dotenv()

CARPETA_COSTOS = "costos_mensuales"

# Columna K de la hoja "Ofertas": el descuento que ponemos nosotros, aparte del
# que da el proveedor (columna J, DESCUENTO TOTAL PROVEEDOR).
COL_DESC_PROPIO = "DESC PROPIO"

# Sube de numero cada vez que cambia lo que esta funcion escribe (una columna
# nueva, otra cuenta). Entra en la huella para que --si-cambio recargue una vez
# aunque ningun Excel se haya tocado: sin esto, la columna agregada hoy queda
# vacia hasta que alguien edite un archivo.
VERSION_ESQUEMA = 2

engine = crear_engine()


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
    

# Cuantas veces tiene que saltar un costo para que sea un error y no un aumento.
#
# 50 y no 5: un aumento de precios fuerte es 30 o 40 %, y un cambio de proveedor
# puede duplicar un costo. Nada de eso llega a 50 veces. Lo que si llega es
# perder la coma decimal: 10.979,019272 se vuelve 10979019272, que son SEIS
# ordenes de magnitud.
SALTO_SOSPECHOSO = 50

# Que proporcion de los articulos tiene que saltar para abortar la carga.
#
# Uno solo puede ser un dato mal tipeado y no justifica frenar el mes entero;
# que salte la quinta parte del catalogo no es un error de carga de nadie, es el
# archivo que vino mal.
PROPORCION_PARA_ABORTAR = 0.20


def _sin_separador_decimal(viejo, nuevo):
    """True si `nuevo` es `viejo` con la coma decimal borrada.

    10.979,019272 -> 1097901927 no es un costo nuevo: son los mismos digitos sin
    el separador. Se prueba multiplicando por potencias de diez porque la
    cantidad de decimales cambia fila por fila, y por eso el factor no es
    siempre el mismo -- lo que hace que "esta todo multiplicado por mil" no
    alcance como explicacion.
    """
    if not viejo or viejo <= 0 or not nuevo or nuevo <= 0:
        return False
    for k in range(1, 8):
        # Tolerancia del 1 %: el costo del mes nuevo casi nunca es identico al
        # del anterior, pero un aumento normal no mueve el orden de magnitud.
        if abs(nuevo - viejo * (10 ** k)) <= viejo * (10 ** k) * 0.01:
            return True
    return False


def _mes_anterior(mes):
    """'2026-09' -> '2026-08'."""
    anio, m = (int(x) for x in mes.split("-"))
    total = anio * 12 + (m - 1) - 1
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def costos_previos(mes, ya_procesados, engine):
    """Los costos del mes anterior: de esta misma corrida o de la base.

    Primero mira lo que ya se leyo en esta corrida --cuando se cargan todos los
    archivos de una, el mes anterior todavia no esta en la base-- y si no,
    consulta lo que hay guardado.
    """
    anterior = _mes_anterior(mes)

    for df in ya_procesados:
        if not df.empty and df["mes_comercial"].iloc[0] == anterior:
            return dict(zip(df["sku"], df["costo_teorico"]))

    try:
        previo = pd.read_sql(
            "select sku, costo_teorico from bronze.costos_historicos "
            "where mes_comercial = %(m)s",
            engine, params={"m": anterior},
        )
    except Exception:
        # Primera carga, o la tabla todavia no existe: no hay contra que
        # comparar y eso no es motivo para frenar nada.
        return {}
    return dict(zip(previo["sku"], previo["costo_teorico"]))


# La columna de costos casi siempre trae decimales: son precios de lista con
# cuatro a seis decimales. En julio y agosto de 2026 los tenia el 95,1 %; en el
# archivo roto de septiembre, el 33 % -- justo los que vinieron como texto con
# coma, que son los unicos que se leyeron bien.
#
# ESTO AVISA, NO CORTA. Es una pista de POR QUE se rompio, y por si sola no
# alcanza para frenar un mes: un proveedor que redondea sus precios haria bajar
# esta proporcion sin que nada este mal. El que corta es el salto de importes,
# que se mide contra la magnitud real.
CAIDA_DECIMALES_SOSPECHOSA = 0.5   # la mitad de lo que traia el mes anterior


def _proporcion_con_decimales(costos):
    positivos = [c for c in costos if c and c > 0]
    if not positivos:
        return None
    return sum(1 for c in positivos if c % 1 != 0) / len(positivos)


def revisar_decimales(nuevos, anteriores):
    """Avisa si la columna de costos vino sin decimales y antes los tenia.

    Cuando el Excel se exporta con otra configuracion regional, "2.508,975" se
    guarda como 2508975: los mismos digitos, sin la coma. Todos los costos del
    mes quedan enteros, que es algo que no pasa nunca con precios de lista.
    """
    antes = _proporcion_con_decimales(anteriores.values())
    ahora = _proporcion_con_decimales(nuevos.values())
    if antes is None or ahora is None:
        return {"antes": antes, "ahora": ahora, "sospechoso": False}
    return {
        "antes": antes,
        "ahora": ahora,
        "sospechoso": ahora < antes * CAIDA_DECIMALES_SOSPECHOSA,
    }


def revisar_saltos(nuevos, anteriores):
    """Compara los costos nuevos contra los del mes anterior.

    `nuevos` y `anteriores` son dicts {sku: costo_teorico}. Devuelve un informe
    con cuantos saltaron y algunos ejemplos, sin decidir nada: quien decide es
    el que llama.

    POR QUE EXISTE. El 07/09/2026 entro un archivo de costos con la coma decimal
    borrada en el 63 % de los articulos. Nadie lo noto en la carga; se noto en el
    tablero, con el margen del dia en -$ 42.421 millones. El cargador no puede
    saber cuanto vale un articulo, pero SI puede saber que un costo no se
    multiplica por cien de un mes al otro.
    """
    comparables = 0
    saltos = []
    for sku, nuevo in nuevos.items():
        viejo = anteriores.get(sku)
        if not viejo or viejo <= 0 or not nuevo or nuevo <= 0:
            continue
        comparables += 1
        if nuevo >= viejo * SALTO_SOSPECHOSO:
            saltos.append((sku, viejo, nuevo, _sin_separador_decimal(viejo, nuevo)))

    proporcion = len(saltos) / comparables if comparables else 0.0
    return {
        "comparables": comparables,
        "saltos": len(saltos),
        "proporcion": proporcion,
        "sin_separador": sum(1 for s in saltos if s[3]),
        "ejemplos": saltos[:5],
        "abortar": comparables > 0 and proporcion >= PROPORCION_PARA_ABORTAR,
    }


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

    Ademas entra VERSION_ESQUEMA, porque un cambio en este script tambien
    cambia lo que hay que escribir aunque los Excel esten iguales.
    """
    h = hashlib.sha256()
    h.update(f"v{VERSION_ESQUEMA}|".encode())
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


def asegurar_columnas(con):
    """La tabla se escribe con to_sql(if_exists="append"), que no crea columnas
       nuevas: si el DataFrame trae una que la tabla no tiene, el INSERT falla.
       Por eso cada columna agregada despues de la creacion original va aca."""
    con.exec_driver_sql(
        "ALTER TABLE bronze.costos_historicos "
        "ADD COLUMN IF NOT EXISTS desc_propio_pct double precision DEFAULT 0"
    )


def cargar_costos(mes=None, revisar_solo=False):
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
        # Columna D = SKU, Columna J = DESCUENTO TOTAL PROVEEDOR, K = DESC PROPIO
        dfo = leer_hoja_flexible(archivo, "Ofertas", ["SKU", "DESCUENTO TOTAL PROVEEDOR"])
        ofertas = dfo[["SKU", "DESCUENTO TOTAL PROVEEDOR"]].copy()
        ofertas.columns = ["sku", "oferta_pct"]
        # "DESC PROPIO" no va en las columnas obligatorias de leer_hoja_flexible:
        # si un mes viejo no la trae, mejor cargar ese mes con el descuento
        # propio en cero que no cargarlo.
        if COL_DESC_PROPIO in dfo.columns:
            ofertas["desc_propio_pct"] = dfo[COL_DESC_PROPIO]
        else:
            print(f"    OJO: la hoja Ofertas no tiene '{COL_DESC_PROPIO}', queda en 0")
            ofertas["desc_propio_pct"] = 0.0
        ofertas["sku"] = ofertas["sku"].astype(str).str.strip()
        ofertas["sku"] = ofertas["sku"].str.replace(r"\.0$", "", regex=True)
        ofertas["oferta_pct"] = ofertas["oferta_pct"].apply(limpiar_pct)
        ofertas["desc_propio_pct"] = ofertas["desc_propio_pct"].apply(limpiar_pct)
        ofertas = ofertas.dropna(subset=["sku"])
        ofertas = ofertas[(ofertas["sku"] != "") & (ofertas["sku"].str.lower() != "nan")]
        ofertas = ofertas.drop_duplicates(subset=["sku"], keep="last")
        con_propio = int((ofertas["desc_propio_pct"] > 0).sum())
        print(f"    Ofertas: {len(ofertas)} SKUs (con o sin descuento), "
              f"{con_propio} con descuento propio")

        # --- Combinar: costo_real = costo_teorico * (1 - oferta%/100) ---
        # El descuento propio NO entra en costo_real: es lo que resignamos
        # nosotros sobre el precio de venta, no una rebaja que da el proveedor.
        # Viaja a la tabla para poder mostrarlo, nada mas.
        costos = costos.merge(ofertas, on="sku", how="left")
        costos["oferta_pct"] = costos["oferta_pct"].fillna(0)
        costos["desc_propio_pct"] = costos["desc_propio_pct"].fillna(0)
        costos["costo_real"] = costos["costo_teorico"] * (1 - costos["oferta_pct"] / 100)
        costos["mes_comercial"] = nombre

        # --- El archivo, contra el mes anterior -------------------------
        #
        # SE CHEQUEA ANTES DE ESCRIBIR NADA. Todo el mes se guarda en una sola
        # transaccion al final, asi que cortar aca deja la base como estaba: con
        # los costos viejos, que son viejos pero no absurdos.
        anteriores = costos_previos(nombre, todos, engine)
        if anteriores:
            nuevos = dict(zip(costos["sku"], costos["costo_teorico"]))
            informe = revisar_saltos(nuevos, anteriores)

            decimales = revisar_decimales(nuevos, anteriores)
            if decimales["sospechoso"]:
                print(f"    OJO: solo el {decimales['ahora']:.0%} de los costos tiene "
                      f"decimales, contra {decimales['antes']:.0%} el mes anterior")
            if informe["saltos"]:
                print(f"    OJO: {informe['saltos']} de {informe['comparables']} costos "
                      f"saltaron {SALTO_SOSPECHOSO}x o mas "
                      f"({informe['proporcion']:.0%})")
                for sku, viejo_v, nuevo_v, sin_sep in informe["ejemplos"]:
                    marca = "  <- parece la coma decimal borrada" if sin_sep else ""
                    print(f"      {sku:<12} {viejo_v:>14,.4f} -> {nuevo_v:>16,.2f}{marca}")
            if informe["abortar"] and not revisar_solo:
                raise RuntimeError(
                    f"El archivo de {nombre} tiene {informe['saltos']} costos "
                    f"({informe['proporcion']:.0%}) al menos {SALTO_SOSPECHOSO} veces "
                    f"mas altos que en el mes anterior, y {informe['sin_separador']} "
                    "son exactamente los mismos digitos sin la coma decimal.\n"
                    f"  Y solo el {decimales['ahora']:.0%} de los costos tiene decimales, "
                    f"contra {decimales['antes']:.0%} el mes anterior.\n"
                    "  No se cargo NADA: la base queda con los costos de antes.\n"
                    "  Casi siempre es el Excel exportado con otra configuracion "
                    "regional: la columna 'Costo Teorico' tiene que venir como "
                    "numero, o como texto con coma decimal ('2.460,85').\n"
                    "  Para revisarlo sin cargar: python costos.py " + nombre + " --revisar"
                )

        todos.append(costos)

    if not todos:
        print("\n  No se cargo nada.")
        return

    if revisar_solo:
        print("\n  (modo revisar: no se guardo nada)")
        return

    final = pd.concat(todos, ignore_index=True)
    final = final.drop_duplicates(subset=["sku", "mes_comercial"], keep="last")
    final = final[["sku", "mes_comercial", "costo_teorico", "oferta_pct",
                   "desc_propio_pct", "costo_real"]]

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
                asegurar_columnas(con)
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
                asegurar_columnas(con)
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
    parser.add_argument("--revisar", action="store_true",
                        help="Lee los Excel y avisa de los costos raros, sin guardar")
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
    if args.si_cambio and not args.revisar:
        huella = huella_de_los_excel()
        if huella == huella_guardada():
            print("Ningun Excel de costos cambio desde la ultima carga. No hay nada que hacer.")
            return
        print("Cambio algun Excel de costos: se recarga.")

    cargar_costos(args.mes, revisar_solo=args.revisar)

    # La huella se guarda DESPUES de cargar bien: si la carga falla, la proxima
    # corrida tiene que volver a intentarlo y no darlo por hecho.
    if args.si_cambio:
        guardar_huella(huella_de_los_excel())

    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()