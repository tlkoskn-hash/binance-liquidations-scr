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

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE_REST = "https://fapi.binance.com"
BINANCE_WS = "wss://fstream.binance.com/ws"

TOP_LIMIT = 100
SYMBOL_REFRESH_SEC = 1800

# --- динамические настройки ---
bot_enabled = True
min_liq_usd = 20_000

symbols = set()
tasks = {}

# ================== TELEGRAM UI ==================

def settings_keyboard():
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
    text = (
        "⚙️ *Настройки ликвидаций Binance*\n\n"
        f"Статус: *{'ВКЛЮЧЕН' if bot_enabled else 'ВЫКЛЮЧЕН'}*\n"
        f"Мин. сумма: *{min_liq_usd:,}$*"
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=settings_keyboard()
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

    text = (
        "⚙️ *Настройки ликвидаций Binance*\n\n"
        f"Статус: *{'ВКЛЮЧЕН' if bot_enabled else 'ВЫКЛЮЧЕН'}*\n"
        f"Мин. сумма: *{min_liq_usd:,}$*"
    )

    await q.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=settings_keyboard()
    )

# ================== ТОП 100 АЛЬТОВ ==================

async def fetch_top_100():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BINANCE_REST}/fapi/v1/ticker/24hr") as r:
            data = await r.json()

    pairs = [
        x for x in data
        if x["symbol"].endswith("USDT")
        and x["symbol"] not in ("BTCUSDT", "ETHUSDT")
    ]

    pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return {x["symbol"].lower() for x in pairs[:TOP_LIMIT]}

# ================== FORCE ORDER ==================

def coinglass_url(symbol: str) -> str:
    base = symbol.replace("USDT", "").upper()
    return f"https://www.coinglass.com/tv/{base}"

async def listen_symbol(app: Application, symbol: str):
    stream = f"{symbol}@forceOrder"
    url = f"{BINANCE_WS}/{stream}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
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

                    text = (
                        f"Binance {emoji} "
                        f"<a href=\"{link}\">#{sym}</a> "
                        f"rekt {direction}: "
                        f"${usd:,.0f}"
                    )

                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=text,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )

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
    app.add_handler(CallbackQueryHandler(buttons))

    app.run_polling()

if __name__ == "__main__":
    main()
