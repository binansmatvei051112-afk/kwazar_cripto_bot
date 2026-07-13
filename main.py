import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler # type: ignore
from aiogram import Bot, Dispatcher, types, F # type: ignore
from aiogram.filters import Command, BaseFilter # type: ignore
from aiogram.client.default import DefaultBotProperties # type: ignore
from aiogram.enums import ParseMode # type: ignore 
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton # type: ignore
from aiogram.utils.keyboard import InlineKeyboardBuilder # type: ignore
from aiogram.fsm.context import FSMContext # type: ignore
from aiogram.fsm.state import State, StatesGroup # type: ignore
import aiosqlite # type: ignore
from aiogram.types import BufferedInputFile # type: ignore
from database_and_api import (
    init_db, fetch_binance_prices, DB_NAME, 
    fetch_binance_24h_stats, get_chart_image, get_all_users, add_users,
    update_crypto_cache, get_cached_prices, get_cached_stats, add_smart_alert,
    fetch_coin_volume_tf, fetch_all_volumes_tf
)
from dotenv import load_dotenv # type: ignore

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

POPULAR_COINS = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "TON", "ADA", "AVAX", "LINK"]

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Создать алерт")],
        [KeyboardButton(text="Мои алерты"), KeyboardButton(text="Показать график монеты")],
        [KeyboardButton(text="🔍 Курсы валют"), KeyboardButton(text="📊 Объемы (24ч)")]
    ],
    resize_keyboard=True
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🚫 Отмена")]],
    resize_keyboard=True
)

class SmartAlertForm(StatesGroup):
    # Шаг 0 и 1: Базовый выбор
    choosing_coin = State()         # Выбор монеты (BTC, ETH...)
    choosing_complexity = State()   # Простой или Сложный
    
    # --- ВЕТКА: ПРОСТОЙ АЛЕРТ ---
    simple_metric = State()         # Что меряем: Цена или Объем
    simple_unit = State()           # В чем меряем: Деньги или Проценты
    simple_value_input = State()    # Ввод числа (если выбрали "Деньги")
    simple_percent_menu = State()   # Интерактивное меню (если выбрали "Проценты")
    
    # --- ВЕТКА: СЛОЖНЫЙ АЛЕРТ ---
    complex_operator = State()      # Оператор: И или ИЛИ
    
    # Настройка 1-го условия (Цена)
    complex_price_unit = State()    # Деньги или Проценты для цены
    complex_price_val = State()     # Ввод/выбор значения цены
    
    # Настройка 2-го условия (Объем)
    complex_vol_unit = State()      # Деньги или Проценты для объема
    complex_vol_val = State()

class ChartStates(StatesGroup):
    choosing_coin = State()
    choosing_tf = State()
    
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
                stats = await get_cached_stats()
                if not prices:
                    await asyncio.sleep(30)
                    continue

                async with aiosqlite.connect(DB_NAME) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute("SELECT * FROM smart_alerts") as cursor:
                        alerts = await cursor.fetchall()
                        
                        for alert in alerts:
                            symbol = alert["coin_symbol"]
                            a_type = alert["alert_type"]
                            user_id = alert["user_id"]
                            alert_id = alert["id"]
                            
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
                                    curr_vol = stats.get(symbol, {}).get("quote_volume", 0)
                                    target_vol = alert["vol_target"]
                                    if curr_vol > 0:
                                        if alert["vol_dir"] == "UP" and curr_vol >= target_vol:
                                            triggered = True
                                            reason_text = f"📊 Объем 24ч превысил <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                                        elif alert["vol_dir"] == "DOWN" and curr_vol <= target_vol:
                                            triggered = True
                                            reason_text = f"📉 Объем 24ч упал ниже <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                            
                            # Сложные алерты (a_type == 'complex') подключим в Спринте 4!
                            
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
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_kb)

@dp.message(F.text == "🔍 Курсы валют")
async def menu_prices(message: types.Message):
    msg = await message.answer("⏳ Запрашиваю цены с Binance...")
    
    prices = await get_cached_prices()
    if not prices:
        return await msg.edit_text("❌ Не удалось получить цены. Попробуйте позже.")
        
    text = "📊 <b>Топ-10 популярных монет:</b>\n\n"
    for coin in POPULAR_COINS:
        symbol = f"{coin}USDT"
        price = prices.get(symbol, "Н/Д")
        if price != "Н/Д":
            text += f"🔹 <b>{coin}</b>: <code>{price}</code> $\n"
            
    text += "\n<i>Узнать цену любой другой монеты:</i>\n<code>/price [тикер]</code> (например, /price PEPE)"
    
    await msg.edit_text(text)

