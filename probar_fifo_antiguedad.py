"""Prueba de la cuenta FIFO de ml_antiguedad.py, con datos armados a mano.

No toca la API ni la base: se le pasan operaciones inventadas y se comprueba
que los tramos den lo que tienen que dar. Es la parte del calculo que se puede
verificar sin credenciales, y es justo donde es facil equivocarse -- el primer
intento consumia las unidades sobrantes sobre una tupla, asi que no descontaba
nada y devolvia 15 unidades donde habia 5.

    python probar_fifo_antiguedad.py
"""

from datetime import datetime, timedelta, timezone
from ml_antiguedad import (DIAS_HISTORIA, DIAS_POR_LLAMADA, antiguedad,
                           stock_segun_operaciones, ventanas)

HOY = datetime(2026, 8, 31, tzinfo=timezone.utc)

def op(dias_atras, delta, tipo="INBOUND_RECEPTION", saldo=None):
    f = (HOY - timedelta(days=dias_atras)).isoformat().replace("+00:00", "Z")
    o = {"date_created": f, "type": tipo, "detail": {"available_quantity": delta}}
    if saldo is not None:
        o["result"] = {"available_quantity": saldo}
    return o

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

# ---------------------------------------------------------------------------
# Solo INBOUND_RECEPTION arranca el reloj. Una venta cancelada devuelve unidades
# que YA tenian edad: contarlas como ingreso nuevo rejuvenece stock viejo.
# ---------------------------------------------------------------------------

todo.append(check("una cancelacion no rejuvenece el stock",
    [op(200, 10),
     op(5, -4, "SALE_CONFIRMATION"),
     op(2, 4, "SALE_CANCELATION")], 10,
    {"unidades": 10, "u_0_30": 0, "u_mas_120": 10, "incompleto": False}))

todo.append(check("una devolucion tampoco",
    [op(150, 6), op(10, -6, "SALE_CONFIRMATION"), op(1, 2, "SALE_RETURN")], 2,
    {"unidades": 2, "u_mas_120": 2, "u_0_30": 0}))

# ---------------------------------------------------------------------------
# El stock de referencia sale de la ultima operacion, no de la tabla: la tabla
# la refresca el catalogo una vez por dia y a la tarde ya esta vieja.
# ---------------------------------------------------------------------------

todo.append(check_bool("el stock sale del saldo de la ultima operacion",
    stock_segun_operaciones([op(9, 5, saldo=300), op(1, -1, "SALE_CONFIRMATION", saldo=286)]) == 286))
todo.append(check_bool("sin operaciones no inventa un stock",
    stock_segun_operaciones([]) is None))

vs = ventanas(HOY.date())
largos = [(h - d).days + 1 for d, h in vs]
todo.append(check_bool("ninguna ventana se pasa del maximo",
                       all(n <= DIAS_POR_LLAMADA for n in largos),
                       f"largos: {largos}"))
todo.append(check_bool("las ventanas no dejan huecos ni se pisan",
                       all((vs[i+1][0] - vs[i][1]).days == 1 for i in range(len(vs)-1))))
# LA REGLA QUE FALTABA, y la que hizo fallar los 20 inventarios: la API rechaza
# una ventana de un solo dia con "date_from can't be greater or equal to
# date_to". Pasa cuando la historia es multiplo del tamaño de ventana.
todo.append(check_bool("ninguna ventana es de un solo dia",
                       all(d < h for d, h in vs),
                       str([(str(d), str(h)) for d, h in vs if d >= h])))
todo.append(check_bool("la ventana mas nueva termina hoy",
                       vs[-1][1] == HOY.date()))
todo.append(check_bool("cubren la historia pedida",
                       (vs[-1][1] - vs[0][0]).days >= DIAS_HISTORIA - 1,
                       f"cubren {(vs[-1][1] - vs[0][0]).days} de {DIAS_HISTORIA}"))

# El caso que rompio: una historia que es multiplo exacto del tamaño de ventana.
import ml_antiguedad as _m
_orig = _m.DIAS_HISTORIA
for h in (60, 120, 180, 240, 365, 61, 119):
    _m.DIAS_HISTORIA = h
    v = _m.ventanas(HOY.date())
    ok = v and all(d < h2 for d, h2 in v) and all((h2 - d).days + 1 <= DIAS_POR_LLAMADA for d, h2 in v)
    todo.append(check_bool(f"historia de {h} dias: ventanas validas", bool(ok), str(v)))
_m.DIAS_HISTORIA = _orig

print("\n" + ("TODO OK" if all(todo) else "HAY FALLAS"))
