import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}",
    connect_args={"client_encoding": "utf8"}
)

with engine.connect() as con:
    resultado = con.execute(text("SELECT COUNT(*) FROM bronze.sigma_ventas;"))
    print("Conectado OK. Filas en bronze.sigma_ventas:", resultado.scalar())