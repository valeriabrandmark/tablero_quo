import argparse
import os
import json
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine
from conexion import crear_engine
import errores_bd

load_dotenv()

engine = crear_engine(connect_args={"client_encoding": "utf8"})

# Fecha de corte ABSOLUTA: nunca se procesa nada anterior a esto (piso historico)
FECHA_CORTE = date(2026, 5, 6)

# Ventana movil: en cada corrida, se re-procesan y reemplazan solo los ultimos N dias.
# Todo lo anterior a esa ventana en gold.fact_ventas queda intacto (no se vuelve a tocar).
WINDOW_DAYS = 7
CUTOFF = max(FECHA_CORTE, date.today() - timedelta(days=WINDOW_DAYS))


# Mapeo de codigo de vendedor a nombre (tabla de Sigma, no se extrae por API)
VENDEDORES = {
    "001": "CASA CENTRAL",
    "002": "AGENCIA",
    "003": "ECOMMERCE",
    "004": "IGNACIO",
    "005": "IVANA",
    "006": "SILVIO",
    "007": "RAMON",
    "008": "PABLO",
    "009": "MELI",
    "010": "ALEJANDRO",
    "011": "TRADE",
    "012": "BTL",
    "013": "PROYECTOS ESPECIALES",
    "014": "RICARDO",
    "WEB": "VENDEDOR WEB",
}


EMPRESAS = {
    "0001": "Quo Marketing SRL",
    "0002": "Noa Comercial SRL",
    "0003": "Presupuesto QUO",
    "0004": "Presupuesto Noa",
}


def empresa_de(codigo):
    if codigo is None:
        return None
    return EMPRESAS.get(str(codigo).strip(), f"Empresa {codigo}")


def vendedor_de(codigo):
    if codigo is None:
        return None
    return VENDEDORES.get(str(codigo).strip(), f"Vendedor {codigo}")


def mes_comercial(fecha):
    """Devuelve el mes comercial 'AAAA-MM' segun la regla del 6 al 5.
       Dia >= 6 -> mes actual. Dia < 6 -> mes anterior."""
    if fecha is None:
        return None
    if fecha.day >= 6:
        anio, mes = fecha.year, fecha.month
    else:
        # mes anterior
        if fecha.month == 1:
            anio, mes = fecha.year - 1, 12
        else:
            anio, mes = fecha.year, fecha.month - 1
    return f"{anio:04d}-{mes:02d}"


# NOTAS DE CREDITO: DOS PREGUNTAS DISTINTAS, DOS LISTAS.
#
# Una NC lleva cantidad negativa, asi que revierte la venta. Pero "revertir la
# venta" son dos cosas separadas y no siempre van juntas:
#
#   1) El COSTO, ¿vuelve? Solo si la mercaderia se recupera y se puede volver a
#      vender (o si alguien la paga).
#   2) El FLETE, ¿vuelve? Solo si el despacho no ocurrio. Si el camion salio,
#      el transporte se pago, y no hay nada que devolver.
#
# Segun el motivo que Sigma guarda en `motivoNc`:
#
#   motivo                 que paso                              costo  flete
#   ---------------------  ------------------------------------  -----  -----
#   INCOBRABLE / MOROSO    devolvio la mercaderia, no pago       vuelve  NO
#   FALLADO / VENCIMIENT   vuelve, se destruye                     NO    NO
#   FALLADO DE FABRICA     vuelve, se destruye                     NO    NO
#   ERROR DE CARGA VEND    se refactura con otro comprobante     vuelve  vuelve
#   CANCELACION DE PEDID   la venta no ocurrio                   vuelve  vuelve
#   ERROR LOGISTICA        no vuelve, lo reconoce el transporte  vuelve  vuelve
#
# El costo solo se pierde con los fallados: son los unicos que vuelven al
# deposito sin poder revenderse. En el incobrable la mercaderia se recupera, y
# en el error de logistica la paga el transportista.
#
# El flete, en cambio, no vuelve ni en el incobrable ni en el fallado: en los
# dos el pedido se despacho de verdad. Esa parte se aplica en
# prorratear_flete.py, que repite esta misma lista.
MOTIVOS_SIN_RECUPERO = ("FALLADO",)


def mercaderia_perdida(motivo):
    """True si esta nota de credito no devuelve mercaderia vendible al stock.

    Solo los fallados. Ojo: NO es lo mismo que "el flete no se revierte" --
    esa lista es mas larga y vive en prorratear_flete.py.

    El motivo llega de una columna de pandas, asi que en las facturas (que no
    tienen motivo) NO viene None: viene NaN, que es un float y ADEMAS es
    truthy. Por eso el filtro es por tipo y no un `or ""`, que dejaba pasar el
    NaN y reventaba con "'float' object has no attribute 'strip'"."""
    if not isinstance(motivo, str):
        return False
    return motivo.strip().upper().startswith(MOTIVOS_SIN_RECUPERO)


def es_anulacion_total(r):
    """True si esta nota de credito revierte la factura ENTERA.

    El criterio es estructural, no la etiqueta: mismas unidades y mismo importe
    que la factura que ajusta, con el signo dado vuelta. Cuando eso pasa, la
    venta se anulo entera -- casi siempre para refacturarla desde otra empresa,
    como F-Z19-00003629 que se rehizo en F-B93-00001167 -- y los dos
    comprobantes tienen que netearse en cero.

    Los importes se comparan redondeados al centavo: son `double precision`, y
    sumar 59 renglones deja basura en el orden de 1e-11 que hace fallar un `==`
    exacto aunque los dos comprobantes sean identicos.
    """
    if pd.isna(r["unidadesOriginal"]) or pd.isna(r["importeOriginal"]):
        return False        # sin vinculo a una factura no hay con que comparar
    if r["unidadesPropias"] != -r["unidadesOriginal"]:
        return False
    return round(float(r["importePropio"]), 2) == round(-float(r["importeOriginal"]), 2)


def importe(valor):
    """Un importe de un DataFrame como float, y los vacios en 0.

    El `or 0` de siempre no alcanza: pandas no devuelve None para una celda
    vacia, devuelve NaN -- que es un float y ADEMAS es truthy, asi que pasa el
    `or` y envenena toda la cuenta que siga. Es el mismo NaN que rompio
    mercaderia_perdida().
    """
    if valor is None or pd.isna(valor):
        return 0.0
    return float(valor)


