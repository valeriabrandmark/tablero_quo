"""MERCADO ADS (publicidad dentro de Mercado Libre) -> bronze.ml_ads_diario.

Lo pidio Marketing para el Tablero de Marca: cuanto se invierte en Product Ads
por marca, y cuanto vende eso. Antes la tarjeta "Mercado Ads" decia
"Pendiente de conectar".

QUE GUARDA

Una fila por DIA y por PUBLICACION con anuncio: inversion, clics, impresiones
y la venta que Mercado Libre le atribuye al anuncio (directa e indirecta). La
marca se resuelve aca y no en el tablero, igual que en gold.fact_ventas: SKU
de la publicacion -> marca de SIGMA, y si no esta, la marca que figura en la
publicacion.

POR QUE DIA POR DIA

La API devuelve el total del rango que se le pide. Pidiendo de a un dia, el
tablero puede cortar por cualquier periodo (mes calendario, mes comercial, 7
dias) y comparar con el anterior sin volver a llamar a la API.

CUANTO SE PIDE EN CADA CORRIDA

La cuenta (QUOSALES) tiene mas de 7.000 anuncios de TODAS las marcas, casi
todos pausados, y la API los devuelve todos, con o sin gasto, de a 50. Un dia
son ~145 llamadas; los 30 dias de la ventana, ~4.300. El primer sondeo
(20/11/2026) los pidio en fila y no termino en 15 minutos.

Por eso:
  - Se piden solo los dias que FALTAN (los cargados quedan anotados en
    bronze.ml_ads_dias) mas los ultimos DIAS_RELEER, que se piden siempre:
    Mercado Libre atribuye ventas al anuncio DESPUES del clic y el costo del
    dia en curso todavia se mueve. Una corrida normal son 3 dias.
  - Las paginas van en paralelo (HILOS), como el catalogo de publicaciones.
  - `--todo` vuelve a pedir la ventana entera.

Cada dia pedido se reemplaza entero: una corrida repetida deja lo mismo.

LA API

  GET /advertising/advertisers?product_id=PADS          (Api-Version: 1)
      -> el advertiser_id de la cuenta
  GET /advertising/{SITIO}/advertisers/{id}/product_ads/ads/search
      ?date_from&date_to&metrics&limit&offset              (api-version: 2)
      -> una fila por anuncio (publicacion) con sus metricas

Si la primera ruta del search contesta 404 se prueba la vieja, sin sitio.
Desde la oficina no se pudo probar contra la API (la red del entorno donde se
escribio no llega a Mercado Libre): por eso existe `--explorar`, que imprime
lo que contesta sin escribir nada. Correrlo con el workflow "Sondeo Mercado
Ads" antes de confiar en los numeros.

PERMISOS

La aplicacion de Mercado Libre necesita el permiso de PUBLICIDAD. Si no lo
tiene, la API contesta 401/403 y este script lo dice con todas las letras: hay
que tildarlo en la app (developers.mercadolibre.com.ar -> Mis aplicaciones ->
Permisos) y volver a autorizar con ml_token.py.

Uso:
  python ml_ads.py                 los dias que faltan de la ventana + los ultimos
  python ml_ads.py --dias 90       ventana mas larga (la API no da mas de 90)
  python ml_ads.py --todo          vuelve a pedir toda la ventana
  python ml_ads.py --explorar      solo muestra lo que contesta la API
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
from sqlalchemy import text

from conexion import crear_engine
from mercadolibre import llamar_ml, token_ml

SITIO = "MLA"
DIAS_VENTANA = 30
DIAS_RELEER = 3
HILOS = 12
LIMITE_API_DIAS = 90
POR_PAGINA = 50

# Nombres de metrica como los documenta Product Ads. Una metrica que la API no
# conozca hace fallar el pedido entero, por eso son pocas y las de siempre.
METRICAS = [
    "clicks",
    "prints",
    "cost",
    "direct_amount",
    "indirect_amount",
    "total_amount",
    "direct_units_quantity",
    "indirect_units_quantity",
    "units_quantity",
]

RUTAS_SEARCH = [
    "/advertising/{sitio}/advertisers/{adv}/product_ads/ads/search",
    "/advertising/advertisers/{adv}/product_ads/ads/search",
]

SIN_PERMISO = (
    "Mercado Libre no deja leer la publicidad con este token. A la aplicacion le "
    "falta el permiso de Publicidad (Advertising): tildarlo en "
    "developers.mercadolibre.com.ar -> Mis aplicaciones -> Permisos, y volver a "
    "autorizar con ml_token.py."
)

DDL = """
create table if not exists bronze.ml_ads_diario (
  fecha            date        not null,
  item_id          text        not null,
  advertiser_id    text,
  campaign_id      text,
  titulo           text,
  estado           text,
  sku              text,
  marca            text,
  clics            integer,
  impresiones      integer,
  costo            numeric,
  venta_directa    numeric,
  venta_indirecta  numeric,
  venta_total      numeric,
  unidades         integer,
  actualizado_en   timestamptz not null default now(),
  primary key (fecha, item_id)
);
alter table bronze.ml_ads_diario enable row level security;
create table if not exists bronze.ml_ads_dias (
  fecha       date        primary key,
  anuncios    integer,
  cargado_en  timestamptz not null default now()
);
alter table bronze.ml_ads_dias enable row level security;
comment on table bronze.ml_ads_diario is
  'Mercado Ads (Product Ads): inversion y venta atribuida por dia y publicacion. La escribe ml_ads.py.';
