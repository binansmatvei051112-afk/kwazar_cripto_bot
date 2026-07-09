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
from database_and_api import init_db, fetch_binance_prices, add_alert, DB_NAME, fetch_binance_24h_stats, get_chart_image, get_all_users, add_users
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

class AlertForm(StatesGroup):
    waiting_for_coin = State()
    waiting_for_price = State()

class ChartStates(StatesGroup):
    choosing_coin = State()
    choosing_tf = State()
    
class IsAdmin(BaseFilter):
    def __init__(self, admin_id:int):
        self.admin_id = admin_id
        
    async def __call__ (self, message: types.Message) -> bool:
        return message.from_user.id == self.admin_id

async def check_alerts_loop():
    logger.info("Фоновый воркер проверки цен запущен")
    try:
        while True:
            try:
                prices = await fetch_binance_prices(quote_asset="USDT")
                if not prices:
                    await asyncio.sleep(30)
                    continue

                async with aiosqlite.connect(DB_NAME) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute("SELECT id, user_id, coin_symbol, target_price, alert_type FROM price_alerts") as cursor:
                        alerts = await cursor.fetchall()
                        
                        for alert in alerts:
                            symbol = alert["coin_symbol"]
                            current_price = prices.get(symbol)

                            if current_price is None:
                                continue

                            triggered = False
                            if alert["alert_type"] == "UP" and current_price >= alert["target_price"]:
                                triggered = True
                            elif alert["alert_type"] == "DOWN" and current_price <= alert["target_price"]:
                                triggered = True

                            if triggered:
                                message_text = (
                                    f"🚨 <b>УВЕДОМЛЕНИЕ О ЦЕНЕ</b> 🚨\n\n"
                                    f"🪙 Пара: <code>{symbol}</code>\n"
                                    f"📈 Текущая цена: <code>{current_price}</code>\n"
                                    f"🎯 Твоя цель: <code>{alert['target_price']}</code>"
                                )
                                try:
                                    await bot.send_message(chat_id=alert["user_id"], text=message_text)
                                except Exception as e:
                                    logger.error(f"Не удалось отправить сообщение {alert['user_id']}: {e}")
                                
                                await db.execute("DELETE FROM price_alerts WHERE id = ?", (alert["id"],))
                    await db.commit()
            except Exception as e:
                logger.error(f"Ошибка в фоновом цикле: {e}")
            
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Фоновый воркер остановлен")
        
async def send_morning_newsletter():
    logger.info("Начинаем утреннюю рассылку...")
    stats = await fetch_binance_24h_stats(quote_asset="USDT")
    
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
    
    prices = await fetch_binance_prices(quote_asset="USDT")
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
        
    prices = await fetch_binance_prices(quote_asset="USDT")
    current_price = prices.get(coin)
    
    if current_price is None:
        return await message.answer(f"❌ Монета <code>{coin}</code> не найдена на Binance.")
        
    await message.answer(f"📈 Текущая цена <b>{coin}</b>: <code>{current_price}</code> $")


