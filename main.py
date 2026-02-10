import asyncio
import json
import os
import aiohttp
import websockets

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MIN_LIQ_USD = 20_000
TOP_LIMIT = 100
SYMBOL_REFRESH_SEC = 1800

BINANCE_REST = "https://fapi.binance.com"
BINANCE_WS = "wss://fstream.binance.com/ws"

symbols = set()
tasks = {}

# ================== TELEGRAM ==================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот запущен и слушает ликвидации Binance Futures\n"
        "Формат: Binance 🟢/🔴 #SYMBOL rekt Long/Short $"
    )

# ================== ТОП 100 АЛЬТОВ ==================

async def fetch_top_100():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BINANCE_REST}/fapi/v1/ticker/24hr") as r:
            data = await r.json()

    usdt_pairs = [
        x for x in data
        if x["symbol"].endswith("USDT")
        and x["symbol"] not in ("BTCUSDT", "ETHUSDT")
    ]

    usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return {x["symbol"].lower() for x in usdt_pairs[:TOP_LIMIT]}

# ================== FORCE ORDER ==================

async def listen_symbol(app: Application, symbol: str):
    stream = f"{symbol}@forceOrder"
    url = f"{BINANCE_WS}/{stream}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print(f"[WS] {symbol} connected")

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

                    side = o["S"]
                    direction = "Long" if side == "SELL" else "Short"
                    emoji = "🟢" if direction == "Long" else "🔴"

                    sym = o["s"].replace("USDT", "")
                    text = (
                        f"Binance {emoji} "
                        f"#{sym} rekt {direction}: "
                        f"${usd:,.0f}"
                    )

                    await app.bot.send_message(chat_id=CHAT_ID, text=text)

        except Exception as e:
            print(f"[ERROR] {symbol}", e)
            await asyncio.sleep(3)

# ================== SYMBOL MANAGER ==================

async def symbol_manager(app: Application):
    global symbols, tasks

    while True:
        new_symbols = await fetch_top_100()

        for sym in new_symbols - symbols:
            tasks[sym] = asyncio.create_task(listen_symbol(app, sym))

        for sym in symbols - new_symbols:
            tasks[sym].cancel()
            del tasks[sym]

        symbols = new_symbols
        print(f"[INFO] active symbols: {len(symbols)}")

        await asyncio.sleep(SYMBOL_REFRESH_SEC)

# ================== LIFECYCLE ==================

async def post_init(app: Application):
    asyncio.create_task(symbol_manager(app))

# ================== MAIN ==================

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.run_polling()

if __name__ == "__main__":
    main()
