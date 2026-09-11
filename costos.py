"""Carga los Excel de costos a bronze.costos_historicos.

============================================================================
 COMO SE LLAMA EL ARCHIVO, QUE ES LO UNICO QUE HAY QUE SABER
============================================================================

    2026-09.xlsx       la lista del mes. Rige desde que ARRANCA el mes
                       comercial: el 6, o el dia que corresponda si ese mes
                       tuvo cierre movido (2026-09 arranca el 7).

    2026-09-18.xlsx    una lista que llego a mitad de mes. Rige DESDE EL 18,
                       inclusive. Las ventas del 7 al 17 se siguen costeando
                       con la lista anterior; las del 18 en adelante, con esta.

Los dos archivos conviven. Cada uno es el catalogo COMPLETO --los 8.243
articulos-- porque asi los exporta Sigma, no hace falta recortar nada: de cada
uno se toma lo que rige desde su fecha.

POR QUE. Hasta el 11/09/2026 el costo valia el mes comercial entero, asi que
cuando un proveedor mandaba lista nueva el dia 20 habia que elegir entre dejar
el costo viejo hasta el 5 o pisarlo y recostear ventas que ya se habian hecho
al precio anterior. Ninguna de las dos era cierta.

Y sirve tambien para CORREGIR: si un costo entro mal, se vuelve a cargar el
archivo de esa vigencia y modelo.py recalcula solo los dias que dependian de
ella.
"""

import argparse
import hashlib
import json
import estado
import os
import glob
import re
import pandas as pd
from datetime import date
from dotenv import load_dotenv
from sqlalchemy import create_engine
from calendario import inicio_del_mes_comercial, mes_comercial
from conexion import crear_engine

load_dotenv()

CARPETA_COSTOS = "costos_mensuales"

# Los dos nombres de archivo validos. Ver el encabezado del modulo.
PATRON_MES = re.compile(r"\d{4}-\d{2}")
PATRON_DIA = re.compile(r"\d{4}-\d{2}-\d{2}")

# Columna K de la hoja "Ofertas": el descuento que ponemos nosotros, aparte del
# que da el proveedor (columna J, DESCUENTO TOTAL PROVEEDOR).
COL_DESC_PROPIO = "DESC PROPIO"

# Sube de numero cada vez que cambia lo que esta funcion escribe (una columna
# nueva, otra cuenta). Entra en la huella para que --si-cambio recargue una vez
# aunque ningun Excel se haya tocado: sin esto, la columna agregada hoy queda
# vacia hasta que alguien edite un archivo.
VERSION_ESQUEMA = 3

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


def _como_fecha(valor):
    """pandas devuelve las fechas como Timestamp; aca se trabaja con date."""
    return valor.date() if hasattr(valor, "date") else valor


def mes_y_vigencia(nombre):
    """Del nombre del archivo al (mes comercial, dia desde el que rige).

    '2026-09'     -> ('2026-09', date(2026, 9, 7))   el arranque del mes
    '2026-09-18'  -> ('2026-09', date(2026, 9, 18))  una lista de media de mes

    El arranque del mes NO es siempre el 6: sale de calendario.py, que es el
    mismo lugar del que sale el mes comercial de cada venta. Escribirlo aca de
    nuevo serian dos versiones de la misma regla.
    """
    if PATRON_MES.fullmatch(nombre):
        return nombre, inicio_del_mes_comercial(nombre)
    if PATRON_DIA.fullmatch(nombre):
        dia = date.fromisoformat(nombre)
        return mes_comercial(dia), dia
    raise ValueError(
        f"'{nombre}' no es AAAA-MM ni AAAA-MM-DD (ej: 2026-09 o 2026-09-18)"
    )


