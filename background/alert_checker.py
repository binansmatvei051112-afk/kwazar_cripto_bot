"""
Фоновый воркер: раз в 30 секунд проверяет все smart_alerts из базы
на совпадение условий (цена/объем, simple/complex) и рассылает уведомления.
"""
import asyncio
import aiosqlite
from config import logger
from bot_instance import bot
from constants import VOL_TF_NAMES
from database_and_api import (
    DB_NAME, get_cached_prices, get_cached_stats, fetch_all_volumes_tf
)

async def check_alerts_loop():
    logger.info("Фоновый воркер проверки алертов запущен")
    try:
        while True:
            try:
                prices = await get_cached_prices()
                stats = await get_cached_stats()
                if not prices:
                    await asyncio.sleep(30)
                    continue

                async with aiosqlite.connect(DB_NAME) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute("SELECT * FROM smart_alerts") as cursor:
                        alerts = await cursor.fetchall()


                        extra_tf_symbols = {}
                        for alert in alerts:
                            vol_tf = alert["vol_tf"] or "1d"
                            if alert["vol_check"] and vol_tf != "1d":
                                extra_tf_symbols.setdefault(vol_tf, set()).add(alert["coin_symbol"])

                            price_tf = alert["price_tf"]
                            if alert["price_check"] and price_tf and price_tf != "1d":
                                extra_tf_symbols.setdefault(price_tf, set()).add(alert["coin_symbol"])

                        extra_stats = {}
                        for tf, symbols in extra_tf_symbols.items():
                            extra_stats[tf] = await fetch_all_volumes_tf(window_size=tf, symbols=list(symbols))
                        
                        for alert in alerts:
                            symbol = alert["coin_symbol"]
                            a_type = alert["alert_type"]
                            user_id = alert["user_id"]
                            alert_id = alert["id"]
                            vol_tf = alert["vol_tf"] or "1d"
                            
                            triggered = False
                            reason_text = ""
                            
                            if a_type == "simple":
                                if alert["price_check"]:
                                    curr_price = prices.get(symbol)
                                    rate_unit = alert["price_rate_unit"]    # "money" или "percent"
                                    price_tf = alert["price_tf"]          # "1h", "4h", "1d", "7d" или None
                                    target_price = alert["price_target"]  # Целевое число из базы
                                    direction = alert["price_dir"] 
                                    if curr_price is not None:
                                        
                                        if rate_unit is None:
                                            if direction == "UP" and curr_price >= target_price:
                                                triggered = True
                                                reason_text = (
                                                    f"📈 <b>Цена достигла уровня!</b>\n"
                                                    f"Текущая цена: <code>{curr_price} $</code>\n"
                                                    f"(🎯 Твоя цель: {target_price} $)"
                                                )
                                            elif direction == "DOWN" and curr_price <= target_price:
                                                triggered = True
                                                reason_text = (
                                                    f"📉 <b>Цена достигла уровня!</b>\n"
                                                    f"Текущая цена: <code>{curr_price} $</code>\n"
                                                    f"(🎯 Твоя цель: {target_price} $)"
                                                )
                                        
                                        elif rate_unit == "percent":
                                            if rate_unit == "percent":
                                                if price_tf == "1d":
                                                    curr_percent = stats.get(symbol, {}).get("price_change_percent", 0.0)
                                                else:
                                                    curr_percent = extra_stats.get(price_tf, {}).get(symbol, {}).get("price_change_percent", 0.0)
                                            tf_name = VOL_TF_NAMES.get(price_tf, price_tf)
                                            
                                            if direction == "UP" and curr_percent >= target_price:
                                                triggered = True
                                                reason_text = (
                                                    f"📈 <b>Цена выросла!</b>\n"
                                                    f"Текущая цена: <code>{curr_price} $</code>\n"
                                                    f"Изменение за {tf_name}: <code>+{curr_percent:.2f}%</code>\n"
                                                    f"(🎯 Твоя цель: {target_price}% за {tf_name})"
                                                )
                                            elif direction == "DOWN" and curr_percent <= target_price:
                                                triggered = True
                                                reason_text = (
                                                    f"📉 <b>Цена упала!</b>\n"
                                                    f"Текущая цена: <code>{curr_price} $</code>\n"
                                                    f"Изменение за {tf_name}: <code>{curr_percent:.2f}%</code>\n"
                                                    f"(🎯 Твоя цель: {target_price}% за {tf_name})"
                                                )
                                                
                                        else:
                                            
                                            if price_tf == "1d":
                                                curr_delta = stats.get(symbol, {}).get("price_delta", 0.0)
                                            else:
                                                curr_delta = extra_stats.get(price_tf, {}).get(symbol, {}).get("price_delta", 0.0)

                                            tf_name = VOL_TF_NAMES.get(price_tf, price_tf)
                                            
                                            # Для текста красиво посчитаем еще и процент, раз ты хотел его вывести:
                                            # Избегаем деления на ноль, если вдруг в базе кривая базовая цена
                                            base_p = curr_price - curr_delta if curr_delta else curr_price
                                            calc_pct = (curr_delta / base_p) * 100 if base_p else 0.0 # или откуда ты берешь точку отсчета
                                            
                                            sign = "+" if curr_delta >= 0 else ""
                                            
                                            if direction == "UP" and curr_delta >= target_price:
                                                triggered = True
                                                reason_text = (
                                                    f"📈 <b>Цена выросла!</b>\n"
                                                    f"Текущая цена: <code>{curr_price} $</code>\n"
                                                    f"Изменение за {tf_name}: <code>{sign}{curr_delta:,.2f} $</code> ({sign}{calc_pct:.1f}%)\n"
                                                    f"(🎯 Твоя цель: {target_price} $ за {tf_name})"
                                                )
                                            elif direction == "DOWN" and curr_delta <= target_price:
                                                triggered = True
                                                reason_text = (
                                                    f"📉 <b>Цена упала!</b>\n"
                                                    f"Текущая цена: <code>{curr_price} $</code>\n"
                                                    f"Изменение за {tf_name}: <code>{curr_delta:,.2f} $</code> ({calc_pct:.1f}%)\n"
                                                    f"(🎯 Твоя цель: {target_price} $ за {tf_name})"
                                                )
                                            
                                elif alert["vol_check"]:
                                    if vol_tf == "1d":
                                        curr_vol = stats.get(symbol, {}).get("quote_volume", 0)
                                    else:
                                        curr_vol = extra_stats.get(vol_tf, {}).get(symbol, {}).get("quote_volume", 0)
                                    target_vol = alert["vol_target"]
                                    tf_name = VOL_TF_NAMES.get(vol_tf, "24 часа")
                                    if curr_vol > 0:
                                        if alert["vol_dir"] == "UP" and curr_vol >= target_vol:
                                            triggered = True
                                            reason_text = f"📊 Объем за {tf_name} превысил <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                                        elif alert["vol_dir"] == "DOWN" and curr_vol <= target_vol:
                                            triggered = True
                                            reason_text = f"📉 Объем за {tf_name} упал ниже <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                            
                            else:
                                curr_price = prices.get(symbol)
                                rate_unit = alert["price_rate_unit"]    # "money" или "percent"
                                price_tf = alert["price_tf"]          # "1h", "4h", "1d", "7d" или None
                                target_price = alert["price_target"]  # Целевое число из базы
                                direction = alert["price_dir"]
                                    
                                if vol_tf == "1d":
                                    curr_vol = stats.get(symbol, {}).get("quote_volume", 0)
                                else:
                                    curr_vol = extra_stats.get(vol_tf, {}).get(symbol, {}).get("quote_volume", 0)
                                        
                                target_vol = alert["vol_target"]
                                tf_name = VOL_TF_NAMES.get(vol_tf, "24 часа")
                                
                                bool_price = False
                                bool_vol = False
                                price_text = ""
                                vol_text = ""
                                
                                if curr_price is not None:
                                    
                                    if rate_unit is None:
                                        if direction == "UP" and curr_price >= target_price:
                                            bool_price = True
                                            price_text = f"📈 Цена выросла до <code>{curr_price} $</code> (Цель: {target_price} $)"
                                        elif direction == "DOWN" and curr_price <= target_price:
                                            bool_price = True
                                            price_text = f"📉 Цена упала до <code>{curr_price} $</code> (Цель: {target_price} $)"
                                    
                                    elif rate_unit == "percent":
                                        if price_tf == "1d":
                                            curr_percent = stats.get(symbol, {}).get("price_change_percent", 0.0)
                                        else:
                                            curr_percent = extra_stats.get(price_tf, {}).get(symbol, {}).get("price_change_percent", 0.0)
                                        tf_name = VOL_TF_NAMES.get(price_tf, price_tf)

                                        if direction == "UP" and curr_percent >= target_price:
                                            bool_price = True
                                            price_text = (
                                                f"📈 <b>Цена выросла!</b>\n"
                                                f"Текущая цена: <code>{curr_price} $</code>\n"
                                                f"Изменение за {tf_name}: <code>+{curr_percent:.2f}%</code>\n"
                                                f"(🎯 Твоя цель: {target_price}% за {tf_name})"
                                            )
                                        elif direction == "DOWN" and curr_percent <= target_price:
                                            bool_price = True
                                            price_text = (
                                                f"📉 <b>Цена упала!</b>\n"
                                                f"Текущая цена: <code>{curr_price} $</code>\n"
                                                f"Изменение за {tf_name}: <code>{curr_percent:.2f}%</code>\n"
                                                f"(🎯 Твоя цель: {target_price}% за {tf_name})"
                                            )
                                        
                                    else:
                                     
                                        if price_tf == "1d":
                                            curr_delta = stats.get(symbol, {}).get("price_delta", 0.0)
                                        else:
                                            curr_delta = extra_stats.get(price_tf, {}).get(symbol, {}).get("price_delta", 0.0)

                                        tf_name = VOL_TF_NAMES.get(price_tf, price_tf)
                                     
                                        base_p = curr_price - curr_delta if curr_delta else curr_price
                                        calc_pct = (curr_delta / base_p) * 100 if base_p else 0.0
                                     
                                        sign = "+" if curr_delta >= 0 else ""
                                        
                                        if direction == "UP" and curr_delta >= target_price:
                                            bool_price = True
                                            price_text = (
                                                f"📈 <b>Цена выросла!</b>\n"
                                                f"Текущая цена: <code>{curr_price} $</code>\n"
                                                f"Изменение за {tf_name}: <code>{sign}{curr_delta:,.2f} $</code> ({sign}{calc_pct:.1f}%)\n"
                                                f"(🎯 Твоя цель: {target_price} $ за {tf_name})"
                                            )
                                        elif direction == "DOWN" and curr_delta <= target_price:
                                            bool_price = True
                                            price_text = (
                                                f"📉 <b>Цена упала!</b>\n"
                                                f"Текущая цена: <code>{curr_price} $</code>\n"
                                                f"Изменение за {tf_name}: <code>{curr_delta:,.2f} $</code> ({calc_pct:.1f}%)\n"
                                                f"(🎯 Твоя цель: {target_price} $ за {tf_name})"
                                            )
                                            
                                if curr_vol > 0:
                                    if alert["vol_dir"] == "UP" and curr_vol >= target_vol:
                                        bool_vol = True
                                        vol_text = f"📊 Объем за {tf_name} превысил <code>{curr_vol / 1_000_000:.2f} млн $</code>!"
                                    elif alert["vol_dir"] == "DOWN" and curr_vol <= target_vol:
                                        bool_vol = True
                                        vol_text = f"📉 Объем за {tf_name} упал ниже <code>{curr_vol / 1_000_000:.2f} млн $</code>!"

                                if alert["operator"] == "AND":
                                    if bool_price and bool_vol:
                                        triggered = True
                                        reason_text = f"{price_text} && {vol_text}"
                                if alert["operator"] == "OR":
                                    if bool_price or bool_vol:
                                        triggered = True
                                        reason_text = "\n".join(filter(None, [price_text, vol_text]))
                                        
                            if triggered:
                                message_text = (
                                    f"🚨 <b>СРАБОТАЛ АЛЕРТ!</b> 🚨\n\n"
                                    f"🪙 Монета: <b>{symbol}</b>\n"
                                    f"{reason_text}"
                                )
                                try:
                                    for _ in range(2):
                                        await bot.send_message(chat_id=user_id, text=message_text)
                                    await db.execute("DELETE FROM smart_alerts WHERE id = ?", (alert_id,))
                                except Exception as e:
                                    logger.error(f"Не удалось отправить алерт юзеру {user_id}: {e}")
                                        
                    await db.commit()
            except Exception as e:
                logger.error(f"Ошибка в цикле проверки алертов: {e}")
            
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Фоновый воркер алертов остановлен")
