import asyncio
import aiohttp # type: ignore
import aiosqlite # type: ignore
import logging
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt # type: ignore
import pandas as pd # type: ignore
import io
import pandas_ta as ta # type: ignore
import matplotlib.dates as mdates # type: ignore

DB_NAME = "alerts.db"

logger = logging.getLogger(__name__)

BINANCE_API_URL = "https://api1.binance.com/api/v3/ticker/price"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS smart_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coin_symbol TEXT NOT NULL,
                alert_type TEXT NOT NULL,       -- 'simple' или 'complex'
                operator TEXT DEFAULT NULL,     -- 'AND', 'OR' (только для complex)
                
                -- Условие по ЦЕНЕ
                price_check BOOLEAN DEFAULT 0,  -- 1 если проверяем цену, 0 если нет
                price_target REAL DEFAULT NULL, -- Целевая цена (уже посчитанная в $)
                price_dir TEXT DEFAULT NULL,    -- 'UP' (выше) или 'DOWN' (ниже)
                
                -- Условие по ОБЪЕМУ
                vol_check BOOLEAN DEFAULT 0,    -- 1 если проверяем объем, 0 если нет
                vol_target REAL DEFAULT NULL,   -- Целевой объем (в $)
                vol_dir TEXT DEFAULT NULL,       -- 'UP' (выше) или 'DOWN' (ниже)
                vol_tf TEXT DEFAULT '1d'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS crypto_cache (
                coin_symbol TEXT PRIMARY KEY,
                price REAL,
                quote_volume REAL,
                price_change_percent REAL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
    
async def add_users(user_id:int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()
        await db.close()
        
async def get_all_users() -> list:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return (row[0] for row in rows)

async def fetch_binance_prices(quote_asset: str = None) -> dict:
    try:
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(BINANCE_API_URL, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    prices = {item['symbol']: float(item['price']) for item in data}
                    
                    if quote_asset:
                        prices = {k: v for k, v in prices.items() if k.endswith(quote_asset)}
                    
                    return prices
                else:
                    logger.warning(f"Binance API вернул странный статус: {response.status}")
                    
    except Exception as e:
        logger.error(f"Сетевая ошибка при запросе к Binance: {repr(e)}")
        
    return {}

def _draw_chart_sync(data, symbol, interval):
    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume", 
        "close_time", "quote_asset_volume", "number_of_trades", 
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df['close'] = pd.to_numeric(df['close'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')

    df.ta.sma(length=20, append=True)
    df.ta.rsi(length=14, append=True)

    plt.style.use('dark_background') 

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), gridspec_kw={'height_ratios': [3, 1]})
    fig.suptitle(f"График {symbol} ({interval})", fontsize=14, fontweight='bold', color='white')

    if interval in ['15m', '1h']:
        date_fmt = mdates.DateFormatter('%H:%M')
    else:
        date_fmt = mdates.DateFormatter('%d.%m')

    ax1.plot(df['time'], df['close'], color='cyan', linewidth=2, label='Цена')
    if 'SMA_20' in df.columns:
        ax1.plot(df['time'], df['SMA_20'], color='yellow', linewidth=1.5, linestyle='--', label='SMA 20')
    
    ax1.grid(True, linestyle='--', alpha=0.3)
    ax1.set_ylabel("Цена (USDT)")
    ax1.legend(loc="upper left")
    ax1.tick_params(axis='x', which='both', bottom=False, labelbottom=False)

    if 'RSI_14' in df.columns:
        ax2.plot(df['time'], df['RSI_14'], color='magenta', linewidth=1.5, label='RSI')
        ax2.axhline(70, color='red', linestyle='--', alpha=0.5)
        ax2.axhline(30, color='green', linestyle='--', alpha=0.5)
        ax2.fill_between(df['time'], 70, 30, color='gray', alpha=0.1)

    ax2.xaxis.set_major_formatter(date_fmt)
    plt.xticks(rotation=45) 
    ax2.grid(True, linestyle='--', alpha=0.3)
    ax2.set_ylabel("RSI")
    ax2.set_xlabel("Время")
    ax2.set_ylim(0, 100)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    
    # ОЧЕНЬ ВАЖНО: очищаем память, иначе при каждом запросе бот будет жрать больше ОЗУ
    plt.clf() 
    plt.close(fig) 
    
    return buf

# 2. Асинхронная обертка для бота
async def get_chart_image(symbol, interval="1h", limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    
    # Быстро и асинхронно скачиваем данные
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    # Отправляем тяжелую отрисовку в соседний поток, чтобы бот не зависал
    buf = await asyncio.to_thread(_draw_chart_sync, data, symbol, interval)
    
    return buf

async def get_cached_prices() -> dict:
    """Достает цены из локальной БД"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT coin_symbol, price FROM crypto_cache") as cursor:
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}
    
async def get_cached_stats() -> dict:
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT coin_symbol, quote_volume, price_change_percent FROM crypto_cache") as cursor:
            rows = await cursor.fetchall()
            return {row[0]: {'quote_volume': row[1], 'price_change_percent': row[2]} for row in rows}

BINANCE_24HR_URL = "https://api.binance.com/api/v3/ticker/24hr"

async def fetch_binance_24h_stats(quote_asset: str = "USDT") -> dict:
    
    try:
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(BINANCE_24HR_URL, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    stats = {}
                    for item in data:
                        if item['symbol'].endswith(quote_asset):
                            stats[item['symbol']] = {
                                'quote_volume': float(item['quoteVolume']), 
                                'price_change_percent': float(item['priceChangePercent'])
                            }
                    return stats
                else:
                    logger.warning(f"Binance 24hr API вернул статус: {response.status}")
                    
    except Exception as e:
        logger.error(f"Сетевая ошибка при запросе объемов: {repr(e)}")
        
    return {}

async def update_crypto_cache(prices: dict, stats: dict):
    async with aiosqlite.connect(DB_NAME) as db:
        
        data_to_insert = []
        for symbol, price in prices.items():
            vol = stats.get(symbol, {}).get('quote_volume', 0.0)
            change = stats.get(symbol, {}).get('price_change_percent', 0.0)
            data_to_insert.append((symbol, price, vol, change))

        await db.executemany("""
            INSERT INTO crypto_cache (coin_symbol, price, quote_volume, price_change_percent, last_updated)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(coin_symbol) DO UPDATE SET
                price=excluded.price,
                quote_volume=excluded.quote_volume,
                price_change_percent=excluded.price_change_percent,
                last_updated=CURRENT_TIMESTAMP
        """, data_to_insert)
        await db.commit()

async def add_smart_alert(
    user_id: int, 
    coin: str, 
    alert_type: str,  # 'simple' или 'complex'
    operator: str = None,
    price_check: int = 0, price_target: float = None, price_dir: str = None,
    vol_check: int = 0, vol_target: float = None, vol_dir: str = None,
    vol_tf: str = "1d"
) -> bool:
    
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("""
                INSERT INTO smart_alerts (
                    user_id, coin_symbol, alert_type, operator,
                    price_check, price_target, price_dir,
                    vol_check, vol_target, vol_dir, vol_tf
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, coin, alert_type, operator, price_check, price_target, price_dir, vol_check, vol_target, vol_dir, vol_tf))
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка сохранения умного алерта: {e}")
        return False

async def fetch_coin_volume_tf(symbol: str, window_size: str = "1d") -> float:
    
    url = f"https://api.binance.com/api/v3/ticker?symbol={symbol}&windowSize={window_size}"
    try:
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data.get('quoteVolume', 0.0))
    except Exception as e:
        logger.error(f"Ошибка получения объема за {window_size} для {symbol}: {e}")
    return 0.0

async def fetch_all_volumes_tf(window_size: str = "1d", quote_asset: str = "USDT") -> dict:
    
    url = f"https://api.binance.com/api/v3/ticker?windowSize={window_size}"
    try:
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(url, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        item['symbol']: {
                            'quote_volume': float(item['quoteVolume']),
                            'price_change_percent': float(item.get('priceChangePercent', 0.0))
                        }
                        for item in data if item['symbol'].endswith(quote_asset)
                    }
    except Exception as e:
        logger.error(f"Ошибка получения всех объемов за {window_size}: {e}")
    return {}

async def main():
    await init_db()
    print("База данных инициализирована")
    
    prices = await fetch_binance_prices()
    if prices:
        print(f"Получено {len(prices)} пар")
        for i, (symbol, price) in enumerate(prices.items()):
            if i >= 5:
                break
            print(f"{symbol}: {price}")
    else:
        print("Не удалось получить цены")

if __name__ == "__main__":
    asyncio.run(main())