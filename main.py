"""
Точка входа в бота.

Ничего "бизнесового" здесь больше нет — только:
1) импорт всех хендлеров (регистрирует их на dp через handlers/__init__.py),
2) запуск фоновых воркеров и планировщика в on_startup(),
3) запуск polling.

Если нужно добавить/изменить хендлер — иди в handlers/<нужный файл>.py.
Если нужно поменять логику проверки алертов — background/alert_checker.py.
"""
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import logger
from bot_instance import bot, dp
from database_and_api import init_db

# Импорт пакета handlers регистрирует все @dp.message/@dp.callback_query хендлеры.
import handlers  # noqa: F401

from background.cache_updater import cache_updater_loop
from background.alert_checker import check_alerts_loop
from background.newsletter import send_morning_newsletter


async def on_startup():
    asyncio.create_task(cache_updater_loop())
    asyncio.create_task(check_alerts_loop())

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_morning_newsletter, 'cron', hour=8, minute=30)
    scheduler.start()
    logger.info("Планировщик задач запущен!")


dp.startup.register(on_startup)


async def main():
    logger.info("Инициализация базы данных...")
    await init_db()
    logger.info("Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception:
        logger.critical("Критическая ошибка при запуске приложения!", exc_info=True)
