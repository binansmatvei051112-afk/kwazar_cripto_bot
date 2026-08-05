"""
Импорт этого пакета регистрирует ВСЕ хендлеры бота на общий `dp`
(см. bot_instance.py) — декораторы @dp.message(...) / @dp.callback_query(...)
срабатывают в момент импорта модуля.

main.py делает один раз `import handlers`, и этого достаточно, чтобы
подключить все команды и кнопки. Если добавляешь новый файл с хендлерами
в эту папку — не забудь добавить строку импорта сюда, иначе бот его
"не увидит".
"""
from . import start
from . import prices
from . import volumes
from . import chart
from . import alerts_list
from . import admin
from . import alert_common
from . import alert_complex_price
from . import alert_complex_volume
from . import alert_simple_price
from . import alert_simple_volume
