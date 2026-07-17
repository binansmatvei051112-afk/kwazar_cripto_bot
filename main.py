import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler 
from aiogram import Bot, Dispatcher, types, F 
from aiogram.filters import Command, BaseFilter 
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode  
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton 
from aiogram.utils.keyboard import InlineKeyboardBuilder 
from aiogram.fsm.context import FSMContext 
from aiogram.fsm.state import State, StatesGroup 
import aiosqlite 
from aiogram.types import BufferedInputFile 
from database_and_api import (
    init_db, fetch_binance_prices, DB_NAME, 
    fetch_binance_24h_stats, get_chart_image, get_all_users, add_users,
    update_crypto_cache, get_cached_prices, get_cached_stats, add_smart_alert,
    fetch_all_volumes_tf, get_symbol_volume
)
from database_and_api import get_symbol_price_change
from dotenv import load_dotenv 

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = "bot.log"

file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

POPULAR_COINS = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "PEPE", "ADA", "AVAX", "LINK"]
 
VOL_TF_NAMES = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
VOL_TF_SHORT = {"1h": "1ч", "4h": "4ч", "1d": "24ч", "7d": "7д"}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Создать алерт")],
        [KeyboardButton(text="Мои алерты"), KeyboardButton(text="Показать график монеты")],
        [KeyboardButton(text="🔍 Курсы валют"), KeyboardButton(text="📊 Объемы ")]
    ],
    resize_keyboard=True,
    is_persistent=True
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🚫 Отмена")]],
    resize_keyboard=True,
    is_persistent=True
)

class SmartAlertForm(StatesGroup):
    
    choosing_coin = State()
    choosing_complexity = State()
    
    
    simple_metric = State()
    simple_vol_tf = State()
    simple_unit = State()
    simple_value_input = State()   
    simple_percent_menu = State()
    
    
    complex_operator = State()
    
    
    complex_price_unit = State() 
    complex_price_input = State() 
    complex_percent_menu_price = State()
    
    
    complex_vol_tf = State()
    complex_vol_unit = State()      
    complex_vol_input = State()
    complex_percent_menu_vol = State()

class ChartStates(StatesGroup):
    choosing_coin = State()
    choosing_tf = State()
    
class Cointf(StatesGroup):
    choosing_tf_coin = State()
    
class IsAdmin(BaseFilter):
    def __init__(self, admin_id:int):
        self.admin_id = admin_id
        
    async def __call__ (self, message: types.Message) -> bool:
        return message.from_user.id == self.admin_id

async def cache_updater_loop():
    logger.info("Фоновый воркер обновления кэша запущен")
    try:
        while True:
            
            prices = await fetch_binance_prices(quote_asset="USDT")
            stats = await fetch_binance_24h_stats(quote_asset="USDT")
            
            
            if prices and stats:
                await update_crypto_cache(prices, stats)
                
            else:
                logger.warning("Binance недоступен! Используем старые данные из кэша.")
                
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Воркер кэша остановлен")

