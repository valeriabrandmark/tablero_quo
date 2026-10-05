"""Cuando un fallo tiene que pintar la corrida de rojo y cuando no.

Reproduce el episodio real del 04-05/10/2026: ocho corridas en rojo porque la
API de DIGIP devolvio 500, sin que el stock estuviera nunca mas de ~3 horas
viejo. Con `avisar_tras: 3` ese episodio no habria mandado un solo mail, y una
caida de verdad si.
"""

from ventana import corrida_en_rojo

OK = 0
MAL = 0


def probar(nombre, obtenido, esperado):
    global OK, MAL
    if obtenido == esperado:
        OK += 1
        print(f"  ok   {nombre}")
    else:
        MAL += 1
        print(f"  MAL  {nombre}\n       esperaba {esperado!r}\n       obtuvo   {obtenido!r}")


def estado(**fallos):
    return {c: {"ok": None, "fallos": n, "ultimo_fallo": None, "error": None}
            for c, n in fallos.items()}


print("\nSin `avisar_tras` se comporta como siempre")
probar("un fallo basta", corrida_en_rojo(["sigma.py"], estado(**{"sigma.py": 1}), {}), ["sigma.py"])
probar("umbral None = 1", corrida_en_rojo(["sigma.py"], estado(**{"sigma.py": 1}),
                                          {"sigma.py": None}), ["sigma.py"])
probar("sin fallados, nada", corrida_en_rojo([], estado(), {}), [])

print("\nCon `avisar_tras: 3`")
u = {"digip.py": 3}
probar("1er fallo: callado", corrida_en_rojo(["digip.py"], estado(**{"digip.py": 1}), u), [])
probar("2do fallo: callado", corrida_en_rojo(["digip.py"], estado(**{"digip.py": 2}), u), [])
probar("3er fallo: avisa", corrida_en_rojo(["digip.py"], estado(**{"digip.py": 3}), u), ["digip.py"])
probar("4to fallo: avisa", corrida_en_rojo(["digip.py"], estado(**{"digip.py": 4}), u), ["digip.py"])

print("\nEl episodio real del 04-05/10 (nunca mas de 2 seguidos)")
# Cada corrida fallada arranca de cero porque la del medio salio bien.
rachas = [1, 2, 1, 2, 1, 1, 2]
avisos = [c for r in rachas
          for c in corrida_en_rojo(["digip.py"], estado(**{"digip.py": r}), u)]
probar("ocho fallos, cero mails", avisos, [])

print("\nUna caida de verdad si avisa")
sostenida = [1, 2, 3, 4, 5]
avisos = [c for r in sostenida
          for c in corrida_en_rojo(["digip.py"], estado(**{"digip.py": r}), u)]
probar("avisa del 3ro en adelante", avisos, ["digip.py"] * 3)

print("\nUn paso tolerante no tapa a los demas")
probar(
    "digip callado, sigma avisa",
    corrida_en_rojo(["digip.py", "sigma.py"],
                    estado(**{"digip.py": 1, "sigma.py": 1}), u),
    ["sigma.py"],
)
probar(
    "los dos pasados de umbral",
    corrida_en_rojo(["digip.py", "sigma.py"],
                    estado(**{"digip.py": 3, "sigma.py": 1}), u),
    ["digip.py", "sigma.py"],
)

print("\nBordes")
probar("paso sin registro cuenta 0 y no avisa",
       corrida_en_rojo(["digip.py"], {}, u), [])
probar("umbrales None no rompe",
       corrida_en_rojo(["sigma.py"], estado(**{"sigma.py": 1}), None), ["sigma.py"])
probar("formato viejo (texto) cuenta 0 fallos",
       corrida_en_rojo(["sigma.py"], {"sigma.py": "2026-10-01T10:00:00"}, {}), [])

print(f"\n{'TODO OK' if not MAL else 'HAY FALLAS'}: {OK} ok, {MAL} mal\n")
raise SystemExit(1 if MAL else 0)
