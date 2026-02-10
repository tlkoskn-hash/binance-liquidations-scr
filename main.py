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

# ================= НАСТРОЙКИ =================

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

# ================= TELEGRAM UI =================

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

# ================= TOP 100 =================

async def fetch_top_100():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BINANCE_REST}/fapi/v1/ticker/24hr") as r:
            data = await r.json()

    if not isinstance(data, list):
        return set()

    pairs = [
        x for x in data
        if x.get("symbol", "").endswith("USDT")
        and x["symbol"] not in ("BTCUSDT", "ETHUSDT")
    ]

    pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return {x["symbol"].lower() for x in pairs[:TOP_LIMIT]}

# ================= FORCE ORDER =================

def coinglass_url(symbol: str):
    return f"https://www.coinglass.com/tv/Binance_{symbol.replace('USDT','').upper()}"

async def listen_symbol(app: Application, symbol: str):
    url = f"{BINANCE_WS}/{symbol}@forceOrder"
    ws = None

    try:
        while True:
            try:
                ws = await websockets.connect(url, ping_interval=20)

                async for msg in ws:
                    if not bot_enabled:
                        continue

                    o = json.loads(msg).get("o")
                    if not o:
                        continue

                    usd = float(o["p"]) * float(o["q"])
                    if usd < min_liq_usd:
                        continue

                    direction = "Long" if o["S"] == "SELL" else "Short"
                    emoji = "🟢" if direction == "Long" else "🔴"
                    sym = o["s"].replace("USDT", "")

                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            f"Binance {emoji} "
                            f"<a href=\"{coinglass_url(o['s'])}\">#{sym}</a> "
                            f"rekt {direction}: ${usd:,.0f}"
                        ),
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS ERROR] {symbol}: {e}")
                await asyncio.sleep(5)

    finally:
        if ws and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        print(f"[WS] {symbol} stopped cleanly")

# ================= SYMBOL MANAGER =================

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

# ================= POST INIT =================

async def post_init(app: Application):
    asyncio.create_task(symbol_manager(app))

# ================= MAIN =================

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(buttons))

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