async def check_alerts_loop():
    logger.info("Фоновый воркер проверки алертов запущен")
    try:
        while True:
            try:
                prices = await get_cached_prices()
                stats_1d = await get_cached_stats()
                if not prices:
                    await asyncio.sleep(30)
                    continue

                async with aiosqlite.connect(DB_NAME) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute("SELECT * FROM smart_alerts") as cursor:
                        alerts = await cursor.fetchall()


                        extra_tf_symbols = {}
                        for alert in alerts:
                            vol_tf = alert["vol_tf"] or "1d"
                            if alert["vol_check"] and vol_tf != "1d":
                                extra_tf_symbols.setdefault(vol_tf, set()).add(alert["coin_symbol"])

                        extra_stats = {} 
                        for tf, symbols in extra_tf_symbols.items():
                            extra_stats[tf] = await fetch_all_volumes_tf(window_size=tf, symbols=list(symbols))

                        for alert in alerts:
                            symbol = alert["coin_symbol"]
                            a_type = alert["alert_type"]
                            user_id = alert["user_id"]
                            alert_id = alert["id"]
                            vol_tf = alert["vol_tf"] or "1d"
                            
                            triggered = False
                            reason_text = ""
                            
                            if a_type == "simple":
                                if alert["price_check"]:
                                    curr_price = prices.get(symbol)
                                    target_price = alert["price_target"]
                                    if curr_price is not None:
                                        if alert["price_dir"] == "UP" and curr_price >= target_price:
                                            triggered = True
                                            reason_text = f"📈 Цена выросла до <code>{curr_price} $</code> (Цель: {target_price} $)"
                                        elif alert["price_dir"] == "DOWN" and curr_price <= target_price:
                                            triggered = True
                                            reason_text = f"📉 Цена упала до <code>{curr_price} $</code> (Цель: {target_price} $)"
                                            
                                elif alert["vol_check"]:
                                    if vol_tf == "1d":
                                        curr_vol = stats_1d.get(symbol, {}).get("quote_volume", 0)
                                    else:
                                        curr_vol = extra_stats.get(vol_tf, {}).get(symbol, {}).get("quote_volume", 0)
                                    target_vol = alert["vol_target"]
                                    tf_name = VOL_TF_NAMES.get(vol_tf, "24 часа")
                                    if curr_vol > 0:
                                        if alert["vol_dir"] == "UP" and curr_vol >= target_vol:
                                            triggered = True
                                            reason_text = f"📊 Объем за {tf_name} превысил <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                                        elif alert["vol_dir"] == "DOWN" and curr_vol <= target_vol:
                                            triggered = True
                                            reason_text = f"📉 Объем за {tf_name} упал ниже <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                            
                            else:
                                curr_price = prices.get(symbol)
                                target_price = alert["price_target"]
                                    
                                if vol_tf == "1d":
                                    curr_vol = stats_1d.get(symbol, {}).get("quote_volume", 0)
                                else:
                                    curr_vol = extra_stats.get(vol_tf, {}).get(symbol, {}).get("quote_volume", 0)
                                        
                                target_vol = alert["vol_target"]
                                tf_name = VOL_TF_NAMES.get(vol_tf, "24 часа")
                                
                                bool_price = False
                                bool_vol = False
                                price_text = ""
                                vol_text = ""
                                
                                if curr_price is not None:
                                    if alert["price_dir"] == "UP" and curr_price >= target_price:
                                        bool_price = True
                                        price_text = f"📈 Цена выросла до <code>{curr_price} $</code> (Цель: {target_price} $)"
                                    elif alert["price_dir"] == "DOWN" and curr_price <= target_price:
                                        bool_price = True
                                        price_text = f"📉 Цена упала до <code>{curr_price} $</code> (Цель: {target_price} $)"


                                if curr_vol > 0:
                                    if alert["vol_dir"] == "UP" and curr_vol >= target_vol:
                                        bool_vol = True
                                        vol_text = f"📊 Объем за {tf_name} превысил <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                                    elif alert["vol_dir"] == "DOWN" and curr_vol <= target_vol:
                                        bool_vol = True
                                        vol_text = f"📉 Объем за {tf_name} упал ниже <code>{curr_vol / 1_000_000:.2f} млн $</code>!"

                                if alert["operator"] == "AND":
                                    if bool_price and bool_vol:
                                        triggered = True
                                        reason_text = f"{price_text} && {vol_text}"
                                if alert["operator"] == "OR":
                                    if bool_price or bool_vol:
                                        triggered = True
                                        reason_text = "\n".join(filter(None, [price_text, vol_text]))
                                        
                            if triggered:
                                message_text = (
                                    f"🚨 <b>СРАБОТАЛ АЛЕРТ!</b> 🚨\n\n"
                                    f"🪙 Монета: <b>{symbol}</b>\n"
                                    f"{reason_text}"
                                )
                                try:
                                    for _ in range(2):
                                        await bot.send_message(chat_id=user_id, text=message_text)
                                    await db.execute("DELETE FROM smart_alerts WHERE id = ?", (alert_id,))
                                except Exception as e:
                                    logger.error(f"Не удалось отправить алерт юзеру {user_id}: {e}")
                                    
                    await db.commit()
            except Exception as e:
                logger.error(f"Ошибка в цикле проверки алертов: {e}")
            
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Фоновый воркер алертов остановлен")
        
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
async def price_cmd_tf(callback: types.CallbackQuery):
    tf_price = callback.data.split(":")[1]
    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    callback.answer()
    
    await callback.message.edit_text(f"⏳ Запрашиваю статистику за <b>{tf_names.get(tf_price)}</b>...")
    
    text = f"📊 <b>Объемы торгов за {tf_names.get(tf_price)} (Топ-10):</b>\n\n"
    for coin in POPULAR_COINS:
        symbol = f"{coin}USDT"
        change = await get_symbol_price_change(symbol, tf_price)
        if not change:
            text = "Ошибка"
        text += f"🔹 <b>{coin}</b>: {change}\n"
        
    builder = InlineKeyboardBuilder()
    for t_key, t_name in [("1h", "1ч"), ("4h", "4ч"), ("1d", "24ч"), ("7d", "7д")]:
        if t_key != tf_price:
            builder.add(InlineKeyboardButton(text=f"⏱ {t_name}", callback_data=f"show_price:{t_key}"))
    builder.adjust(3)
            
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.message(Command("price"))
async def cmd_price(message:types.Message):
    args = message.text.split(" ")
    
    if len(args) < 2:
        return await message.answer("<b>Напишите правильную форму команды:</b><code>/price НАЗВАНИЕ МОНЕТЫ</code>")
    
    raw_coin = args[1]
    
    if raw_coin.endswith("USDT"):
        raw_coin = raw_coin[:-4]
    
    raw_coins = [ raw_coin,  raw_coin.lower(),  raw_coin.upper()]
    
    coins = [coin + "USDT" for coin in raw_coins]
        
    prise = await get_cached_prices()
    
    for coin in coins:
        current_prise = prise.get(coin, None)
        if current_prise != None:
            return await message.answer(f"<i>📊 Монета {coin} стоит </i><code>{current_prise} $</code>\n")
    
    return await message.answer(
        f"<b>❌ Монета {raw_coin} не найдена</b>\n"
        f"───────────────────\n"
        f"Проверьте правильность написания тикера или попробуйте позже.",)
    
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
    builder.add(InlineKeyboardButton(text="💵 В деньгах ($)", callback_data="complex_unit:money"))
    builder.add(InlineKeyboardButton(text="📈 В процентах (%)", callback_data="complex_unit:percent"))
    builder.adjust(2)
        
    await callback.message.edit_text(
        f"📊 Отслеживание с оператором <b>{operator}</b>\n\n"
        "<b>Шаг 4: В чем будем измерять цену монеты?</b>\n"
        "💵 <i>В деньгах</i> — вводишь точную сумму (например: 65000$).\n"
        "📈 <i>В процентах</i> — выберешь рост или падение в % от текущего значения.",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.complex_price_unit)
    
