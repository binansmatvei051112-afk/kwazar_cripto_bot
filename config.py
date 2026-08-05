"""
Конфигурация проекта: переменные окружения (.env) и логгер.
Импортируется первым во всех модулях, где нужен BOT_TOKEN/ADMIN_ID/logger.
"""
import logging
from logging.handlers import RotatingFileHandler
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = "bot.log"

file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)