"""


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _status(e):
    return getattr(getattr(e, "response", None), "status_code", None)


def anunciantes(tok):
    r = llamar_ml("/advertising/advertisers", tok, params={"product_id": "PADS"},
                  headers_extra={"Api-Version": "1"})
    lista = r.get("advertisers", []) if isinstance(r, dict) else []
    return [a for a in lista if (a.get("site_id") or SITIO) == SITIO]


def _pagina(tok, ruta, dia, offset, pausa=True):
    return llamar_ml(
        ruta, tok,
        params={
            "date_from": dia.isoformat(),
            "date_to": dia.isoformat(),
            "metrics": ",".join(METRICAS),
            "limit": POR_PAGINA,
            "offset": offset,
        },
        headers_extra={"api-version": "2"},
        pausa=pausa,
    )


def elegir_ruta(tok, adv, dia):
    """La primera ruta del search que contesta. Un 404 es 'esta no es'."""
    ultimo = None
    for plantilla in RUTAS_SEARCH:
        ruta = plantilla.format(sitio=SITIO, adv=adv)
        try:
            _pagina(tok, ruta, dia, 0)
            return ruta
        except requests.HTTPError as e:
            if _status(e) in (401, 403):
                raise
            ultimo = e
            if _status(e) != 404:
                raise
    raise RuntimeError(f"Ninguna ruta de Product Ads contesto: {ultimo}")


def anuncios_de_los_dias(tok, ruta, dias):
    """{dia: [anuncios]} de todos los dias pedidos, con las paginas en paralelo.

    Primero la pagina 0 de cada dia (trae el total), despues el resto de las
    paginas de todos los dias juntas. `pausa=False`: el freno lo pone HILOS.
    """
    def pedir(par):
        dia, offset = par
        r = _pagina(tok, ruta, dia, offset, pausa=False)
        return dia, offset, r if isinstance(r, dict) else {}

    out = {d: [] for d in dias}
    with ThreadPoolExecutor(max_workers=HILOS) as ex:
        primeras = list(ex.map(pedir, [(d, 0) for d in dias]))
        resto = []
        for dia, _, r in primeras:
            out[dia].extend(r.get("results", []))
            total = (r.get("paging") or {}).get("total") or 0
            resto.extend((dia, o) for o in range(POR_PAGINA, total, POR_PAGINA))
        print(f"  {len(dias)} dia(s) · {len(primeras) + len(resto)} llamadas")
        for dia, _, r in ex.map(pedir, resto):
            out[dia].extend(r.get("results", []))
    return out


def _metrica(anuncio, clave):
    m = anuncio.get("metrics") or {}
    v = m.get(clave, anuncio.get(clave))
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Marca de cada publicacion
# ---------------------------------------------------------------------------

def _atributo(attributes, ident):
    try:
        lista = json.loads(attributes) if isinstance(attributes, str) else (attributes or [])
    except (TypeError, ValueError):
        return None
    for a in lista or []:
        if isinstance(a, dict) and a.get("id") == ident:
            return a.get("value_name")
    return None


def marcas_por_item(engine):
    """item_id -> (sku, marca). Igual criterio que gold.fact_ventas: el SKU
       manda (marca de SIGMA); si no hay, la marca de la publicacion."""
    pubs = pd.read_sql('select id, seller_custom_field, attributes from bronze.ml_publicaciones', engine)
    sigma = pd.read_sql('select id, "attributes.marca" as marca from bronze.sigma_articulos', engine)
    marca_sigma = {str(k).strip(): v for k, v in zip(sigma["id"], sigma["marca"]) if v}
    out = {}
    for _, p in pubs.iterrows():
        # OJO: pandas trae los vacios como NaN, que es "verdadero" para `or`.
        # Asi se guardaron SKU "nan" hasta el 25/11/2026 (los arregla
        # `completar_skus` en cada corrida).
        scf = p["seller_custom_field"]
        sku = scf if isinstance(scf, str) and scf.strip() else _atributo(p["attributes"], "SELLER_SKU")
        sku = str(sku).strip() if sku else None
        marca = marca_sigma.get(sku) if sku else None
        marca = marca or _atributo(p["attributes"], "BRAND")
        out[p["id"]] = (sku, str(marca).strip().upper() if marca else None)
    return out


def completar_skus(engine, marcas):
    """Filas ya guardadas sin SKU (o con "nan"): se completan con el catalogo,
       sin volver a pedirle nada a la API. El SKU es lo que cruza el anuncio
       con la venta en el detalle de ventas del tablero."""
    pares = [{"i": i, "s": s, "m": m} for i, (s, m) in marcas.items() if s]
    if not pares:
        return
    with engine.begin() as con:
        r = con.execute(
            text("""update bronze.ml_ads_diario a set sku = v.s, marca = coalesce(v.m, a.marca)
                      from (select unnest(cast(:ids as text[])) i, unnest(cast(:skus as text[])) s,
                                   unnest(cast(:marcas as text[])) m) v
                     where a.item_id = v.i and (a.sku is null or a.sku = 'nan')"""),
            {"ids": [p["i"] for p in pares], "skus": [p["s"] for p in pares], "marcas": [p["m"] for p in pares]},
        )
        if r.rowcount:
            print(f"  SKU completado en {r.rowcount} filas viejas")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def explorar(tok):
    print("=== ANUNCIANTES (product_id=PADS) ===")
    advs = anunciantes(tok)
    print(json.dumps(advs, indent=2, ensure_ascii=False)[:2000])
    if not advs:
        print("La cuenta no tiene anunciante de Product Ads en", SITIO)
        return
    adv = advs[0]["advertiser_id"]
    ayer = date.today() - timedelta(days=1)
    ruta = elegir_ruta(tok, adv, ayer)
    print(f"\n=== RUTA QUE CONTESTA: {ruta} ===")
    r = _pagina(tok, ruta, ayer, 0)
    resultados = r.get("results", [])
    print(f"Claves de la respuesta: {list(r.keys())}")
    print(f"paging: {r.get('paging')}")
    print(f"Anuncios con datos de ayer ({ayer}): {len(resultados)}")
    for a in resultados[:3]:
        print(json.dumps(a, indent=2, ensure_ascii=False)[:1500])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dias", type=int, default=DIAS_VENTANA)
    ap.add_argument("--explorar", action="store_true")
    ap.add_argument("--todo", action="store_true", help="volver a pedir toda la ventana")
    args = ap.parse_args()
    dias = max(1, min(args.dias, LIMITE_API_DIAS))

    print("\n=== MERCADO ADS ===")
    tok = token_ml()
    try:
        if args.explorar:
            explorar(tok)
            return
        advs = anunciantes(tok)
    except requests.HTTPError as e:
        if _status(e) in (401, 403):
            print(f"  {SIN_PERMISO}\n  Respuesta: {getattr(e.response, 'text', '')[:300]}")
            sys.exit(1)
        raise
    if not advs:
        print(f"  La cuenta no tiene anunciante de Product Ads en {SITIO}: nada que traer.")
        return

    hasta = date.today()
    desde = hasta - timedelta(days=dias - 1)
    engine = crear_engine()
    with engine.begin() as con:
        con.exec_driver_sql(DDL)
        cargados = {r[0] for r in con.execute(
            text("select fecha from bronze.ml_ads_dias where fecha between :d and :h"), {"d": desde, "h": hasta})}
    releer_desde = hasta - timedelta(days=DIAS_RELEER - 1)
    pedir = [desde + timedelta(days=i) for i in range(dias)]
    if not args.todo:
        pedir = [d for d in pedir if d not in cargados or d >= releer_desde]
    marcas = marcas_por_item(engine)
    filas = []
    por_dia_total = {d: 0 for d in pedir}
    for a in advs:
        adv = a["advertiser_id"]
        ruta = elegir_ruta(tok, adv, hasta - timedelta(days=1))
        print(f"  Anunciante {adv} ({a.get('advertiser_name') or a.get('account_name') or '-'}) · {ruta}")
        for dia, anuncios in anuncios_de_los_dias(tok, ruta, pedir).items():
            por_dia_total[dia] += len(anuncios)
            for an in anuncios:
                item = str(an.get("item_id") or an.get("id") or "")
                if not item:
                    continue
                costo = _metrica(an, "cost")
                impresiones = _metrica(an, "prints")
                if not costo and not impresiones:
                    continue
                sku, marca = marcas.get(item, (None, None))
                # La marca de SIGMA manda; si la publicacion no tiene SKU de
                # SIGMA, la que informa el propio anuncio.
                marca = marca or (str(an.get("brand_value_name")).strip().upper() if an.get("brand_value_name") else None)
                filas.append({
                    "fecha": dia,
                    "item_id": item,
                    "advertiser_id": str(adv),
                    "campaign_id": str(an.get("campaign_id")) if an.get("campaign_id") else None,
                    "titulo": an.get("title"),
                    "estado": an.get("status"),
                    "sku": sku,
                    "marca": marca,
                    "clics": int(_metrica(an, "clicks")),
                    "impresiones": int(impresiones),
                    "costo": costo,
                    "venta_directa": _metrica(an, "direct_amount"),
                    "venta_indirecta": _metrica(an, "indirect_amount"),
                    "venta_total": _metrica(an, "total_amount")
                    or _metrica(an, "direct_amount") + _metrica(an, "indirect_amount"),
                    "unidades": int(_metrica(an, "units_quantity")
                                    or _metrica(an, "direct_units_quantity") + _metrica(an, "indirect_units_quantity")),
                })

    df = pd.DataFrame(filas)
    if not df.empty:
        # Un mismo item en dos campanas el mismo dia: se suma, la clave es dia+item.
        num = ["clics", "impresiones", "costo", "venta_directa", "venta_indirecta", "venta_total", "unidades"]
        primero = {c: "first" for c in df.columns if c not in num + ["fecha", "item_id"]}
        df = df.groupby(["fecha", "item_id"], as_index=False).agg({**{c: "sum" for c in num}, **primero})
        df["actualizado_en"] = datetime.now(timezone.utc)

    # LOS DIAS PEDIDOS SE REEMPLAZAN ENTEROS Y DE UNA: borrar e insertar en la
    # misma transaccion, asi el tablero nunca ve un dia a medio cargar.
    with engine.begin() as con:
        if pedir:
            con.execute(text("delete from bronze.ml_ads_diario where fecha = any(:dias)"), {"dias": pedir})
        if not df.empty:
            df.to_sql("ml_ads_diario", con, schema="bronze", if_exists="append", index=False)
        for d in pedir:
            con.execute(
                text("""insert into bronze.ml_ads_dias (fecha, anuncios, cargado_en) values (:f, :n, now())
                        on conflict (fecha) do update set anuncios = excluded.anuncios, cargado_en = now()"""),
                {"f": d, "n": por_dia_total[d]},
            )
    completar_skus(engine, marcas)
    sin_marca = int(df["marca"].isna().sum()) if not df.empty else 0
    print(f"  Guardado: bronze.ml_ads_diario · {len(pedir)} dia(s) pedidos de {desde} a {hasta} · {len(df)} filas"
          f" · inversion ${df['costo'].sum() if not df.empty else 0:,.0f}"
          + (f" · {sin_marca} sin marca" if sin_marca else ""))


if __name__ == "__main__":
    main()
