"""Arma la propuesta de objetivos de un mes, lista para revisar y cargar.

    python proponer_objetivos.py 2026-11

escribe objetivos_mensuales/2026-11 objetivos (propuesta).xlsx: la misma
planilla de siempre, con una hoja mas ("Por que") que dice de donde sale cada
numero y que candidatos se miraron y no entraron.

LA PROPUESTA NO SE CARGA SOLA. objetivos.py ignora el archivo por el
"(propuesta)" del nombre. Se revisa, se cambia lo que haga falta y se guarda
como "2026-11 objetivos.xlsx": recien ahi entra al tablero. Un numero que
nadie miro no deberia llegar a la pagina de un vendedor.

Que se propone y con que reglas: propuesta_objetivos.py (puro, con sus
pruebas). Los candidatos salen de propuesta_objetivos.sql.

Tambien corre desde GitHub, sin instalar nada: pestaña Actions, "Proponer
objetivos", Run workflow. El Excel queda para bajar en la corrida.
"""

import argparse
import os
import sys
from datetime import date

from dotenv import load_dotenv
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from sqlalchemy import text

import objetivos_planilla as planilla
import propuesta_objetivos as propuesta
from calendario import mes_comercial
from conexion import crear_engine

load_dotenv()

CARPETA_SQL = os.path.dirname(os.path.abspath(__file__))


def candidatos(con, mes, vendedores, meses):
    sql = open(os.path.join(CARPETA_SQL, "propuesta_objetivos.sql"), encoding="utf-8").read()
    r = propuesta.ritmo(mes)
    filas = con.execute(text(sql), {
        "vendedores": vendedores,
        "codigos": [propuesta.CODIGO_SIGMA[v] for v in vendedores],
        "meses": meses,
        "mes": mes,
        "patron": propuesta.PATRON_VERANO,
        "verano_desde": propuesta.VERANO_DESDE,
        "verano_hasta": propuesta.VERANO_HASTA,
        # Fuera de temporada el ritmo es 0 y elegir() no toma ninguno de
        # temporada: la consulta los devuelve igual, para la hoja "Por que".
        "ritmo": r or 0,
        "limite": 20,
    }).mappings().all()
    return [dict(f) for f in filas]


def empresa_del_mes_anterior(con, mes, vendedores):
    """Los objetivos de ventas netas y clientes del mes anterior, tal cual.

    No se recalculan: el 10/11/2026 se decidio dejarlos como estaban (no se
    cumplieron, pero tampoco se pueden bajar por ahora).
    """
    import objetivos as carga
    anterior = propuesta.meses_anteriores(mes, 1)[0]
    grupos = carga.grupos_en_base(con)
    filas = con.execute(text(
        "select o.vendedor, o.grupo, o.cantidad from gold.objetivos o "
        "join gold.objetivos_grupo g on g.grupo = o.grupo "
        "where o.mes_comercial = :m and g.criterio = 'empresa' and o.vendedor = any(:v)"
    ), {"m": anterior, "v": vendedores}).all()
    return planilla.filas_desde_base(
        mes, [(v, g, float(c)) for v, g, c in filas],
        {g: d for g, d in grupos.items() if d["criterio"] == "empresa"})


def escribir(ruta, mes, meses, elegidos, descartados, filas_empresa):
    filas = filas_empresa + propuesta.filas_producto(mes, elegidos)
    planilla.escribir_planilla(ruta, filas)

    wb = load_workbook(ruta)
    ws = wb.create_sheet("Por que", 1)
    for linea in propuesta.reglas_por_que(mes, meses):
        ws.append([linea])
        ws.merge_cells(start_row=ws.max_row, start_column=1,
                       end_row=ws.max_row, end_column=len(propuesta.ENCABEZADO_POR_QUE))
    ws.append([])
    ws.append(propuesta.ENCABEZADO_POR_QUE)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for fila in propuesta.filas_por_que(elegidos, descartados, meses):
        ws.append(fila)
        ws.cell(row=ws.max_row, column=5).alignment = Alignment(wrap_text=True, vertical="top")
    for col, ancho in zip("ABCDEFGHIJ", (10, 40, 11, 10, 70, 16, 14, 11, 9, 9)):
        ws.column_dimensions[col].width = ancho
    wb.save(ruta)


def main():
    parser = argparse.ArgumentParser(description="Propone los objetivos de un mes comercial.")
    parser.add_argument("mes", nargs="?", default=mes_comercial(date.today()),
                        help="AAAA-MM. Sin esto, el mes comercial de hoy.")
    parser.add_argument("--carpeta", default=planilla.CARPETA)
    args = parser.parse_args()
    if not planilla.PATRON_MES.fullmatch(args.mes):
        parser.error(f"'{args.mes}' no es AAAA-MM")

    mes, vendedores = args.mes, propuesta.VENDEDORES_PROPUESTA
    meses = propuesta.meses_anteriores(mes)
    with crear_engine().begin() as con:
        cands = candidatos(con, mes, vendedores, meses)
        empresa = empresa_del_mes_anterior(con, mes, vendedores)

    elegidos, descartados = propuesta.elegir(cands, vendedores, mes, meses)
    os.makedirs(args.carpeta, exist_ok=True)
    ruta = os.path.join(args.carpeta, propuesta.nombre_archivo_propuesta(mes))
    escribir(ruta, mes, meses, elegidos, descartados, empresa)

    # Se valida con las MISMAS reglas que la carga, para que lo que se
    # propone se pueda subir sin tocar (salvo el nombre).
    _, _, errores = planilla.leer_hoja(
        planilla.nombre_de_archivo(mes),
        load_workbook(ruta, data_only=True)[planilla.HOJA].iter_rows(values_only=True))
    for v, lista in elegidos.items():
        print(f"\n  {v}:")
        for e in lista:
            print(f"    {e['objetivo']:>6}  {e['nombre']}  ({e['tipo']})")
    print(f"\n  Listo: {ruta}")
    if errores:
        print("\n  OJO, la planilla no pasaria la carga:")
        for e in errores:
            print(f"   - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
