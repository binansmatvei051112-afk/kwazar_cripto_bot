"""
Начало создания алерта: выбор монеты и выбор типа (простой/сложный).
Это общая "точка входа" для веток alert_simple_* и alert_complex_*.
"""
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot_instance import dp
from keyboards import cancel_kb
from states import SmartAlertForm
from constants import POPULAR_COINS
from database_and_api import get_cached_prices, get_cached_stats

@dp.message(F.text == "Создать алерт")
async def start_alert_creation(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 <b>Настройка нового алерта</b>", reply_markup=cancel_kb)
    
    builder = InlineKeyboardBuilder()
    for coin in POPULAR_COINS:
        builder.add(InlineKeyboardButton(text=coin, callback_data=f"smart_coin:{coin}"))
    builder.adjust(2)
    
    await message.answer(
        "🪙 <b>Шаг 1: Выбери монету</b> из списка\n"
        "<i>Или напиши её тикер вручную (например, SOL или SOLUSDT):</i>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.choosing_coin)



async def save_coin_and_ask_complexity(message_or_call, state: FSMContext, coin: str):
    prices = await get_cached_prices()
    stats = await get_cached_stats()
    
    current_price = prices.get(coin)
    if current_price is None:
        text = f"❌ Монета <code>{coin}</code> не найдена в кэше Binance. Попробуй другой тикер:"
        if isinstance(message_or_call, types.CallbackQuery):
            return await message_or_call.message.edit_text(text)
        else:
            return await message_or_call.answer(text)
            
    current_vol = stats.get(coin, {}).get('quote_volume', 0.0)
    
    
    await state.update_data(coin=coin, base_price=current_price, base_vol=current_vol)
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔹 Простой алерт", callback_data="complexity:simple"))
    builder.add(InlineKeyboardButton(text="🔸 Сложный алерт (И/ИЛИ)", callback_data="complexity:complex"))
    builder.adjust(1)
    
    text = (
        f"✅ Монета: <b>{coin}</b>\n"
        f"💵 Текущая цена: <code>{current_price} $</code>\n"
        f"📊 Объем 24ч: <code>{current_vol / 1_000_000:.2f} млн $</code>\n\n"
        "<b>Шаг 2: Выбери тип уведомления:</b>\n"
        "🔹 <i>Простой</i> — отслеживание только цены ИЛИ только объема.\n"
        "🔸 <i>Сложный</i> — комбинация условий (например: цена > X И объем > Y)."
    )
    
    if isinstance(message_or_call, types.CallbackQuery):
        await message_or_call.message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message_or_call.answer(text, reply_markup=builder.as_markup())
        
    await state.set_state(SmartAlertForm.choosing_complexity)

@dp.callback_query(SmartAlertForm.choosing_coin, F.data.startswith("smart_coin:"))
async def inline_coin_chosen(callback: types.CallbackQuery, state: FSMContext):
    coin = callback.data.split(":")[1] + "USDT"
    await callback.answer()
    await save_coin_and_ask_complexity(callback, state, coin)

@dp.message(SmartAlertForm.choosing_coin)
async def text_coin_chosen(message: types.Message, state: FSMContext):
    coin = message.text.upper().strip()
    if not coin.endswith("USDT"):
        coin += "USDT"
    await save_coin_and_ask_complexity(message, state, coin)



@dp.callback_query(SmartAlertForm.choosing_complexity, F.data == "complexity:simple")
async def simple_alert_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💰 По цене", callback_data="s_metric:price"))
    builder.add(InlineKeyboardButton(text="📊 По объему торгов", callback_data="s_metric:vol"))
    builder.adjust(2)
    
    await callback.message.edit_text(
        "🔹 <b>Простой алерт</b>\n\n"
        "<b>Шаг 3: Что именно будем отслеживать?</b>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.simple_metric)

@dp.callback_query(SmartAlertForm.choosing_complexity, F.data == "complexity:complex")
async def complex_alert_stub(callback: types.CallbackQuery, state: FSMContext):
    
    await callback.answer()
    await state.update_data(alert_type="complex")
    await state.update_data(price_check=1)
    await state.update_data(vol_check=1)
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="ИЛИ (OR)", callback_data="complex_operator:or"))
    builder.add(InlineKeyboardButton(text="И (AND)", callback_data="complex_operator:and"))
    builder.adjust(2)
    
    await callback.message.edit_text(
        "🔸 <i>Сложный выбор</i>\n"
        "<b>Шаг 3: Какой оператор выберите?</b>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.complex_operator)
    
@dp.callback_query(SmartAlertForm.complex_operator, F.data.startswith("complex_operator"))
async def complex_operator_cmd(callback: types.CallbackQuery, state: FSMContext):
    operator = callback.data.split(":")[1]
    await state.update_data(operator=operator)
    await callback.answer()
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🎯 Уровень цены", callback_data="complex_price_mode:level"))
    builder.add(InlineKeyboardButton(text="⚡ Скорость изменения", callback_data="complex_price_mode:rate"))
    builder.adjust(1)
        
    await callback.message.edit_text(
        f"📊 Отслеживание с оператором <b>{operator}</b>\n\n"
        "<b>Шаг 3.5: Что именно отслеживаем?</b>\n"
        "🎯 <i>Уровень цены</i> — сработает, когда цена пересечёт заданную отметку "
        "(например, $65000), независимо от времени.\n"
        "⚡ <i>Скорость изменения</i> — сработает, если цена изменится на X% (или $) "
        "именно за выбранный период (например, +5% за 4 часа).",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.complex_price_mode)
    
