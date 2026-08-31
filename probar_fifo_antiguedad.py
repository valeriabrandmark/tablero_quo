"""Prueba de la cuenta FIFO de ml_antiguedad.py, con datos armados a mano.

No toca la API ni la base: se le pasan operaciones inventadas y se comprueba
que los tramos den lo que tienen que dar. Es la parte del calculo que se puede
verificar sin credenciales, y es justo donde es facil equivocarse -- el primer
intento consumia las unidades sobrantes sobre una tupla, asi que no descontaba
nada y devolvia 15 unidades donde habia 5.

    python probar_fifo_antiguedad.py
"""

from datetime import datetime, timedelta, timezone
from ml_antiguedad import DIAS_HISTORIA, DIAS_POR_LLAMADA, antiguedad, ventanas

HOY = datetime(2026, 8, 31, tzinfo=timezone.utc)

def op(dias_atras, delta, tipo="INBOUND_RECEPTION"):
    f = (HOY - timedelta(days=dias_atras)).isoformat().replace("+00:00", "Z")
    return {"date_created": f, "type": tipo, "detail": {"available_quantity": delta}}

def check_bool(nombre, ok, detalle=""):
    print(("OK  " if ok else "MAL ") + nombre)
    if not ok and detalle:
        print("     " + detalle)
    return ok

def check(nombre, ops, stock, esperado):
    r = antiguedad(ops, stock, hoy=HOY)
    ok = all(r[k] == v for k, v in esperado.items())
    print(("OK  " if ok else "MAL ") + nombre)
    if not ok:
        print("     esperado:", esperado)
        print("     obtenido:", {k: r[k] for k in esperado})
    return ok

todo = []

# 1. Una entrada sola, sin salidas: todo en el tramo de su edad.
todo.append(check("entrada unica de 10 hace 5 dias",
    [op(5, 10)], 10,
    {"unidades": 10, "u_0_30": 10, "u_mas_120": 0, "incompleto": False}))

# 2. FIFO de verdad: entran 10 hace 200 dias y 5 hace 10; se venden 10.
#    Las que se van son las VIEJAS, asi que queda solo lo nuevo.
todo.append(check("FIFO consume las viejas primero",
    [op(200, 10), op(10, 5), op(1, -10, "SALE_CONFIRMATION")], 5,
    {"unidades": 5, "u_0_30": 5, "u_mas_120": 0, "incompleto": False}))

# 3. Salidas parciales: quedan 4 viejas y 5 nuevas.
todo.append(check("salida parcial deja mezcla de tramos",
    [op(200, 10), op(10, 5), op(1, -6, "SALE_CONFIRMATION")], 9,
    {"unidades": 9, "u_0_30": 5, "u_mas_120": 4, "incompleto": False}))

# 4. Historia incompleta: hay 20 unidades pero solo se ven 5 de entrada.
#    Las 15 que faltan entraron antes de la ventana -> las mas viejas.
todo.append(check("historia incompleta cuenta como +120",
    [op(10, 5)], 20,
    {"unidades": 20, "u_0_30": 5, "u_mas_120": 15, "incompleto": True}))

# 5. Los cuatro tramos, una unidad en cada uno.
todo.append(check("cada tramo en su lugar",
    [op(10, 1), op(45, 1), op(75, 1), op(100, 1), op(300, 1)], 5,
    {"u_0_30": 1, "u_31_60": 1, "u_61_90": 1, "u_91_120": 1, "u_mas_120": 1}))

# 6. Sin operaciones y con stock: todo desconocido, o sea viejo.
todo.append(check("sin historia, todo al tramo mas viejo",
    [], 7, {"unidades": 7, "u_mas_120": 7, "incompleto": True}))

# 7. Promedio de dias ponderado por unidades.
r = antiguedad([op(10, 3), op(50, 1)], 4, hoy=HOY)
esperado = round((10*3 + 50*1) / 4, 1)
ok = r["dias_promedio"] == esperado
print(("OK  " if ok else "MAL ") + f"promedio ponderado ({r['dias_promedio']} vs {esperado})")
todo.append(ok)


# ---------------------------------------------------------------------------
# Las ventanas de fechas. La API cuenta LOS DOS EXTREMOS, asi que una ventana de
# "60 dias" va de D a D+59: con D+60 son 61 y contesta 400 en todas las
# llamadas. Es exactamente lo que paso la primera vez que se corrio.
# ---------------------------------------------------------------------------

vs = ventanas(HOY.date())
largos = [(h - d).days + 1 for d, h in vs]
todo.append(check_bool("ninguna ventana se pasa del maximo",
                       all(n <= DIAS_POR_LLAMADA for n in largos),
                       f"largos: {largos}"))
todo.append(check_bool("las ventanas no dejan huecos ni se pisan",
                       all((vs[i+1][0] - vs[i][1]).days == 1 for i in range(len(vs)-1))))
todo.append(check_bool("cubren toda la historia pedida",
                       (vs[-1][1] - vs[0][0]).days == DIAS_HISTORIA))

print("\n" + ("TODO OK" if all(todo) else "HAY FALLAS"))
