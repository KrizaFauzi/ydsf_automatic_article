import os
from sqlmodel import create_engine, Session
from dotenv import load_dotenv

load_dotenv()

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "33100")
DB_USER     = os.getenv("DB_USER", "usr_aiarticle")
DB_PASSWORD = os.getenv("DB_PASSWORD", "usr_aiarticle")
DB_NAME     = os.getenv("DB_NAME", "dbaiarticle")

# pymysql sebagai driver untuk MariaDB
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(
    DATABASE_URL,
    echo=True,
    connect_args={
        "init_command": f"USE `{DB_NAME}`"
    }
)

def get_session():
    with Session(engine) as session:
        yield session