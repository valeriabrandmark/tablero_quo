"""El stock de Mercado Libre Flex copia el de FIXY.

QUE HACE

FIXY (el deposito de Buenos Aires) es la fuente: lo que FIXY dice que tiene es
lo que Mercado Libre muestra en las publicaciones Flex de esos SKU. El stock de
FIXY es uno solo para todos los canales -- Tienda Nube vende del mismo pozo --,
asi que cuando una Tienda Nube vende, FIXY baja, y en la corrida siguiente baja
tambien Mercado Libre.

QUE TOCA, Y SOBRE TODO QUE NO

  - Solo publicaciones con logistica `self_service` (Flex). NUNCA Full (el
    stock lo maneja Mercado Libre en su deposito) y NUNCA `cross_docking`
    (colecta desde Tucuman: ponerle stock de FIXY haria que ML mande a buscar
    a Tucuman mercaderia que esta en Buenos Aires).
  - TODAS las publicaciones Flex, porque desde 11/2026 todo Flex sale de FIXY
    (Tucuman ya no despacha Flex). Un SKU que FIXY no tiene va a CERO en Flex:
    publicarlo seria vender algo que nadie puede despachar.

    El 24/11/2026 habia 126 SKU en Flex con 5.333 u. publicadas y ninguno en
    FIXY (era stock de Tucuman de antes del cambio). La primera corrida los
    pone en cero, y por eso frena: ver FRENOS.

COMO ESCRIBE

Cada publicacion tiene un `user_product_id`, y varias publicaciones del mismo
producto comparten ese stock. Se escribe UNA vez por user product:

  1. GET  /user-products/{id}/stock          -> cantidad actual y `x-version`
  2. PUT  /user-products/{id}/stock/type/selling_address
          con el header `x-version` y {"quantity": N}

Si el user product no tiene stock de tipo `selling_address` (o ML no acepta la
via de user products), se cae a PUT /items/{id} {"available_quantity": N},
publicacion por publicacion.

Solo se escribe lo que CAMBIA: lo que ya coincide no se toca.

FRENOS

  - Por defecto NO ESCRIBE: muestra el plan. Escribe solo con --aplicar, o
    con FIXY_SINCRONIZAR_ML=aplicar (en GitHub Actions es una VARIABLE del
    repo, ver orquestador.yml; corriendo a mano, en el .env).
  - Si FIXY no contesta o contesta vacio, no se toca nada: "FIXY no respondio"
    no es lo mismo que "FIXY no tiene nada".
  - Si el plan pone en cero mas de TOPE_CEROS user products de una vez, frena
    (salvo --forzar). Es la firma de una respuesta rota de FIXY, no de un dia
    de ventas.
  - FIXY_ML_RESERVA (por defecto 0): unidades que se guardan y no se publican
    en Mercado Libre, como colchon contra la venta simultanea en Tienda Nube.

QUE GUARDA

  bronze.ml_stock_fixy_log  cada cambio escrito (o intentado), con antes,
                            despues y la respuesta. Es la constancia de que se
                            publico. El plan sin aplicar se guarda solo con
                            --registrar-plan.

CUANDO CORRE

Cada 10 minutos, en su propio workflow (.github/workflows/ml-stock-fixy.yml),
y no en el orquestador: las ventas van mas rapido que una corrida por hora.

    python ml_stock_fixy.py              muestra el plan, no escribe
    python ml_stock_fixy.py --aplicar    escribe en Mercado Libre
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

URL_ML = "https://api.mercadolibre.com"
TIMEOUT_HTTP = (10, 30)
PAUSA = 0.3

# Mas que esto en cero de una vez, se frena: ver FRENOS arriba.
TOPE_CEROS = 10


# --------------------------------------------------------------------------
# El plan (sin red ni base: se prueba en probar_ml_stock_fixy.py)
# --------------------------------------------------------------------------

def cantidad_objetivo(disponible_fixy, reserva=0):
    """Lo que se publica: lo de FIXY menos la reserva, entero y nunca negativo."""
    if disponible_fixy is None:
        return 0
    return max(0, int(disponible_fixy) - max(0, int(reserva)))


def armar_plan(publicaciones, stock_fixy, reserva=0):
    """Que user product poner en que cantidad.

    publicaciones  [{"item_id", "user_product_id", "sku", "logistica", "status"}]
    stock_fixy     {sku: disponible} de la foto de AHORA. Un SKU que no esta
                   es un SKU que FIXY no tiene: va a cero.

    Devuelve una lista de {"user_product_id", "sku", "items", "objetivo"}, uno
    por user product. Lo que no es Flex no aparece. Una publicacion Flex sin
    SKU tampoco: no hay con que buscarla en FIXY, y se avisa aparte.
    """
    por_up = {}
    for p in publicaciones:
        sku = (p.get("sku") or "").strip()
        if p.get("logistica") != "self_service":
            continue
        if not sku:
            continue
        up = p.get("user_product_id") or f"item:{p['item_id']}"
        fila = por_up.setdefault(up, {
            "user_product_id": p.get("user_product_id"),
            "sku": sku,
            "items": [],
            "objetivo": cantidad_objetivo(stock_fixy.get(sku, 0), reserva),
        })
        fila["items"].append(p["item_id"])
    return sorted(por_up.values(), key=lambda f: (f["sku"], str(f["user_product_id"])))


def demasiados_ceros(plan, actuales, tope=TOPE_CEROS):
    """True si el plan apaga (lleva de >0 a 0) mas de `tope` user products."""
    apaga = [f for f in plan if f["objetivo"] == 0 and (actuales.get(_clave(f)) or 0) > 0]
    return len(apaga) > tope


def _clave(f):
    return f["user_product_id"] or f"item:{f['items'][0]}"


# --------------------------------------------------------------------------
# Mercado Libre
# --------------------------------------------------------------------------

def _pedir(metodo, endpoint, token_ref, headers=None, **kw):
    """Una llamada con el token del pipeline. Renueva ante 401, espera ante 429/5xx.

    `token_ref` es una lista de un elemento para que la renovacion le llegue
    tambien a las llamadas siguientes.
    """
    from mercadolibre import token_ml

    r = None
    for intento in range(1, 5):
        h = {"Authorization": f"Bearer {token_ref[0]}", **(headers or {})}
        r = requests.request(metodo, URL_ML + endpoint, headers=h, timeout=TIMEOUT_HTTP, **kw)
        if r.status_code == 401:
            token_ref[0] = token_ml(forzar=True)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(int(r.headers.get("Retry-After", 0) or 0) or 2 * intento)
            continue
        break
    time.sleep(PAUSA)
    return r


def stock_actual(fila, token_ref):
    """(cantidad, x-version, via) de lo que ML muestra hoy, o (None, None, None)."""
    up = fila["user_product_id"]
    if up:
        r = _pedir("GET", f"/user-products/{up}/stock", token_ref)
        if r.status_code == 200:
            for loc in r.json().get("locations") or []:
                if loc.get("type") == "selling_address":
                    return int(loc.get("quantity") or 0), r.headers.get("x-version"), "user_product"
    # Sin user product o sin stock propio: se mira la publicacion.
    r = _pedir("GET", f"/items/{fila['items'][0]}",
               token_ref, params={"attributes": "available_quantity"})
    if r.status_code == 200:
        return int(r.json().get("available_quantity") or 0), None, "item"
    return None, None, None


def escribir(fila, version, via, token_ref):
    """Escribe el objetivo. Devuelve (ok, detalle)."""
    if via == "user_product":
        r = _pedir("PUT", f"/user-products/{fila['user_product_id']}/stock/type/selling_address",
                   token_ref, headers={"x-version": str(version)},
                   json={"quantity": fila["objetivo"]})
        if r.status_code in (200, 204):
            return True, "user_product"
        # Version vieja (otro lo cambio en el medio) o via no habilitada: se
        # cae a la publicacion, que es la forma de siempre.
        detalle_up = f"user_product {r.status_code}: {r.text[:200]}"
    else:
        detalle_up = ""

    errores = []
    for item in fila["items"]:
        r = _pedir("PUT", f"/items/{item}", token_ref,
                   json={"available_quantity": fila["objetivo"]})
        if r.status_code != 200:
            errores.append(f"{item} {r.status_code}: {r.text[:200]}")
    if errores:
        return False, "; ".join(filter(None, [detalle_up, *errores]))
    return True, "items" + (f" (antes {detalle_up})" if detalle_up else "")


# --------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------

def publicaciones_flex(engine):
    sql = """
        select p.id as item_id, p.user_product_id, p.status,
               p."shipping.logistic_type" as logistica,
               (select a->>'value_name' from jsonb_array_elements(p.attributes::jsonb) a
                 where a->>'id' = 'SELLER_SKU' limit 1) as sku
          from bronze.ml_publicaciones p
         where p."shipping.logistic_type" = 'self_service'
           and p.status in ('active', 'paused')"""
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.exec_driver_sql(sql)]


DDL_LOG = """
create table if not exists bronze.ml_stock_fixy_log (
    corrido_en       timestamptz not null,
    sku              text not null,
    user_product_id  text,
    items            text,
    antes            integer,
    despues          integer,
    aplicado         boolean not null,
    resultado        text
);
"""


def guardar_log(engine, filas):
    if not filas:
        return
    with engine.begin() as con:
        con.exec_driver_sql(DDL_LOG)
        for f in filas:
            con.exec_driver_sql(
                """insert into bronze.ml_stock_fixy_log
                     (corrido_en, sku, user_product_id, items, antes, despues, aplicado, resultado)
                   values (%(corrido_en)s, %(sku)s, %(user_product_id)s, %(items)s,
                           %(antes)s, %(despues)s, %(aplicado)s, %(resultado)s)""", f)


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="Escribir en Mercado Libre.")
    ap.add_argument("--registrar-plan", action="store_true",
                    help="Guardar en la base tambien el plan que no se aplica.")
    ap.add_argument("--forzar", action="store_true",
                    help=f"Escribir aunque se apaguen mas de {TOPE_CEROS} user products.")
    args = ap.parse_args()

    aplicar = args.aplicar or os.getenv("FIXY_SINCRONIZAR_ML", "").strip().lower() == "aplicar"
    reserva = int(os.getenv("FIXY_ML_RESERVA", "0") or 0)

    import fixy
    if not fixy.TOKEN:
        print("Falta FIXY_API_TOKEN (secret del repo, o .env a mano): no hay de donde copiar. Se saltea.")
        return

    print(f"=== Mercado Libre Flex <- FIXY ({'APLICA' if aplicar else 'solo plan, no escribe'}) ===")

    from conexion import crear_engine
    engine = crear_engine(connect_args={"client_encoding": "utf8"})
    pubs = publicaciones_flex(engine)
    skus_flex = sorted({(p["sku"] or "").strip() for p in pubs if p["sku"]})
    sin_sku = [p["item_id"] for p in pubs if not (p["sku"] or "").strip()]

    # La foto de AHORA, no la de la ultima corrida: lo que se publica tiene que
    # ser lo mas fresco posible, porque el mismo stock lo vende Tienda Nube.
    #
    # DOS PEDIDOS Y NO UNO, porque aca un SKU que falta va a CERO. El "todo"
    # (skus como texto) no esta documentado; si un dia FIXY lo cambia y trae
    # menos, se pondrian en cero publicaciones con stock. Por eso se pregunta
    # ademas, explicitamente, por cada SKU que esta en Flex.
    foto = fixy.normalizar(fixy.llamar("consultarStock", {"skus": "[]"}))
    for i in range(0, len(skus_flex), fixy.LOTE):
        foto.update(fixy.normalizar(
            fixy.llamar("consultarStock", {"skus": skus_flex[i:i + fixy.LOTE]})))
    if not foto:
        print("  FIXY no devolvio stock: NO se toca Mercado Libre.")
        sys.exit(1)
    stock_fixy = {sku: cant for sku, (cant, _) in foto.items()}

    plan = armar_plan(pubs, stock_fixy, reserva)
    print(f"  FIXY: {len(stock_fixy)} SKU con stock")
    print(f"  Flex: {len(plan)} user products ({sum(len(f['items']) for f in plan)} publicaciones), "
          f"{sum(1 for f in plan if f['sku'] in stock_fixy)} con stock en FIXY")
    if sin_sku:
        print(f"  ATENCION: {len(sin_sku)} publicaciones Flex sin SKU, no se pueden "
              f"sincronizar: {', '.join(sin_sku[:10])}")
    if reserva:
        print(f"  Reserva: {reserva} u. por SKU no se publican")
    if not plan:
        print("  No hay publicaciones Flex: nada que sincronizar.")
        return

    from mercadolibre import token_ml
    token_ref = [token_ml()]

    actuales, versiones, vias = {}, {}, {}
    for f in plan:
        cant, ver, via = stock_actual(f, token_ref)
        actuales[_clave(f)], versiones[_clave(f)], vias[_clave(f)] = cant, ver, via

    cambios = [f for f in plan if actuales[_clave(f)] is not None
               and actuales[_clave(f)] != f["objetivo"]]
    sin_dato = [f for f in plan if actuales[_clave(f)] is None]

    print(f"\n  {len(cambios)} a cambiar, {len(plan) - len(cambios) - len(sin_dato)} ya coinciden, "
          f"{len(sin_dato)} sin respuesta de ML")
    for f in cambios:
        print(f"    {f['sku']:<12} {str(f['user_product_id']):<20} "
              f"{actuales[_clave(f)]:>5} -> {f['objetivo']:<5} ({len(f['items'])} pub.)")

    if demasiados_ceros(cambios, actuales) and not args.forzar:
        print(f"\n  FRENO: el plan apaga mas de {TOPE_CEROS} productos de una vez. "
              "Revisar FIXY; si es correcto, correr con --forzar.")
        sys.exit(1)

    ahora = datetime.now().astimezone()
    log = []
    for f in cambios:
        clave = _clave(f)
        if aplicar:
            ok, detalle = escribir(f, versiones[clave], vias[clave], token_ref)
        else:
            ok, detalle = False, "plan (no aplicado)"
        log.append({
            "corrido_en": ahora, "sku": f["sku"], "user_product_id": f["user_product_id"],
            "items": json.dumps(f["items"]), "antes": actuales[clave], "despues": f["objetivo"],
            "aplicado": bool(aplicar and ok), "resultado": detalle[:1000],
        })
        if aplicar and not ok:
            print(f"    ERROR {f['sku']}: {detalle[:200]}")

    # Sin aplicar, el plan se muestra pero no se guarda (salvo que se pida):
    # cada 10 minutos serian miles de filas por dia que no dicen nada nuevo.
    if aplicar or args.registrar_plan:
        guardar_log(engine, log)
    if aplicar:
        bien = sum(1 for x in log if x["aplicado"])
        print(f"\n  Escritos en Mercado Libre: {bien} de {len(cambios)}")
        if bien < len(cambios):
            sys.exit(1)
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
