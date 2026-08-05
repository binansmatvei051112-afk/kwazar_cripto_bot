"""
Общие клавиатуры бота: главное меню, кнопка отмены,
а также построители меню выбора процента для алертов (simple/complex).
"""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from constants import VOL_TF_NAMES

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Создать алерт")],
        [KeyboardButton(text="Мои алерты"), KeyboardButton(text="Показать график монеты")],
        [KeyboardButton(text="🔍 Курсы валют"), KeyboardButton(text="📊 Объемы")]
    ],
    resize_keyboard=True,
    is_persistent=True
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🚫 Отмена")]],
    resize_keyboard=True,
    is_persistent=True
)

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
    current_pct = data.get('current_pct', 0.0)
        
    sign = "+" if current_pct > 0 else ""
    
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
    return builder.as_markup()
