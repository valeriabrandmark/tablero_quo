import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from conexion import crear_engine

load_dotenv()

engine = crear_engine(connect_args={"client_encoding": "utf8"})

with engine.connect() as con:
    resultado = con.execute(text("SELECT COUNT(*) FROM bronze.sigma_ventas;"))
    print("Conectado OK. Filas en bronze.sigma_ventas:", resultado.scalar())