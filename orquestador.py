"""
BLOQUE PARA AGREGAR A orquestador.py
=====================================
Reemplaza la tarea separada de Task Scheduler "ML - actualizacion diaria"
(la que se elimino sin querer). En vez de depender de que la compu este
prendida justo a un horario exacto, esto se apoya en el pipeline que YA
corre solo cada 1-2 horas: cada vez que el orquestador corre, chequea si
paso suficiente tiempo desde la ultima vez que actualizo Mercado Libre, y
si es asi, lo corre en ese momento. Si la compu estuvo apagada durante la
"ventana" de 20hs, lo va a correr apenas se prenda de nuevo -- no se puede
quedar trabado indefinidamente como paso con la tarea separada.

COMO INTEGRARLO:
1. Agregar estos imports arriba del todo de orquestador.py (si no estan ya):
     import json
     import os
     import subprocess
     import sys
     from datetime import datetime, timedelta

2. Pegar las 3 funciones de abajo en cualquier parte de orquestador.py
   (por ejemplo, antes del bloque `if __name__ == "__main__":`).

3. Dentro del bloque principal donde ya llamas a sigma, digip_pedidos, etc,
   agregar UNA linea mas, en el orden que prefieras (sugerido: al final):

     actualizar_mercadolibre_si_corresponde()

   Quedaria algo asi (ejemplo, ajustar a como esta el tuyo realmente):

     if __name__ == "__main__":
         extraer_sigma()
         extraer_digip_pedidos()
         extraer_digip_preparaciones()
         actualizar_costos()
         correr_modelo()
         prorratear_flete()
         clasificar_clientes()
         actualizar_mercadolibre_si_corresponde()   # <-- NUEVA LINEA

4. Listo. La primera vez que corra el pipeline (cada 1-2hs), como no existe
   todavia el archivo estado_ml.json, va a correr mercadolibre.py de una,
   y de ahi en mas solo cuando pasen 20+ horas desde la ultima corrida OK.
"""

ESTADO_ML_PATH = "estado_ml.json"
HORAS_MINIMAS_ENTRE_CORRIDAS_ML = 20


def necesita_actualizar_ml():
    """True si nunca corrio, o si paso mas tiempo del minimo desde la ultima vez OK."""
    if not os.path.exists(ESTADO_ML_PATH):
        return True
    try:
        with open(ESTADO_ML_PATH) as f:
            estado = json.load(f)
        ultima = datetime.fromisoformat(estado["ultima_corrida_ok"])
    except (ValueError, KeyError, json.JSONDecodeError):
        # archivo corrupto o con formato viejo -> forzar actualizacion para curarlo
        return True
    return datetime.now() - ultima >= timedelta(hours=HORAS_MINIMAS_ENTRE_CORRIDAS_ML)


def marcar_ml_actualizado_ok():
    with open(ESTADO_ML_PATH, "w") as f:
        json.dump({"ultima_corrida_ok": datetime.now().isoformat()}, f, indent=2)


def actualizar_mercadolibre_si_corresponde():
    print("\n=== Chequeo de actualizacion Mercado Libre ===")
    if not necesita_actualizar_ml():
        print(f"  Todavia no pasaron {HORAS_MINIMAS_ENTRE_CORRIDAS_ML}hs desde la ultima corrida OK. Se saltea.")
        return

    print("  Corriendo mercadolibre.py...")
    # sys.executable = el mismo interprete de Python (venv) que esta corriendo el orquestador ahora mismo
    resultado = subprocess.run(
        [sys.executable, "mercadolibre.py"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    print(resultado.stdout)

    if resultado.returncode != 0:
        print("  ERROR corriendo mercadolibre.py, NO se marca como actualizado (se reintenta en la proxima corrida del pipeline):")
        print(resultado.stderr)
        return

    marcar_ml_actualizado_ok()
    print("  mercadolibre.py OK. Proxima corrida programada en ~" + str(HORAS_MINIMAS_ENTRE_CORRIDAS_ML) + "hs.")

"""
Orquestador del pipeline de datos.
Corre cada script en orden, con reintentos automaticos si falla por
problemas transitorios (conexion, timeouts de Supabase, etc.).

Si un paso falla despues de agotar los reintentos, se DETIENE el pipeline
(porque los pasos siguientes dependen de los datos de los anteriores),
salvo mercadolibre.py que es opcional e independiente.

Uso:
    python orquestador.py            -> corre el pipeline normal (sin ML)
    python orquestador.py --con-ml   -> incluye tambien mercadolibre.py al final
"""

import subprocess
import sys
import time
import datetime
import argparse

# (nombre_script, cantidad_de_reintentos, segundos_de_espera_entre_intentos)
PASOS = [
    ("sigma.py", 3, 60),
    ("digip_pedidos.py", 3, 60),
    ("digip_preparaciones.py", 3, 60),
    ("costos.py", 2, 30),
    ("modelo.py", 3, 60),
    ("prorratear_flete.py", 2, 30),
    ("clasificar_clientes.py", 2, 30),
]

PASO_ML = ("mercadolibre.py", 2, 60)  # opcional, mas pesado y lento

ARCHIVO_LOG = "orquestador_log.txt"


def log(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{stamp}] {msg}"
    print(linea)
    with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def correr_paso(script, intentos, espera):
    for intento in range(1, intentos + 1):
        log(f"Ejecutando {script} (intento {intento}/{intentos})...")
        resultado = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
        )
        if resultado.returncode == 0:
            log(f"OK: {script} termino sin errores.")
            return True

        # Guardamos el error completo en el log (no solo en pantalla)
        log(f"ERROR en {script} (intento {intento}/{intentos}):")
        log(resultado.stderr[-1500:] if resultado.stderr else "(sin salida de error)")

        if intento < intentos:
            log(f"Esperando {espera}s antes de reintentar {script}...")
            time.sleep(espera)

    log(f"FALLO DEFINITIVO: {script} no pudo completarse tras {intentos} intentos.")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--con-ml", action="store_true",
                         help="Incluir tambien la actualizacion de Mercado Libre (mas lenta)")
    args = parser.parse_args()

    log("========== INICIO DEL ORQUESTADOR ==========")
    inicio = time.time()

    for script, intentos, espera in PASOS:
        exito = correr_paso(script, intentos, espera)
        if not exito:
            log(f"Se detiene el pipeline: los pasos siguientes dependen de {script}.")
            log("========== ORQUESTADOR TERMINADO CON ERRORES ==========")
            sys.exit(1)

    if args.con_ml:
        script, intentos, espera = PASO_ML
        exito = correr_paso(script, intentos, espera)
        if not exito:
            # ML es independiente: si falla, no invalida el resto del pipeline ya corrido
            log(f"AVISO: {script} fallo, pero el resto del pipeline ya se completo bien.")

    duracion = round((time.time() - inicio) / 60, 1)
    log(f"========== ORQUESTADOR TERMINADO CON EXITO ({duracion} min) ==========")


if __name__ == "__main__":
    main()
