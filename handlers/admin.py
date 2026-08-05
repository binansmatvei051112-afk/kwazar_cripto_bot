"""
Админ-панель: /admin (статистика) и /send (рассылка всем пользователям).
Доступно только ADMIN_ID (см. filters.IsAdmin).
"""
import asyncio
import aiosqlite
from aiogram import types
from aiogram.filters import Command

from config import ADMIN_ID, logger
from bot_instance import dp, bot
from filters import IsAdmin
from database_and_api import DB_NAME, get_all_users

@dp.message(Command("admin"), IsAdmin(ADMIN_ID))
async def admin_panel(message: types.Message):
    users = list(await get_all_users())
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM smart_alerts") as cursor:
            alert_count = (await cursor.fetchone())[0]
    
    text = (
        "👑 <b>Панель Администратора</b>\n\n"
        f"👥 Всего пользователей: <b>{len(users)}</b>\n"
        f"🔔 Активных алертов: <b>{alert_count}</b>\n\n"
        "<i>Чтобы сделать рассылку, напиши:\n"
        "<code>/send Твой текст здесь</code></i>"
    )
    
    await message.answer(text)
    
@dp.message(Command("send"), IsAdmin(ADMIN_ID))
async def send_text(message: types.Message):
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("❌ Ошибка. Напиши текст после команды, например:\n<code>/send Привет всем!</code>")
    
    text_to_send = parts[1]
    users = list(await get_all_users())
    
    await message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей...")
    
    success_count = 0
    for user_id in users:
        try:
            await bot.send_message(chat_id=user_id, text=f"📢 <b>Сообщение от разработчика:</b>\n\n{text_to_send}")
            success_count += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения от разработчика:{e}")
            
    await message.answer(f"✅ Рассылка завершена!\nДоставлено: {success_count} из {len(users)}")
