"""El sell in del proveedor, desde la planilla de Google.

============================================================================
 QUE ES Y POR QUE NO SIRVE EL QUE YA TENIAMOS
============================================================================

El sell in es el descuento que el PROVEEDOR tiene vigente: el numero con el que
se le pide, y el que va en la columna FDESCU1 de la orden de compra.

En la base ya habia algo parecido --`costos_historicos.oferta_pct`-- y NO es lo
mismo: ese es un sell in CALCULADO a partir de nuestras compras, que se usa para
trasladarlo a las ofertas del mes y para valorizar el costo real. Sirve para
saber a cuanto nos quedo lo que ya compramos. Usarlo para pedir seria pedirle al
proveedor con un descuento inventado, y el error viajaria en un archivo que
alguien importa a Sigma sin volver a mirarlo.

============================================================================
 DE DONDE SALE
============================================================================

De la hoja "Tablero" del Google Sheet "2026 - Sell In Historico", que es el
resumen: una fila por articulo y una columna por oferta.

    PROVEEDOR | MARCA | COD PROV | SKU | EAN | DESCRIPCION | 1/8/2026 | HOT SALE | ...

Las seis primeras columnas identifican el articulo; de ahi en adelante, CADA
COLUMNA ES UNA OFERTA y el encabezado dice cual:

    "1/8/2026"           la oferta de agosto 2026          -> mes, evento vacio
    "1/7/2026 (Glade)"   un corte especifico de julio      -> mes + evento
    "HOT SALE"           una promo sin mes en el encabezado-> evento sin mes

LOS EVENTOS NO SE INVENTAN UN MES. "HOT SALE" esta entre las columnas de mayo y
junio, y seria facil deducir que es de mayo -- pero deducirlo mal significa
aplicar el descuento de una promo de tres dias a la compra de un mes entero. Se
guarda con mes vacio y se ve como evento, que es lo que es.

Se lee la hoja resumen y no las 25 hojas mensuales a proposito: cada hoja tiene
sus propios encabezados, y leerlas por posicion ya nos desalineo 4.368 valores
una vez.

============================================================================
 QUE HACE FALTA PARA QUE CORRA (ver tambien el README)
============================================================================

    SELL_IN_SHEET_ID    el id del Google Sheet (esta en la URL). Obligatorio.
    SELL_IN_HOJA        opcional, por si la hoja deja de llamarse "Tablero"

Y la credencial, por UNO de los dos caminos:

    sin clave           Workload Identity Federation. El workflow le pide a
                        Google un permiso de minutos con el token de GitHub.
                        Es lo recomendado: no existe ninguna clave que robar,
                        y no hace falta que nadie apague una politica.
    GOOGLE_SA_JSON      la clave descargada de la cuenta de servicio. Simple,
                        pero muchas empresas la tienen prohibida por politica.

En los dos casos la cuenta de servicio necesita permiso de LECTURA sobre la
planilla: se comparte como con cualquier persona, usando el mail que termina en
.iam.gserviceaccount.com.

    python sell_in.py            lee y guarda
    python sell_in.py --probar   lee, muestra lo que entendio y NO guarda

============================================================================
 DOS ORIGENES, UN SOLO PARSER
============================================================================

La hoja puede llegar por dos caminos, y el codigo que la interpreta es el
mismo en los dos:

  api      se le pide a Google directamente. Necesita credencial, y la
           credencial necesita que un administrador configure algo.
  script   la manda la propia planilla. Un Apps Script adentro del Sheet lee
           las celdas una vez por dia y las deja en bronze.sell_in_crudo, tal
           cual, sin interpretar. No hace falta ningun permiso especial: corre
           con el usuario que es dueño de la planilla.

EL APPS SCRIPT NO INTERPRETA NADA a proposito. Manda los textos como se ven
--"1/8/2026", "7,69%"-- que es exactamente lo que devuelve la API de Google con
FORMATTED_VALUE. Asi las dos rutas entregan lo mismo y hay UN parser, con sus
pruebas, en vez de dos que se van separando sin que nadie lo note.
"""