# Umbral de envio gratis de Mercado Libre, con IVA, sobre el precio de UNA
# unidad de la publicacion (no sobre el total de la linea ni del carrito).
#
# Por arriba de este precio ML obliga al envio gratis y nos cobra el flete a
# nosotros; por abajo lo paga el comprador y a nosotros no nos cuesta nada. Por
# eso el flete de un paquete lo tienen que cargar los productos que lo
# dispararon, y no los baratos que viajaron de arriba.
#
# Contrastado contra agosto 2026, sobre los 7.789 paquetes de un solo producto:
# de los 974 en los que pagamos flete, 969 tenian el producto por encima de los
# $33.000; de los 6.815 en los que no pagamos, 6.812 lo tenian por debajo. Ocho
# excepciones sobre 7.789 (0,1%).
#
# ES UN VALOR NOMINAL Y ML LO ACTUALIZA. El dia que lo suban hay que cambiarlo
# aca; mientras tanto un --todo reprocesa meses viejos con el umbral de hoy.
# Eso mueve el reparto DENTRO de un paquete, nunca el flete total del canal.
UMBRAL_ENVIO_GRATIS = 33000.0


def repartir_envio(costo, lineas):
    """Reparte el costo de UN envio entre las lineas del paquete que despacho.

    `lineas` es una lista de (indice, precio_unitario, valor_linea) y devuelve
    {indice: parte_del_envio}. Las tres reglas, en orden:

      1. Una sola linea                -> se lleva el envio entero.
      2. Alguna linea supera el umbral -> el envio se parte en PARTES IGUALES
         entre esas, y las baratas quedan en cero. Viajaron gratis: el flete lo
         disparo el producto caro.
      3. Ninguna supera el umbral      -> ponderado por monto entre todas.

    Sobre la regla 3: en agosto los 453 paquetes multiproducto sin ningun
    producto caro tuvieron TODOS costo de envio cero, y los 37 con algun
    producto caro tuvieron TODOS costo. La regla 2 es la que hace el trabajo;
    la 3 esta para que un caso raro no se quede sin repartir.

    El reparto de la regla 2 es en partes iguales POR LINEA, no por unidad: una
    linea de tres unidades caras cuenta lo mismo que una de una sola. Es la
    lectura literal del criterio ("en partes iguales entre los productos caros").
    """
    if not lineas or not costo:
        return {}

    if len(lineas) == 1:
        return {lineas[0][0]: costo}

    caras = [l for l in lineas if l[1] > UMBRAL_ENVIO_GRATIS]
    if caras:
        parte = costo / len(caras)
        return {idx: parte for idx, _, _ in caras}

    total = sum(valor for _, _, valor in lineas)
    if total <= 0:
        # Un paquete entero a precio cero no deberia existir, pero si existe es
        # mejor partirlo en iguales que dividir por cero y dejar todo en NaN.
        parte = costo / len(lineas)
        return {idx: parte for idx, _, _ in lineas}
    return {idx: costo * (valor / total) for idx, _, valor in lineas}


ZONA = "America/Argentina/Buenos_Aires"


def to_date(valor):
    """Fecha CALENDARIO argentina del dato, o None.

    Antes esto hacia `pd.to_datetime(valor, utc=True).date()`, o sea que se
    quedaba con la fecha en UTC. Para Sigma daba igual (manda "2026-08-19", sin
    hora), pero Mercado Libre manda "2026-08-19T09:54:37.000-04:00" y Tienda
    Nube "2026-06-11T12:52:39+0000": ahi la hora existe, y con UTC toda venta
    hecha despues de las 21:00 hora argentina quedaba anotada al dia siguiente.

    Eran 6.719 ordenes de 38.287, un 17,5%. El dia del tablero terminaba a las
    21:00 en vez de a medianoche, y en el borde del mes comercial (del 5 al 6)
    algunas ventas caian en el mes equivocado.

    El `tzinfo is None` no es un detalle: sin el, la fecha pelada de Sigma se
    tomaria como medianoche UTC y al pasarla a hora argentina daria las 21:00
    del dia ANTERIOR, corriendo todas las ventas mayoristas un dia para atras.
    Un dato sin hora ya viene en hora local y no hay nada que convertir.
    """
    ts = pd.to_datetime(valor, errors="coerce")
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        return ts.date()
    return ts.tz_convert(ZONA).date()


def piso_sql():
    """El `desde` que se le pasa a las consultas de bronze, como 'YYYY-MM-DD'.

    POR QUE EXISTE
    Antes las consultas a bronze no filtraban por fecha: se traian los CUATRO
    MESES enteros -- 38.490 ordenes de Mercado Libre, 10.453 lineas de Sigma --
    se les parseaba el JSON a todas, y despues se descartaba el 94% con un `if`
    en Python. Reconstruir una semana costaba procesar el historico completo, y
    ahi se iban casi seis minutos de cada corrida.

    POR QUE VA UN DIA ANTES DE CUTOFF
    Las fechas en bronze son texto y vienen en el huso de cada origen -- Mercado
    Libre manda -04:00, que no es el de Argentina --, asi que una orden de las
    23 hs puede caer en el dia siguiente una vez convertida. Con un dia de
    margen no se escapa ninguna.

    El filtro EXACTO lo sigue haciendo Python con `if f < CUTOFF: continue`, que
    ya convierte a hora argentina. Este piso no decide que entra: solo evita
    traer de la base lo que se va a tirar igual.

    COMO SE COMPARA, Y POR QUE IMPORTA TANTO
    Se compara la columna DERECHA contra el piso -- `fecha >= '2026-08-14'` --
    y NO `left(fecha, 10) >= '2026-08-14'`.

    Las dos dan exactamente el mismo resultado, porque las fechas de bronze son
    texto ISO-8601 de ancho fijo ('2026-08-21T12:06:26.000-04:00') y en ese
    formato el orden alfabetico ES el orden cronologico: cualquier cosa que
    empiece con '2026-08-14' es mayor que '2026-08-14' a secas, y '2026-08-13T..'
    es menor. Verificado sobre las tres tablas: 3.278 / 864 / 3 filas de un lado
    y del otro.

    Pero para Postgres NO son lo mismo. `left(columna, 10)` es una funcion
    aplicada a la columna, y eso hace que el indice sobre esa columna no se pueda
    usar: no hay mas remedio que leer la tabla entera.

    El 22/08/2026 eso tumbo el pipeline. Con `left()`, la consulta de ml_ventas
    leia 118 MB de DISCO por llamada -- la tabla completa, incluido el JSON de
    `order_items` -- y como corre en un loop paginado, varias veces por corrida.
    Eran el 89,8 % de todo el I/O de la base. Mientras corria, cualquier otra
    consulta se pasaba del statement_timeout de 2 minutos: `SELECT id,
    "ivaPorcentual" FROM sigma_articulos` (8.000 filas) llego a tardar 97
    segundos, y un INSERT en ops.estado (3 filas) tardo 52. Doce corridas
    seguidas en rojo.

    Sacando el left(), la misma consulta pasa a Index Scan: 6,7 ms y 8 MB de
    cache. Medido con EXPLAIN (ANALYZE, BUFFERS).
    """
    return (CUTOFF - timedelta(days=1)).isoformat()