def costos_previos(vigencia, ya_procesados, engine):
    """Los costos que regian JUSTO ANTES de esta vigencia.

    Es contra lo que se mide el salto de importes. Antes se comparaba contra el
    mes anterior; ahora contra el TRAMO anterior, que para la lista de un mes es
    el ultimo del mes pasado --lo mismo de siempre-- y para una lista de media
    de mes es la que esta reemplazando, que es justo la comparacion que importa.

    Primero mira lo que ya se leyo en esta corrida --cuando se cargan todos los
    archivos de una, el tramo anterior todavia no esta en la base-- y si en la
    base hay uno mas nuevo que ese, gana el de la base.
    """
    mejor_vig, mejor = None, {}
    for df in ya_procesados:
        if df.empty:
            continue
        v = _como_fecha(df["vigente_desde"].iloc[0])
        if v < vigencia and (mejor_vig is None or v > mejor_vig):
            mejor_vig, mejor = v, dict(zip(df["sku"], df["costo_teorico"]))

    try:
        previo = pd.read_sql(
            "select sku, costo_teorico, vigente_desde from bronze.costos_historicos "
            "where vigente_desde = (select max(vigente_desde) "
            "                         from bronze.costos_historicos "
            "                        where vigente_desde < %(v)s)",
            engine, params={"v": vigencia},
        )
    except Exception:
        # Primera carga, o la tabla todavia no existe: no hay contra que
        # comparar y eso no es motivo para frenar nada.
        return mejor

    if previo.empty:
        return mejor
    v_base = _como_fecha(previo["vigente_desde"].iloc[0])
    if mejor_vig is not None and mejor_vig >= v_base:
        return mejor
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




# De a un mega por vez: son cinco archivos de hasta 4 MB y no hay ninguna razon
# para tenerlos enteros en memoria a la vez.
TROZO_HUELLA = 1024 * 1024


def huella_de_los_excel():
    """Una firma de los .xlsx: cambia si cambia cualquiera de ellos.

    ES EL CONTENIDO, NO LA FECHA DE MODIFICACION.

    Antes entraban tamano + mtime, con el argumento de que leer los archivos
    para hashearlos costaria casi lo mismo que procesarlos. No es cierto:
    hashear los 18 MB son 0,06 s con los archivos en cache y ~1 s leyendolos del
    disco, contra los ~50 s que tarda pandas en parsearlos.

    Y donde mas hacia falta no funcionaba. GitHub Actions hace checkout limpio
    en cada corrida, asi que los cinco .xlsx aparecen con la fecha de ESE
    momento: la huella daba distinta siempre y el paso que existe para no
    recargar de gusto recargaba los cinco meses cada dos horas. Se vio en la
    corrida 418 del 11/09/2026.

    EL NOMBRE TAMBIEN ENTRA, y desde las vigencias no es un detalle: renombrar
    2026-09.xlsx a 2026-09-11.xlsx cambia desde cuando rige esa lista aunque el
    contenido sea identico. El tamano va al lado del nombre para que el hash no
    dependa de donde separa un archivo del siguiente.

    Y entra VERSION_ESQUEMA, porque un cambio en este script tambien cambia lo
    que hay que escribir aunque los Excel esten iguales.
    """
    h = hashlib.sha256()
    h.update(f"v{VERSION_ESQUEMA}|".encode())
    for archivo in sorted(glob.glob(os.path.join(CARPETA_COSTOS, "*.xlsx"))):
        h.update(f"{os.path.basename(archivo)}:{os.path.getsize(archivo)}|".encode())
        with open(archivo, "rb") as f:
            for trozo in iter(lambda: f.read(TROZO_HUELLA), b""):
                h.update(trozo)
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


def archivos_disponibles():
    """Los nombres de archivo que hay en la carpeta, sin la extension."""
    return sorted(
        os.path.splitext(os.path.basename(a))[0]
        for a in glob.glob(os.path.join(CARPETA_COSTOS, "*.xlsx"))
    )


def archivos_a_cargar(objetivo):
    """Que archivos entran, y con que mes y vigencia cada uno.

    Devuelve una lista de (nombre, mes_comercial, vigente_desde) ordenada por
    vigencia, que es el orden en el que hay que leerlos: cada uno se compara
    contra el anterior.

        None           todos los que haya en la carpeta
        '2026-09'      la lista del mes Y todas las de media de mes que caigan
                       adentro de ese mes comercial
        '2026-09-18'   solo esa
    """
    catalogados = []
    for nombre in archivos_disponibles():
        try:
            mes, vigencia = mes_y_vigencia(nombre)
        except ValueError as e:
            print(f"  (se ignora {nombre}.xlsx: {e})")
            continue
        catalogados.append((nombre, mes, vigencia))

    if objetivo is None:
        elegidos = catalogados
    elif PATRON_DIA.fullmatch(objetivo):
        elegidos = [c for c in catalogados if c[0] == objetivo]
    else:
        elegidos = [c for c in catalogados if c[1] == objetivo]

    return sorted(elegidos, key=lambda c: c[2])


