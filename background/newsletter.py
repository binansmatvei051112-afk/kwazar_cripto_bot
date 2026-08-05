"""
Утренняя рассылка: топ-3 растущих монет за 24ч всем пользователям бота.
Запускается по расписанию (APScheduler) из main.py.
"""
import asyncio
from config import logger
from bot_instance import bot
from constants import POPULAR_COINS
from database_and_api import get_cached_stats, get_all_users

async def send_morning_newsletter():
    logger.info("Начинаем утреннюю рассылку...")
    stats = await get_cached_stats()
    
    if not stats:
        logger.error("Не удалось получить статистику для рассылки.")
        return

    popular_stats = []
    for coin in POPULAR_COINS:
        symbol = f"{coin}USDT"
        if symbol in stats:
            popular_stats.append({
                'coin': coin,
                'change': stats[symbol]['price_change_percent']
            })


    top_3 = sorted(popular_stats, key=lambda x: x['change'], reverse=True)[:3]

    text = "🌅 <b>Доброе утро, трейдеры!</b>\n\n🔥 <b>Топ-3 растущих монет за 24 часа:</b>\n\n"
    for item in top_3:
        sign = "🟢 +" if item['change'] > 0 else "🔴 "
        text += f"🚀 <b>{item['coin']}</b>: <i>{sign}{item['change']}%</i>\n"

    text += "\n<i>Заходи в меню, чтобы поставить новые алерты на сегодня! /start</i>"


    users = await get_all_users()
    count = 0
    
    for user_id in users:
        try:
            await bot.send_message(chat_id=user_id, text=text)
            count += 1
            
            await asyncio.sleep(0.05) 
        except Exception as e:
            
            logger.warning(f"Не удалось отправить рассылку юзеру {user_id}: {e}")

    logger.info(f"Рассылка завершена. Доставлено {count} пользователям.")
