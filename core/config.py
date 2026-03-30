import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    APP_NAME       : str = os.getenv("APP_NAME", "Chatbot")
    SECRET_KEY     : str = os.getenv("SECRET_KEY", "758377195hhashduqw979149kalll99u14bj")
    ALGORITHM      : str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES : int = 60 * 24  # 1 hari

settings = Settings()

