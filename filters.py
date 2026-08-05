"""
Кастомные фильтры aiogram. Сейчас только проверка "это админ?".
"""
from aiogram import types
from aiogram.filters import BaseFilter


class IsAdmin(BaseFilter):
    def __init__(self, admin_id: int):
        self.admin_id = admin_id

    async def __call__(self, message: types.Message) -> bool:
        return message.from_user.id == self.admin_id
