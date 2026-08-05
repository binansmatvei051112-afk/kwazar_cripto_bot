"""
Раздел "Показать график монеты": выбор монеты, выбор таймфрейма,
генерация и отправка PNG-графика (matplotlib) через database_and_api.
"""
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, BufferedInputFile

from bot_instance import dp
from states import ChartStates
from constants import POPULAR_COINS
from database_and_api import get_chart_image

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
