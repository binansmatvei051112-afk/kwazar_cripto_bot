"""
Раздел "📊 Объемы": просмотр объёмов торгов топ-10 монет по периодам.
"""
from aiogram import types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot_instance import dp
from constants import POPULAR_COINS
from database_and_api import fetch_all_volumes_tf

@dp.message(F.text == "📊 Объемы")
async def menu_volumes_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="⏱ 1 час", callback_data="show_vol:1h"))
    builder.add(InlineKeyboardButton(text="🕒 4 часа", callback_data="show_vol:4h"))
    builder.add(InlineKeyboardButton(text="📆 24 часа", callback_data="show_vol:1d"))
    builder.add(InlineKeyboardButton(text="📈 7 дней", callback_data="show_vol:7d"))
    builder.adjust(2, 2)
    
    await message.answer(
        "📊 <b>Выбери период для просмотра объемов торгов:</b>",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("show_vol:"))
async def menu_volumes_show(callback: types.CallbackQuery):
    tf = callback.data.split(":")[1]
    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    
    await callback.message.edit_text(f"⏳ Запрашиваю статистику за <b>{tf_names.get(tf)}</b>...")
    
    stats = await fetch_all_volumes_tf(window_size=tf)
    if not stats:
        return await callback.message.edit_text("❌ Не удалось получить данные с Binance. Попробуй позже.")
        
    text = f"📊 <b>Объемы торгов за {tf_names.get(tf)} (Топ-10):</b>\n\n"
    
    for coin in POPULAR_COINS:
        symbol = f"{coin}USDT"
        data = stats.get(symbol)
        
        if data:
            vol = data['quote_volume']
            if vol >= 1_000_000_000:
                vol_str = f"{vol / 1_000_000_000:.2f} млрд $"
            elif vol >= 1_000_000:
                vol_str = f"{vol / 1_000_000:.2f} млн $"
            else:
                vol_str = f"{vol:,.0f} $"
                
            change = data['price_change_percent']
            sign = "🟢 +" if change > 0 else "🔴 "
            text += f"🔹 <b>{coin}</b>: {vol_str} (<i>{sign}{change:.2f}%</i>)\n"
            
            
    builder = InlineKeyboardBuilder()
    for t_key, t_name in [("1h", "1ч"), ("4h", "4ч"), ("1d", "24ч"), ("7d", "7д")]:
        if t_key != tf:
            builder.add(InlineKeyboardButton(text=f"⏱ {t_name}", callback_data=f"show_vol:{t_key}"))
    builder.adjust(3)
            
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    