import argparse
import json
import os
import re
from datetime import date, datetime

import pandas as pd
from dotenv import load_dotenv

from conexion import crear_engine

load_dotenv()

HOJA_POR_DEFECTO = "Tablero"

# Las columnas que identifican al articulo. Todo lo que venga despues de estas
# es una oferta. Se comparan sin acentos ni mayusculas: el encabezado real dice
# "DESCRIPCIÓN" y cualquier dia alguien lo escribe sin tilde.
COLUMNAS_ARTICULO = {
    "proveedor": "proveedor",
    "marca": "marca",
    "cod prov": "cod_prov",
    "sku": "sku",
    "ean": "ean",
    "descripcion": "descripcion",
}

# Cuanto puede valer un descuento antes de que sea un error de carga. Hay un
# antecedente: costos_historicos tiene un SKU con 973,08 %, que por eso figura
# con costo negativo.
DESCUENTO_MAXIMO = 100.0

DDL = """
create schema if not exists bronze;
create table if not exists bronze.sell_in (
  mes_comercial  text    not null,
  evento         text    not null default '',
  sku            text    not null,
  descuento_pct  numeric not null,
  proveedor      text,
  encabezado     text,
  actualizado    timestamptz not null default now(),
  primary key (mes_comercial, evento, sku)
);
alter table bronze.sell_in add column if not exists evento text not null default '';
alter table bronze.sell_in add column if not exists encabezado text;
create index if not exists sell_in_mes on bronze.sell_in (mes_comercial);
"""


def _sin_acentos(texto):
    reemplazos = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return texto.translate(reemplazos)


def normalizar(texto):
    """El encabezado listo para comparar: sin acentos, sin espacios de mas."""
    return " ".join(_sin_acentos(str(texto or "")).strip().lower().split())


def interpretar_encabezado(texto):
    """Que oferta es esta columna: (mes_comercial, evento).

    Devuelve None si la columna no es una oferta (esta vacia, o es una de las
    que identifican al articulo).

        "1/8/2026"          -> ("2026-08", "")
        "01/08/2026"        -> ("2026-08", "")
        "1/7/2026 (Glade)"  -> ("2026-07", "Glade")
        "HOT SALE"          -> ("", "HOT SALE")
        ""                  -> None

    LA FECHA ES DIA/MES/AÑO y el dia tiene que ser 1. No es una formalidad: con
    "1/8/2026", dia/mes/año dice agosto y mes/dia/año dice enero, y las dos
    lecturas son plausibles. Exigir que el dia sea 1 --que es como se escriben
    las columnas de mes-- hace que una fecha con otro dia no se tome por mes
    equivocado sino que caiga como evento, que es lo conservador.
    """
    crudo = str(texto or "").strip()
    if not crudo:
        return None
    if normalizar(crudo) in COLUMNAS_ARTICULO:
        return None

    # La fecha puede venir sola o con un aclarador entre parentesis.
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\s*(?:\((.*)\))?\s*$", crudo)
    if m:
        dia, mes, anio, aclaracion = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        if dia == 1 and 1 <= mes <= 12:
            return (f"{anio:04d}-{mes:02d}", (aclaracion or "").strip())

    # Cualquier otra cosa es un evento, y NO se le inventa un mes.
    return ("", crudo)


