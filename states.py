"""
States (FSM) — все состояния диалогов для создания алертов и графиков.
"""
from aiogram.fsm.state import State, StatesGroup

class SmartAlertForm(StatesGroup):
    
    choosing_coin = State()
    choosing_complexity = State()
    
    
    simple_metric = State()
    simple_price_mode = State()      # уровень vs скорость
    simple_price_rate_tf = State()   # период для скорости
    simple_price_rate_unit = State() # деньги/проценты для скорости
    simple_price_rate_input = State()
    simple_price_rate_menu_percent = State()
    simple_vol_tf = State()
    simple_unit = State()
    simple_value_input = State()   
    simple_percent_menu = State()
    
    
    complex_operator = State()
    complex_price_mode = State()
    
    complex_price_rate_tf = State()   # период для скорости
    complex_price_rate_unit = State()
    complex_price_rate_input = State()
    complex_price_rate_menu_percent = State()
    
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
    choosing_price_coin = State()
    choosing_tf_coin = State()