def asegurar_columnas(con):
    """La tabla se escribe con to_sql(if_exists="append"), que no crea columnas
       nuevas: si el DataFrame trae una que la tabla no tiene, el INSERT falla.
       Por eso cada columna agregada despues de la creacion original va aca."""
    con.exec_driver_sql(
        "ALTER TABLE bronze.costos_historicos "
        "ADD COLUMN IF NOT EXISTS desc_propio_pct double precision DEFAULT 0"
    )
    # Sin DEFAULT a proposito: una fila sin vigencia no se sabe desde cuando
    # rige, y adivinarla es peor que no cargarla. La columna se creo con el
    # backfill de costos_vigente_desde.sql, que le puso a cada fila vieja el
    # arranque de su propio mes comercial.
    con.exec_driver_sql(
        "ALTER TABLE bronze.costos_historicos "
        "ADD COLUMN IF NOT EXISTS vigente_desde date"
    )


# La anotacion que deja este script y levanta modelo.py. Los dos nombres tienen
# que decir lo mismo: alla se llama CLAVE_COSTOS_PENDIENTES.
CLAVE_RECONSTRUIR = "costos_reconstruir_desde"


def _numero(valor):
    """El costo como float comparable: los NaN y los None se vuelven None.

    NaN nunca es igual a NaN, asi que sin esto un articulo sin costo figuraria
    como "cambio" en cada carga y mandaria a recalcular gold para nada.
    """
    if valor is None:
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return None if v != v else round(v, 6)


def vigencia_mas_vieja_que_cambia(con, final, borrado, params):
    """Desde que dia hay que recalcular gold, o None si nada cambio de valor.

    POR QUE NO ALCANZA CON "SE CARGO ALGO". El orquestador llama a este script
    en cada corrida y recarga TODOS los meses apenas se toca un Excel: si
    cualquier carga estirara la ventana, cada correccion de una lista de
    septiembre mandaria a reconstruir gold desde mayo, que son varios minutos
    de trabajo para escribir exactamente los mismos numeros.

    Asi que se comparan los costos que estan contra los que van a quedar --los
    que se van tambien cuentan: una linea que se queda sin costo cambia igual--
    y se devuelve la vigencia mas vieja que de verdad quedo distinta.
    """
    filas = con.exec_driver_sql(
        "SELECT sku, mes_comercial, vigente_desde, costo_real "
        "FROM bronze.costos_historicos" + borrado,
        params,
    ).fetchall()
    viejas = {
        (f[0], f[1], _como_fecha(f[2])): _numero(f[3]) for f in filas
    }
    nuevas = {
        (r.sku, r.mes_comercial, _como_fecha(r.vigente_desde)): _numero(r.costo_real)
        for r in final.itertuples()
    }

    cambiadas = [
        clave[2] for clave in set(viejas) | set(nuevas)
        if viejas.get(clave) != nuevas.get(clave)
    ]
    cambiadas = [v for v in cambiadas if v is not None]
    return min(cambiadas) if cambiadas else None


def anotar_para_reconstruir(desde):
    """Le deja anotado a modelo.py hasta donde tiene que estirar la ventana.

    Se guarda la mas VIEJA de las pendientes: si ya habia una anotacion sin
    consumir --porque modelo.py todavia no corrio, o se cayo-- pisarla con una
    fecha mas nueva dejaria dias sin recalcular.
    """
    if desde is None:
        print("  Ningun costo quedo distinto: gold no necesita recalcular nada")
        return

    anterior = estado.leer(CLAVE_RECONSTRUIR, None)
    if anterior:
        try:
            desde = min(desde, date.fromisoformat(anterior))
        except (TypeError, ValueError):
            print(f"  (aviso: {CLAVE_RECONSTRUIR} ilegible: {anterior!r}, se pisa)")

    estado.guardar(CLAVE_RECONSTRUIR, desde.isoformat())
    print(f"  Cambiaron costos vigentes desde {desde}: "
          "modelo.py va a recalcular gold desde ahi")


