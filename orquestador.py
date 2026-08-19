"""
Orquestador del pipeline de datos.

Corre cada script en orden, con reintentos automaticos si falla por problemas
transitorios (conexion, timeouts de Supabase, etc.).

QUE PASO ANTES Y POR QUE ESTE ARCHIVO CAMBIO
--------------------------------------------
La version anterior corria solo siete scripts: sigma, digip_pedidos,
digip_preparaciones, costos, modelo, prorratear_flete y clasificar_clientes.
Quedaban afuera CUATRO extracciones que igual existen en el repo:

  - mercadolibre.py  -> ventas, publicaciones y stock Full de Mercado Libre
  - ml_envios.py     -> costo de envio de cada orden de Mercado Libre
  - digip.py         -> stock de DIGIP
  - tiendanube.py    -> ventas de Tienda Nube

Como nadie las corria, el tablero mostraba: ventas de Mercado Libre que
cortaban el 14/08, envio en CERO desde julio (y por lo tanto rentabilidad
inflada), stock de DIGIP congelado en junio y Tienda Nube vacia.

Mercado Libre estaba detras de un flag `--con-ml` que habia que acordarse de
poner. Ademas habia, pegado arriba de todo, un bloque de funciones para
correrlo cada 20hs que NUNCA se llego a conectar (ni siquiera importaba `json`
ni `os`, asi que habria explotado si alguien la llamaba). Esa idea era la
correcta y es la que quedo implementada abajo, pero de verdad y para todos los
pasos: cada paso puede declarar `cada_horas`, y el orquestador se acuerda en
`estado_pasos.json` de cuando corrio bien por ultima vez.

Asi el pipeline se puede seguir corriendo cada 1-2 horas sin que los pasos
lentos (el catalogo de Mercado Libre son ~3.800 llamadas a la API) lo hagan
eterno, y sin que nadie tenga que acordarse de ningun flag.

Uso:
    python orquestador.py                  -> el pipeline entero, respetando
                                              la frecuencia de cada paso
    python orquestador.py --forzar         -> ignora las frecuencias y corre todo
    python orquestador.py --solo modelo.py -> corre solo ese paso (y nada mas)
    python orquestador.py --listar         -> muestra que corre, cada cuanto y
                                              cuando fue la ultima vez
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

ARCHIVO_LOG = "orquestador_log.txt"
ARCHIVO_ESTADO = "estado_pasos.json"

# TECHOS DE TIEMPO
# ----------------
# La corrida TIENE que terminar antes del proximo disparo del Programador de
# tareas de Windows, que por defecto viene con "si la tarea ya se esta
# ejecutando, no iniciar una nueva instancia". Una corrida que se cuelga no
# molesta solo a si misma: hace que TODAS las siguientes se salteen en silencio,
# sin quedar ni como error. Asi es como el pipeline puede pasar medio dia sin
# actualizar sin que nada avise.
#
# Por eso hay dos topes, y son distintos:
#
#   PRESUPUESTO_TOTAL  el tope de la corrida ENTERA. Antes de arrancar cada
#                      paso se mira cuanto se lleva gastado; si ya no entra, los
#                      que quedan se saltean y la corrida termina. Lo que se
#                      saltea no se pierde: al no quedar registrado como "corrio
#                      bien", entra en la corrida siguiente.
#
#   techo (por paso)   el tope de UN paso. Sale de multiplicar por 3 o 4 la
#                      mediana medida en 24 corridas reales (ver README), asi
#                      que un dia lento no lo corta: corta un cuelgue.
#
# El total es de 100 minutos contra un intervalo de 120: deja 20 de colchon.
# Si algun dia el pipeline se corre cada hora, este numero hay que bajarlo.
PRESUPUESTO_TOTAL = 100 * 60
TECHO_POR_DEFECTO = 10 * 60

# Cada paso es:
#   comando     lo que se ejecuta (script + argumentos)
#   intentos    cuantas veces se reintenta si falla
#   espera      segundos entre reintentos
#   cada_horas  None = en cada corrida. Un numero = solo si paso ese tiempo
#               desde la ultima vez que TERMINO BIEN.
#   critico     True = si falla definitivamente, se corta el pipeline porque
#               los pasos de abajo dependen de este.
#   techo       segundos como maximo que se le dan al paso. Si no esta, se usa
#               TECHO_POR_DEFECTO. Pasado ese tiempo se lo mata y cuenta como
#               falla.
#
# El orden importa: modelo.py arma gold.fact_ventas leyendo lo que dejaron
# todas las extracciones, asi que TODAS van antes que el.
PASOS = [
    # --- Extracciones ---------------------------------------------------
    # Las ventas mayoristas son el corazon del tablero: van en cada corrida.
    {"comando": "sigma.py --ventas",           "intentos": 3, "espera": 60,
     "cada_horas": None, "critico": True,  "escribe": "sigma_ventas", "techo": 30 * 60},

    # El catalogo se reescribe entero (8.200 articulos) y un alta o un cambio de
    # descripcion no pasa cada dos horas. Estaba pegado a las ventas, y asi
    # acumulo 335.816 inserciones para tener 8.194 filas vivas: 41 recargas del
    # mismo catalogo.
    {"comando": "sigma.py --catalogo",         "intentos": 2, "espera": 60,
     "cada_horas": 24, "critico": False, "escribe": "sigma_articulos", "techo": 30 * 60},

    {"comando": "digip_pedidos.py",            "intentos": 3, "espera": 60,
     "cada_horas": None, "critico": True,  "escribe": "digip_pedidos", "techo": 20 * 60},

    # El paso mas caro de todos: 9,8 min de mediana, una llamada por pedido de
    # la ventana. Alimenta Logistica, que se mira por semana y no por hora.
    {"comando": "digip_preparaciones.py",      "intentos": 2, "espera": 60,
     "cada_horas": 6, "critico": False, "escribe": "digip_preparaciones", "techo": 40 * 60},

    # Ventas de ML: ventana movil de 7 dias, es barato. Va seguido porque es
    # lo que mas rapido queda viejo.
    {"comando": "mercadolibre.py --ventas",    "intentos": 2, "espera": 60,
     "cada_horas": 2, "critico": False, "escribe": "ml_ventas", "techo": 20 * 60},

    # Costo de envio: incremental (solo pide las ordenes que todavia no tiene).
    # Va DESPUES de las ventas: lee bronze.ml_ventas para saber que le falta.
    {"comando": "ml_envios.py",                "intentos": 2, "espera": 60,
     "cada_horas": 4, "critico": False, "escribe": "ml_envios", "techo": 45 * 60},

    # Catalogo y stock Full de ML: ~3.800 llamadas a la API, es EL paso lento.
    {"comando": "mercadolibre.py --catalogo",  "intentos": 2, "espera": 60,
     "cada_horas": 12, "critico": False, "escribe": "ml_publicaciones, ml_stock_full", "techo": 60 * 60},

    # Stock de DIGIP: dos llamadas y el stock si se mueve durante el dia.
    {"comando": "digip.py",                    "intentos": 2, "espera": 30,
     "cada_horas": 4, "critico": False, "escribe": "digip_stock, digip_stock_detalle", "techo": 20 * 60},

    # Tienda Nube vende poco (unos 8 pedidos por mes), pero desde que tiene
    # tablero propio la frecuencia ya no la manda el volumen sino la espera:
    # con 12 h, una venta de la manana recien aparecia a la noche. La bajada es
    # una sola pagina de la API y tarda segundos, asi que sale barato.
    {"comando": "tiendanube.py",               "intentos": 2, "espera": 30,
     "cada_horas": 4, "critico": False, "escribe": "tn_pedidos + tn_pedidos_items", "techo": 15 * 60},

    # Los Excel de costos solo cambian cuando alguien los edita: el script
    # compara una huella de los archivos y no hace nada si no se movio ninguno.
    # Se lo llama en cada corrida a proposito -- el que decide es el script, asi
    # que un Excel corregido entra en la corrida siguiente y no hay que
    # acordarse de nada.
    {"comando": "costos.py --si-cambio",       "intentos": 2, "espera": 30,
     "cada_horas": None, "critico": True,  "escribe": "costos_historicos", "techo": 20 * 60},

    # --- Transformaciones -----------------------------------------------
    {"comando": "modelo.py",                   "intentos": 3, "espera": 60,
     "cada_horas": None, "critico": True,  "escribe": "gold.fact_ventas", "techo": 30 * 60},
    {"comando": "prorratear_flete.py",         "intentos": 2, "espera": 30,
     "cada_horas": None, "critico": True,  "escribe": "gold.fact_ventas_flete", "techo": 20 * 60},
    {"comando": "clasificar_clientes.py",      "intentos": 2, "espera": 30,
     "cada_horas": None, "critico": True,  "escribe": "gold.clientes_clasificados", "techo": 20 * 60},
]

CARPETA = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{stamp}] {msg}"
    print(linea)
    with open(os.path.join(CARPETA, ARCHIVO_LOG), "a", encoding="utf-8") as f:
        f.write(linea + "\n")


# --- Estado: cuando corrio bien cada paso por ultima vez ---------------------
#
# Vive en un archivo y no en memoria porque la gracia es justamente que
# sobreviva entre corridas (y entre apagadas de la computadora): si la maquina
# estuvo apagada tres dias, al prenderla el paso vencido corre en la primera
# pasada, en vez de esperar a un horario fijo que ya paso.

def cargar_estado():
    ruta = os.path.join(CARPETA, ARCHIVO_ESTADO)
    if not os.path.exists(ruta):
        return {}
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        # Archivo corrupto: se arranca de cero y todos los pasos corren una vez.
        log(f"AVISO: {ARCHIVO_ESTADO} ilegible, se ignora y se regenera.")
        return {}


def guardar_estado(estado):
    ruta = os.path.join(CARPETA, ARCHIVO_ESTADO)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2, ensure_ascii=False)


def registro(estado, comando):
    """El estado de un paso, normalizado.

    Las versiones viejas guardaban solo la fecha del ultimo OK como texto; se
    acepta ese formato para no perder el estado al actualizar.
    """
    valor = estado.get(comando)
    if isinstance(valor, str):
        return {"ok": valor, "fallos": 0, "ultimo_fallo": None, "error": None}
    if isinstance(valor, dict):
        return {
            "ok": valor.get("ok"),
            "fallos": valor.get("fallos", 0),
            "ultimo_fallo": valor.get("ultimo_fallo"),
            "error": valor.get("error"),
        }
    return {"ok": None, "fallos": 0, "ultimo_fallo": None, "error": None}


def ultima_corrida(estado, comando):
    valor = registro(estado, comando).get("ok")
    if not valor:
        return None
    try:
        return datetime.datetime.fromisoformat(valor)
    except ValueError:
        return None


def toca_correr(paso, estado):
    """(corre_si_o_no, motivo_para_el_log)"""
    if paso["cada_horas"] is None:
        return True, ""

    ultima = ultima_corrida(estado, paso["comando"])
    if ultima is None:
        return True, "nunca corrio"

    pasadas = (datetime.datetime.now() - ultima).total_seconds() / 3600
    if pasadas >= paso["cada_horas"]:
        return True, f"pasaron {pasadas:.1f}hs de {paso['cada_horas']}hs"
    faltan = paso["cada_horas"] - pasadas
    return False, f"corrio hace {pasadas:.1f}hs, faltan {faltan:.1f}hs"


def correr_paso(comando, intentos, espera, techo):
    """Corre un paso con reintentos y un tope de tiempo. Devuelve (exito, error).

    `techo` son los segundos que se le dan como maximo. Si se pasa, se lo mata.
    """
    # Lista partida y no `shell=True`: el comando puede traer argumentos
    # ("mercadolibre.py --ventas") y asi no hay que meter una shell en el medio.
    partes = comando.split()
    ultimo_error = None
    for intento in range(1, intentos + 1):
        log(f"Ejecutando {comando} (intento {intento}/{intentos}, techo {techo // 60} min)...")
        try:
            resultado = subprocess.run(
                [sys.executable] + partes,
                capture_output=True,
                text=True,
                cwd=CARPETA,
                timeout=techo,
            )
        except subprocess.TimeoutExpired:
            # UN paso colgado NO se reintenta, aunque le queden intentos.
            #
            # Reintentar costaria otro techo entero, y dos techos seguidos se
            # pueden comer la ventana de dos horas hasta el disparo siguiente --
            # que es exactamente el problema que este tope viene a evitar. Un
            # cuelgue ademas no suele ser transitorio: es un socket esperando una
            # respuesta que no llega, y el reintento se cuelga igual.
            ultimo_error = (
                f"Se paso de {techo // 60} minutos y se lo corto. "
                f"Suele ser una llamada a una API que quedo esperando respuesta."
            )
            log(f"CORTADO POR TIEMPO: {comando} — {ultimo_error}")
            return False, ultimo_error

        if resultado.returncode == 0:
            log(f"OK: {comando} termino sin errores.")
            return True, None

        ultimo_error = (resultado.stderr or "").strip() or "(sin salida de error)"
        log(f"ERROR en {comando} (intento {intento}/{intentos}):")
        log(ultimo_error[-1500:])

        if intento < intentos:
            log(f"Esperando {espera}s antes de reintentar {comando}...")
            time.sleep(espera)

    log(f"FALLO DEFINITIVO: {comando} no pudo completarse tras {intentos} intentos.")
    return False, ultimo_error


def listar():
    estado = cargar_estado()
    ahora = datetime.datetime.now()
    print(f"\n{'PASO':<28} {'CADA':>8}  {'ULTIMA CORRIDA OK':<18} {'ESTADO':<26} ESCRIBE")
    print("-" * 124)
    con_fallos = []
    for paso in PASOS:
        reg = registro(estado, paso["comando"])
        ultima = ultima_corrida(estado, paso["comando"])
        cada = "siempre" if paso["cada_horas"] is None else f"{paso['cada_horas']}hs"

        if paso["cada_horas"] is None:
            texto_ultima, estado_txt = "-", "corre siempre"
        elif ultima is None:
            texto_ultima, estado_txt = "nunca", "PENDIENTE"
        else:
            horas = (ahora - ultima).total_seconds() / 3600
            texto_ultima = ultima.strftime("%Y-%m-%d %H:%M")
            estado_txt = ("PENDIENTE" if horas >= paso["cada_horas"]
                          else f"al dia (hace {horas:.1f}hs)")

        # Un paso que viene fallando NO se puede ver igual que uno al que
        # todavia no le toco el turno. Es la diferencia entre "esperá" y
        # "esto esta roto y nadie se dio cuenta".
        if reg["fallos"]:
            estado_txt = f"FALLA x{reg['fallos']}"
            con_fallos.append(paso["comando"])

        print(f"{paso['comando']:<28} {cada:>8}  {texto_ultima:<18} {estado_txt:<26} {paso['escribe']}")
    print("Los pasos que dicen \"corre siempre\" no cortan por frecuencia; los demas")
    print("esperan su turno. Ninguno se saltea si se usa --forzar.")

    for comando in con_fallos:
        reg = registro(estado, comando)
        print(f"\n  {comando} viene fallando ({reg['fallos']} veces, la ultima "
              f"{reg['ultimo_fallo'][:16] if reg['ultimo_fallo'] else '?'}):")
        for linea in (reg["error"] or "").strip().splitlines()[-3:]:
            print(f"      {linea}")
        print(f"      Para verlo entero: python orquestador.py --solo {comando.split()[0]}")
    print()


def elegir_pasos(solo):
    """Los pasos que pidio `--solo`. Acepta 'modelo.py' o 'mercadolibre.py --ventas'."""
    pedido = solo.split()
    if len(pedido) > 1:
        return [p for p in PASOS if p["comando"] == solo]
    # Con el nombre pelado se corren todas sus variantes: `--solo mercadolibre.py`
    # trae ventas Y catalogo, que es lo que uno espera al pedirlo asi.
    return [p for p in PASOS if p["comando"].split()[0] == pedido[0]]


def main():
    parser = argparse.ArgumentParser(
        description="Orquestador del pipeline de datos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--forzar", action="store_true",
                        help="Ignora las frecuencias y corre todos los pasos")
    parser.add_argument("--solo", metavar="PASO",
                        help="Corre unicamente ese paso (ej: --solo modelo.py)")
    parser.add_argument("--listar", action="store_true",
                        help="Muestra que corre, cada cuanto y cuando fue la ultima vez")
    args = parser.parse_args()

    if args.listar:
        listar()
        return

    estado = cargar_estado()

    if args.solo:
        elegidos = elegir_pasos(args.solo)
        if not elegidos:
            print(f"No hay ningun paso que se llame '{args.solo}'. Los que hay son:")
            for p in PASOS:
                print("  " + p["comando"])
            sys.exit(2)
    else:
        elegidos = PASOS

    log("========== INICIO DEL ORQUESTADOR ==========")
    inicio = time.time()
    salteados, fallados, sin_tiempo = [], [], []

    # Con --solo el pedido es explicito y de un paso solo: no se le pone tope a
    # la corrida. Es el modo en que uno se sienta a mirar como termina, no el
    # automatico que tiene que devolver la maquina a horario.
    presupuesto = None if args.solo else PRESUPUESTO_TOTAL

    for paso in elegidos:
        comando = paso["comando"]
        techo = paso.get("techo", TECHO_POR_DEFECTO)

        # Antes de arrancar el paso: ¿todavia entra en la corrida?
        #
        # Se compara contra el techo del paso y no contra un minuto suelto: si
        # quedan 5 minutos y el paso puede tardar 30, arrancarlo es firmar que la
        # corrida se va a pasar. Mejor saltearlo -- al no quedar registrado como
        # "corrio bien", entra primero en la corrida siguiente.
        if presupuesto is not None:
            gastado = time.time() - inicio
            if gastado + techo > presupuesto:
                log(
                    f"Se saltea {comando}: quedan {(presupuesto - gastado) / 60:.0f} min "
                    f"de la corrida y el paso puede tardar {techo // 60}. "
                    f"Va a correr en la proxima."
                )
                sin_tiempo.append(comando)
                continue

        # Con --solo o --forzar el pedido es explicito: no se saltea nada.
        if not args.forzar and not args.solo:
            corre, motivo = toca_correr(paso, estado)
            if not corre:
                log(f"Se saltea {comando}: {motivo}.")
                salteados.append(comando)
                continue
            if motivo:
                log(f"Toca {comando}: {motivo}.")

        exito, ultimo_error = correr_paso(comando, paso["intentos"], paso["espera"], techo)

        ahora = datetime.datetime.now().isoformat()
        reg = registro(estado, comando)

        if exito:
            # El estado se guarda paso por paso y no al final: si la corrida se
            # corta a la mitad, lo que ya salio bien no se vuelve a hacer.
            estado[comando] = {"ok": ahora, "fallos": 0, "ultimo_fallo": None, "error": None}
            guardar_estado(estado)
            continue

        # Un paso que falla tiene que DEJAR RASTRO. Antes solo se anotaba el
        # exito, asi que un paso no critico que fallaba una y otra vez se veia
        # en --listar exactamente igual que uno al que todavia no le habia
        # tocado el turno: "PENDIENTE". Fue lo que hizo que tiendanube.py
        # estuviera meses sin traer nada sin que nadie se enterara.
        estado[comando] = {
            "ok": reg["ok"],
            "fallos": reg["fallos"] + 1,
            "ultimo_fallo": ahora,
            "error": (ultimo_error or "")[-300:] or None,
        }
        guardar_estado(estado)

        fallados.append(comando)
        if paso["critico"]:
            log(f"Se detiene el pipeline: los pasos siguientes dependen de {comando}.")
            log("========== ORQUESTADOR TERMINADO CON ERRORES ==========")
            sys.exit(1)

        # No critico: el tablero se queda con el dato viejo de ESE pedazo, pero
        # el resto se actualiza igual. Cortar todo seria peor.
        log(f"AVISO: {comando} fallo. Se sigue con el resto del pipeline.")

    duracion = round((time.time() - inicio) / 60, 1)
    if salteados:
        log(f"Salteados por frecuencia: {', '.join(salteados)}")

    # Los que no entraron en el presupuesto se avisan APARTE de los salteados
    # por frecuencia, aunque los dos sean "no corrio". Son cosas distintas: uno
    # es el pipeline funcionando como se lo penso, el otro es la corrida
    # quedandose sin tiempo. Si este aparece seguido, o algun paso se volvio
    # lento o el presupuesto quedo corto -- y eso hay que mirarlo.
    if sin_tiempo:
        log(f"NO ENTRARON en los {PRESUPUESTO_TOTAL // 60} min de la corrida: {', '.join(sin_tiempo)}")
        log("Van a correr primero en la proxima. Si se repite, revisar los techos.")

    if fallados:
        log(f"TERMINADO CON AVISOS ({duracion} min). Fallaron: {', '.join(fallados)}")
    else:
        log(f"========== ORQUESTADOR TERMINADO CON EXITO ({duracion} min) ==========")


if __name__ == "__main__":
    main()
