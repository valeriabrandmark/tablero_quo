import pandas as pd
ruta = "costos_mensuales/2026-07.xlsx"

print("=== Hojas del archivo ===")
xl = pd.ExcelFile(ruta)
print(xl.sheet_names)

print("\n=== Leyendo normal (header=0) ===")
df0 = pd.read_excel(ruta)
print("Tiene 'Codigo'?", "Codigo" in df0.columns)

print("\n=== Primeras 3 filas crudas (sin header) ===")
draw = pd.read_excel(ruta, header=None, nrows=3)
print(draw.to_string())