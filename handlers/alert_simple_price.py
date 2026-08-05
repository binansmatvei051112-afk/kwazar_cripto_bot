"""
Простой алерт — ветка настройки УСЛОВИЯ ПО ЦЕНЕ
(уровень цены или скорость изменения в $/%) + сохранение в базу.

ask_simple_unit — общий хелпер (используется и веткой объёма в
alert_simple_volume.py), поэтому он импортируется оттуда.
"""
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot_instance import dp
from keyboards import get_percent_menu_text_and_kb, main_kb
from states import SmartAlertForm
from constants import VOL_TF_NAMES
from database_and_api import get_symbol_price_delta, add_smart_alert

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

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🎯 Уровень цены", callback_data="simple_price_mode:level"))
    builder.add(InlineKeyboardButton(text="⚡ Скорость изменения", callback_data="simple_price_mode:rate"))
    builder.adjust(1)

    await callback.message.edit_text(
        "📊 Отслеживание по: <b>Цене</b>\n\n"
        "<b>Шаг 3.5: Что именно отслеживаем?</b>\n"
        "🎯 <i>Уровень цены</i> — сработает, когда цена пересечёт заданную отметку "
        "(например, $65000), независимо от времени.\n"
        "⚡ <i>Скорость изменения</i> — сработает, если цена изменится на X% (или $) "
        "именно за выбранный период (например, +5% за 4 часа).",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.simple_price_mode)
    
@dp.callback_query(SmartAlertForm.simple_price_mode, F.data == "simple_price_mode:level")
async def price_mode_level_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    await ask_simple_unit(callback, state)
    
@dp.callback_query(SmartAlertForm.simple_price_mode, F.data == "simple_price_mode:rate")
async def price_mode_rate_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    builder = InlineKeyboardBuilder()
    for tf_key in ["1h", "4h", "1d", "7d"]:
        builder.add(InlineKeyboardButton(text=f"⏱ {VOL_TF_NAMES[tf_key]}", callback_data=f"simple_price_rate_tf:{tf_key}"))
    builder.adjust(2, 2)

    await callback.message.edit_text(
        "⚡ <b>Скорость изменения цены</b>\n\n"
        "<b>Шаг 4: За какой период отслеживать изменение?</b>\n"
        "<i>Например, «1 час» — алерт сработает, если цена изменится на заданную "
        "величину именно за последний час, а не с момента создания алерта.</i>",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SmartAlertForm.simple_price_rate_tf)
    