@dp.message(F.text == "Создать алерт")
async def start_alert_creation(message: types.Message, state: FSMContext):
    await message.answer("Перехожу в режим настройки...", reply_markup=cancel_kb)
    
    builder = InlineKeyboardBuilder()
    for coin in POPULAR_COINS:
        builder.add(InlineKeyboardButton(text=coin, callback_data=f"popcoin:{coin}"))
    builder.adjust(3) 
    
    await message.answer(
        "🪙 <b>Выбери монету</b> из быстрых кнопок ниже\n\n"
        "<i>Или напиши её тикер вручную в чат (например, ARB):</i>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AlertForm.waiting_for_coin)

@dp.callback_query(AlertForm.waiting_for_coin, F.data.startswith("popcoin:"))
async def process_coin_inline(callback: types.CallbackQuery, state: FSMContext):
    coin = callback.data.split(":")[1] + "USDT"
    
    prices = await fetch_binance_prices(quote_asset="USDT")
    current_price = prices.get(coin)
    
    if current_price is None:
        return await callback.answer("❌ Ошибка получения цены", show_alert=True)
        
    await state.update_data(coin=coin, current_price=current_price)
    
    await callback.answer()
    await callback.message.edit_text(
        f"✅ Выбрана монета: <b>{coin}</b>\nТекущая цена: <code>{current_price}</code>\n\n"
        "🎯 <b>Введи целевую цену</b> (например, 62000 или 62000.50):"
    )
    await state.set_state(AlertForm.waiting_for_price)

@dp.message(AlertForm.waiting_for_coin)
async def process_coin_text(message: types.Message, state: FSMContext):
    coin = message.text.upper().strip()
    if not coin.endswith("USDT"):
        coin += "USDT"

    prices = await fetch_binance_prices(quote_asset="USDT")
    current_price = prices.get(coin)

    if current_price is None:
        return await message.answer(
            f"❌ Монета <code>{coin}</code> не найдена на Binance.\n"
            "Попробуй ввести другой тикер или нажми «Отмена»."
        )

    await state.update_data(coin=coin, current_price=current_price)
    
    await message.answer(
        f"✅ Найдено! Текущая цена <b>{coin}</b>: <code>{current_price}</code>\n\n"
        "🎯 <b>Введи целевую цену</b> (например, 62000 или 62000.50):"
    )
    await state.set_state(AlertForm.waiting_for_price)

@dp.message(AlertForm.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    try:
        target_price = float(message.text.replace(",", "."))
    except ValueError:
        return await message.answer("❌ Ошибка. Цена должна быть числом. Попробуй еще раз:")

    user_data = await state.get_data()
    coin = user_data["coin"]
    current_price = user_data["current_price"]

    alert_type = "UP" if target_price > current_price else "DOWN"

    result = await add_alert(user_id=message.chat.id, coin=coin, price=target_price, alert_type=alert_type)
    
    await state.clear()

    if not result:
        return await message.answer("⚠️ Точно такой же алерт уже установлен.", reply_markup=main_kb)

    direction_emoji = "📈 выше" if alert_type == "UP" else "📉 ниже"
    await message.answer(
        f"✅ <b>Уведомление установлено!</b>\n\n"
        f"🪙 Пара: <code>{coin}</code>\n"
        f"🎯 Цель: <code>{target_price}</code>\n"
        f"🔔 Я напишу тебе, когда цена станет {direction_emoji} текущей.",
        reply_markup=main_kb
    )

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
    
    await callback.message.edit_text("⏳ *Генерирую график... Пожалуйста, подождите.*")
    
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
            "SELECT id, coin_symbol, target_price, alert_type FROM price_alerts WHERE user_id = ?",
            (message.chat.id,)
        ) as cursor:
            user_alerts = await cursor.fetchall()
    
    if not user_alerts:
        return await message.answer("У вас нет активных уведомлений.")
    
    builder = InlineKeyboardBuilder()
    
    for a in user_alerts:
        direction = "⬆️" if a["alert_type"] == "UP" else "⬇️"
        button_text = f"{direction} {a['coin_symbol']} → {a['target_price']} ❌"
        
        builder.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"delete_alert:{a['id']}"
        ))
    
    builder.adjust(1)
    
    await message.answer(
        "📋 <b>Ваши уведомления:</b>\n\n"
        "<i>Нажмите на кнопку с алертом, чтобы быстро удалить его.</i>",
        reply_markup=builder.as_markup() 
    )
    
@dp.callback_query(F.data.startswith("delete_alert:"))
async def process_delete_alert(callback: types.CallbackQuery):
    alert_id = int(callback.data.split(":")[1])
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM price_alerts WHERE id = ? AND user_id = ?", 
            (alert_id, callback.from_user.id)
        )
        await db.commit()
   
    await callback.answer("✅ Уведомление успешно удалено!")
    
    await callback.message.edit_text(
        "✅ <b>Алерт удален!</b>\n\nОбновите меню «Мои алерты», чтобы увидеть актуальный список."
    )

async def on_startup():
    
    asyncio.create_task(check_alerts_loop())
    
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    
    scheduler.add_job(send_morning_newsletter, 'cron', hour=8, minute=30)
    
    scheduler.start()
    logger.info("Планировщик задач запущен!")
    
@dp.message(F.text == "📊 Объемы (24ч)")
async def menu_volumes(message: types.Message):
    msg = await message.answer("⏳ Запрашиваю статистику объемов с Binance...")
    
    stats = await fetch_binance_24h_stats(quote_asset="USDT")
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