def limpiar_pct(valor):
    """El descuento como numero en PUNTOS (15,00 % -> 15.0).

    Aguanta las tres formas en que puede llegar el mismo dato:
    texto "7,69%", texto "7.69", y numero de Google Sheets (0.0769 si la celda
    tiene formato porcentaje). El corte en 1 es el mismo criterio que usa
    costos.py, por lo mismo: un descuento de menos del 1 % no existe en esta
    planilla, asi que un valor <= 1 es una fraccion.
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        v = float(valor)
        # ESTRICTAMENTE MENOR QUE 1, no "menor o igual". costos.py usa <= 1 y
        # ahi da igual; aca no: con <=, un descuento del 1 % escrito como el
        # numero 1 se leeria como 100 % y se iria asi a una orden de compra. Al
        # reves --un 100 % escrito como 1.0-- se lee 1 %, que hace pagar de mas:
        # se nota enseguida y no compromete el pedido.
        return round(v * 100 if abs(v) < 1 else v, 4)

    texto = str(valor).strip()
    if not texto or texto in {"-", "--", "N/A", "n/a"}:
        return None
    texto = texto.replace("%", "").replace(" ", "").replace("\u00a0", "")
    # "1.234,56" -> "1234.56" ; "7,69" -> "7.69" ; "7.69" queda igual
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        # Redondeado: 0.0769 * 100 da 7.6899999999999995 en punto flotante, y
        # ese resto despues aparece en la pantalla y en el archivo. Cuatro
        # decimales es mas precision de la que tiene un descuento comercial.
        return round(float(texto), 4)
    except ValueError:
        return None


def filas_de_la_planilla(valores):
    """Convierte lo que devuelve la API en filas listas para guardar.

    `valores` es la hoja entera como lista de listas, la primera es el
    encabezado. Devuelve (filas, resumen) y NO toca la base: es la parte que se
    puede probar sin credenciales, que es justamente donde estan los errores
    que duelen.
    """
    if not valores:
        return [], {"columnas_oferta": 0, "ignoradas": [], "sin_sku": 0, "fuera_de_rango": 0}

    encabezado = valores[0]
    posiciones = {}
    for i, celda in enumerate(encabezado):
        clave = COLUMNAS_ARTICULO.get(normalizar(celda))
        if clave and clave not in posiciones:
            posiciones[clave] = i

    if "sku" not in posiciones:
        raise RuntimeError(
            "La hoja no tiene columna SKU. Encabezado leido: "
            + " | ".join(str(c) for c in encabezado[:12])
        )

    ofertas = []
    ignoradas = []
    for i, celda in enumerate(encabezado):
        if i in posiciones.values():
            continue
        leido = interpretar_encabezado(celda)
        if leido is None:
            if str(celda or "").strip():
                ignoradas.append(str(celda))
            continue
        ofertas.append((i, leido[0], leido[1], str(celda).strip()))

    filas = []
    sin_sku = 0
    fuera_de_rango = 0
    for fila in valores[1:]:
        def celda(clave):
            j = posiciones.get(clave)
            return fila[j] if j is not None and j < len(fila) else None

        sku = str(celda("sku") or "").strip()
        if not sku:
            sin_sku += 1
            continue
        proveedor = str(celda("proveedor") or "").strip() or None

        for j, mes, evento, texto in ofertas:
            pct = limpiar_pct(fila[j] if j < len(fila) else None)
            if pct is None:
                continue
            # UN CERO NO SE GUARDA. "Sin oferta" y "oferta del 0 %" son lo
            # mismo para comprar, y la planilla trae 0,00% en casi todas las
            # celdas: guardarlos serian cientos de miles de filas que no dicen
            # nada y que ademas harian que el panel muestre "0" como si fuera
            # un dato cargado en vez de "no hay oferta".
            if pct == 0:
                continue
            if pct < 0 or pct > DESCUENTO_MAXIMO:
                fuera_de_rango += 1
                continue
            filas.append({
                "mes_comercial": mes,
                "evento": evento,
                "sku": sku,
                "descuento_pct": pct,
                "proveedor": proveedor,
                "encabezado": texto,
            })

    resumen = {
        "columnas_oferta": len(ofertas),
        "ignoradas": ignoradas,
        "sin_sku": sin_sku,
        "fuera_de_rango": fuera_de_rango,
    }
    return filas, resumen


def credenciales():
    """La credencial de Google, por cualquiera de los dos caminos.

    ============================================================================
     HAY DOS, Y EL BUENO ES EL SEGUNDO
    ============================================================================

    1. GOOGLE_SA_JSON -- la clave descargada de una cuenta de servicio. Es la
       forma directa, pero MUCHAS EMPRESAS LA TIENEN PROHIBIDA por politica de
       organizacion (`iam.disableServiceAccountKeyCreation`), y con razon: es un
       archivo que da acceso, no vence nunca, y viaja por mail y por chat.

    2. SIN CLAVE, por Workload Identity Federation. GitHub le da al runner un
       token OIDC que dice "soy la corrida tal del repo tal", Google lo cambia
       por un permiso de unos minutos, y no existe ninguna clave que robar.
       Es lo que Google recomienda y lo que un administrador aprueba sin
       discutir, porque no le pide que apague nada.

    El codigo no elige: usa la clave si esta, y si no busca la credencial que
    dejo el runner (ADC). Asi los dos caminos andan sin tocar nada, y el dia
    que se pase de uno al otro solo cambia el workflow.
    """
    from google.oauth2 import service_account

    alcance = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

    crudo = os.getenv("GOOGLE_SA_JSON")
    if crudo:
        try:
            info = json.loads(crudo)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "GOOGLE_SA_JSON no es un JSON valido. Tiene que ser el archivo "
                "entero que baja Google, no solo el mail ni la clave."
            ) from e
        return service_account.Credentials.from_service_account_info(info, scopes=alcance)

    import google.auth

    try:
        cred, _ = google.auth.default(scopes=alcance)
        return cred
    except Exception as e:
        raise RuntimeError(
            "No hay credencial de Google. Hay dos formas de darsela:\n"
            "  1. Workload Identity Federation (recomendada, sin claves): el paso\n"
            "     google-github-actions/auth en el workflow la deja lista sola.\n"
            "  2. GOOGLE_SA_JSON con el JSON de una cuenta de servicio.\n"
            "  En los dos casos, la planilla tiene que estar compartida como\n"
            "  Lector con el mail de la cuenta (.iam.gserviceaccount.com).\n"
            f"  Detalle: {e}"
        ) from e


def leer_crudo():
    """La ultima foto que dejo el Apps Script de la planilla."""
    engine = crear_engine()
    with engine.begin() as con:
        fila = con.exec_driver_sql(
            "select filas, recibido, hoja from bronze.sell_in_crudo"
            " order by id desc limit 1"
        ).fetchone()

    if not fila:
        raise RuntimeError(
            "No hay ninguna foto de la planilla en bronze.sell_in_crudo.\n"
            "  La manda el Apps Script que vive adentro del Google Sheet.\n"
            "  Si todavia no se instalo, ver apps_script/sell_in.gs y el README."
        )

    valores, recibido, hoja = fila
    dias = (datetime.now(recibido.tzinfo) - recibido).days
    print(f"  Foto de la planilla: hoja '{hoja}', recibida hace {dias} dia(s)")
    if dias > 3:
        # No aborta: mejor un sell in de hace una semana que ninguno. Pero se
        # dice, porque un disparador que se apago no avisa solo.
        print("  ATENCION: hace mas de 3 dias que la planilla no manda nada."
              " Revisar el disparador en el Apps Script.")
    return valores


def leer_hoja():
    """La hoja "Tablero" entera, tal como la muestra Google.

    FORMATTED_VALUE y no UNFORMATTED: asi los encabezados de fecha llegan como
    "1/8/2026" --que es lo que se ve y lo que se puede interpretar-- y no como
    el numero de serie 46234, que no dice nada.
    """
    # Se importa aca adentro y no arriba para que las pruebas del parser anden
    # en una maquina sin google-auth instalado.
    from google.auth.transport.requests import AuthorizedSession

    if not os.getenv("SELL_IN_SHEET_ID"):
        raise RuntimeError(
            "Falta SELL_IN_SHEET_ID: el id del Google Sheet, que esta en la URL "
            "entre /d/ y /edit."
        )

    sesion = AuthorizedSession(credenciales())

    hoja = os.getenv("SELL_IN_HOJA", HOJA_POR_DEFECTO)
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{os.getenv('SELL_IN_SHEET_ID')}"
        f"/values/{hoja}"
    )
    r = sesion.get(url, params={"valueRenderOption": "FORMATTED_VALUE"}, timeout=(10, 120))
    if not r.ok:
        # El cuerpo dice cual de los dos problemas es --la planilla no existe o
        # la cuenta no tiene permiso-- y sin el los dos se leen igual.
        raise RuntimeError(
            f"Google rechazo la lectura (HTTP {r.status_code}).\n"
            f"  Respuesta: {r.text[:400]}\n"
            "  404 -> el SELL_IN_SHEET_ID no existe o la hoja no se llama asi.\n"
            "  403 -> la planilla no esta compartida con la cuenta de servicio."
        )
    return r.json().get("values", [])


def guardar(filas):
    engine = crear_engine()
    df = pd.DataFrame(filas)
    df["actualizado"] = datetime.now()
    with engine.begin() as con:
        con.exec_driver_sql(DDL)
        # Se reemplaza entero y en UNA transaccion: la planilla es la verdad, y
        # una oferta que se saco de la planilla tiene que desaparecer de la
        # base. Borrar y despues insertar en dos transacciones dejaria la tabla
        # vacia en el medio, y el panel de compras la lee en vivo.
        con.exec_driver_sql("delete from bronze.sell_in")
        df.to_sql("sell_in", con, schema="bronze", if_exists="append", index=False)
    print(f"  Guardado: bronze.sell_in ({len(df)} filas)")


def main():
    parser = argparse.ArgumentParser(description="Sell in del proveedor, desde Google Sheets.")
    parser.add_argument("--probar", action="store_true",
                        help="Lee y muestra lo que entendio, sin guardar")
    parser.add_argument("--origen", choices=["auto", "api", "script"], default="auto",
                        help="De donde sale la hoja. auto: la API si hay credencial,"
                             " si no la foto que dejo el Apps Script")
    args = parser.parse_args()

    print("\n=== SELL IN DEL PROVEEDOR ===")

    origen = args.origen
    if origen == "auto":
        # Si hay credencial de Google configurada se le pide a la API, que es el
        # dato del momento. Si no, se usa lo que dejo la planilla.
        hay_credencial = bool(os.getenv("GOOGLE_SA_JSON") or os.getenv("GCP_WIF_PROVIDER"))
        origen = "api" if hay_credencial else "script"
    print(f"  Origen: {origen}")

    valores = leer_hoja() if origen == "api" else leer_crudo()
    print(f"  Hoja leida: {len(valores)} filas")

    filas, resumen = filas_de_la_planilla(valores)
    print(f"  {resumen['columnas_oferta']} columnas de oferta · {len(filas)} descuentos > 0")
    if resumen["ignoradas"]:
        print(f"  Columnas ignoradas: {', '.join(resumen['ignoradas'][:8])}")
    if resumen["sin_sku"]:
        print(f"  Filas sin SKU: {resumen['sin_sku']}")
    if resumen["fuera_de_rango"]:
        print(f"  ATENCION: {resumen['fuera_de_rango']} descuentos fuera de 0-{DESCUENTO_MAXIMO} %, salteados")

    por_mes = {}
    for f in filas:
        clave = f["mes_comercial"] or f"(evento) {f['evento']}"
        por_mes[clave] = por_mes.get(clave, 0) + 1
    for clave in sorted(por_mes, reverse=True)[:12]:
        print(f"    {clave:<24} {por_mes[clave]:>6} articulos con oferta")

    if args.probar:
        for f in filas[:10]:
            print(f"    {f['sku']:<12} {f['mes_comercial'] or '-':<8} "
                  f"{f['evento'][:16]:<16} {f['descuento_pct']:>6.2f} %")
        print("\n  (modo prueba: no se guardo nada)")
        return

    if not filas:
        # NO se vacia la tabla: una planilla que ese dia esta a medio cargar
        # borraria el sell in del mes y las ordenes saldrian sin descuento.
        print("  Sin descuentos para guardar: se deja lo que ya habia.")
        return

    guardar(filas)


if __name__ == "__main__":
    main()