@dp.callback_query(SmartAlertForm.complex_price_unit, F.data == "complex_unit:money")
async def complex_init_money_cmd(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    coin = data['coin']
    
    base_val = f"{data['base_price']} $"
    ex_val = "65000 или 0.05"
    name = "целевую цену"
        
    await callback.message.edit_text(
        f"💵 <b>Ввод точного значения</b>\n\n"
        f"🪙 Монета: <b>{coin}</b>\n"
        f"📍 Текущее значение: <code>{base_val}</code>\n\n"
        f"✏️ <b>Напиши в чат {name}:</b>\n"
        f"<i>(Пример числа: <code>{ex_val}</code>)</i>"
    )
    
    await state.set_state(SmartAlertForm.complex_price_input)
    
@dp.callback_query(SmartAlertForm.complex_price_unit, F.data == "complex_unit:percent")
async def complex_init_percent_price_cmd(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    
    text, kb = get_percent_menu_text_and_kb_complex(data, metric="price")
    await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(SmartAlertForm.complex_percent_menu_price)
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_price, F.data.startswith("complex_pct_add:"))
async def complex_percent_add_handler_price(callback: types.CallbackQuery, state: FSMContext):
    delta = float(callback.data.split(":")[1])
    data = await state.get_data()
    new_pct = round(data.get('current_pct', 0.0) + delta, 1)
    
    await state.update_data(current_pct=new_pct)
    data['current_pct'] = new_pct
    
    text, kb = get_percent_menu_text_and_kb_complex(data, metric='price')
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer(f"{'+' if delta > 0 else ''}{delta}%")
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_price, F.data == "complex_pct_reset")
async def complex_percent_reset_handler_price(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    data['current_pct'] = 0.0
    
    text, kb = get_percent_menu_text_and_kb_complex(data, metric='price')
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Сброшено в 0%")
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_price, F.data == "complex_pct_manual")
async def percent_manual_start_price(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "✏️ <b>Ручной ввод процента</b>\n\n"
        "Напиши в чат любое число (процент изменения).\n"
        "• Для роста пиши просто число: <code>15</code> или <code>2.5</code>\n"
        "• Для падения пиши с минусом: <code>-7</code> или <code>-3.3</code>"
    )
    
    await state.update_data(is_manual_percent=True)
    await state.set_state(SmartAlertForm.complex_price_input)
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_price, F.data == "complex_pct_confirm")
async def complex_percent_confirm_handler_price(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    data = await state.get_data()
    current_pct = data['current_pct']
    
    if current_pct == 0.0:
        return await callback.answer("❌ Процент изменения не может быть равен 0!", show_alert=True)
        
    target_val = data['base_price'] * (1 + current_pct / 100)
    direction = "UP" if current_pct > 0 else "DOWN"
    dir_text = "вырастет выше" if direction == "UP" else "упадет ниже"
    
    await state.update_data(price_target=target_val, price_dir=direction)
    
    builder = InlineKeyboardBuilder()
    for tf_key in ["1h", "4h", "1d", "7d"]:
        builder.add(InlineKeyboardButton(text=f"⏱ {VOL_TF_NAMES[tf_key]}", callback_data=f"c_voltf:{tf_key}"))
    builder.adjust(2, 2)

    
    await callback.message.answer(
        f"✅ Условие по цене сохранено: цена <b>{dir_text} {target_val:,.2f} $</b>\n\n"
        "<b>Шаг 5: Теперь настроим второе условие — объем.</b>\n"
        "За какой период сравнивать объем торгов?",
        reply_markup=builder.as_markup()
    )
    
    await state.set_state(SmartAlertForm.complex_vol_tf)
    
@dp.message(SmartAlertForm.complex_price_input)
async def cmd_input_price(message: types.Message, state: FSMContext):

    data = await state.get_data()
    is_percent = data.get("is_manual_percent", False)
    try:
        row_price = float(message.text.replace(",", ".").strip())
        if (not is_percent and row_price <= 0) or (is_percent and row_price == 0):
            raise ValueError
    except ValueError:
        return await message.answer(
            "<i>Напишите цену монеты больше 0</i>\n"
            "<i>Или напишите корректную желаемую цену монеты</i>"
            "<b>(Пример числа: <code>62000 или 12.5</code>)</b>"
        )
    
    if is_percent:
        current_pct = row_price
        price = data['base_price'] * (1 + current_pct / 100)
    else:
        price = row_price
        current_pct = ((price - data['base_price']) / data['base_price']) * 100
    
    direction = "UP" if price > data['base_price'] else "DOWN"
    await state.update_data(price_target=price, price_dir=direction)

    dir_text = "вырастет выше" if direction == "UP" else "упадет ниже"

    builder = InlineKeyboardBuilder()
    for tf_key in ["1h", "4h", "1d", "7d"]:
        builder.add(InlineKeyboardButton(text=f"⏱ {VOL_TF_NAMES[tf_key]}", callback_data=f"c_voltf:{tf_key}"))
    builder.adjust(2, 2)

    await message.answer(
        f"✅ Условие по цене сохранено: цена <b>{dir_text} {price:,.2f} $</b>\n\n"
        "<b>Шаг 5: Теперь настроим второе условие — объем.</b>\n"
        "За какой период сравнивать объем торгов?",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.complex_vol_tf)
    
@dp.callback_query(SmartAlertForm.complex_vol_tf, F.data.startswith("c_voltf:"))
async def complex_tf_cmd(callback: types.CallbackQuery, state: FSMContext):
    tf_key = callback.data.split(":")[1]
    await callback.answer()
    
    data = await state.get_data()
    coin = data['coin']
    
    if tf_key == '1d':
        current_val = data.get('base_vol')
    else:
        await callback.message.edit_text(f"⏳ Уточняю объем за {VOL_TF_NAMES[tf_key]}...")
        current_val = await get_symbol_volume(coin, window_size=tf_key)
    
    if current_val is None:
        return await callback.message.edit_text(
            "❌ Не удалось получить объем за этот период у Binance. Попробуй другой период "
            "или повтори позже.",
            reply_markup=InlineKeyboardBuilder()
                .add(*[InlineKeyboardButton(text=f"⏱ {VOL_TF_NAMES[k]}", callback_data=f"c_voltf:{k}") for k in ["1h", "4h", "1d", "7d"]])
                .adjust(2, 2).as_markup()
        )
    
    await state.update_data(vol_tf=tf_key, base_vol=current_val)

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💵 В деньгах ($)", callback_data="c_vol_unit:money"))
    builder.add(InlineKeyboardButton(text="📈 В процентах (%)", callback_data="c_vol_unit:percent"))
    builder.adjust(2)

    await callback.message.edit_text(
        f"✅ Период для объема: <b>{VOL_TF_NAMES[tf_key]}</b>\n\n"
        f"<i> Объем продажи за это время равен {current_val/ 1_000_000:.2f}$ </i>"
        "<b>Шаг 6: В чем задать цель по объему?</b>\n"
        "💵 <i>В деньгах</i> — точная сумма (например: 5000000$).\n"
        "📈 <i>В процентах</i> — рост/падение в % от текущего объема.",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.complex_vol_unit)
    
@dp.callback_query(SmartAlertForm.complex_vol_unit, F.data == "c_vol_unit:money")
async def complex_vol_money_cmd(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    coin = data['coin']

    base_vol = f"{data['base_vol'] / 1_000_000:.2f} млн $"
    ex_val = f"{int(data['base_vol'] * 1.2)}"
    name = "целевой объем (в долларах)"

    await callback.message.edit_text(
        f"💵 <b>Ввод точного значения</b>\n\n"
        f"🪙 Монета: <b>{coin}</b>\n"
        f"📍 Текущий объем: <code>{base_vol}</code>\n\n"
        f"✏️ <b>Напиши в чат {name}:</b>\n"
        f"<i>(Пример числа: <code>{ex_val}</code>)</i>"
    )

    await state.set_state(SmartAlertForm.complex_vol_input)
    
@dp.callback_query(SmartAlertForm.complex_vol_unit, F.data == "c_vol_unit:percent")
async def complex_init_percent_vol_cmd(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    
    text, kb = get_percent_menu_text_and_kb_complex(data, metric="vol")
    await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(SmartAlertForm.complex_percent_menu_vol)
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_vol, F.data == "complex_pct_reset")
async def complex_percent_reset_handler_vol(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    data['current_pct'] = 0.0
    
    text, kb = get_percent_menu_text_and_kb_complex(data, metric='vol')
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Сброшено в 0%")
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_vol, F.data == "complex_pct_manual")
async def percent_manual_start_vol(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "✏️ <b>Ручной ввод процента</b>\n\n"
        "Напиши в чат любое число (процент изменения).\n"
        "• Для роста пиши просто число: <code>15</code> или <code>2.5</code>\n"
        "• Для падения пиши с минусом: <code>-7</code> или <code>-3.3</code>"
    )
    
    await state.update_data(is_manual_percent=True)
    await state.set_state(SmartAlertForm.complex_vol_input)
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_vol, F.data.startswith("complex_pct_add:"))
async def complex_percent_add_handler_vol(callback: types.CallbackQuery, state: FSMContext):
    delta = float(callback.data.split(":")[1])
    data = await state.get_data()
    new_pct = round(data.get('current_pct', 0.0) + delta, 1)
    
    await state.update_data(current_pct=new_pct)
    data['current_pct'] = new_pct
    
    text, kb = get_percent_menu_text_and_kb_complex(data, metric='vol')
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer(f"{'+' if delta > 0 else ''}{delta}%")
    
@dp.callback_query(SmartAlertForm.complex_percent_menu_vol, F.data == "complex_pct_confirm")
async def complex_percent_confirm_handler_vol(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_pct = data.get('current_pct', 0.0)
    
    if current_pct == 0.0:
        return await callback.answer("❌ Процент изменения не может быть равен 0!", show_alert=True)
        
    await callback.answer()
    
    await callback.message.delete()
    
    vol_tf = data.get('vol_tf', '1d')
    vol = data['base_vol'] * (1 + current_pct / 100)
    direction = "UP" if current_pct > 0 else "DOWN"
    
    success = await add_smart_alert(
        user_id=callback.message.chat.id, coin=data['coin'], alert_type='complex',
        operator=data['operator'].upper(),
        price_check=1, price_target=data['price_target'], price_dir=data['price_dir'],
        vol_check=1, vol_target=vol, vol_dir=direction, vol_tf=vol_tf
    )
    
    await state.clear()
    
    dir_price = "выше" if data['price_dir'] == "UP" else "ниже"
    dir_vol = "выше" if direction == "UP" else "ниже"
    op_text = "И" if data['operator'].upper() == "AND" else "ИЛИ"

    if success:
        
        await callback.message.answer(
            f"✅ <b>Сложный алерт установлен!</b>\n\n"
            f"🪙 Монета: <code>{data['coin']}</code>\n"
            f"🎯 Цена {dir_price} <code>{data['price_target']:,.2f} $</code>\n"
            f"🔗 <b>{op_text}</b>\n"
            f"📊 Объем за {VOL_TF_NAMES[vol_tf]} {dir_vol} <code>{vol:,.0f} $</code>",
            reply_markup=main_kb
        )
    else:
        
        await callback.message.answer(
            "❌ Не удалось сохранить алерт, попробуй ещё раз.", 
            reply_markup=main_kb
        )

@dp.message(SmartAlertForm.complex_vol_input)
async def cmd_input_vol(message: types.Message, state: FSMContext):
    
    data = await state.get_data()
    is_percent = data.get("is_manual_percent", False)
    try:
        row_vol = float(message.text.replace(",", ".").strip())
        if not is_percent:
            if not row_vol > 0:
                raise ValueError
        else:
            if not row_vol != 0:
                raise ValueError
    except ValueError:
        return await message.answer(
            "<i>Напишите объем монеты больше 0</i>\n"
            "<i>Или напишите корректный желаемый объем монеты</i>"
            "<b>(Пример числа: <code>6200000 или 130000</code>)</b>"
        )

    if is_percent:
        current_pct = row_vol
        vol = data['base_vol'] * (1 + current_pct / 100)
    else:
        vol = row_vol
        
    direction = "UP" if vol > data['base_vol'] else "DOWN"
    vol_tf = data.get('vol_tf', '1d')

    success = await add_smart_alert(
        user_id=message.chat.id, coin=data['coin'], alert_type='complex',
        operator=data['operator'].upper(),
        price_check=1, price_target=data['price_target'], price_dir=data['price_dir'],
        vol_check=1, vol_target=vol, vol_dir=direction, vol_tf=vol_tf
    )

    await state.clear()

    dir_price = "выше" if data['price_dir'] == "UP" else "ниже"
    dir_vol = "выше" if direction == "UP" else "ниже"
    op_text = "И" if data['operator'].upper() == "AND" else "ИЛИ"

    if success:
        await message.answer(
            f"✅ <b>Сложный алерт установлен!</b>\n\n"
            f"🪙 Монета: <code>{data['coin']}</code>\n"
            f"🎯 Цена {dir_price} <code>{data['price_target']:,.2f} $</code>\n"
            f"🔗 <b>{op_text}</b>\n"
            f"📊 Объем за {VOL_TF_NAMES[vol_tf]} {dir_vol} <code>{vol:,.0f} $</code>",
            reply_markup=main_kb
        )
    else:
        await message.answer("❌ Не удалось сохранить алерт, попробуй ещё раз.", reply_markup=main_kb)

@dp.callback_query(SmartAlertForm.simple_metric, F.data.startswith("s_metric:"))
async def simple_metric_chosen(callback: types.CallbackQuery, state: FSMContext):
    metric = callback.data.split(":")[1]
    await state.update_data(metric=metric)
    await callback.answer()

    if metric == "vol":
        
        builder = InlineKeyboardBuilder()
        for tf_key in ["1h", "4h", "1d", "7d"]:
            builder.add(InlineKeyboardButton(text=f"⏱ {VOL_TF_NAMES[tf_key]}", callback_data=f"s_voltf:{tf_key}"))
        builder.adjust(2, 2)

        await callback.message.edit_text(
            "📊 Отслеживание по: <b>Объему торгов</b>\n\n"
            "<b>Шаг 3.5: За какой период сравнивать объем?</b>\n"
            "<i>Например, «1 час» — алерт сработает, когда объем торгов именно за последний час "
            "пересечет заданную границу.</i>",
            reply_markup=builder.as_markup()
        )
        await state.set_state(SmartAlertForm.simple_vol_tf)
        return

    await ask_simple_unit(callback, state)

async def ask_simple_unit(callback: types.CallbackQuery, state: FSMContext):
    
    data = await state.get_data()
    metric = data['metric']

    if metric == "price":
        metric_name = "Цене"
    else:
        metric_name = f"Объему торгов ({VOL_TF_NAMES[data.get('vol_tf', '1d')]})"

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💵 В деньгах ($)", callback_data="s_unit:money"))
    builder.add(InlineKeyboardButton(text="📈 В процентах (%)", callback_data="s_unit:percent"))
    builder.adjust(2)

    await callback.message.edit_text(
        f"🔹 Отслеживание по: <b>{metric_name}</b>\n\n"
        "<b>Шаг 4: В чем задавать цель?</b>\n"
        "💵 <i>В деньгах</i> — вводишь точную сумму (например: 65000$).\n"
        "📈 <i>В процентах</i> — выберешь рост или падение в % от текущего значения.",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.simple_unit)

@dp.callback_query(SmartAlertForm.simple_vol_tf, F.data.startswith("s_voltf:"))
async def simple_vol_tf_chosen(callback: types.CallbackQuery, state: FSMContext):
    tf = callback.data.split(":")[1]
    await callback.answer()

    data = await state.get_data()
    coin = data['coin']

    if tf == "1d":
        actual_vol = data.get('base_vol')
    else:
        await callback.message.edit_text(f"⏳ Уточняю объем за {VOL_TF_NAMES[tf]}...")
        actual_vol = await get_symbol_volume(coin, window_size=tf)

    if actual_vol is None:
        return await callback.message.edit_text(
            "❌ Не удалось получить объем за этот период у Binance. Попробуй другой период "
            "или повтори позже.",
            reply_markup=InlineKeyboardBuilder()
                .add(*[InlineKeyboardButton(text=f"⏱ {VOL_TF_NAMES[k]}", callback_data=f"s_voltf:{k}") for k in ["1h", "4h", "1d", "7d"]])
                .adjust(2, 2).as_markup()
        )

    await state.update_data(vol_tf=tf, base_vol=actual_vol)
    await ask_simple_unit(callback, state)



@dp.callback_query(SmartAlertForm.simple_unit, F.data == "s_unit:money")
async def simple_unit_money_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    coin = data['coin']
    
    if data['metric'] == 'price':
        base_val = f"{data['base_price']} $"
        ex_val = "65000 или 0.05"
        name = "целевую цену"
    else:
        base_val = f"{data['base_vol'] / 1_000_000:.2f} млн $"
        ex_val = f"{int(data['base_vol'] * 1.2)}"
        tf_name = VOL_TF_NAMES.get(data.get('vol_tf', '1d'), '24 часа')
        name = f"целевой объем за {tf_name} (в долларах)"
        
    await callback.message.edit_text(
        f"💵 <b>Ввод точного значения</b>\n\n"
        f"🪙 Монета: <b>{coin}</b>\n"
        f"📍 Текущее значение: <code>{base_val}</code>\n\n"
        f"✏️ <b>Напиши в чат {name}:</b>\n"
        f"<i>(Пример числа: <code>{ex_val}</code>)</i>"
    )
    await state.set_state(SmartAlertForm.simple_value_input)

def get_percent_menu_text_and_kb_complex(data: dict, metric: str):
    coin = data['coin']
    current_pct = data.get('current_pct', 0.0)
    
    if metric == 'price':
        base_val = data['base_price']
        target_val = base_val * (1 + current_pct / 100)
        base_str = f"{base_val:,.2f} $"
        target_str = f"{target_val:,.2f} $"
        name = "Цена"
    else:
        base_val = data['base_vol']
        target_val = base_val * (1 + current_pct / 100)
        base_str = f"{base_val / 1_000_000:,.2f} млн $"
        target_str = f"{target_val / 1_000_000:,.2f} млн $"
        tf_name = VOL_TF_NAMES.get(data.get('vol_tf', '1d'), '24 часа')
        name = f"Объем ({tf_name})"
        
    sign = "+" if current_pct > 0 else ""
    
    text = (
        f"📈 <b>Настройка алерта в процентах</b>\n\n"
        f"🪙 Монета: <b>{coin}</b> ({name})\n"
        f"📍 Текущее значение: <code>{base_str}</code>\n\n"
        f"🎛 Выбранное изменение: <b>{sign}{current_pct:.1f}%</b>\n"
        f"🎯 Целевое значение: <code>{target_str}</code>\n\n"
        f"<i>Нажимай кнопки ниже, чтобы настроить нужный процент:</i>"
    )
    
    builder = InlineKeyboardBuilder()
    
    builder.add(InlineKeyboardButton(text="-10%", callback_data="complex_pct_add:-10"))
    builder.add(InlineKeyboardButton(text="-5%", callback_data="complex_pct_add:-5"))
    builder.add(InlineKeyboardButton(text="-1%", callback_data="complex_pct_add:-1"))
    
    builder.add(InlineKeyboardButton(text="+1%", callback_data="complex_pct_add:1"))
    builder.add(InlineKeyboardButton(text="+5%", callback_data="complex_pct_add:5"))
    builder.add(InlineKeyboardButton(text="+10%", callback_data="complex_pct_add:10"))
    
    builder.add(InlineKeyboardButton(text="🔄 Сбросить (0%)", callback_data="complex_pct_reset"))
    builder.add(InlineKeyboardButton(text="✏️ Ввести свой %", callback_data="complex_pct_manual"))
    
    if current_pct != 0.0:
        builder.add(InlineKeyboardButton(
            text=f"✅ Установить алерт ({sign}{current_pct:.1f}%)", 
            callback_data="complex_pct_confirm"
        ))
        
    builder.adjust(3, 3, 2, 1)
    return text, builder.as_markup()

def get_percent_menu_text_and_kb(data: dict):
    coin = data['coin']
    metric = data['metric']
    current_pct = data.get('current_pct', 0.0)
    
    if metric == 'price':
        base_val = data['base_price']
        target_val = base_val * (1 + current_pct / 100)
        base_str = f"{base_val:,.2f} $"
        target_str = f"{target_val:,.2f} $"
        name = "Цена"
    else:
        base_val = data['base_vol']
        target_val = base_val * (1 + current_pct / 100)
        base_str = f"{base_val / 1_000_000:,.2f} млн $"
        target_str = f"{target_val / 1_000_000:,.2f} млн $"
        tf_name = VOL_TF_NAMES.get(data.get('vol_tf', '1d'), '24 часа')
        name = f"Объем ({tf_name})"
        
    sign = "+" if current_pct > 0 else ""
    
    text = (
        f"📈 <b>Настройка алерта в процентах</b>\n\n"
        f"🪙 Монета: <b>{coin}</b> ({name})\n"
        f"📍 Текущее значение: <code>{base_str}</code>\n\n"
        f"🎛 Выбранное изменение: <b>{sign}{current_pct:.1f}%</b>\n"
        f"🎯 Целевое значение: <code>{target_str}</code>\n\n"
        f"<i>Нажимай кнопки ниже, чтобы настроить нужный процент:</i>"
    )
    
    builder = InlineKeyboardBuilder()
    
    builder.add(InlineKeyboardButton(text="-10%", callback_data="pct_add:-10"))
    builder.add(InlineKeyboardButton(text="-5%", callback_data="pct_add:-5"))
    builder.add(InlineKeyboardButton(text="-1%", callback_data="pct_add:-1"))
    
    builder.add(InlineKeyboardButton(text="+1%", callback_data="pct_add:1"))
    builder.add(InlineKeyboardButton(text="+5%", callback_data="pct_add:5"))
    builder.add(InlineKeyboardButton(text="+10%", callback_data="pct_add:10"))
    
    builder.add(InlineKeyboardButton(text="🔄 Сбросить (0%)", callback_data="pct_reset"))
    builder.add(InlineKeyboardButton(text="✏️ Ввести свой %", callback_data="pct_manual"))
    
    if current_pct != 0.0:
        builder.add(InlineKeyboardButton(
            text=f"✅ Установить алерт ({sign}{current_pct:.1f}%)", 
            callback_data="pct_confirm"
        ))
        
    builder.adjust(3, 3, 2, 1)
    return text, builder.as_markup()

@dp.callback_query(SmartAlertForm.simple_unit, F.data == "s_unit:percent")
async def simple_unit_percent_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    
    text, kb = get_percent_menu_text_and_kb(data)
    await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(SmartAlertForm.simple_percent_menu)

@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data.startswith("pct_add:"))
async def s_percent_add_handler(callback: types.CallbackQuery, state: FSMContext):
    delta = float(callback.data.split(":")[1])
    data = await state.get_data()
    new_pct = round(data.get('current_pct', 0.0) + delta, 1)
    
    await state.update_data(current_pct=new_pct)
    data['current_pct'] = new_pct
    
    text, kb = get_percent_menu_text_and_kb(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass 
    await callback.answer(f"{'+' if delta > 0 else ''}{delta}%")

@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data == "pct_reset")
async def percent_reset_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    data['current_pct'] = 0.0
    
    text, kb = get_percent_menu_text_and_kb(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Сброшено в 0%")

@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data == "pct_manual")
async def percent_manual_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "✏️ <b>Ручной ввод процента</b>\n\n"
        "Напиши в чат любое число (процент изменения).\n"
        "• Для роста пиши просто число: <code>15</code> или <code>2.5</code>\n"
        "• Для падения пиши с минусом: <code>-7</code> или <code>-3.3</code>"
    )
    
    await state.update_data(is_manual_percent=True)
    await state.set_state(SmartAlertForm.simple_value_input)

