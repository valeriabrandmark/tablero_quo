import os
import pandas as pd
from dotenv import load_dotenv
from conexion import crear_engine

load_dotenv()

engine = crear_engine()


def calcular_clave_fila(row):
    """Misma logica que gold.reporte_logistica: SEBASTIAN parte por Quo/Noa,
       el resto consolida por preparacion_id solo."""
    if pd.isna(row["preparacion_id"]):
        return None
    prep_id = int(row["preparacion_id"])
    transporte = (row["transporte"] or "").upper()
    if "SEBASTIAN" in transporte:
        sufijo = "Quo" if row["unidad"] == "Quo" else "Noa"
        return f"{prep_id}-{sufijo}"
    return str(prep_id)


def detectar_refacturaciones(fact, prep, flete_map, clave_de_pedido):
    """Encuentra ventas anuladas que se volvieron a facturar con OTRO pedido.

    QUE PROBLEMA RESUELVE

    Por un error de carga se anula una venta (factura + nota de credito) y se
    la vuelve a facturar con un pedido nuevo. La mercaderia viajo UNA sola vez,
    bajo la preparacion del pedido viejo, asi que el flete queda pegado a la
    orden anulada -- que no tiene venta -- y la orden que si quedo viva no
    tiene preparacion y se come una estimacion del 5 % que no existe.

    EL CRITERIO, Y POR QUE ES SEGURO

    Lo que hace confiable el apareo no es que coincidan los SKU, sino que la
    orden origen este ANULADA ENTERA: unidades netas cero. Eso es objetivo y
    poco frecuente (11 veces en cuatro meses). Una venta repetida de verdad no
    lo cumple, porque la primera no se anulo.

    Sobre esa base se pide ademas: mismo cliente, misma firma de SKU y
    cantidades, y que el destino NO tenga preparacion propia.

    Medido sobre la base al 25/08/2026: 6 pares, todos uno a uno, ninguno
    ambiguo. Los tres con flete cargado movian $ 207.312 que hoy se descartan.

    DOS FRENOS

    - Si una anulada matchea mas de una candidata, no se toca nada. Mover plata
      a la orden equivocada es peor que dejar la estimacion.
    - Si la preparacion de la anulada no tiene flete cargado, tampoco se aparea:
      heredar un cero convertiria una estimacion razonable en un cero falso.
    """
    def firma(grupo):
        """SKU y cantidades vendidas de una orden, como texto comparable.
           Solo las positivas: las de la nota de credito son el reverso."""
        pos = grupo[grupo["cantidad"] > 0].groupby("sku")["cantidad"].sum()
        return "|".join(f"{sku}:{cant}" for sku, cant in sorted(pos.items()))

    resumen = {}
    for orden, grupo in fact.groupby("nro_orden_str"):
        resumen[orden] = {
            "netas": grupo["cantidad"].fillna(0).sum(),
            "firma": firma(grupo),
            "cliente": (grupo["cliente"].dropna().iloc[0]
                        if grupo["cliente"].notna().any() else None),
        }

    con_preparacion = set(prep["pedido_codigo"].astype(str))

    anuladas = {o: d for o, d in resumen.items()
                if d["netas"] == 0 and o in con_preparacion and d["firma"]}
    vivas = {o: d for o, d in resumen.items()
             if d["netas"] > 0 and o not in con_preparacion and d["firma"]}

    pares, ambiguas = [], []
    for anulada, da in anuladas.items():
        candidatas = [v for v, dv in vivas.items()
                      if dv["cliente"] == da["cliente"] and dv["firma"] == da["firma"]]
        if len(candidatas) > 1:
            ambiguas.append((anulada, candidatas))
            continue
        if not candidatas:
            continue
        # Sin flete cargado no hay nada que heredar.
        claves = clave_de_pedido(anulada)
        if not any(c in flete_map for c in claves):
            continue
        pares.append((anulada, candidatas[0], claves))

    if ambiguas:
        print("\n  OJO: estas ventas anuladas coinciden con MAS DE UNA orden "
              "viva, asi que no se les mueve el flete:")
        for anulada, cands in ambiguas:
            print(f"    {anulada} -> {', '.join(cands)}")

    return pares