@dp.message(Command("price"))
async def cmd_price(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        return await message.answer("❌ Использование: <code>/price [тикер]</code>\nПример: <code>/price ETH</code>")
        
    coin = args[1].upper()
    if not coin.endswith("USDT"):
        coin += "USDT"
        
    prices = await get_cached_prices()
    current_price = prices.get(coin)
    
    if current_price is None:
        return await message.answer(f"❌ Монета <code>{coin}</code> не найдена на Binance.")
        
    await message.answer(f"📈 Текущая цена <b>{coin}</b>: <code>{current_price}</code> $")


@dp.message(F.text == "Создать алерт")
async def start_alert_creation(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 <b>Настройка нового алерта</b>", reply_markup=cancel_kb)
    
    builder = InlineKeyboardBuilder()
    for coin in POPULAR_COINS:
        builder.add(InlineKeyboardButton(text=coin, callback_data=f"smart_coin:{coin}"))
    builder.adjust(3)
    
    await message.answer(
        "🪙 <b>Шаг 1: Выбери монету</b> из списка\n"
        "<i>Или напиши её тикер вручную (например, SOL или SOLUSDT):</i>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.choosing_coin)

# --- ВЫБОР МОНЕТЫ (Кнопкой или Текстом) ---

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
    
    # Сохраняем базовые данные монеты в FSM
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

# --- ВЫБОР СЛОЖНОСТИ ---

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
    await callback.answer("🔸 Сложные алерты реализуем в Спринте 4! Выбери Простой:", show_alert=True)

# --- ВЫБОР МЕТРИКИ (Цена или Объем) ---

@dp.callback_query(SmartAlertForm.simple_metric, F.data.startswith("s_metric:"))
async def simple_metric_chosen(callback: types.CallbackQuery, state: FSMContext):
    metric = callback.data.split(":")[1] # 'price' или 'vol'
    await state.update_data(metric=metric)
    await callback.answer()
    
    metric_name = "Цене" if metric == 'price' else "Объему торгов"
    
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

# --- ВЫБОР ЕДИНИЦЫ ИЗМЕРЕНИЯ ---

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
        name = "целевой объем 24ч (в долларах)"
        
    await callback.message.edit_text(
        f"💵 <b>Ввод точного значения</b>\n\n"
        f"🪙 Монета: <b>{coin}</b>\n"
        f"📍 Текущее значение: <code>{base_val}</code>\n\n"
        f"✏️ <b>Напиши в чат {name}:</b>\n"
        f"<i>(Пример числа: <code>{ex_val}</code>)</i>"
    )
    await state.set_state(SmartAlertForm.simple_value_input)

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
        name = "Объем 24ч"
        
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
    # 1 ряд: Минусы (падение)
    builder.add(InlineKeyboardButton(text="-10%", callback_data="pct_add:-10"))
    builder.add(InlineKeyboardButton(text="-5%", callback_data="pct_add:-5"))
    builder.add(InlineKeyboardButton(text="-1%", callback_data="pct_add:-1"))
    # 2 ряд: Плюсы (рост)
    builder.add(InlineKeyboardButton(text="+1%", callback_data="pct_add:1"))
    builder.add(InlineKeyboardButton(text="+5%", callback_data="pct_add:5"))
    builder.add(InlineKeyboardButton(text="+10%", callback_data="pct_add:10"))
    # 3 ряд: Точные действия
    builder.add(InlineKeyboardButton(text="🔄 Сбросить (0%)", callback_data="pct_reset"))
    builder.add(InlineKeyboardButton(text="✏️ Ввести свой %", callback_data="pct_manual"))
    # 4 ряд: Подтверждение (только если процент не 0)
    if current_pct != 0.0:
        builder.add(InlineKeyboardButton(
            text=f"✅ Установить алерт ({sign}{current_pct:.1f}%)", 
            callback_data="pct_confirm"
        ))
        
    builder.adjust(3, 3, 2, 1)
    return text, builder.as_markup()

# --- СТАРТ МЕНЮ ПРОЦЕНТОВ ---

@dp.callback_query(SmartAlertForm.simple_unit, F.data == "s_unit:percent")
async def simple_unit_percent_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_pct=0.0) # Стартуем с 0%
    data = await state.get_data()
    
    text, kb = get_percent_menu_text_and_kb(data)
    await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(SmartAlertForm.simple_percent_menu)

# --- НАЖАТИЕ НА КНОПКИ ПЛЮС/МИНУС/СБРОС ---

@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data.startswith("pct_add:"))
async def percent_add_handler(callback: types.CallbackQuery, state: FSMContext):
    delta = float(callback.data.split(":")[1])
    data = await state.get_data()
    new_pct = round(data.get('current_pct', 0.0) + delta, 1)
    
    await state.update_data(current_pct=new_pct)
    data['current_pct'] = new_pct
    
    text, kb = get_percent_menu_text_and_kb(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass # Игнорируем, если текст не изменился
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

# --- РУЧНОЙ ВВОД ПРОЦЕНТА ---

@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data == "pct_manual")
async def percent_manual_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "✏️ <b>Ручной ввод процента</b>\n\n"
        "Напиши в чат любое число (процент изменения).\n"
        "• Для роста пиши просто число: <code>15</code> или <code>2.5</code>\n"
        "• Для падения пиши с минусом: <code>-7</code> или <code>-3.3</code>"
    )
    # Используем старый стейт simple_value_input, но с флагом, что это проценты!
    await state.update_data(is_manual_percent=True)
    await state.set_state(SmartAlertForm.simple_value_input)

