"""
Раздел "🔍 Курсы валют": просмотр цен топ-10 монет по периодам
и просмотр цены/изменения конкретной монеты через /price.
"""
from aiogram import types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot_instance import dp
from keyboards import cancel_kb
from states import Cointf
from constants import POPULAR_COINS
from database_and_api import get_cached_stats, get_cached_prices, fetch_all_volumes_tf, get_symbol_price_change

@dp.message(F.text == "🔍 Курсы валют")
async def menu_prices(message: types.Message):
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="⏱ 1 час", callback_data="show_price:1h"))
    builder.add(InlineKeyboardButton(text="🕒 4 часа", callback_data="show_price:4h"))
    builder.add(InlineKeyboardButton(text="📆 24 часа", callback_data="show_price:1d"))
    builder.add(InlineKeyboardButton(text="📈 7 дней", callback_data="show_price:7d"))
    builder.adjust(2, 2)
    
    await message.answer(
        "📊 <b>Выбери период для просмотра цены монеты:</b>",
        reply_markup=builder.as_markup()
    )
    
@dp.callback_query(F.data.startswith("show_price:"))
async def price_cmd_tf(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    tf_price = callback.data.split(":")[1]
    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    await callback.answer()
    
    await callback.message.edit_text(f"⏳ Запрашиваю статистику за <b>{tf_names.get(tf_price)}</b>...")
    
    symbols = [f"{c}USDT" for c in POPULAR_COINS]

    if tf_price == "1d":
        stats = await get_cached_stats()
    else:
        stats = await fetch_all_volumes_tf(window_size=tf_price, symbols=symbols)

    all_price = await get_cached_prices()

    text = f"📊 <b>Цена и ее период за {tf_names.get(tf_price)} (Топ-10):</b>\n\n"
    for coin in POPULAR_COINS:
        symbol = f"{coin}USDT"
        price = all_price.get(symbol)
        change_procent = stats.get(symbol, {}).get('price_change_percent')
        if change_procent is None:
            text += f"<b>Цена или объем монеты {coin} недоступны — попробуйте позже</b>\n"
            continue
        sign = "🟢 +" if change_procent > 0 else "🔴 "
        text += f"🔹 <b>{coin}</b>: {price} (<i>{sign}{change_procent:.2f}%</i>)\n"
    
    text += "Если хотите узнать цену и процент изменения другой монеты \n Напишите <code>/price &lt;Монета&gt;</code>"
        
    builder = InlineKeyboardBuilder()
    for t_key, t_name in [("1h", "1ч"), ("4h", "4ч"), ("1d", "24ч"), ("7d", "7д")]:
        if t_key != tf_price:
            builder.add(InlineKeyboardButton(text=f"⏱ {t_name}", callback_data=f"show_price:{t_key}"))
    builder.adjust(3)
            
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await state.set_state(Cointf.choosing_price_coin)
    
    
@dp.message(Cointf.choosing_price_coin, Command("price"))
async def menu_prices_coin_cmd(message: types.Message, state:FSMContext):
    args = message.text.split(" ")
    
    if len(args) < 2:
        return await message.answer("<b>Напишите правильную форму команды:</b><code>/price НАЗВАНИЕ МОНЕТЫ</code>", reply_markup=cancel_kb)
    
    raw_coin = args[1]
    
    if raw_coin.endswith("USDT"):
        raw_coin = raw_coin[:-4]
    
    raw_coins = [ raw_coin,  raw_coin.lower(),  raw_coin.upper()]
    
    coins = [coin + "USDT" for coin in raw_coins]
    
    await state.update_data(coins=coins)
    await message.answer(f"<b>Отлично ваша монета:{raw_coin}</b>", reply_markup=cancel_kb)
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="⏱ 1 час", callback_data="show_only_one_price:1h"))
    builder.add(InlineKeyboardButton(text="🕒 4 часа", callback_data="show_only_one_price:4h"))
    builder.add(InlineKeyboardButton(text="📆 24 часа", callback_data="show_only_one_price:1d"))
    builder.add(InlineKeyboardButton(text="📈 7 дней", callback_data="show_only_one_price:7d"))
    builder.adjust(2, 2)
    
    await message.answer(
        "📊 <b>Выбери период для просмотра цены монеты:</b>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(Cointf.choosing_tf_coin)

@dp.callback_query(Cointf.choosing_tf_coin, F.data.startswith("show_only_one_price:"))
async def cmd_price(callback: types.CallbackQuery, state: FSMContext):
    price_tf = callback.data.split(":")[1]
    data = await state.get_data()
    coins = data['coins']
    await callback.answer()

    prise = await get_cached_prices()

    coin, current_prise = None, None
    for c in coins:
        cp = prise.get(c)
        if cp is not None:
            coin, current_prise = c, cp
            break

    if coin is None:
        return await callback.message.edit_text(
            f"<b>❌ Монета {coins[0]} не найдена</b>\n"
            f"───────────────────\n"
            f"Проверьте правильность написания тикера или попробуйте позже."
        )

    change_procent = await get_symbol_price_change(coin, price_tf)
    if change_procent is None:
        return await callback.message.edit_text(f"<b>Данные по {coin} временно недоступны — попробуй позже</b>")

    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    sign_emoji = "🟢" if change_procent >= 0 else "🔴"
    sign_str = f"+{change_procent:.2f}%" if change_procent >= 0 else f"{change_procent:.2f}%"

    builder = InlineKeyboardBuilder()
    for t_key, t_name in [("1h", "1ч"), ("4h", "4ч"), ("1d", "24ч"), ("7d", "7д")]:
        if t_key != price_tf:
            builder.add(InlineKeyboardButton(text=f"⏱ {t_name}", callback_data=f"show_only_one_price:{t_key}"))
    builder.adjust(3)

    await callback.message.edit_text(
        f"📊 <b>{coin}</b>\n\n"
        f"💰 Цена: <code>{current_prise} $</code>\n"
        f"{sign_emoji} Изменение за {tf_names[price_tf]}: <b>{sign_str}</b>",
        reply_markup=builder.as_markup()
    )
    
