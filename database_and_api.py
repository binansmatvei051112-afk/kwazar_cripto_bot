import asyncio
import aiohttp # type: ignore
import aiosqlite # type: ignore
import logging
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
            CREATE TABLE IF NOT EXISTS price_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coin_symbol TEXT NOT NULL,
                target_price REAL NOT NULL,
                alert_type TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.commit()

async def add_alert(user_id: int, coin: str, price: float, alert_type: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT id FROM price_alerts WHERE user_id=? AND coin_symbol=? AND target_price=? AND alert_type=?",
            (user_id, coin.upper(), price, alert_type)
        )
        if await cursor.fetchone():
            return False
        
        await db.execute(
            "INSERT INTO price_alerts (user_id, coin_symbol, target_price, alert_type) VALUES (?, ?, ?, ?)",
            (user_id, coin.upper(), price, alert_type)
        )
        await db.commit()
        return True
    
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

async def get_chart_image(symbol, interval="1h", limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume", 
        "close_time", "quote_asset_volume", "number_of_trades", 
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df['close'] = pd.to_numeric(df['close'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')

    df.ta.sma(length=20, append=True)
    df.ta.rsi(length=14, append=True)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), gridspec_kw={'height_ratios': [3, 1]})
    fig.suptitle(f"График {symbol} ({interval})", fontsize=14, fontweight='bold')


    if interval in ['15m', '1h']:
        date_fmt = mdates.DateFormatter('%H:%M')
    else:
        date_fmt = mdates.DateFormatter('%d.%m')

    ax1.plot(df['time'], df['close'], color='cyan', linewidth=2, label='Цена')
    if 'SMA_20' in df.columns:
        ax1.plot(df['time'], df['SMA_20'], color='yellow', linewidth=1.5, linestyle='--', label='SMA 20')
    
    ax1.grid(True, linestyle='--', alpha=0.6)
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
    
    ax2.grid(True, linestyle='--', alpha=0.6)
    ax2.set_ylabel("RSI")
    ax2.set_xlabel("Время")
    ax2.set_ylim(0, 100)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close() 
    return buf

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