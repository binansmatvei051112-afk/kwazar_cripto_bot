"""
/start и общая кнопка "Отмена".
"""
from aiogram import types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot_instance import dp
from keyboards import main_kb
from database_and_api import add_users

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    
    await add_users(message.chat.id)
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! 🪙\n"
        "Я бот для отслеживания курсов криптовалют с Binance.\n\n"
        "Воспользуйся меню ниже, чтобы настроить уведомления.",
        reply_markup=main_kb
    )

@dp.message(F.text == "🚫 Отмена")
async def cancel_handlane(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Действие отменено", reply_markup=main_kb)
