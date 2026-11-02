"""La marca de fin de corrida. Sin base.

POR QUE EXISTE. El boton "Actualizar ahora" del tablero decide con esta marca
cuando decir "termino". Las dos formas de equivocarse son silenciosas:

1. QUE ANOTE DE MENOS. Si la marca se escribiera con una copia de las listas
   tomada al registrarla --el error que es facil cometer-- diria siempre que no
   fallo nada y que no corrio ningun paso, porque al momento de `atexit.register`
   las listas estan vacias. La pantalla mostraria un verde que no significa nada.

2. QUE VOLTEE LA CORRIDA. Si la base no contesta justo al final, escribir la
   marca no puede convertir un pipeline que anduvo bien en una corrida en rojo.

    python probar_marca_corrida.py
"""

import orquestador

FALLOS = []


def revisar(nombre, obtenido, esperado):
    ok = obtenido == esperado
    print(f"OK  {nombre}" if ok else
          f"MAL {nombre}\n     esperado: {esperado}\n     obtenido: {obtenido}")
    if not ok:
        FALLOS.append(nombre)


class EstadoFalso:
    """Se queda con lo ultimo que le guardaron. Puede explotar a pedido."""

    def __init__(self, explota=False):
        self.guardado = {}
        self.explota = explota

    def guardar(self, clave, valor):
        if self.explota:
            raise RuntimeError("la base no contesta")
        self.guardado[clave] = valor


def anotar(corrieron, fallados, salteados=(), sin_tiempo=(), explota=False):
    """Corre la funcion con un estado de mentira y devuelve lo que anoto."""
    falso = EstadoFalso(explota)
    real = orquestador.estado
    orquestador.estado = falso
    try:
        orquestador.anotar_fin_de_corrida(
            orquestador.time.time(), corrieron, fallados, list(salteados), list(sin_tiempo))
    finally:
        orquestador.estado = real
    return falso.guardado


# --- LA CLAVE TIENE QUE SER LA QUE LEE EL TABLERO -------------------------

marca = anotar(["modelo.py"], [])
revisar("escribe bajo la clave 'corrida'", list(marca), ["corrida"])
revisar("y esa es la constante", orquestador.CLAVE_CORRIDA, "corrida")

# --- QUE SE ANOTA ---------------------------------------------------------

m = anotar(["sigma.py --ventas", "modelo.py"], [])
revisar("ok cuando no fallo nada", m["corrida"]["ok"], True)
revisar("lista lo que corrio", m["corrida"]["corrieron"], ["sigma.py --ventas", "modelo.py"])
revisar("y nada fallado", m["corrida"]["fallaron"], [])
revisar("trae la hora de fin", isinstance(m["corrida"]["fin"], str), True)
revisar("y cuanto tardo", isinstance(m["corrida"]["duracion_min"], float), True)

m = anotar(["modelo.py"], ["tiendanube.py"])
revisar("un paso fallado deja ok en False", m["corrida"]["ok"], False)
revisar("y queda nombrado", m["corrida"]["fallaron"], ["tiendanube.py"])

# UN SALTEADO POR FRECUENCIA NO ES UN FALLO. Es el pipeline funcionando como se
# lo penso: si contara como fallo, casi ninguna corrida saldria ok -- en una
# corrida normal se saltean seis o siete pasos.
m = anotar(["modelo.py"], [], salteados=["ml_competencia.py", "tiendanube.py"])
revisar("saltear no es fallar", m["corrida"]["ok"], True)
revisar("pero se anota igual", m["corrida"]["salteados"], ["ml_competencia.py", "tiendanube.py"])

m = anotar(["modelo.py"], [], sin_tiempo=["auditoria.py"])
revisar("lo que no entro en el presupuesto se anota", m["corrida"]["sin_tiempo"], ["auditoria.py"])
revisar("y tampoco es un fallo", m["corrida"]["ok"], True)

# --- LAS LISTAS SE LEEN AL FINAL, NO AL REGISTRAR -------------------------

# ESTE ES EL CASO QUE JUSTIFICA LA PRUEBA. `atexit.register` se llama cuando la
# corrida ARRANCA y las listas estan vacias; lo que tiene que anotarse es lo que
# termino pasando. Si alguien cambiara el `atexit.register` por algo que copie
# las listas --o reasignara `corrieron = []` despues de registrarlas-- la marca
# diria "no corrio nada, no fallo nada" en todas las corridas.
corrieron, fallados = [], []
falso = EstadoFalso()
real = orquestador.estado
orquestador.estado = falso
try:
    import atexit as _atexit
    _atexit.register(orquestador.anotar_fin_de_corrida,
                     orquestador.time.time(), corrieron, fallados, [], [])
    # La corrida "pasa" DESPUES de registrar, como en la vida real.
    corrieron.append("modelo.py")
    fallados.append("tiendanube.py")
    _atexit._run_exitfuncs()
finally:
    orquestador.estado = real

revisar("ve los pasos que corrieron despues de registrar",
        falso.guardado["corrida"]["corrieron"], ["modelo.py"])
revisar("y los que fallaron despues",
        falso.guardado["corrida"]["fallaron"], ["tiendanube.py"])
revisar("asi que ok queda en False", falso.guardado["corrida"]["ok"], False)

# --- SI LA BASE NO CONTESTA, LA CORRIDA SIGUE -----------------------------

# Una marca que no se pudo escribir no tiene por que pintar de rojo un pipeline
# que anduvo bien. El boton se queda esperando y cae en su propio timeout, que
# es exactamente lo que hacia antes de que esta marca existiera.
try:
    anotar(["modelo.py"], [], explota=True)
    revisar("una base caida no levanta excepcion", True, True)
except Exception as e:
    revisar("una base caida no levanta excepcion", f"levanto {e!r}", True)

print(f"\n{len(FALLOS)} pruebas fallaron" if FALLOS else "\nTodo OK")
raise SystemExit(1 if FALLOS else 0)
