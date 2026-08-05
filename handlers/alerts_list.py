"""
Раздел "Мои алерты": список активных алертов пользователя с кнопками
удаления.
"""
import aiosqlite
from aiogram import types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot_instance import dp
from constants import VOL_TF_SHORT
from database_and_api import DB_NAME, get_cached_prices

async def get_alerts_keyboard(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, coin_symbol, alert_type, operator, 
                      price_check, price_target, price_dir, 
                      vol_check, vol_target, vol_dir, vol_tf, price_tf, price_rate_unit
               FROM smart_alerts WHERE user_id = ?""",
            (user_id,)
        ) as cursor:
            user_alerts = await cursor.fetchall()
            
    if not user_alerts:
        return None, None

    builder = InlineKeyboardBuilder()
    price = await get_cached_prices()
    vol_tf_names = {"1h": "1 часа", "4h": "4 часов", "1d": "1 день", "7d": "7 дней"}

    for a in user_alerts:
        coin = a["coin_symbol"].replace("USDT", "")
        real_coin = a["coin_symbol"]
        
        if a["alert_type"] == "simple":
            if a["price_check"]:
                direction = "⬆️" if a["price_dir"] == "UP" else "⬇️"
                # Проверяем, задан ли вообще таймфрейм для изменения цены
                if a["price_tf"]:
                    math_symbols = "$" if a['price_rate_unit'] == "money" else "%"
                    tf_name = vol_tf_names.get(a["price_tf"], a["price_tf"])
                    delta_money = "цены" if a['price_rate_unit'] == "money" else "процента цены"
                    button_text = f"{direction} {coin} → Изменение {delta_money} ({price.get(real_coin, 'N/A')}$) на {a['price_target']}{math_symbols}/Период {tf_name} ❌"
                else:
                    button_text = f"{direction} {coin} Цена → {a['price_target']}$ ❌"
                    
            elif a["vol_check"]:
                direction = "⬆️" if a["vol_dir"] == "UP" else "⬇️"
                vol = a["vol_target"]
                if vol >= 1_000_000_000:
                    vol_str = f"{vol / 1_000_000_000:.2f} млрд$"
                elif vol >= 1_000_000:
                    vol_str = f"{vol / 1_000_000:.2f} млн$"
                else:
                    vol_str = f"{vol:,.0f}$"
                tf_short = VOL_TF_SHORT.get(a["vol_tf"] or "1d", "24ч")
                button_text = f"{direction} {coin} Объем ({tf_short}) → {vol_str} ❌"
            else:
                button_text = f"❓ {coin} (простой алерт) ❌"
        else:
            op_symbol = "&" if a["operator"] == "AND" else "||"
            direction_price = "⬆️" if a["price_dir"] == "UP" else "⬇️"
            if a["price_tf"]:
                tf_name = vol_tf_names.get(a["price_tf"], a["price_tf"])
                math_symbols = "$" if a['price_rate_unit'] == "money" else "%"
                delta_money = "цены" if a['price_rate_unit'] == "money" else "процента цены"
                val_str_price = f"Изменение {delta_money} ({(price.get(real_coin, 'N/A')):.2f}$) на {a['price_target']:.2f}{math_symbols}/Период {tf_name}"
            else:  
                val_str_price = f"{a['price_target']:,.2f}$".replace(".00$", "$")
            
            direction_vol = "⬆️" if a["vol_dir"] == "UP" else "⬇️"
            vol = a["vol_target"]
            if vol >= 1_000_000_000:
                vol_str = f"{vol / 1_000_000_000:.2f} млрд$"
            elif vol >= 1_000_000:
                vol_str = f"{vol / 1_000_000:.2f} млн$"
            else:
                vol_str = f"{vol:,.0f}$"
            beautiful_vol = f"{vol:,.2f}".replace(",", " ").replace(".", ",")
            button_text = f"⚡️ {coin} [Цена → {direction_price}{val_str_price} {op_symbol} Объем → {direction_vol}{beautiful_vol} $] ❌"
        
        builder.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"delete_alert:{a['id']}"
        ))
    
    builder.adjust(1)
    return builder.as_markup(), len(user_alerts)


# Хэндлер команды
@dp.message(F.text == "Мои алерты")
async def button_my_alerts(message: types.Message):
    reply_markup, total = await get_alerts_keyboard(message.chat.id)
    
    if not reply_markup:
        return await message.answer(
            "📭 <b>У тебя пока нет активных уведомлений.</b>\n\n"
            "Нажми кнопку <b>«Создать алерт»</b> в меню ниже, чтобы добавить первое!"
        )
        
    await message.answer(
        "📋 <b>Твои активные алерты:</b>\n\n"
        "<i>Нажми на любую кнопку с алертом, чтобы удалить его из базы:</i>",
        reply_markup=reply_markup
    )


# Хэндлер удаления
@dp.callback_query(F.data.startswith("delete_alert:"))
async def process_delete_alert(callback: types.CallbackQuery):
    alert_id = int(callback.data.split(":")[1])
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM smart_alerts WHERE id = ? AND user_id = ?", 
            (alert_id, callback.from_user.id)
        )
        await db.commit()
   
    await callback.answer("✅ Уведомление удалено!")
    
    reply_markup, total = await get_alerts_keyboard(callback.message.chat.id)
    
    if not reply_markup:
        return await callback.message.edit_text(
            "📭 <b>У тебя пока нет активных уведомлений.</b>\n\n"
            "Нажми кнопку <b>«Создать алерт»</b> в меню ниже, чтобы добавить первое!"
        )
        
    await callback.message.edit_text(
        "📋 <b>Твои активные алерты:</b>\n\n"
        "<i>Нажми на любую кнопку с алертом, чтобы удалить его из базы:</i>",
        reply_markup=reply_markup
    )
