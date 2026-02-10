import asyncio
import json
import os
import aiohttp
import websockets

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =====================================================
# НАСТРОЙКИ
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE_REST = "https://fapi.binance.com"
BINANCE_WS = "wss://fstream.binance.com/ws"

TOP_LIMIT = 100
SYMBOL_REFRESH_SEC = 1800

bot_enabled = True
min_liq_usd = 20_000

symbols = set()
tasks = {}

# =====================================================
# TELEGRAM UI
# =====================================================

def keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⏸ Выкл" if bot_enabled else "▶️ Вкл",
                callback_data="toggle"
            )
        ],
        [
            InlineKeyboardButton("➖ 5k", callback_data="dec"),
            InlineKeyboardButton("➕ 5k", callback_data="inc"),
        ]
    ])

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"⚙️ *Ликвидации Binance Futures*\n\n"
        f"Статус: *{'ВКЛЮЧЕН' if bot_enabled else 'ВЫКЛЮЧЕН'}*\n"
        f"Мин. сумма: *{min_liq_usd:,}$*",
        parse_mode="Markdown",
        reply_markup=keyboard()
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_enabled, min_liq_usd

    q = update.callback_query
    await q.answer()

    if q.data == "toggle":
        bot_enabled = not bot_enabled
    elif q.data == "inc":
        min_liq_usd += 5000
    elif q.data == "dec":
        min_liq_usd = max(1000, min_liq_usd - 5000)

    await q.edit_message_text(
        f"⚙️ *Ликвидации Binance Futures*\n\n"
        f"Статус: *{'ВКЛЮЧЕН' if bot_enabled else 'ВЫКЛЮЧЕН'}*\n"
        f"Мин. сумма: *{min_liq_usd:,}$*",
        parse_mode="Markdown",
        reply_markup=keyboard()
    )

# =====================================================
# TOP 100 SYMBOLS
# =====================================================

async def fetch_top_100():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BINANCE_REST}/fapi/v1/ticker/24hr") as r:
            data = await r.json()

    if not isinstance(data, list):
        return set()

    pairs = []
    for x in data:
        if not isinstance(x, dict):
            continue
        if "symbol" not in x or "quoteVolume" not in x:
            continue
        if not x["symbol"].endswith("USDT"):
            continue
        if x["symbol"] in ("BTCUSDT", "ETHUSDT"):
            continue
        pairs.append(x)

    pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return {x["symbol"].lower() for x in pairs[:TOP_LIMIT]}

# =====================================================
# FORCE ORDER
# =====================================================

def coinglass_url(symbol: str) -> str:
    base = symbol.replace("USDT", "").upper()
    return f"https://www.coinglass.com/tv/{base}"

async def listen_symbol(app: Application, symbol: str):
    url = f"{BINANCE_WS}/{symbol}@forceOrder"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print(f"[WS] connected {symbol}")

                async for msg in ws:
                    if not bot_enabled:
                        continue

                    data = json.loads(msg)
                    o = data.get("o")
                    if not o:
                        continue

                    price = float(o["p"])
                    qty = float(o["q"])
                    usd = price * qty

                    if usd < min_liq_usd:
                        continue

                    side = o["S"]
                    direction = "Long" if side == "SELL" else "Short"
                    emoji = "🟢" if direction == "Long" else "🔴"

                    sym = o["s"].replace("USDT", "")
                    link = coinglass_url(o["s"])

                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            f"Binance {emoji} "
                            f"<a href=\"{link}\">#{sym}</a> "
                            f"rekt {direction}: "
                            f"${usd:,.0f}"
                        ),
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )

        except Exception as e:
            print(f"[ERROR] {symbol}", e)
            await asyncio.sleep(5)

# =====================================================
# SYMBOL MANAGER
# =====================================================

async def symbol_manager(app: Application):
    global symbols, tasks

    while True:
        new_symbols = await fetch_top_100()

        for s in new_symbols - symbols:
            tasks[s] = asyncio.create_task(listen_symbol(app, s))

        for s in symbols - new_symbols:
            tasks[s].cancel()
            del tasks[s]

        symbols = new_symbols
        print(f"[INFO] active symbols: {len(symbols)}")

        await asyncio.sleep(SYMBOL_REFRESH_SEC)

# =====================================================
# MAIN
# =====================================================

async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(buttons))

    await app.initialize()
    await app.start()
    await app.start_polling()

    asyncio.create_task(symbol_manager(app))

    print("[INFO] Bot fully started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
