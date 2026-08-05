"""
Единственное место, где создаются объекты Bot и Dispatcher.
Все файлы в handlers/ и background/ импортируют их отсюда:

    from bot_instance import dp, bot

Так все хендлеры регистрируются на один и тот же Dispatcher,
независимо от того, в каком файле лежит @dp.message(...) / @dp.callback_query(...).
"""
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
