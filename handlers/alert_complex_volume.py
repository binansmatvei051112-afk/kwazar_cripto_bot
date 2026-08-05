"""
Сложный алерт (И/ИЛИ) — ветка настройки УСЛОВИЯ ПО ОБЪЁМУ
и финальное сохранение алерта в базу (add_smart_alert).
"""
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot_instance import dp
from keyboards import get_percent_menu_text_and_kb_complex, main_kb
from states import SmartAlertForm
from constants import VOL_TF_NAMES
from database_and_api import get_symbol_volume, add_smart_alert

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
        f"<i> Объем продажи за это время равен {current_val / 1_000_000:,.2f} млн $ </i>"
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
        price_check=1, price_target=data['price_target'], price_dir=data['price_dir'], price_tf=data['price_tf'], price_rate_unit=data['price_rate_unit'],
        vol_check=1, vol_target=vol, vol_dir=direction, vol_tf=vol_tf
    )
    
    await state.clear()
    
    dir_price = "📈 выростет до" if data['price_dir'] == "UP" else "📉 упадет до"
    dir_vol = "📈 выростет до" if direction == "UP" else "📉 упадет до"
    op_text = "И" if data['operator'].upper() == "AND" else "ИЛИ"

    if success:
            await callback.message.answer(
                f"✅ <b>Сложный алерт установлен!</b>\n\n"
                f"🪙 Монета: <code>{data['coin']}</code>\n"
                f"🎯 Условие: я пришлю сообщение когда процент изменение монеты {dir_price} <code>{data['price_target']} %</code>\n"
                f"🔗 <b>{op_text}</b>\n"
                f"📊 Когда Объем за {VOL_TF_NAMES[vol_tf]} {dir_vol} <code>{vol:,.0f} $</code>",
                reply_markup=main_kb
            )
    else:
        
        await callback.message.answer(
            "❌ Не удалось сохранить алерт, попробуй ещё раз.", 
            reply_markup=main_kb
        )

@dp.message(SmartAlertForm.complex_vol_input)
async def cmd_input_vol(message: types.Message, state: FSMContext):
    tf_name = {"1h":"1 часа", "4h":"4 часов", "1d": "24 часов", "7d": "7 дней"}
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
        price_check=1, price_target=data['price_target'], price_dir=data['price_dir'], price_tf=data['price_tf'], price_rate_unit=data['price_rate_unit'],
        vol_check=1, vol_target=vol, vol_dir=direction, vol_tf=vol_tf
    )

    await state.clear()

    dir_price = "📈 выростет до" if data['price_dir'] == "UP" else "📉 упадет до"
    dir_vol = "📈 выростет до" if direction == "UP" else "📉 упадет до"
    op_text = "И" if data['operator'].upper() == "AND" else "ИЛИ"

    if success:
        if data['price_rate_unit'] == 'percent':
            await message.answer(
                f"✅ <b>Сложный алерт установлен!</b>\n\n"
                f"🪙 Монета: <code>{data['coin']}</code>\n"
                f"🎯 Условие: я пришлю сообщение когда процент изменение монеты {dir_price} <code>{data['price_target']} %</code> за период {tf_name[data['price_tf']]}\n"
                f"🔗 <b>{op_text}</b>\n"
                f"📊 Когда Объем за {VOL_TF_NAMES[vol_tf]} {dir_vol} <code>{vol:,.0f} $</code>",
                reply_markup=main_kb
            )
        else:
            await message.answer(
                f"✅ <b>Сложный алерт установлен!</b>\n\n"
                f"🪙 Монета: <code>{data['coin']}</code>\n"
                f"🎯 Условие: я пришлю сообщение когда изменение монеты {dir_price} <code>{data['price_target']} $</code> за период {tf_name[data['price_tf']]}\n"
                f"🔗 <b>{op_text}</b>\n"
                f"📊 Когда Объем за {VOL_TF_NAMES[vol_tf]} {dir_vol} <code>{vol:,.0f} $</code>",
                reply_markup=main_kb
            )
    else:
        await message.answer("❌ Не удалось сохранить алерт, попробуй ещё раз.", reply_markup=main_kb)
