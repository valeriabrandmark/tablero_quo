import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from conexion import crear_engine

load_dotenv()

try:
    engine = crear_engine()
    with engine.connect() as conexion:
        resultado = conexion.execute(text("SELECT version();"))
        print("CONEXION EXITOSA")
        print(resultado.fetchone()[0])
except Exception as error:
    print("ERROR DE CONEXION:")
    print(error)