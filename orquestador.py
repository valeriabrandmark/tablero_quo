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