def construir_fact_ventas():
    print(f"=== Construyendo fact_ventas (ventana movil: {CUTOFF} en adelante) ===")

    # --- Catalogos auxiliares ---
    print("Leyendo costos historicos y IVA...")
    # Costo real por (sku, mes_comercial)
    cost = pd.read_sql("SELECT sku, mes_comercial, costo_real FROM bronze.costos_historicos", engine)
    costo_idx = {(r["sku"], r["mes_comercial"]): r["costo_real"] for _, r in cost.iterrows()}

    # IVA por sku (de sigma_articulos)
    art = pd.read_sql('SELECT id, "ivaPorcentual" FROM bronze.sigma_articulos', engine)
    iva_por_sku = dict(zip(art["id"].astype(str), art["ivaPorcentual"]))

    # Proveedor y marca por sku (de sigma_articulos)
    # NOTA: la columna "marca" plana viene vacia desde la API de Sigma.
    # El dato real esta anidado en "attributes.marca".
    prov = pd.read_sql(
        'SELECT id, "proveedorNombre", "attributes.marca" AS marca FROM bronze.sigma_articulos',
        engine
    )
    proveedor_por_sku = {str(k).strip(): v for k, v in zip(prov["id"], prov["proveedorNombre"])}
    marca_por_sku = {str(k).strip(): v for k, v in zip(prov["id"], prov["marca"])}

    def proveedor_de(sku):
        return proveedor_por_sku.get(str(sku).strip())

    def marca_de(sku):
        return marca_por_sku.get(str(sku).strip())

    # Costo de envio, resuelto por ENVIO y no por orden.
    #
    # Mercado Libre cobra el envio por PAQUETE, no por orden: un carrito (pack)
    # junta varias ordenes en un solo envio. bronze.ml_envios guarda una fila por
    # envio, con el id de UNA sola de esas ordenes (las demas se descartan al
    # deduplicar por shipping_id).
    #
    # Si ese costo se le carga entero a esa unica orden, las otras del mismo
    # paquete quedan en cero y esa se come todo. Sobre el total general no cambia
    # nada, pero deja lineas con una rentabilidad que no es la suya -- y son
    # justo las que despues aparecen como "margen muy bajo" en las alertas.
    #
    # Por eso se trae a que envio pertenece cada orden, y el costo se reparte
    # entre TODAS las lineas de TODAS las ordenes del envio. Con que criterio se
    # reparte lo decide repartir_envio(): ya no es siempre proporcional al monto.
    # Para un envio de una sola linea da lo mismo que antes -- se lo lleva entero.
    env = pd.read_sql("""
        SELECT e.shipping_id,
               e.costo_envio,
               v.id::bigint::text AS order_id
        FROM bronze.ml_envios e
        JOIN bronze.ml_ventas v
          ON v."shipping.id"::bigint::text = e.shipping_id
        -- Los dos estados que el modelo cuenta como venta. Antes decia solo
        -- 'paid': una orden parcialmente devuelta dentro de un paquete no
        -- encontraba su envio y entraba al tablero con flete cero.
        WHERE v.status IN ('paid', 'partially_refunded')
    """, engine)
    env = env.drop_duplicates(subset=["shipping_id", "order_id"])

    # order_id -> shipping_id, y shipping_id -> costo del envio.
    #
    # Van separados porque el reparto ya no se puede resolver orden por orden:
    # la regla mira a TODOS los productos del paquete para saber cuales
    # dispararon el flete, y esos productos estan repartidos entre varias
    # ordenes. Primero se juntan las lineas de cada envio, y recien al final se
    # reparte (ver el bloque de Mercado Libre mas abajo).
    envio_de_orden = dict(zip(env["order_id"], env["shipping_id"]))
    costo_de_envio = dict(zip(env["shipping_id"], env["costo_envio"]))

    def costo_de(sku, mc):
        return costo_idx.get((str(sku), mc))

    def iva_de(sku):
        v = iva_por_sku.get(str(sku))
        return float(v) if v is not None else 21.0   # default 21 si no se encuentra

    # ARANCELES DE LAS PASARELAS DE TIENDA NUBE.
    #
    # Tienda Nube no informa la comision en el pedido -- no hay campo con el
    # monto ni con el neto liquidado --, pero si manda que pasarela y que medio
    # se uso. Con eso y la tabla de aranceles se calcula.
    #
    # Las filas estan versionadas por `vigente_desde` porque los aranceles
    # cambian y una venta de mayo se liquido con el arancel de mayo. Es el mismo
    # criterio que costos_historicos, y evita el problema que si tiene el umbral
    # de envio gratis de Mercado Libre, que es un valor unico para toda la
    # historia. Ver comisiones_pasarela.sql.
    try:
        com = pd.read_sql("""
            SELECT gateway, metodo, vigente_desde,
                   tasa_pct * (1 + iva_pct/100) + cpt_pct AS efectiva_pct
            FROM bronze.comisiones_pasarela
            ORDER BY gateway, metodo, vigente_desde
        """, engine)
    except Exception as e:
        # La tabla la crea comisiones_pasarela.sql, que puede no haberse
        # aplicado todavia. Sin ella el canal sigue como estaba -- comision en
        # cero, que es como venia -- en vez de dejar el modelo sin actualizar.
        if not errores_bd.es_tabla_inexistente(e):
            raise
        print("  (bronze.comisiones_pasarela no existe: Tienda Nube va sin comision)")
        com = pd.DataFrame(columns=["gateway", "metodo", "vigente_desde", "efectiva_pct"])

    # (gateway, metodo) -> [(vigente_desde, pct), ...] ordenado por fecha
    aranceles = {}
    for r in com.itertuples():
        aranceles.setdefault((r.gateway, r.metodo), []).append(
            (to_date(r.vigente_desde), float(r.efectiva_pct))
        )

    def arancel_de(gateway, metodo, fecha):
        """El % que cobra la pasarela por un pedido, o None si no hay tarifa.

        Devuelve None y no 0 a proposito: 0 significa "no cobra" (un pedido
        100% bonificado) y None significa "no se cuanto cobra". Confundirlos
        haria que un medio de pago nuevo entre al tablero como gratis, y nadie
        se enteraria."""
        tarifas = aranceles.get((gateway, metodo))
        if not tarifas or fecha is None:
            return None
        # La ultima que ya estaba vigente el dia del pedido.
        vigentes = [pct for desde, pct in tarifas if desde is not None and desde <= fecha]
        return vigentes[-1] if vigentes else None

    filas = []

    # --- 1) SIGMA ---
    print("Procesando Sigma...")
    sigma = pd.read_sql("""
        SELECT sv.empresa, sv.surcursal, sv.fecha, sv."itemArticuloId", sv."itemDescripcion",
               sv."itemCantidad", sv."itemPrecioUnitario", sv."clienteNombre",
               sv."itemDescuento", sv."itemDescuentoGlobal", sv."itemDescuentoFinanciero",
               sv."itemPedidoId", sv.vendedor, sv."comprobanteCodigo", sv."comprobanteNumero",
               sv."comprobanteTipo", sv."motivoNc",
               -- Fecha de la factura que esta nota de credito ajusta. Sirve para
               -- costear la devolucion al costo con el que salio, no al de hoy.
               orig.fecha AS "fechaOriginal",
               -- Unidades e importe de la nota y de la factura que ajusta. Si
               -- son exactamente opuestos, la nota anula la venta entera. Ver
               -- `anula_todo` mas abajo.
               propio.unidades AS "unidadesPropias",
               propio.importe  AS "importePropio",
               orig.unidades   AS "unidadesOriginal",
               orig.importe    AS "importeOriginal"
        FROM bronze.sigma_ventas sv
        LEFT JOIN (SELECT id, min(fecha) AS fecha,
                          sum("itemCantidad") AS unidades,
                          sum("itemCantidad" * "itemPrecioUnitario") AS importe
                     FROM bronze.sigma_ventas GROUP BY id) orig
               ON orig.id = sv."ajustaComprobanteId"::bigint
        LEFT JOIN (SELECT id,
                          sum("itemCantidad") AS unidades,
                          sum("itemCantidad" * "itemPrecioUnitario") AS importe
                     FROM bronze.sigma_ventas GROUP BY id) propio
               ON propio.id = sv.id
        WHERE sv.empresa IN ('0001','0002','0003','0004')
          -- Sin left(), por lo mismo. Aca ademas es literalmente un no-op:
          -- sigma_ventas.fecha ya viene como 'YYYY-MM-DD' pelado, 10 caracteres.
          AND sv.fecha >= '{desde}'
          -- FUERA LO QUE NO ES MERCADERIA.
          --
          -- Sigma tiene un rubro aparte, el 9999 "RUBRO FINANCIERO", con
          -- articulos que no son productos: ARTICULO FINANCIERO, COMISIONES
          -- POR COBRANZAS, ARTICULO PARA DESCUENTOS Y PUNITORIOS, REVALUOS.
          -- No tienen costo, asi que entraban con un margen igual a todo su
          -- importe y ensuciaban todo lo que se calcule sobre eso.
          --
          -- OJO AL EDITAR ESTE COMENTARIO: no escribas el signo de porcentaje
          -- aca adentro. psycopg2 lo lee como marcador de parametro incluso
          -- dentro de un comentario SQL, y la consulta muere con
          -- "immutabledict is not a sequence", que no dice nada de lo que pasa.
          -- Si hace falta el signo, va duplicado.
          --
          -- El SKU 9990 (CHEQUE RECHAZADO) va aparte porque en Sigma esta mal
          -- clasificado: figura en el rubro 0131 VARIOS, division Capilares.
          -- Existe un 9998 "ART FINANCIERO CH RECHAZADO" que si esta en el
          -- rubro correcto y no se usa. Lo ideal seria arreglarlo en Sigma;
          -- mientras tanto se lo nombra aca.
          --
          -- NO alcanza con filtrar por tipo de comprobante. La primera version
          -- de esto sacaba las notas de debito (comprobanteTipo = 'D') y esta
          -- MAL: la orden 3584 tiene la nota de debito D-DB93-00000012 por
          -- +$ 4.343.588 y su contrasiento C-CB93-00000095 por -$ 4.343.588.
          -- Sacando solo la D, la nota de credito quedaba sola y el tablero
          -- mostraba $ 4,3 M NEGATIVOS de la nada. Peor que no tocar nada.
          -- Por eso el corte va por articulo, que es lo que de verdad no es
          -- una venta, y no por el papel con el que se emitio.
          --
          -- Lo que sale hoy: $ 14.821.420 de "facturacion", todo margen.
          --   9990  CHEQUE RECHAZADO          2 renglones   $ 8.755.742
          --   9999  ARTICULO FINANCIERO      14 renglones   $ 4.052.616
          --   9992  COMISIONES POR COBRANZAS  5 renglones   $ 2.013.062
          AND NOT EXISTS (
              SELECT 1 FROM bronze.sigma_articulos a
              WHERE a.id = sv."itemArticuloId"
                AND (a."rubroCodigo" = '9999' OR a.id = '9990')
          )
    """.format(desde=piso_sql()), engine)

    for _, r in sigma.iterrows():
        f = to_date(r["fecha"])
        if f is None or f < CUTOFF:
            continue
        mc = mes_comercial(f)
        sku = r["itemArticuloId"]
        if r["empresa"] == "0001" and r["surcursal"] == "0001":
            unidad = "Quo Agencia"
        elif r["empresa"] in ("0001", "0003"):
            unidad = "Quo"
        else:
            unidad = "Noa"
        tipo = "Fiscal" if r["empresa"] in ("0001", "0002") else "No fiscal"
        cant = r["itemCantidad"] or 0
        precio_lista = r["itemPrecioUnitario"] or 0
        # Aplicar descuentos por artículo (Sigma da precio de lista + % de descuento)
        desc = (r["itemDescuento"] or 0) / 100
        desc_g = (r["itemDescuentoGlobal"] or 0) / 100
        desc_f = (r["itemDescuentoFinanciero"] or 0) / 100
        precio = precio_lista * (1 - desc) * (1 - desc_g) * (1 - desc_f)   # precio real de venta
        # % de descuento/oferta combinado (los 3 descuentos de Sigma juntos, como un solo %)
        oferta_pct = round((1 - (1 - desc) * (1 - desc_g) * (1 - desc_f)) * 100, 2)
        iva = iva_de(sku)
        precio_neto = precio                          # Sigma ya viene SIN IVA (y ahora con descuento)
        precio_con_iva = precio * (1 + iva / 100)     # para el % de rentabilidad

        # COSTO DE LAS NOTAS DE CREDITO. Ver MOTIVOS_SIN_RECUPERO arriba.
        #
        # Una NC lleva cantidad negativa, asi que el costo entra restando: es la
        # mercaderia que vuelve al deposito y se puede volver a vender. Cuando
        # NO vuelve -- o vuelve rota -- ese costo se perdio y no hay que
        # devolverlo.
        #
        # Ademas, cuando la mercaderia si se recupera hay que costearla al costo
        # con el que SALIO, no al del mes en que se emite la NC. La factura
        # F-FA9-00000802 salio en 2026-05 y su NC es de 2026-08; en el medio
        # LO03032 y LO03033 subieron 25 %, y esos $ 21.500 de diferencia
        # aparecian como margen de la nada y no se cancelaban nunca.
        # LA ANULACION TOTAL MANDA SOBRE EL MOTIVO.
        #
        # `motivoNc` lo elige una persona de una lista, y se equivoca. De las 8
        # notas cargadas como FALLADO, 6 son anulaciones enteras: la nota
        # revierte la factura completa, mismos SKUs y las unidades exactamente
        # opuestas. Nadie devuelve 249 unidades de 19 SKUs porque vinieron
        # falladas -- eso es una anulacion administrativa, casi siempre para
        # refacturar con otro comprobante.
        #
        # C-Z19-00003664 es justo eso: figura como FALLADO, anula las 196
        # unidades de F-Z19-00003629 y se refactura igual en F-B93-00001167. Al
        # creerle a la etiqueta, la nota quedaba con costo 0 y le metia
        # -$ 647.297 de perdida inventada al cliente.
        #
        # Cuando la nota anula la venta entera se revierte todo, costo incluido,
        # diga lo que diga el motivo: la factura y su nota se netean en cero. El
        # costo solo se pierde en la devolucion PARCIAL de fallados, que es la
        # unica donde la etiqueta describe lo que de verdad paso.
        anula_todo = es_anulacion_total(r)
        if mercaderia_perdida(r["motivoNc"]) and not anula_todo:
            costo = 0.0
        else:
            f_orig = to_date(r["fechaOriginal"])
            mc_costo = mes_comercial(f_orig) if f_orig is not None else mc
            costo = costo_de(sku, mc_costo)
        margen = None if costo is None else (precio_neto - costo) * cant
        filas.append({
            "canal": "Mayorista", "unidad": unidad, "tipo": tipo, "nro_orden": r["itemPedidoId"],
            "fecha": f, "mes_comercial": mc, "sku": sku, "producto": r["itemDescripcion"],
            "cantidad": cant, "precio_unitario": precio, "precio_neto": precio_neto,
            "iva_pct": iva, "costo_unitario": costo, "comision": 0,
            "total_linea": cant * precio_con_iva, "margen_total": margen,
            "proveedor": proveedor_de(sku),
            "marca": marca_de(sku),
            "cliente": r["clienteNombre"],
            "vendedor": vendedor_de(r["vendedor"]),
            "comprobante": f"{r['comprobanteTipo']}-{r['comprobanteCodigo']}-{r['comprobanteNumero']}" if r["comprobanteCodigo"] else None,
            "oferta_pct": oferta_pct,
            "empresa": empresa_de(r["empresa"]),
        })

    # --- 2) TIENDA NUBE ---
    #
    # QUE CUENTA COMO VENTA: que este PAGADA (`estado_pago = 'paid'`) y que el
    # pedido no este cancelado.
    #
    # No se usa el estado del pedido para decidirlo, que seria lo intuitivo: en
    # Tienda Nube las ventas NO pasan solas a `closed` -- hay que cerrarlas a
    # mano y nadie lo hace. De hecho en toda la historia de la tienda no hay ni
    # un solo pedido `closed`. Si el criterio fuera el estado, el tablero
    # mostraria cero para siempre.
    #
    # Los dos filtros hacen falta por separado: hay pedidos `paid` que despues
    # se cancelaron (12 pedidos, $403.571) y son plata que no entro, y hay
    # pedidos `open` con el pago anulado o devuelto (`voided`,
    # `partially_refunded`) que tampoco son venta.
    print("Procesando Tienda Nube...")

    # Medios de pago para los que no hay arancel cargado. Se junta durante el
    # recorrido y se avisa una sola vez al final, en vez de una linea por pedido.
    medios_sin_arancel = set()

    # `envio_costo_tienda` lo empezo a traer tiendanube.py despues, asi que puede
    # no estar todavia: el orquestador corre Tienda Nube cada 12 h pero modelo.py
    # en todas las corridas, y sin este chequeo la primera corrida despues de un
    # git pull moriria con `column "envio_costo_tienda" does not exist`.
    # Si falta, se sigue sin envio -- que es exactamente como estaba antes --
    # en vez de dejar el modelo entero sin actualizar.
    with engine.begin() as con:
        hay_envio = con.exec_driver_sql("""
            SELECT count(*) = 2 FROM information_schema.columns
            WHERE table_schema = 'bronze' AND table_name = 'tn_pedidos_items'
              AND column_name IN ('envio_costo_tienda', 'envio_cobrado')
        """).scalar()
    if not hay_envio:
        print("  (bronze.tn_pedidos_items todavia no trae envio: corre tiendanube.py)")

    cols_envio = (
        "envio_costo_tienda, envio_cobrado" if hay_envio
        else "NULL AS envio_costo_tienda, NULL AS envio_cobrado"
    )

    # `cupon` lo empezo a traer tiendanube.py despues que `descuento`, asi que
    # puede faltar por una corrida. Mismo criterio que con el envio: si no esta,
    # se sigue sin el codigo -- el descuento se aplica igual, que es lo que
    # mueve la plata -- en vez de dejar el canal entero sin actualizar.
    with engine.begin() as con:
        hay_cupon = con.exec_driver_sql("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'bronze' AND table_name = 'tn_pedidos_items'
                  AND column_name = 'cupon'
            )
        """).scalar()
    col_cupon = "cupon" if hay_cupon else "NULL AS cupon"

    # Mismo guard, para el medio de pago crudo con el que se cruza el arancel.
    with engine.begin() as con:
        hay_pago = con.exec_driver_sql("""
            SELECT count(*) = 2 FROM information_schema.columns
            WHERE table_schema = 'bronze' AND table_name = 'tn_pedidos_items'
              AND column_name IN ('gateway', 'metodo_pago')
        """).scalar()
    if not hay_pago:
        print("  (bronze.tn_pedidos_items todavia no trae el medio de pago: corre tiendanube.py)")
    cols_pago = (
        "gateway, metodo_pago" if hay_pago
        else "NULL AS gateway, NULL AS metodo_pago"
    )

    tn = pd.read_sql(f"""
        SELECT pedido_id, pedido_numero, fecha, sku, nombre, cantidad, precio,
               cliente_nombre, subtotal_pedido, descuento, total_pedido,
               {col_cupon}, {cols_pago}, {cols_envio}
        FROM bronze.tn_pedidos_items
        WHERE estado_pago = 'paid' AND estado <> 'cancelled'
          AND fecha >= '{piso_sql()}'
    """, engine)

    # EL FLETE DE TIENDA NUBE ES LA DIFERENCIA, NO EL BRUTO.
    #
    # La API manda dos importes de envio y no son lo mismo:
    #   envio_costo_tienda (shipping_cost_owner)    -> lo que le pagamos al correo
    #   envio_cobrado      (shipping_cost_customer) -> lo que nos reembolsa el cliente
    #
    # Lo que nos cuesta el flete es la RESTA. En el pedido #160 los dos dan
    # $19.222: el cliente pago el envio entero y a nosotros no nos costo nada.
    # En el #115 pagamos $16.461 y el cliente $0 -- ahi va "Descuento por envio
    # gratis" en la pantalla de Tienda Nube -- y esos $16.461 si son costo.
    #
    # Antes se restaba el bruto y listo. El reembolso del cliente no se sumaba
    # por ningun lado: vive en la cabecera del pedido (total = subtotal -
    # descuento + envio_cobrado) y no en ninguna linea de producto, asi que
    # nunca entraba a gold.fact_ventas. Resultado: se descontaba un flete que en
    # la mayoria de los pedidos ya estaba cobrado.
    #
    # No se topa en cero a proposito. Si algun dia el cliente paga mas envio del
    # que nos sale, esa diferencia es ganancia y corresponde que sume margen.
    # Hoy no pasa en ningun pedido.
    #
    # Los dos importes vienen CON IVA, asi que al restarlos el IVA se cancela y
    # la diferencia se pasa a neto igual que antes.
    #
    # POR QUE NETEAR A CERO ES LO CORRECTO Y NO NOS ESTAMOS PERDIENDO UN MARGEN.
    #
    # Al cliente se le cobra un recargo sobre el costo del envio para cubrir los
    # impuestos que nos facturan por ese pago. En el pedido #160 la etiqueta del
    # correo vale $17.522 y al cliente se le cobraron $19.222: esos $1.700 no son
    # ganancia, se van en impuestos. El resultado economico real es cero, que es
    # justo lo que da la resta.
    #
    # El valor de la etiqueta NO viaja en la API -- solo se ve en la pantalla de
    # Tienda Nube; `fulfillments` trae tracking y transportista, nada de plata --
    # asi que los dos importes de arriba son todo lo que hay, y alcanzan.
    #
    # LO QUE ESTO NO ARREGLA, dos cosas, las dos chicas:
    #
    # 1) El "Envio local Tucuman" viene en cero de los dos lados. Es reparto
    #    propio, y lo que cuesta (nafta, cadete) no esta en ningun campo de la
    #    API. Queda en cero en vez de inventarlo.
    #
    # 2) En los pedidos con ENVIO GRATIS absorbemos el precio cotizado, que ya
    #    trae el recargo adentro. Si lo que realmente sale de caja fuera solo la
    #    etiqueta, ahi estariamos cargando de mas. Sobre los 3 pedidos con costo
    #    real de la ventana el techo del error son $7.068 -- contra los $221.041
    #    que corrige netear, es ruido. Y puede estar bien como esta: cuando
    #    absorbemos el envio pagamos la etiqueta Y los impuestos.
    # El importe de envio viene en la CABECERA del pedido, repetido igual en
    # todas sus lineas. Se reparte entre ellas proporcional al valor de cada
    # una, para que un pedido de tres productos no le cargue el flete entero a
    # uno solo. (Aca no aplica la regla por umbral de Mercado Libre: Tienda Nube
    # no tiene envio gratis por precio de publicacion.)
    valor_pedido = {}
    for _, r in tn.iterrows():
        precio = float(r["precio"]) if r["precio"] else 0
        valor_pedido[r["pedido_id"]] = valor_pedido.get(r["pedido_id"], 0) + precio * (r["cantidad"] or 0)

    for _, r in tn.iterrows():
        f = to_date(r["fecha"])
        if f is None or f < CUTOFF:
            continue
        mc = mes_comercial(f)
        sku = r["sku"]
        cant = r["cantidad"] or 0
        precio = float(r["precio"]) if r["precio"] else 0
        iva = iva_de(sku)
        precio_neto = precio / (1 + iva / 100)
        costo = costo_de(sku, mc)

        valor_item = precio * cant
        total = valor_pedido.get(r["pedido_id"], 0)

        # EL DESCUENTO SE RESTA DE LA VENTA.
        #
        # `descuento` vive en la CABECERA del pedido, igual que el envio, asi que
        # no venia por ningun lado y las lineas quedaban a precio de lista. El
        # pedido 130 -- premio de un sorteo, cupon GANADOR100K de $100.000 --
        # figuraba como una venta de $56.973 cuando en realidad entro $0.
        #
        # El cupon puede ser mas grande que la mercaderia y comerse tambien el
        # envio (`includes_shipping`), asi que se parte en dos:
        #   desc_producto -> lo que se come de la venta, topeado al subtotal
        #   desc_envio    -> el sobrante, que es envio que el cliente NO pago
        #
        # No se mira el flag `includes_shipping` sino los importes: si el
        # descuento supera al subtotal, la diferencia solo puede haber salido del
        # envio. Es aritmetica y no depende de que ML/TN mantenga el flag.
        subtotal = importe(r["subtotal_pedido"])
        descuento = importe(r["descuento"])
        desc_producto = min(descuento, subtotal)
        desc_envio = min(max(descuento - subtotal, 0.0), importe(r["envio_cobrado"]))

        # La parte del descuento que le toca a esta linea, proporcional a lo que
        # vale. Viene CON IVA, como el precio, asi que se pasa a neto con el IVA
        # de ESTA linea y no con un 21% fijo: un pedido puede mezclar alicuotas.
        desc_item = desc_producto * (valor_item / total) if total > 0 else 0.0
        precio_real = precio - desc_item / cant if cant else precio
        # Un descuento del 100% deja el precio en 1e-13 y no en cero: el reparto
        # proporcional divide y multiplica por el mismo total. Sin este corte, un
        # pedido enteramente bonificado -- el 130 -- sumaba -0,0000000001 y el
        # tablero mostraba "-$ 0", con el signo menos. Mismo bicho que llevo a
        # redondear los importes en las consultas.
        if abs(precio_real) < 1e-9:
            precio_real = 0.0
        precio_neto_real = precio_real / (1 + iva / 100)

        # Lo que nos cuesta el flete: lo que pagamos menos lo que nos reembolsa
        # el cliente DE VERDAD. Ver la nota larga arriba.
        #
        # El `- desc_envio` es lo que evita que un cupon que cubre el envio lo
        # haga desaparecer: en el pedido 130 el envio figura cobrado en $39.574,
        # pero lo pago el cupon, no el cliente. Sin esto neteaba a cero y ese
        # flete -- que salio de nuestro bolsillo -- no lo veia nadie.
        cobrado_real = importe(r["envio_cobrado"]) - desc_envio
        envio_bruto = importe(r["envio_costo_tienda"]) - cobrado_real
        envio_item = (envio_bruto / 1.21) * (valor_item / total) if total > 0 else 0

        # COMISION DE LA PASARELA.
        #
        # Se cobra sobre lo que el cliente PAGO DE VERDAD -- `total_pedido`, que
        # ya viene con el descuento restado y el envio sumado -- y no sobre el
        # valor de la mercaderia: la pasarela cobra por mover plata, y le da
        # igual si esa plata era producto o flete. En el pedido 130 el total fue
        # $0, asi que la comision da 0 sola, sin ningun caso especial.
        #
        # Se prorratea entre las lineas igual que el envio y el descuento, y se
        # guarda POR UNIDAD porque asi la usa el resto del modelo: Mercado Libre
        # guarda `comision` por unidad y el margen la multiplica por la cantidad.
        pct = arancel_de(r["gateway"], r["metodo_pago"], f)
        if pct is None:
            # Medio de pago sin arancel cargado. Queda en 0 -- que es como venia
            # el canal -- pero se avisa: si no, una pasarela nueva entraria al
            # tablero como gratis y nadie se enteraria.
            medios_sin_arancel.add((r["gateway"], r["metodo_pago"]))
            comision_item = 0.0
        else:
            cobrado_pedido = importe(r["total_pedido"])
            comision_item = (cobrado_pedido * pct / 100) * (valor_item / total) if total > 0 else 0.0
        comision_unidad = comision_item / cant if cant else 0.0

        margen = (
            None if costo is None
            else (precio_neto_real - costo - comision_unidad) * cant - envio_item
        )
        filas.append({
            "canal": "Tienda Nube", "unidad": "Quo", "tipo": "Fiscal",
            "nro_orden": r["pedido_numero"],
            "fecha": f, "mes_comercial": mc, "sku": sku, "producto": r["nombre"],
            # El precio que se guarda es el EFECTIVO, ya con el descuento
            # aplicado: asi la venta del tablero es la que se facturo de verdad
            # y ninguna consulta tiene que acordarse de restar nada. Cuanto se
            # resigno queda en `descuento`, y por que, en `cupon`.
            "cantidad": cant, "precio_unitario": precio_real, "precio_neto": precio_neto_real,
            "iva_pct": iva, "costo_unitario": costo, "comision": comision_unidad,
            "envio": round(envio_item, 2),
            "descuento": round(desc_item, 2),
            "cupon": r["cupon"] if isinstance(r["cupon"], str) else None,
            "total_linea": cant * precio_real, "margen_total": margen,
            "proveedor": proveedor_de(sku),
            "marca": marca_de(sku),
            "cliente": r["cliente_nombre"],
        })

    if medios_sin_arancel:
        detalle = ", ".join(f"{g or '?'}/{m or '?'}" for g, m in sorted(
            medios_sin_arancel, key=lambda x: (x[0] or "", x[1] or "")))
        print(f"  OJO: sin arancel cargado, comision en 0 para: {detalle}")
        print("  Se agregan en bronze.comisiones_pasarela (ver comisiones_pasarela.sql)")

# --- 3) MERCADO LIBRE (en lotes; reparte envio proporcional al precio) ---
    #
    # QUE CUENTA COMO VENTA: `paid` y `partially_refunded`.
    #
    # Las CANCELADAS quedan afuera y eso no se discute: no son venta. Son ~5.000
    # ordenes ($14,2 M solo en agosto) y se miran aparte, en su propio panel del
    # tablero, que las lee directo de bronze.
    #
    # Las PARCIALMENTE DEVUELTAS si entran, y antes no entraban. Son ventas
    # reales donde el cliente devolvio una parte y se quedo con el resto: dejarlas
    # afuera enteras borraba plata que si entro (9 ordenes y $220.445 en agosto).
    #
    # OJO CON LO QUE ESTO NO HACE: se cuentan por el importe COMPLETO, sin
    # descontar lo devuelto, porque la API no informa el monto de la devolucion
    # en la orden. O sea que sobreestiman un poco. Es menos malo que el error
    # anterior -- contarlas en cero -- pero no es exacto, y el tablero lo aclara.
    print("Procesando Mercado Libre (puede tardar)...")

    # shipping_id -> [(indice en `filas`, precio unitario, valor de la linea)]
    #
    # Se acumula mientras se recorren los lotes y se reparte despues del while:
    # un paquete se parte en varias ordenes que no tienen por que caer en el
    # mismo lote, asi que dentro del loop todavia no se sabe con quien viajo
    # cada producto.
    lineas_por_envio = {}

    LOTE = 5000
    offset = 0
    while True:
        ml = pd.read_sql(f"""
            SELECT id, date_created, order_items, "buyer.nickname"
            FROM bronze.ml_ventas
            WHERE status IN ('paid', 'partially_refunded')
              -- SIN left(). Ver la nota de arriba de piso_sql(): envolver la
              -- columna en una funcion tira el indice a la basura, y esta
              -- consulta lee `order_items`, que es el JSON entero de la orden.
              AND date_created >= '{piso_sql()}'
            ORDER BY id
            LIMIT {LOTE} OFFSET {offset}
        """, engine)
        if ml.empty:
            break
        print(f"  ML lote desde {offset}: {len(ml)} ordenes")

        for _, r in ml.iterrows():
            f = to_date(r["date_created"])
            if f is None or f < CUTOFF:
                continue
            mc = mes_comercial(f)
            items = json.loads(r["order_items"]) if isinstance(r["order_items"], str) else r["order_items"]
            items = items or []

            # A que envio pertenece esta orden. El costo todavia no se toca: se
            # reparte al final, cuando ya estan juntas las lineas de todas las
            # ordenes del paquete.
            ship = envio_de_orden.get(str(r["id"]))

            for it in items:
                sku = (it.get("item") or {}).get("seller_sku")
                cant = it.get("quantity") or 0
                precio = it.get("unit_price") or 0          # ML viene CON IVA
                # OJO: sale_fee es la comision de UNA unidad, no la del item entero.
                # Se guarda por unidad (igual que precio_neto y costo_unitario), asi
                # que el que la use tiene que multiplicarla por la cantidad.
                comision_con_iva = it.get("sale_fee") or 0
                comision = comision_con_iva / 1.21
                iva = iva_de(sku)
                precio_neto = precio / (1 + iva / 100)
                costo = costo_de(sku, mc)

                # El envio queda en cero por ahora y el margen sin el: los dos se
                # completan abajo, una vez repartido el flete del paquete.
                #
                # La comision SI va multiplicada por la cantidad: es por unidad.
                # Sin el * cant, una linea de 10 unidades descontaba una sola
                # comision y la ganancia de Mercado Libre quedaba inflada
                # (21,5 M de mas sobre el historico, un 13%).
                margen = (
                    None if costo is None
                    else (precio_neto - costo - comision) * cant
                )
                if ship is not None:
                    lineas_por_envio.setdefault(ship, []).append(
                        (len(filas), precio, precio * cant)
                    )
                filas.append({
                    "canal": "Mercado Libre", "unidad": "Quo", "tipo": "Fiscal", "nro_orden": r["id"],
                    "fecha": f, "mes_comercial": mc, "sku": sku,
                    "producto": (it.get("item") or {}).get("title"),
                    "cantidad": cant, "precio_unitario": precio, "precio_neto": precio_neto,
                    "iva_pct": iva, "costo_unitario": costo, "comision": comision,
                    "envio": 0.0,
                    "total_linea": cant * precio, "margen_total": margen,
                    "proveedor": proveedor_de(sku),
                    "marca": marca_de(sku),
                    "cliente": r["buyer.nickname"],
                })

        offset += LOTE

    # --- Reparto del envio de Mercado Libre ---
    #
    # Recien aca, con todas las lineas de todas las ordenes ya juntas por envio,
    # se puede aplicar el criterio: mira a todo el paquete para saber que
    # productos dispararon el flete. Ver repartir_envio().
    #
    # El costo viene CON IVA de la API de ML y se pasa a neto para poder
    # restarlo de una venta neta.
    #
    # OJO: se reparte el 100% del flete entre las lineas que TENEMOS. Si una
    # orden del paquete quedo afuera de la ventana o del criterio de venta, su
    # parte la absorben las demas en vez de perderse. El total del canal cierra
    # siempre; lo que puede correrse es a que linea le toca.
    repartidos = 0
    for ship, lineas in lineas_por_envio.items():
        costo_bruto = costo_de_envio.get(ship)
        # El pd.isna() no es de mas: costo_envio sale de un DataFrame, asi que un
        # envio sin costo llega como NaN y NO como None. NaN es truthy y pasaria
        # el `not`, dejando el envio y el margen de todo el paquete en NaN.
        if costo_bruto is None or pd.isna(costo_bruto) or not costo_bruto:
            continue
        for idx, parte in repartir_envio(costo_bruto / 1.21, lineas).items():
            fila = filas[idx]
            fila["envio"] = round(parte, 2)
            if fila["margen_total"] is not None:
                fila["margen_total"] -= parte
            repartidos += 1
    print(f"  Envio de ML repartido en {repartidos} lineas de {len(lineas_por_envio)} paquetes")


    # El texto sale de CUTOFF y no de WINDOW_DAYS, que es una constante y no
    # cambia con --todo ni con --dias. Antes decia "ventana de 7 dias" incluso
    # corriendo --todo, que reconstruye cuatro meses: quien leia esa linea se
    # quedaba pensando que el --todo no habia funcionado.
    dias = (date.today() - CUTOFF).days
    print(f"Total de lineas (desde {CUTOFF}, {dias} dias): {len(filas)}")
    df = pd.DataFrame(filas)

    with engine.begin() as con:
        con.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS gold;")

        # Columnas agregadas despues de que la tabla ya existia. Sin esto el
        # `to_sql(if_exists="append")` muere con `column "descuento" of relation
        # "fact_ventas" does not exist` en la primera corrida tras el deploy, que
        # es exactamente lo que paso con envio_costo_tienda en bronze.
        #
        # El IF NOT EXISTS lo hace idempotente: corre en todas las corridas y no
        # hace nada cuando ya estan.
        if con.exec_driver_sql("""
            SELECT EXISTS (SELECT 1 FROM information_schema.tables
                           WHERE table_schema='gold' AND table_name='fact_ventas')
        """).scalar():
            con.exec_driver_sql(
                'ALTER TABLE gold.fact_ventas '
                '  ADD COLUMN IF NOT EXISTS descuento double precision, '
                '  ADD COLUMN IF NOT EXISTS cupon text'
            )

    # EL BORRADO Y LA INSERCION VAN EN LA MISMA TRANSACCION.
    #
    # Antes estaban en dos: se confirmaba el DELETE y recien despues se
    # insertaba. Entre una cosa y la otra, gold.fact_ventas -- que es la tabla
    # que lee el tablero EN VIVO -- se quedaba sin los ultimos 7 dias. Quien
    # entrara justo en ese momento veia la semana en cero y lo leia como que no
    # se vendio nada.
    #
    # Ahora quien consulta sigue viendo la version anterior COMPLETA hasta que
    # la nueva esta entera. Nunca hay un momento con el agujero a la vista.
    #
    # Ojo: `df.to_sql` recibe `con` y no `engine`. Con `engine` abriria su
    # propia transaccion y volveriamos a tener el mismo problema.
    with engine.begin() as con:
        existe = con.exec_driver_sql("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'gold' AND table_name = 'fact_ventas'
            )
        """).scalar()

        if existe:
            # Solo se borra (y se va a re-insertar) la ventana movil. Todo lo anterior
            # a CUTOFF en gold.fact_ventas queda intacto -- no se vuelve a tocar ni reprocesar.
            resultado = con.exec_driver_sql(
                "DELETE FROM gold.fact_ventas WHERE fecha >= %(cutoff)s",
                {"cutoff": CUTOFF}
            )
            print(f"  Filas viejas borradas dentro de la ventana (se van a reemplazar): {resultado.rowcount}")

        df.to_sql("fact_ventas", con, schema="gold", if_exists="append", index=False)

    print("Guardado: gold.fact_ventas")


