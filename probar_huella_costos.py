"""Pruebas de la huella de los Excel de costos, sin base ni red.

POR QUE EXISTE. `costos.py --si-cambio` se llama en CADA corrida del
orquestador y el que decide si hay algo que hacer es la huella. Si se equivoca
falla para los dos lados y ninguno avisa:

  - de mas: recarga los cinco meses cada dos horas para escribir exactamente lo
    mismo. Es lo que estuvo pasando en GitHub Actions, porque la huella incluia
    la fecha de modificacion y el checkout la resetea en cada corrida;
  - de menos: un Excel corregido no entra nunca, y el tablero sigue mostrando
    margenes con el costo viejo.

    python probar_huella_costos.py
"""

import os
import shutil
import tempfile

# costos.py arma el engine al importarse. NO se conecta a nada --SQLAlchemy solo
# arma la URL-- pero sin estas variables ni siquiera puede parsearla.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", "nadie")
os.environ.setdefault("DB_PASS", "nada")
os.environ.setdefault("DB_NAME", "ninguna")

import costos  # noqa: E402

FALLOS = []


def revisar(nombre, obtenido, esperado):
    if obtenido == esperado:
        print(f"OK  {nombre}")
    else:
        print(f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
        FALLOS.append(nombre)


carpeta = tempfile.mkdtemp(prefix="huella-")
costos.CARPETA_COSTOS = carpeta


def escribir(nombre, contenido):
    with open(os.path.join(carpeta, nombre), "wb") as f:
        f.write(contenido)


def borrar(nombre):
    os.remove(os.path.join(carpeta, nombre))


try:
    # Carpeta vacia: no explota y da algo estable.
    vacia = costos.huella_de_los_excel()
    revisar("carpeta vacia: no explota", vacia == costos.huella_de_los_excel(), True)

    escribir("2026-09.xlsx", b"lista de septiembre")
    escribir("2026-08.xlsx", b"lista de agosto")
    base = costos.huella_de_los_excel()

    revisar("con archivos, la huella cambia", base != vacia, True)
    revisar("dos veces seguidas da lo mismo",
            costos.huella_de_los_excel(), base)

    # EL CASO QUE FALLABA. En GitHub Actions el checkout deja los archivos con
    # la fecha de la corrida: si la fecha entrara en la huella, esto daria
    # distinto y recargaria los cinco meses cada dos horas.
    os.utime(os.path.join(carpeta, "2026-09.xlsx"), (0, 0))
    revisar("cambiar la fecha de modificacion NO cambia la huella",
            costos.huella_de_los_excel(), base)

    # Y lo que si tiene que cambiarla.
    escribir("2026-09.xlsx", b"lista de septiembre corregida")
    revisar("cambiar el contenido si la cambia",
            costos.huella_de_los_excel() != base, True)

    escribir("2026-09.xlsx", b"lista de septiembre")
    revisar("volver el contenido atras la deja como estaba",
            costos.huella_de_los_excel(), base)

    # Desde que el nombre dice la vigencia, renombrar cambia lo que hay que
    # escribir aunque el contenido sea identico.
    os.rename(os.path.join(carpeta, "2026-09.xlsx"),
              os.path.join(carpeta, "2026-09-18.xlsx"))
    revisar("renombrar el archivo la cambia",
            costos.huella_de_los_excel() != base, True)
    os.rename(os.path.join(carpeta, "2026-09-18.xlsx"),
              os.path.join(carpeta, "2026-09.xlsx"))

    escribir("2026-09-18.xlsx", b"lista de media de mes")
    revisar("agregar un archivo la cambia",
            costos.huella_de_los_excel() != base, True)
    borrar("2026-09-18.xlsx")
    revisar("sacarlo la deja como estaba", costos.huella_de_los_excel(), base)

    # Un cambio en lo que el script ESCRIBE tambien tiene que forzar recarga,
    # aunque ningun Excel se haya tocado: si no, la columna agregada hoy queda
    # vacia hasta que alguien edite un archivo.
    version = costos.VERSION_ESQUEMA
    costos.VERSION_ESQUEMA = version + 1
    revisar("subir VERSION_ESQUEMA la cambia",
            costos.huella_de_los_excel() != base, True)
    costos.VERSION_ESQUEMA = version
    revisar("y volverla atras la restaura", costos.huella_de_los_excel(), base)

    # Los .xlsx y nada mas: al lado de los Excel puede quedar cualquier cosa.
    escribir("notas.txt", b"no es una lista de costos")
    revisar("un archivo que no es .xlsx no la toca",
            costos.huella_de_los_excel(), base)
finally:
    shutil.rmtree(carpeta, ignore_errors=True)


print("\nTODO OK" if not FALLOS else f"\n{len(FALLOS)} FALLARON: {', '.join(FALLOS)}")
raise SystemExit(1 if FALLOS else 0)
