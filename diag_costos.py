import pandas as pd
archivo = "costos_mensuales/2026-05.xlsx"
dfo = pd.read_excel(archivo, sheet_name="Ofertas", header=0)
col = dfo.iloc[:, 9].astype(str).str.strip()
con_oferta = col[(col != "0,00%") & (col != "nan") & (col != "")]
print(f"Filas con oferta != 0: {len(con_oferta)}")