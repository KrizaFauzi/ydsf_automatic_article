"""
Logger utility untuk konsistensi logging di seluruh aplikasi.
Format: [MODULE] Message
"""

import logging
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(name)s] - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),  # print ke console
    ]
)


def get_logger(name: str) -> logging.Logger:
    """
    Dapatkan logger dengan nama tertentu.
    
    Args:
        name: Nama modul/komponen (e.g., "ai", "crawler", "auth")
    
    Returns:
        logging.Logger instance
    
    Example:
        logger = get_logger("auth")
        logger.info("User registered successfully")
    """
    return logging.getLogger(name)


# Convenience loggers untuk modul utama
logger_auth = get_logger("auth")
logger_chat = get_logger("chat")
logger_ai = get_logger("ai")
logger_crawler = get_logger("crawler")
logger_crud = get_logger("crud")
logger_db = get_logger("db")