@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data == "pct_confirm")
async def percent_confirm_handler(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    coin = data['coin']
    metric = data['metric']
    current_pct = data['current_pct']
    
    if current_pct == 0.0:
        return await callback.answer("❌ Процент изменения не может быть равен 0!", show_alert=True)
        
    if metric == 'price':
        target_val = data['base_price'] * (1 + current_pct / 100)
        direction = "UP" if current_pct > 0 else "DOWN"
        success = await add_smart_alert(
            user_id=callback.from_user.id, coin=coin, alert_type='simple',
            price_check=1, price_target=target_val, price_dir=direction
        )
        dir_text = "📈 выросла на" if direction == "UP" else "📉 упала на"
        val_str = f"<b>{abs(current_pct)}%</b> (до <code>{target_val:,.2f} $</code>)"
    else:
        vol_tf = data.get('vol_tf', '1d')
        target_val = data['base_vol'] * (1 + current_pct / 100)
        direction = "UP" if current_pct > 0 else "DOWN"
        success = await add_smart_alert(
            user_id=callback.from_user.id, coin=coin, alert_type='simple',
            vol_check=1, vol_target=target_val, vol_dir=direction, vol_tf=vol_tf
        )
        dir_text = f"📈 объем за {VOL_TF_NAMES[vol_tf]} вырастет на" if direction == "UP" else f"📉 объем за {VOL_TF_NAMES[vol_tf]} упадет на"
        val_str = f"<b>{abs(current_pct)}%</b> (до <code>{target_val / 1_000_000:,.2f} млн $</code>)"
        
    await state.clear()
    await callback.message.delete()
    
    if success:
        await callback.message.answer(
            f"✅ <b>Алерт по процентам установлен!</b>\n\n"
            f"🪙 Монета: <code>{coin}</code>\n"
            f"🎯 Условие: я пришлю уведомление, когда {dir_text} {val_str}.",
            reply_markup=main_kb
        )
    else:
        await callback.message.answer("❌ Произошла ошибка при сохранении в базу.", reply_markup=main_kb)

@dp.message(SmartAlertForm.simple_value_input)
async def simple_value_received(message: types.Message, state: FSMContext):
    data = await state.get_data()
    is_percent = data.get('is_manual_percent', False)
    
    try:
        raw_val = float(message.text.replace(",", ".").replace(" ", ""))
        
        if (not is_percent and raw_val <= 0) or (is_percent and raw_val == 0):
            raise ValueError
    except ValueError:
        if is_percent:
            return await message.answer("❌ Ошибка! Введи процент (например: <code>5</code> или <code>-3.5</code>):")
        else:
            return await message.answer("❌ Ошибка! Введи положительное число без букв:")
            
    coin = data['coin']
    metric = data['metric']
    
    
    if is_percent:
        current_pct = raw_val
        if metric == 'price':
            target_val = data['base_price'] * (1 + current_pct / 100)
        else:
            target_val = data['base_vol'] * (1 + current_pct / 100)
    else:
        target_val = raw_val
        if metric == 'price':
            current_pct = ((target_val - data['base_price']) / data['base_price']) * 100
        else:
            current_pct = ((target_val - data['base_vol']) / data['base_vol']) * 100


    if metric == 'price':
        direction = "UP" if target_val > data['base_price'] else "DOWN"
        success = await add_smart_alert(
            user_id=message.chat.id, coin=coin, alert_type='simple',
            price_check=1, price_target=target_val, price_dir=direction
        )
        dir_text = "📈 выросла до" if direction == "UP" else "📉 упала до"
        val_str = f"<code>{target_val:,.2f} $</code> ({'+' if current_pct>0 else ''}{current_pct:.1f}%)"
    else:
        vol_tf = data.get('vol_tf', '1d')
        direction = "UP" if target_val > data['base_vol'] else "DOWN"
        success = await add_smart_alert(
            user_id=message.chat.id, coin=coin, alert_type='simple',
            vol_check=1, vol_target=target_val, vol_dir=direction, vol_tf=vol_tf
        )
        dir_text = f"📈 объем за {VOL_TF_NAMES[vol_tf]} превысит" if direction == "UP" else f"📉 объем за {VOL_TF_NAMES[vol_tf]} упадет ниже"
        val_str = f"<code>{target_val:,.0f} $</code> ({'+' if current_pct>0 else ''}{current_pct:.1f}%)"
        
    await state.clear()
    
    if success:
        await message.answer(
            f"✅ <b>Алерт успешно установлен!</b>\n\n"
            f"🪙 Монета: <code>{coin}</code>\n"
            f"🎯 Условие: я пришлю уведомление, когда {dir_text} {val_str}.",
            reply_markup=main_kb
        )
    else:
        await message.answer("❌ Произошла ошибка при сохранении в базу. Попробуй еще раз.", reply_markup=main_kb)

@dp.message(F.text == "Показать график монеты")
async def cmd_chart(message: types.Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    
    for coin in POPULAR_COINS:
        builder.add(InlineKeyboardButton(text=coin, callback_data=f"sel_coin:{coin}"))
    builder.adjust(2)
    
    msg = await message.answer("📊 <b>Выберите монету для графика:</b>", reply_markup=builder.as_markup())
    await state.update_data(msg_id=msg.message_id)
    await state.set_state(ChartStates.choosing_coin)
    
@dp.callback_query(ChartStates.choosing_coin, F.data.startswith("sel_coin:"))
async def process_coin(callback: types.CallbackQuery, state: FSMContext):
    coin = callback.data.split(":")[1]
    await state.update_data(chosen_coin=coin)
    
    builder = InlineKeyboardBuilder()
    for tf in ["15m", "1h", "4h", "1d"]:
        builder.add(InlineKeyboardButton(text=tf, callback_data=f"sel_tf:{tf}"))
    builder.adjust(2)
    
    await callback.message.edit_text(
        f"✅ Монета: {coin}\n🕒 <b>Теперь выберите таймфрейм:</b>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(ChartStates.choosing_tf)
    
@dp.callback_query(ChartStates.choosing_tf, F.data.startswith("sel_tf:"))
async def process_tf(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    coin = data['chosen_coin']
    tf = callback.data.split(":")[1]
    
    await callback.message.edit_text("⏳ <b>Генерирую график... Пожалуйста, подождите.</b>")
    
    image_buffer = await get_chart_image(f"{coin}USDT", interval=tf)
    
    await callback.message.delete()
    await callback.message.answer_photo(
        photo=BufferedInputFile(image_buffer.getvalue(), filename="chart.png"),
        caption=f"📈 График {coin} ({tf})"
    )
    await state.clear() 


@dp.message(F.text == "Мои алерты")
async def button_my_alerts(message: types.Message):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute(
            """SELECT id, coin_symbol, alert_type, operator, 
                      price_check, price_target, price_dir, 
                      vol_check, vol_target, vol_dir, vol_tf
               FROM smart_alerts WHERE user_id = ?""",
            (message.chat.id,)
        ) as cursor:
            user_alerts = await cursor.fetchall()
    
    if not user_alerts:
        return await message.answer(
            "📭 <b>У тебя пока нет активных уведомлений.</b>\n\n"
            "Нажми кнопку <b>«Создать алерт»</b> в меню ниже, чтобы добавить первое!"
        )
    
    builder = InlineKeyboardBuilder()
    
    for a in user_alerts:
        coin = a["coin_symbol"].replace("USDT", "")
        
        if a["alert_type"] == "simple":
            if a["price_check"]:
                direction = "⬆️" if a["price_dir"] == "UP" else "⬇️"
                val_str = f"{a['price_target']:,.2f}$".replace(".00$", "$")
                button_text = f"{direction} {coin} Цена → {val_str} ❌"
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
    
    await message.answer(
        "📋 <b>Твои активные алерты:</b>\n\n"
        "<i>Нажми на любую кнопку с алертом, чтобы удалить его из базы:</i>",
        reply_markup=builder.as_markup() 
    )

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
    
    await callback.message.edit_text(
        "🗑 <b>Алерт успешно удален!</b>\n\n"
        "Нажми кнопку <b>«Мои алерты»</b> в нижнем меню, чтобы посмотреть оставшиеся."
    )

async def on_startup():
    
    asyncio.create_task(cache_updater_loop())
    
    asyncio.create_task(check_alerts_loop())
    
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    
    scheduler.add_job(send_morning_newsletter, 'cron', hour=8, minute=30)
    
    scheduler.start()
    logger.info("Планировщик задач запущен!")
    
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
    except Exception as e:
        logger.critical("Критическая ошибка при запуске приложения!", exc_info=True)