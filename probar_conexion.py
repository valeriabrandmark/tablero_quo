import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

url = (
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

try:
    engine = create_engine(url)
    with engine.connect() as conexion:
        resultado = conexion.execute(text("SELECT version();"))
        print("CONEXION EXITOSA")
        print(resultado.fetchone()[0])
except Exception as error:
    print("ERROR DE CONEXION:")
    print(error)