def construir_fact_ventas_flete():
    print("=== Construyendo gold.fact_ventas_flete ===")

    # --- 1) Traer lineas de la distribuidora (Mayorista) ---
    print("Leyendo fact_ventas (Mayorista)...")
    fact = pd.read_sql("""
        SELECT nro_orden, sku, unidad, cantidad, precio_neto, cliente, comprobante
        FROM gold.fact_ventas
        WHERE canal = 'Mayorista'
          AND unidad IN ('Quo', 'Noa')
          AND nro_orden IS NOT NULL
    """, engine)
    print(f"  Lineas de distri: {len(fact)}")

    # NOTAS DE CREDITO DONDE EL DESPACHO SI OCURRIO: el flete NO se revierte.
    #
    # OJO: esta lista NO es la misma que MOTIVOS_SIN_RECUPERO en modelo.py, y
    # la diferencia es a proposito. Alla se pregunta si vuelve el COSTO; aca,
    # si vuelve el FLETE. Son dos preguntas distintas:
    #
    #   INCOBRABLE  -> la mercaderia se recupera (el costo vuelve), pero el
    #                  camion salio igual: el flete se pago y no vuelve.
    #   FALLADO     -> ni una cosa ni la otra.
    #
    # El resto de los motivos (error de carga, cancelacion de pedido, error de
    # logistica) son ventas que no ocurrieron o que las cubre el transportista,
    # y ahi el flete se revierte como siempre.
    #
    # Antes esas notas se llevaban un 5 % NEGATIVO que borraba el flete de la
    # factura original, y encima, como el margen ajustado RESTA el flete,
    # restar un negativo SUMABA al margen. Asi C-CA9-00000114 mostraba
    # +$ 92.329 de margen ajustado sobre una venta perdida de $ 377.817.
    #
    # Estas lineas quedan neutras: no aportan volumen, no entran en la base del
    # 5 % y no cuentan para el neteo de unidades. La factura conserva su flete,
    # que es el que de verdad se pago. En una devolucion parcial eso deja el
    # flete entero sobre las unidades que quedaron, que es lo correcto: se pago
    # por despachar todas.
    sin_recupero = set(pd.read_sql("""
        SELECT DISTINCT "comprobanteTipo" || '-' || "comprobanteCodigo"
               || '-' || "comprobanteNumero" AS comprobante
        FROM bronze.sigma_ventas
        WHERE "comprobanteTipo" = 'C'
          AND (upper(coalesce("motivoNc", '')) LIKE 'INCOBRABLE%%'
            OR upper(coalesce("motivoNc", '')) LIKE 'FALLADO%%')
    """, engine)["comprobante"])
    fact["_sin_recupero"] = fact["comprobante"].isin(sin_recupero)
    print(f"  Lineas de NC sin recupero (flete neutro): {int(fact['_sin_recupero'].sum())}")

    # Identidad de la linea ANTES de cualquier merge. Con esto se puede saber
    # despues si dos renglones son la misma linea repetida por un join o dos
    # lineas de verdad. Ver el drop_duplicates del paso 4.
    fact["_fila_id"] = range(len(fact))

    # --- 1b) Traer volumetria por SKU (litros por unidad, cargados en Sigma) ---
    print("Leyendo volumetria (litrosUnitarios) de sigma_articulos...")
    vol = pd.read_sql('SELECT id AS sku, "litrosUnitarios" FROM bronze.sigma_articulos', engine)
    litros_por_sku = dict(zip(vol["sku"].astype(str), vol["litrosUnitarios"]))

    def litros_de(sku):
        v = litros_por_sku.get(str(sku))
        return float(v) if v is not None and v != 0 else None

    # --- 2) Traer preparaciones (para saber preparacion_id y transporte por pedido) ---
    print("Leyendo digip_preparaciones...")
    prep = pd.read_sql("""
        SELECT pedido_codigo, preparacion_id, transporte
        FROM bronze.digip_preparaciones
    """, engine)

    # --- 3) Traer fletes reales cargados ---
    print("Leyendo bronze.fletes...")
    fletes = pd.read_sql("""
        SELECT clave_fila, neto_cobrado_transporte
        FROM bronze.fletes
        WHERE neto_cobrado_transporte IS NOT NULL
    """, engine)
    flete_map = dict(zip(fletes["clave_fila"], fletes["neto_cobrado_transporte"]))
    print(f"  Claves con flete real cargado: {len(flete_map)}")

    fact["nro_orden_str"] = fact["nro_orden"].astype("Int64").astype(str)

    # --- 3b) Refacturaciones: la orden nueva hereda la preparacion de la vieja ---
    #
    # Se resuelve agregando filas a `prep`, no tocando el prorrateo: la orden
    # refacturada pasa a tener la misma preparacion que la anulada, y de ahi
    # para abajo todo funciona igual que siempre.
    #
    # Las dos ordenes quedan colgadas de la misma clave, y eso esta bien: la
    # anulada aporta volumen neto CERO (factura + nota de credito se cancelan),
    # asi que el prorrateo por volumen le da el 100 % del flete a la que quedo
    # viva. Y la anulada termina en cero por la regla de unidades netas del
    # paso 7.
    def claves_del_pedido(pedido):
        filas = prep[prep["pedido_codigo"].astype(str) == str(pedido)]
        claves = set()
        for _, fila in filas.iterrows():
            unidad_pedido = fact.loc[fact["nro_orden_str"] == str(pedido), "unidad"]
            for unidad in (unidad_pedido.unique() if len(unidad_pedido) else [None]):
                clave = calcular_clave_fila({
                    "preparacion_id": fila["preparacion_id"],
                    "transporte": fila["transporte"],
                    "unidad": unidad,
                })
                if clave is not None:
                    claves.add(clave)
        return claves

    pares = detectar_refacturaciones(fact, prep, flete_map, claves_del_pedido)
    if pares:
        print(f"\n  Ventas anuladas y refacturadas con otro pedido: {len(pares)}")
        heredadas = []
        for anulada, refacturada, claves in pares:
            monto = sum(float(flete_map[c]) for c in claves if c in flete_map)
            print(f"    {anulada} -> {refacturada}: hereda ${monto:,.2f} "
                  f"({', '.join(sorted(claves))})")
            copia = prep[prep["pedido_codigo"].astype(str) == str(anulada)].copy()
            copia["pedido_codigo"] = str(refacturada)
            heredadas.append(copia)
        prep = pd.concat([prep] + heredadas, ignore_index=True)

    # --- 4) Unir fact con preparaciones (nro_orden = pedido_codigo) ---
    merged = fact.merge(
        prep, left_on="nro_orden_str", right_on="pedido_codigo", how="left"
    )

    # EL MERGE PUEDE MULTIPLICAR LAS LINEAS, Y ESO INFLA EL FLETE.
    #
    # `prep` tiene una fila por (preparacion_id, pedido_codigo). Si el mismo par
    # aparece dos veces, este merge devuelve la linea de venta DUPLICADA, y de
    # ahi para abajo todo la cuenta dos veces.
    #
    # No es hipotetico: el 22/08 quedaron 741 lineas con un flete estimado del
    # 50 % de la venta en vez del 5 % -- $ 8,53 M cargados contra $ 853 mil que
    # correspondian, $ 7,68 M de flete fantasma sobre $ 17 M de venta. El origen
    # fueron filas repetidas en bronze.digip_preparaciones, que a su vez salieron
    # del DELETE que se tragaba el timeout e insertaba igual (el mismo bug que
    # duplico 2.548 ordenes de ML el 21/08; arreglado en digip_preparaciones.py).
    #
    # Aquel arreglo evita que se vuelvan a generar duplicados. Este evita que un
    # duplicado, venga de donde venga, se convierta en plata mal contada.
    #
    # Se deduplica por (linea, preparacion): si una linea entra de verdad en dos
    # preparaciones distintas -- envio partido -- las dos filas se conservan.
    antes = len(merged)
    merged = merged.drop_duplicates(subset=["_fila_id", "preparacion_id"])
    if len(merged) < antes:
        print(f"  Aviso: {antes - len(merged)} filas repetidas por preparaciones "
              f"duplicadas. Se descartan (si no, el flete se contaria de mas).")

    # Aviso informativo: un mismo (nro_orden, sku) puede repetirse porque Sigma
    # cargo el mismo articulo en 2 renglones de factura (normal en el ERP).
    # Confirmado que cada pedido va a UNA sola preparacion (sin envios partidos),
    # asi que no hace falta repartir nada: cada linea prorratea normal y al final
    # se suman las que comparten nro_orden+sku en una sola fila (paso 7).
    dup = merged.groupby(["nro_orden", "sku"]).size()
    duplicados = dup[dup > 1]
    if len(duplicados) > 0:
        print(f"  Info: {len(duplicados)} combinaciones nro_orden+sku con mas de un renglon "
              f"de factura (normal). Se suman en una sola fila al final.")

    # --- 5) Calcular clave_fila por linea (misma logica que la vista) ---
    merged["clave_fila"] = merged.apply(calcular_clave_fila, axis=1)

    # Base de prorrateo: litros x cantidad (volumen real de la linea).
    # Si el SKU no tiene litrosUnitarios cargado (raro, ~0.3% de los casos),
    # usamos cantidad sola como respaldo para no perder la linea del calculo.
    def volumen_de_fila(row):
        # Estas NC no devuelven mercaderia vendible: aportan volumen 0 para no
        # cancelar el de la factura dentro de la misma preparacion.
        if row.get("_sin_recupero"):
            return 0
        litros = litros_de(row["sku"])
        if litros is not None:
            return litros * (row["cantidad"] or 0)
        return row["cantidad"] or 0  # fallback: sin dato de volumen, usar unidades

    merged["volumen_linea"] = merged.apply(volumen_de_fila, axis=1)
    merged["flete_total_clave"] = merged["clave_fila"].map(flete_map)

    # Volumen total de la preparacion, para prorratear proporcionalmente
    merged["volumen_total_clave"] = merged.groupby("clave_fila")["volumen_linea"].transform("sum")

    # --- 6) Prorrateo por linea (real donde hay dato, estimado 5% donde no) ---
    lineas = []
    claves_sin_volumen = set()
    for _, row in merged.iterrows():
        clave = row["clave_fila"]
        flete_total = row["flete_total_clave"]
        volumen_total_clave = row["volumen_total_clave"]

        # "Real" quiere decir QUE HAY UN DATO CARGADO para esa preparacion, no
        # que se haya podido repartir. Antes tambien exigia volumen > 0, y por
        # eso una preparacion con la venta anulada -- factura + NC, volumen neto
        # cero -- caia a la estimacion del 5 % aunque el flete estuviera cargado
        # a mano. Son dos preguntas distintas y ahora estan separadas.
        tiene_dato_cargado = pd.notna(clave) and pd.notna(flete_total)
        hay_volumen = bool(volumen_total_clave) and volumen_total_clave > 0

        if tiene_dato_cargado and hay_volumen:
            flete_linea_calc = flete_total * (row["volumen_linea"] / volumen_total_clave)
        elif tiene_dato_cargado:
            claves_sin_volumen.add(clave)
            # Hay dato pero no hay sobre que repartirlo. Si la venta se anulo,
            # imputarle costo de transporte a una linea que quedo en $ 0 solo
            # ensucia el margen. Se avisa mas abajo con el monto, para que no
            # desaparezca en silencio.
            flete_linea_calc = 0.0
        else:
            flete_linea_calc = None  # se recalcula como estimado al reunir, si hace falta

        linea_real = tiene_dato_cargado

        lineas.append({
            "_fila_id": row["_fila_id"],
            "nro_orden": row["nro_orden_str"],
            "sku": row["sku"],
            "cantidad": row["cantidad"],
            "precio_neto": row["precio_neto"],
            "clave_fila": clave if pd.notna(clave) else None,
            "flete_linea_real": flete_linea_calc,
            "linea_real": linea_real,
            "_sin_recupero": bool(row.get("_sin_recupero")),
        })

    df_lineas = pd.DataFrame(lineas)

    # --- 7) Reunir en UNA fila por linea (nro_orden + sku) ---
    resultado = []
    for (nro_orden, sku), grupo in df_lineas.groupby(["nro_orden", "sku"], dropna=False):
        todas_reales = grupo["linea_real"].all()
        if todas_reales:
            flete_final = grupo["flete_linea_real"].sum()
            tiene_real = True
        else:
            # si algun renglon no tuvo flete real, la combinacion entera cae a estimado
            # (evita mezclar "un poco real + un poco estimado" en la misma linea)
            # EL 5 % VA SOBRE LA VENTA NETA DE LA LINEA, sumada renglon por
            # renglon. Antes era `precio_neto.iloc[0] * cantidad.sum()`, que
            # toma el precio del PRIMER renglon y lo multiplica por la cantidad
            # de TODOS. Eso da bien solo si todos los renglones valen lo mismo;
            # con un renglon bonificado a $ 0, o con la linea repetida por el
            # merge, se dispara.
            #
            # `drop_duplicates` por las dudas: si algo volviera a repetir la
            # linea, el 5 % se calcula igual una sola vez.
            unicas = grupo.drop_duplicates(subset=["_fila_id"])
            # Sin esas NC: si entraran, la base del 5 % se netea contra la
            # factura y el estimado sale 0 o negativo.
            unicas = unicas[~unicas["_sin_recupero"]]
            venta_linea = (
                unicas["precio_neto"].fillna(0) * unicas["cantidad"].fillna(0)
            ).sum()
            flete_final = venta_linea * 0.05
            tiene_real = False

        # Venta anulada: la factura y su nota de credito se cancelan y no queda
        # nada vendido. El flete tiene que ser 0, no importa por que camino se
        # haya calculado arriba. Si no, la linea queda con costo de transporte
        # sobre una venta de $ 0 y la rentabilidad se va a menos infinito.
        #
        # Las devoluciones PARCIALES no entran aca a proposito: ahi si quedaron
        # unidades vendidas, y como el prorrateo usa la cantidad con signo, el
        # flete que les toca ya sale proporcional a lo que quedo. Una devolucion
        # parcial tiene que mostrar rentabilidad baja, no cero.
        netas = grupo.drop_duplicates(subset=["_fila_id"])
        # Idem: estas NC no anulan el despacho -- la mercaderia salio igual. Si
        # contaran aca, la factura perderia el flete que ya se pago.
        netas = netas[~netas["_sin_recupero"]]
        unidades_netas = netas["cantidad"].fillna(0).sum()
        if unidades_netas == 0:
            flete_final = 0.0

        claves_validas = [c for c in grupo["clave_fila"] if pd.notna(c)]
        clave_repr = ", ".join(sorted(set(claves_validas))) if claves_validas else None
        resultado.append({
            "nro_orden": nro_orden,
            "sku": sku,
            "clave_fila": clave_repr,
            "flete_prorrateado": round(float(flete_final), 2),
            "tiene_flete_real": bool(tiene_real),
            # Cuantos renglones de fact_ventas se juntaron en esta fila. Es el
            # divisor que necesita el tablero: alla el join va por
            # (nro_orden, sku), asi que una linea facturada en dos comprobantes
            # trae el flete DOS veces y lo suma de mas. Con este numero cada
            # renglon se lleva su parte y el total cierra.
            "lineas_venta": int(grupo["_fila_id"].nunique()),
        })

    df_resultado = pd.DataFrame(resultado)
    print(f"\nTotal lineas procesadas: {len(df_resultado)}")
    print(f"  Con flete real prorrateado: {int(df_resultado['tiene_flete_real'].sum())}")
    print(f"  Con estimacion 5%: {int((~df_resultado['tiene_flete_real']).sum())}")
    # Control: la suma de los divisores tiene que dar los renglones leidos al
    # principio. Si no da, alguna linea quedo sin representar y el flete que
    # muestre el tablero va a estar corto.
    representados = int(df_resultado["lineas_venta"].sum())
    print(f"  Renglones de venta representados: {representados} de {len(fact)}")
    anuladas = int((df_resultado["flete_prorrateado"] == 0).sum())
    print(f"  Lineas con flete 0 (anuladas o sin costo): {anuladas}")

    # Plata realmente pagada al transportista que no se puede imputar a ninguna
    # linea, porque la venta de esa preparacion se anulo entera. No entra al
    # margen de nadie, pero salio de la caja: si no se avisa, desaparece.
    sin_donde_imputar = {
        clave: monto for clave, monto in flete_map.items()
        if monto and float(monto) != 0
        and clave in claves_sin_volumen
    }
    if sin_donde_imputar:
        total = sum(float(m) for m in sin_donde_imputar.values())
        print(f"\n  OJO: ${total:,.2f} de flete cargado a mano no se imputa a "
              f"ninguna linea, porque esas ventas estan anuladas enteras:")
        for clave, monto in sorted(sin_donde_imputar.items(),
                                   key=lambda kv: -float(kv[1])):
            print(f"    {clave}: ${float(monto):,.2f}")
    if representados != len(fact):
        print(f"  OJO: faltan {len(fact) - representados} renglones sin flete asignado")

    # --- 8) Guardar en gold.fact_ventas_flete (tabla aparte, no toca fact_ventas) ---
    with engine.begin() as con:
        con.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS gold.fact_ventas_flete (
                nro_orden text,
                sku text,
                clave_fila text,
                flete_prorrateado numeric,
                tiene_flete_real boolean,
                lineas_venta integer,
                fecha_calculo timestamptz DEFAULT now()
            );
        """)
        # La tabla ya existe en produccion sin esta columna, y mas abajo se
        # escribe con to_sql(if_exists="append"), que no crea columnas: sin el
        # ALTER el INSERT falla.
        con.exec_driver_sql("""
            ALTER TABLE gold.fact_ventas_flete
            ADD COLUMN IF NOT EXISTS lineas_venta integer;
        """)

    # DELETE + APPEND en una sola transaccion (no `replace`, que hace DROP y
    # rompe si alguien crea una vista encima).
    #
    # Las dos juntas para que gold.fact_ventas_flete no quede vacia en el medio:
    # la lee el tablero de Logistica en vivo.
    with engine.begin() as con:
        try:
            con.exec_driver_sql("DELETE FROM gold.fact_ventas_flete;")
        except Exception as e:
            print(f"  No se pudo vaciar (¿tabla recien creada?): {e}")

        df_resultado.to_sql(
            "fact_ventas_flete", con, schema="gold", if_exists="append", index=False
        )
    print("Guardado: gold.fact_ventas_flete")


if __name__ == "__main__":
    construir_fact_ventas_flete()
    print("\n=== LISTO ===")