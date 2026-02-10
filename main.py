import asyncio
import json
import os
import aiohttp
import websockets
from telegram import Bot

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MIN_LIQ_USD = 20_000        # минимум ликвидации
TOP_LIMIT = 100             # сколько альтов
SYMBOL_REFRESH_SEC = 1800   # обновление топа (30 мин)

BINANCE_REST = "https://fapi.binance.com"
BINANCE_WS = "wss://fstream.binance.com/ws"

bot = Bot(token=BOT_TOKEN)

symbols = set()
tasks = {}

# ================== ТОП 100 АЛЬТОВ ==================

async def fetch_top_100():
    url = f"{BINANCE_REST}/fapi/v1/ticker/24hr"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            data = await r.json()

    usdt_pairs = [
        x for x in data
        if x["symbol"].endswith("USDT")
        and x["symbol"] not in ("BTCUSDT", "ETHUSDT")
    ]

    usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)

    top = [x["symbol"].lower() for x in usdt_pairs[:TOP_LIMIT]]
    return set(top)

# ================== FORCE ORDER ==================

async def listen_symbol(symbol: str):
    stream = f"{symbol}@forceOrder"
    url = f"{BINANCE_WS}/{stream}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print(f"[WS] connected {symbol}")

                async for msg in ws:
                    data = json.loads(msg)
                    o = data.get("o")
                    if not o:
                        continue

                    price = float(o["p"])
                    qty = float(o["q"])
                    usd = price * qty

                    if usd < MIN_LIQ_USD:
                        continue

                    side = o["S"]  # BUY / SELL
                    direction = "Long" if side == "SELL" else "Short"
                    emoji = "🟢" if direction == "Long" else "🔴"

                    sym = o["s"].replace("USDT", "")

                    text = (
                        f"Binance {emoji} "
                        f"#{sym} rekt {direction}: "
                        f"${usd:,.0f}"
                    )

                    await bot.send_message(chat_id=CHAT_ID, text=text)
                    print("[LIQ]", text)

        except Exception as e:
            print(f"[ERROR] {symbol}", e)
            await asyncio.sleep(3)

# ================== ОБНОВЛЕНИЕ СПИСКА ==================

async def symbol_manager():
    global symbols, tasks

    while True:
        try:
            new_symbols = await fetch_top_100()

            added = new_symbols - symbols
            removed = symbols - new_symbols

            for sym in added:
                task = asyncio.create_task(listen_symbol(sym))
                tasks[sym] = task
                print(f"[ADD] {sym}")

            for sym in removed:
                tasks[sym].cancel()
                del tasks[sym]
                print(f"[REMOVE] {sym}")

            symbols = new_symbols
            print(f"[INFO] symbols active: {len(symbols)}")

        except Exception as e:
            print("[ERROR] symbol_manager", e)

        await asyncio.sleep(SYMBOL_REFRESH_SEC)

# ================== MAIN ==================

async def main():
    await symbol_manager()

if __name__ == "__main__":
    asyncio.run(main())