def cargar_costos(objetivo=None, revisar_solo=False):
    """Carga los costos de todos los archivos, o de uno solo si se pasa `objetivo`.

    Con `objetivo` NO se reescribe la tabla entera. Reescribir todo con un solo
    archivo cargado se llevaria puestos los demas, que es justo lo que uno NO
    quiere cuando corrige una lista suelta.

        '2026-09'      se rehace ese mes comercial entero, con todas sus
                       vigencias, a partir de los archivos que haya en la
                       carpeta. Una vigencia cuyo archivo se borro desaparece.
        '2026-09-18'   se rehace SOLO ese tramo. Los demas del mes quedan
                       intactos.
    """
    print("=== Cargando costos historicos con ofertas ===")

    elegidos = archivos_a_cargar(objetivo)
    if not elegidos:
        if objetivo:
            print(f"  No hay ningun archivo para {objetivo} en {CARPETA_COSTOS}/")
        else:
            print(f"  No hay archivos .xlsx en {CARPETA_COSTOS}/")
        disponibles = ", ".join(archivos_disponibles()) or "(ninguno)"
        print(f"  Archivos disponibles: {disponibles}")
        return

    if objetivo:
        cuales = ", ".join(n for n, _, _ in elegidos)
        print(f"  Solo {cuales} (el resto queda como esta)")

    todos = []
    for nombre, mes, vigencia in elegidos:
        archivo = os.path.join(CARPETA_COSTOS, f"{nombre}.xlsx")
        print(f"\n  === Mes comercial {mes}, vigente desde {vigencia} ===")

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
        costos["mes_comercial"] = mes
        costos["vigente_desde"] = vigencia

        # --- El archivo, contra el mes anterior -------------------------
        #
        # SE CHEQUEA ANTES DE ESCRIBIR NADA. Todo el mes se guarda en una sola
        # transaccion al final, asi que cortar aca deja la base como estaba: con
        # los costos viejos, que son viejos pero no absurdos.
        anteriores = costos_previos(vigencia, todos, engine)
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
                    f"El archivo {nombre}.xlsx tiene {informe['saltos']} costos "
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
    # La clave es (sku, mes, vigencia) y ya no (sku, mes): un mismo mes puede
    # tener varios tramos. Si dos archivos del mismo mes caen en el mismo dia
    # --2026-09.xlsx y 2026-09-07.xlsx, que arrancan los dos el 7-- gana el
    # ultimo leido, que por el orden es el de nombre mas largo.
    final = final.drop_duplicates(
        subset=["sku", "mes_comercial", "vigente_desde"], keep="last")
    final = final[["sku", "mes_comercial", "vigente_desde", "costo_teorico",
                   "oferta_pct", "desc_propio_pct", "costo_real"]]

    # QUE SE BORRA ANTES DE ESCRIBIR.
    #
    # Sin objetivo se rehace la tabla entera. Con un mes, ese mes con todas sus
    # vigencias (asi desaparece un tramo cuyo archivo se borro de la carpeta).
    # Con un dia, solo ese tramo: los demas del mes no se tocan.
    if objetivo is None:
        borrado, params, que = "", {}, "toda la tabla"
    elif PATRON_DIA.fullmatch(objetivo):
        mes, vigencia = mes_y_vigencia(objetivo)
        borrado = " WHERE mes_comercial = %(mes)s AND vigente_desde = %(vig)s"
        params, que = {"mes": mes, "vig": vigencia}, f"{mes} desde {vigencia}"
    else:
        borrado = " WHERE mes_comercial = %(mes)s"
        params, que = {"mes": objetivo}, objetivo

    # DELETE + APPEND EN UNA SOLA TRANSACCION, y no `if_exists="replace"`:
    # replace hace DROP, y el DROP falla si alguien crea una vista encima de la
    # tabla. (Ver tiendanube.py, que estuvo dos meses roto por esto.)
    #
    # Y las dos operaciones juntas para que la tabla no quede vacia en el medio:
    # modelo.py lee los costos de aca, y si corre justo en ese hueco arma
    # gold.fact_ventas entero sin margenes.
    reconstruir_desde = None
    with engine.begin() as con:
        existe = con.exec_driver_sql("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'bronze' AND table_name = 'costos_historicos'
            )
        """).scalar()
        if existe:
            asegurar_columnas(con)
            reconstruir_desde = vigencia_mas_vieja_que_cambia(
                con, final, borrado, params)
            borradas = con.exec_driver_sql(
                "DELETE FROM bronze.costos_historicos" + borrado, params
            ).rowcount
            print(f"\n  Filas viejas borradas ({que}): {borradas}")
        final.to_sql("costos_historicos", con, schema="bronze",
                     if_exists="append", index=False)

    # RECIEN DESPUES DE COMMITEAR. Si la escritura se cae, no hay nada que
    # recalcular y la anotacion habria mandado a modelo.py a rehacer meses al
    # pedo.
    anotar_para_reconstruir(reconstruir_desde)

    print(f"\n  Guardado: bronze.costos_historicos ({len(final)} filas de esta corrida)")
    print(f"  Meses cargados ahora: {sorted(final['mes_comercial'].unique())}")

    # Estado de la tabla entera, no solo de lo que se acaba de escribir: con un
    # objetivo es el unico numero que dice si lo demas sigue ahi. Las vigencias
    # van en la misma linea: son lo que hay que mirar para ver si un tramo entro
    # donde se esperaba.
    resumen = pd.read_sql(
        "SELECT mes_comercial, vigente_desde, count(*) AS skus "
        "FROM bronze.costos_historicos GROUP BY 1, 2 ORDER BY 1, 2", engine)
    print("\n  Tabla completa:")
    print(resumen.to_string(index=False))
    # Muestra de control
    print("\n  Ejemplo (primeras 5 con oferta > 0):")
    print(final[final['oferta_pct'] > 0].head(5).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(
        description="Carga los costos de costos_mensuales/*.xlsx a bronze.costos_historicos.",
        epilog="COMO SE LLAMA EL ARCHIVO:\n"
               "  2026-09.xlsx      la lista del mes, rige desde que arranca el\n"
               "                    mes comercial (el 6, o el dia que sea si ese\n"
               "                    mes tuvo cierre movido)\n"
               "  2026-09-18.xlsx   una lista que llego a mitad de mes: rige\n"
               "                    desde el 18. Lo de antes no se toca.\n"
               "\n"
               "Ejemplos:\n"
               "  python costos.py               todos los archivos\n"
               "  python costos.py 2026-08       agosto entero, con sus vigencias\n"
               "  python costos.py 2026-09-18    solo ese tramo\n"
               "  python costos.py --listar      que hay en la carpeta",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("mes", nargs="?", metavar="MES_O_FECHA",
                        help="Mes comercial AAAA-MM o vigencia AAAA-MM-DD. "
                             "Sin esto carga todos.")
    parser.add_argument("--listar", action="store_true",
                        help="Muestra los archivos que hay en la carpeta y sale")
    parser.add_argument("--revisar", action="store_true",
                        help="Lee los Excel y avisa de los costos raros, sin guardar")
    parser.add_argument("--si-cambio", action="store_true",
                        help="No hace nada si ningun .xlsx cambio desde la ultima vez")
    args = parser.parse_args()

    if args.listar:
        catalogados = archivos_a_cargar(None)
        if not catalogados:
            print("No hay archivos .xlsx en " + CARPETA_COSTOS + "/")
            return
        print("Archivos en " + CARPETA_COSTOS + "/:")
        for nombre, mes, vigencia in catalogados:
            print(f"  {nombre}.xlsx   mes {mes}, vigente desde {vigencia}")
        return

    # Se valida el formato antes de tocar nada: un mes mal escrito no encuentra
    # el archivo y sin este chequeo el mensaje seria "no existe", que hace
    # pensar que falta el Excel cuando lo que esta mal es lo que se tipeo.
    if args.mes and not (PATRON_MES.fullmatch(args.mes)
                         or PATRON_DIA.fullmatch(args.mes)):
        parser.error(f"'{args.mes}' no tiene el formato AAAA-MM ni AAAA-MM-DD "
                     "(ej: 2026-08 o 2026-09-18)")

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