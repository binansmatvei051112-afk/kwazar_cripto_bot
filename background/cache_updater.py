"""
Фоновый воркер: раз в 30 секунд обновляет локальный кэш цен и статистики
с Binance (crypto_cache в SQLite).
"""
import asyncio
from config import logger
from database_and_api import fetch_binance_prices, fetch_binance_24h_stats, update_crypto_cache

async def cache_updater_loop():
    logger.info("Фоновый воркер обновления кэша запущен")
    try:
        while True:
            
            prices = await fetch_binance_prices(quote_asset="USDT")
            stats = await fetch_binance_24h_stats(quote_asset="USDT")
            
            
            if prices and stats:
                await update_crypto_cache(prices, stats)
                
            else:
                logger.warning("Binance недоступен! Используем старые данные из кэша.")
                
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Воркер кэша остановлен")