@dp.callback_query(SmartAlertForm.simple_price_rate_tf, F.data.startswith("simple_price_rate_tf:"))
async def simple_price_rate_tf_cmd(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    tf_price = callback.data.split(":")[1]
    await state.update_data(tf_price=tf_price)
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💵 В деньгах ($)", callback_data="rate_unit:money"))
    builder.add(InlineKeyboardButton(text="📈 В процентах (%)", callback_data="rate_unit:percent"))
    builder.adjust(2)
    
    await callback.message.edit_text(
        f"🔹 Отслеживание по: <b>цене за период {tf_price}</b>\n\n"
        "<b>Шаг 5: В чем задавать цель?</b>\n"
        "💵 <i>В деньгах</i> — вводишь точную сумму (например: 65000$).\n"
        "📈 <i>В процентах</i> — выберешь рост или падение в % от текущего значения.",
        reply_markup=builder.as_markup()
    )
    
    await state.set_state(SmartAlertForm.simple_price_rate_unit)
    
@dp.callback_query(SmartAlertForm.simple_price_rate_unit, F.data== "rate_unit:percent")
async def simple_price_rate_unit_handler_percent(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    
    text = (
        f"⚡️ <b>Настройка скорости изменения цены</b>\n\n"
        f"🪙 Монета: <b>{data['coin']}</b>\n"
        f"⏱ Период: <b>{tf_names.get((data.get('tf_price', '1d')), (data.get('tf_price', '1d')))}</b>\n"
        f"📍 Текущая цена: <code>{data['base_price']:,.2f} $</code>\n\n"
        f"🎛 Изменение за период: <b>{"+" if (data.get('current_pct', 0.0)) > 0 else ""}{data.get('current_pct', 0.0):.1f}%</b>\n"
        f"<i>Укажи, на сколько процентов должна измениться цена за {tf_names.get((data.get('tf_price', '1d')), (data.get('tf_price', '1d')))}:</i>"
    )
    
    kb = get_percent_menu_text_and_kb(data)
    await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(SmartAlertForm.simple_price_rate_menu_percent)
    
@dp.callback_query(SmartAlertForm.simple_price_rate_menu_percent, F.data.startswith("pct_add:"))
async def s_percent_add_handler_rate(callback: types.CallbackQuery, state: FSMContext):
    delta = float(callback.data.split(":")[1])
    data = await state.get_data()
    new_pct = round(data.get('current_pct', 0.0) + delta, 1)
    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    
    await state.update_data(current_pct=new_pct)
    data['current_pct'] = new_pct
    
    text = (
        f"⚡️ <b>Настройка скорости изменения цены</b>\n\n"
        f"🪙 Монета: <b>{data['coin']}</b>\n"
        f"⏱ Период: <b>{tf_names.get((data.get('tf_price', '1d')), (data.get('tf_price', '1d')))}</b>\n"
        f"📍 Текущая цена: <code>{data['base_price']:,.2f} $</code>\n\n"
        f"🎛 Изменение за период: <b>{"+" if (new_pct) > 0 else ""}{new_pct:.1f}%</b>\n"
        f"<i>Укажи, на сколько процентов должна измениться цена за {tf_names.get((data.get('tf_price', '1d')), (data.get('tf_price', '1d')))}:</i>"
    )
    
    kb = get_percent_menu_text_and_kb(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass 
    await callback.answer(f"{'+' if delta > 0 else ''}{delta}%")

@dp.callback_query(SmartAlertForm.simple_price_rate_menu_percent, F.data == "pct_reset")
async def percent_reset_handler_rate(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(current_pct=0.0)
    data = await state.get_data()
    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    data['current_pct'] = 0.0
    
    text = (
        f"⚡️ <b>Настройка скорости изменения цены</b>\n\n"
        f"🪙 Монета: <b>{data['coin']}</b>\n"
        f"⏱ Период: <b>{tf_names.get((data.get('tf_price', '1d')), (data.get('tf_price', '1d')))}</b>\n"
        f"📍 Текущая цена: <code>{data['base_price']:,.2f} $</code>\n\n"
        f"🎛 Изменение за период: <b>0%</b>\n"
        f"<i>Укажи, на сколько процентов должна измениться цена за {tf_names.get((data.get('tf_price', '1d')), (data.get('tf_price', '1d')))}:</i>"
    )
    
    kb = get_percent_menu_text_and_kb(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Сброшено в 0%")

@dp.callback_query(SmartAlertForm.simple_price_rate_menu_percent, F.data == "pct_manual")
async def percent_manual_start_rate(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "✏️ <b>Ручной ввод процента</b>\n\n"
        "Напиши в чат любое число (процент изменения).\n"
        "• Для роста пиши просто число: <code>15</code> или <code>2.5</code>\n"
        "• Для падения пиши с минусом: <code>-7</code> или <code>-3.3</code>"
    )
    
    await state.update_data(rate_unit="percent")
    await state.set_state(SmartAlertForm.simple_price_rate_input)
    
@dp.callback_query(SmartAlertForm.simple_price_rate_menu_percent, F.data == "pct_confirm")
async def percent_confirm_handler_rate(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    coin = data['coin']
    current_pct = data['current_pct']
    tf_price = data['tf_price']
    price_tf_names = {"1h": "1 часа", "4h": "4 часов", "1d": "24 часов", "7d": "7 дней"}
    
    if current_pct == 0.0:
        return await callback.answer("❌ Процент изменения не может быть равен 0!", show_alert=True)
        
    direction = "UP" if current_pct > 0 else "DOWN"
    dir_text = "📈 выростет до" if direction == "UP" else "📉 упадет до"
            
    success = await add_smart_alert(
        user_id=callback.message.chat.id, coin=coin, alert_type='simple',
        price_check=1, price_target=current_pct, price_dir=direction,
        price_tf=tf_price, price_rate_unit="percent",
    )
    
    sign = "+" if current_pct > 0 else ""
        
    await state.clear()
    await callback.message.delete()
    
    if success:
        await callback.message.answer(
                f"✅ <b>Алерт успешно установлен!</b>\n\n"
                f"🪙 Монета: <code>{coin}</code>\n"
                f"🎯 Условие: я пришлю уведомление, когда процент изменение монеты {dir_text} {sign}{current_pct}% за период {price_tf_names[tf_price]}).",
                reply_markup=main_kb
        )
    else:
        await callback.message.answer("❌ Произошла ошибка при сохранении в базу.", reply_markup=main_kb)
    
@dp.callback_query(SmartAlertForm.simple_price_rate_unit, F.data == "rate_unit:money")
async def simple_price_rate_unit_handler_money(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    tf_names = {"1h": "1 час", "4h": "4 часа", "1d": "24 часа", "7d": "7 дней"}
    await state.update_data(rate_unit="money")
    data = await state.get_data()
    coin = data['coin']
    tf_price = data['tf_price']
    
    base_val = f"{data['base_price']} $"
    name = "целевую цену"
    data_delta = await get_symbol_price_delta(coin, tf_price)
    
    await callback.message.edit_text(
        f"💵 <b>Ввод точного значения</b>\n\n"
        f"🪙 Монета: <b>{coin}</b>\n"
        f"📍 Текущее значение: <code>{base_val}</code>\n"
        f"📈 Текущее изменение цена за период <b>{tf_names[tf_price]}</b> равно <i>{'+' + str(data_delta)if data_delta >= 0 else  str(data_delta) }</i>\n\n"
        f"✏️ <b>Напиши в чат {name}:</b>\n"
        f"<i>(Пример числа: <code>-{(data['base_price'] - round(data['base_price'] * 0.9, 0)):.1f}$ или {(round(data['base_price'] * 1.1, 0) - data['base_price']):.1f}$</code> от {data['base_price']}$)</i>"
    )
    await state.set_state(SmartAlertForm.simple_price_rate_input)
    
@dp.message(SmartAlertForm.simple_price_rate_input)
async def simple_price_rate_input_handler(message: types.Message, state: FSMContext):
    
    data = await state.get_data()
    price_tf_names = {"1h": "1 часа", "4h": "4 часов", "1d": "24 часов", "7d": "7 дней"}
    rate_unit = data['rate_unit']
    is_percent = True if rate_unit == "percent" else False
    coin = data["coin"]
    tf_price = data['tf_price']
    
    try:
        raw_val = float(message.text.replace(",", ".").replace(" ", ""))
        
        if (not is_percent and raw_val == 0) or (is_percent and raw_val == 0):
            raise ValueError
    except ValueError:
        if is_percent:
            return await message.answer("❌ Ошибка! Введи процент (например: <code>5</code> или <code>-3.5</code>):")
        else:
            return await message.answer("❌ Ошибка! Введи положительное число без букв:")
        
    if is_percent:
        current_pct = raw_val
        target_val = raw_val                          # а вот это идёт в БД
    else:
        current_pct = (raw_val/data['base_price']*100)
        target_val = raw_val  
        

    if rate_unit == "money":
        direction = "UP" if raw_val > 0 else "DOWN"
    else:
        direction = "UP" if raw_val > 0 else "DOWN"
        
    success = await add_smart_alert(
        user_id=message.chat.id, coin=coin, alert_type='simple',
        price_check=1, price_target=target_val, price_dir=direction,
        price_tf=tf_price, price_rate_unit=rate_unit,
    )
    dir_text = "📈 выростет до" if direction == "UP" else "📉 упадет до"
    val_str = f"<code>{target_val:,.2f} $</code> ({'+' if current_pct>0 else ''}{current_pct:.1f}% от {data['base_price']}"
    sign = "+" if target_val > 0 else ""    
    
    await state.clear()
    
    if success:
        if rate_unit == "money":
            await message.answer(
                f"✅ <b>Алерт успешно установлен!</b>\n\n"
                f"🪙 Монета: <code>{coin}</code>\n"
                f"🎯 Условие: я пришлю уведомление, когда изменение монеты {dir_text} {val_str} за период {price_tf_names[tf_price]}).",
                reply_markup=main_kb
            )
        else:
            await message.answer(
                f"✅ <b>Алерт успешно установлен!</b>\n\n"
                f"🪙 Монета: <code>{coin}</code>\n"
                f"🎯 Условие: я пришлю уведомление, когда процент изменение монеты {dir_text} {sign}{target_val}% за период {price_tf_names[tf_price]}).",
                reply_markup=main_kb
            )
    else:
        await message.answer("❌ Произошла ошибка при сохранении в базу. Попробуй еще раз.", reply_markup=main_kb)

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