def main():
    parser = argparse.ArgumentParser(
        description="Arma gold.fact_ventas desde las tablas de bronze."
    )
    parser.add_argument(
        "--dias", type=int, default=WINDOW_DAYS,
        help=f"Cuantos dias hacia atras reconstruir (por defecto {WINDOW_DAYS})",
    )
    parser.add_argument(
        "--todo", action="store_true",
        help=f"Reconstruye TODO desde {FECHA_CORTE}. Tarda, pero es la unica forma "
             f"de que un dato que llego tarde a bronze (por ejemplo el costo de "
             f"envio de meses viejos) entre a gold.",
    )
    args = parser.parse_args()

    # La ventana movil es lo correcto para la corrida de todos los dias: reprocesar
    # cuatro meses cada hora seria tirar tiempo. Pero deja un agujero: si una tabla
    # de bronze se rellena hacia atras -- que es exactamente lo que pasa cuando
    # ml_envios.py se pone al dia despues de meses sin correr -- ese dato nunca
    # entra a gold, porque gold ya no vuelve a mirar esas fechas. Para eso esta
    # --todo, que se corre a mano una vez y despues no se toca mas.
    global CUTOFF
    if args.todo:
        CUTOFF = FECHA_CORTE
    else:
        CUTOFF = max(FECHA_CORTE, date.today() - timedelta(days=args.dias))

    print(f"Ventana a reconstruir: desde {CUTOFF} (hoy es {date.today()})")
    construir_fact_ventas()
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()