# --- ПОДТВЕРЖДЕНИЕ И СОХРАНЕНИЕ В БД ---

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
        target_val = data['base_vol'] * (1 + current_pct / 100)
        direction = "UP" if current_pct > 0 else "DOWN"
        success = await add_smart_alert(
            user_id=callback.from_user.id, coin=coin, alert_type='simple',
            vol_check=1, vol_target=target_val, vol_dir=direction
        )
        dir_text = "📈 объем вырастет на" if direction == "UP" else "📉 объем упадет на"
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

# --- ФИНАЛ СПРИНТА 2: ВВОД ЧИСЛА И СОХРАНЕНИЕ ---

@dp.message(SmartAlertForm.simple_value_input)
async def simple_value_received(message: types.Message, state: FSMContext):
    data = await state.get_data()
    is_percent = data.get('is_manual_percent', False)
    
    try:
        raw_val = float(message.text.replace(",", ".").replace(" ", ""))
        # Если это доллары — число должно быть строго > 0. Если проценты — любое кроме 0!
        if (not is_percent and raw_val <= 0) or (is_percent and raw_val == 0):
            raise ValueError
    except ValueError:
        if is_percent:
            return await message.answer("❌ Ошибка! Введи процент (например: <code>5</code> или <code>-3.5</code>):")
        else:
            return await message.answer("❌ Ошибка! Введи положительное число без букв:")
            
    coin = data['coin']
    metric = data['metric']
    
    # ЕСЛИ ЭТО РУЧНОЙ ВВОД ПРОЦЕНТОВ — считаем цель от базовой цены!
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

    # Определяем направление (UP если цель больше текущего, иначе DOWN)
    if metric == 'price':
        direction = "UP" if target_val > data['base_price'] else "DOWN"
        success = await add_smart_alert(
            user_id=message.chat.id, coin=coin, alert_type='simple',
            price_check=1, price_target=target_val, price_dir=direction
        )
        dir_text = "📈 выросла до" if direction == "UP" else "📉 упала до"
        val_str = f"<code>{target_val:,.2f} $</code> ({'+' if current_pct>0 else ''}{current_pct:.1f}%)"
    else:
        direction = "UP" if target_val > data['base_vol'] else "DOWN"
        success = await add_smart_alert(
            user_id=message.chat.id, coin=coin, alert_type='simple',
            vol_check=1, vol_target=target_val, vol_dir=direction
        )
        dir_text = "📈 объем превысит" if direction == "UP" else "📉 объем упадет ниже"
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
                      vol_check, vol_target, vol_dir 
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
        coin = a["coin_symbol"].replace("USDT", "") # Убираем USDT для красоты на кнопке
        
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
                button_text = f"{direction} {coin} Объем → {vol_str} ❌"
            else:
                button_text = f"❓ {coin} (простой алерт) ❌"
        else:
            # Задел под Спринт 4 (сложные алерты)
            op_symbol = "&" if a["operator"] == "AND" else "|"
            button_text = f"⚡️ {coin} [Цена {op_symbol} Объем] ❌"
        
        builder.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"delete_alert:{a['id']}"
        ))
    
    builder.adjust(1) # По 1 кнопке в ряд, чтобы текст не обрезался
    
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
    
@dp.message(F.text == "📊 Объемы (24ч)")
async def menu_volumes(message: types.Message):
    msg = await message.answer("⏳ Запрашиваю статистику объемов с Binance...")
    
    stats = await get_cached_stats()
    if not stats:
        return await msg.edit_text("❌ Не удалось получить данные. Попробуйте позже.")
        
    text = "📊 <b>Объемы торгов за 24 часа (Топ-10):</b>\n\n"
    
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
                vol_str = f"{vol:.0f} $"
                
            change = data['price_change_percent']
            sign = "🟢 +" if change > 0 else "🔴 "
            
            text += f"🔹 <b>{coin}</b>: {vol_str} (<i>{sign}{change}%</i>)\n"
            
    await msg.edit_text(text)
    
@dp.message(Command("admin"), IsAdmin(ADMIN_ID))
async def admin_panel(message: types.Message):
    users = list(await get_all_users())
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM price_alerts") as cursor:
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