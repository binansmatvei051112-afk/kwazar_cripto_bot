"""
Простой алерт — ветка настройки УСЛОВИЯ ПО ОБЪЁМУ + сохранение в базу.
"""
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from config import logger
from bot_instance import dp
from keyboards import get_percent_menu_text_and_kb, main_kb
from states import SmartAlertForm
from constants import VOL_TF_NAMES
from database_and_api import get_symbol_volume, add_smart_alert
from handlers.alert_simple_price import ask_simple_unit

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
@dp.callback_query(SmartAlertForm.simple_unit, F.data == "s_unit:percent")
async def simple_unit_percent_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    
    coin = data['coin']
    metric = data.get('metric', 'price')
    current_pct = data.get('current_pct', 0.0)
    
    # Формируем строки значений до сборки текста
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
    
    kb = get_percent_menu_text_and_kb(data)
    await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(SmartAlertForm.simple_percent_menu)

@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data.startswith("pct_add:"))
async def s_percent_add_handler(callback: types.CallbackQuery, state: FSMContext):
    delta = float(callback.data.split(":")[1])
    data = await state.get_data()
    new_pct = round(data.get('current_pct', 0.0) + delta, 1)
    tf_name = VOL_TF_NAMES.get(data.get('vol_tf', '1d'), '24 часа')
    
    await state.update_data(current_pct=new_pct)
    data['current_pct'] = new_pct

    # 1. Заранее рассчитываем целевое значение в зависимости от метрики
    current_pct_val = data.get('current_pct', 0.0)
    if data['metric'] == 'price':
        target_val = data['base_price'] * (1 + current_pct_val / 100)
        target_text = f"{target_val:,.2f} $"
    else:
        target_val = (data['base_vol'] * (1 + current_pct_val / 100)) / 1_000_000
        target_text = f"{target_val:,.2f} млн $"
    
    # 2. Формируем чистый текст сообщения
    text = (
        f"📈 <b>Настройка алерта в процентах</b>\n\n"
        f"🪙 Монета: <b>{data['coin']}</b> ({'Цена' if data['metric'] == 'price' else f'Объем ({tf_name})'})\n"
        f"📍 Текущее значение: <code>{f'{data['base_price']:,.2f} $' if data['metric'] == 'price' else f'{data['base_vol'] / 1_000_000:,.2f} млн $'}</code>\n\n"
        f"🎛 Выбранное изменение: <b>{'+' if current_pct_val > 0 else ''}{current_pct_val:.1f}%</b>\n"
        f"🎯 Целевое значение: <code>{target_text}</code>\n\n" # Теперь тег строго парный
        f"<i>Нажимай кнопки ниже, чтобы настроить нужный процент:</i>"
    )
    
    kb = get_percent_menu_text_and_kb(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка обновления меню процентов: {e}")
        
    await callback.answer(f"{'+' if delta > 0 else ''}{delta}%")


@dp.callback_query(SmartAlertForm.simple_percent_menu, F.data == "pct_reset")
async def percent_reset_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    data['current_pct'] = 0.0
    tf_name = VOL_TF_NAMES.get(data.get('vol_tf', '1d'), '24 часа')
    
    text = (
        f"📈 <b>Настройка алерта в процентах</b>\n\n"
        f"🪙 Монета: <b>{data['coin']}</b> ({'Цена' if data['metric'] == 'price' else f'Объем ({tf_name})'})\n"
        
        f"📍 Текущее значение: <code>{f'{data['base_price']:,.2f} $' if data['metric'] == 'price' else f'{data['base_vol'] / 1_000_000:,.2f} млн $'}</code>\n\n"
        
        f"🎛 Выбранное изменение: <b>{'+' if data.get('current_pct', 0.0) > 0 else ''}{data.get('current_pct', 0.0):.1f}%</b>\n"
        
        f"🎯 Целевое значение: <code>"
        f"{data['base_price'] * (1 + data.get('current_pct', 0.0) / 100):,.2f} $" 
        if data['metric'] == 'price' else 
        f"{(data['base_vol'] * (1 + data.get('current_pct', 0.0) / 100)) / 1_000_000:,.2f} млн $"
        f"</code>\n\n"
        
        f"<i>Нажимай кнопки ниже, чтобы настроить нужный процент:</i>"
    )
    
    kb = get_percent_menu_text_and_kb(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        logger.error(f"Ошибка обновления меню процентов: {e}")
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
        dir_text = "📈 монета выростет до" if direction == "UP" else "📉 монета упадет до"
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
