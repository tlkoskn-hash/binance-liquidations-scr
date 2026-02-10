import asyncio
import json
import datetime
import requests
import websockets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from config import BOT_TOKEN, CHAT_ID, EXCLUDED_SYMBOLS

# ─── BINANCE ─────────────────────────────────────────────────────
BINANCE_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"
SYMBOLS_UPDATE_INTERVAL = 3600  # 1 час

# ─── НАСТРОЙКИ (МЕНЯЮТСЯ КНОПКАМИ) ────────────────────────────────
MIN_LIQUIDATION_USD = 1000
BOT_ENABLED = True
# ─────────────────────────────────────────────────────────────────

symbols = set()
daily_counter = {}
current_date = datetime.date.today()

background_tasks = []


# ─── BINANCE SYMBOLS ─────────────────────────────────────────────
def get_top_100_symbols():
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    data = requests.get(url, timeout=10).json()

    filtered = [
        x for x in data
        if x.get("symbol", "").endswith("USDT")
        and x["symbol"] not in EXCLUDED_SYMBOLS
    ]

    filtered.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return set(x["symbol"] for x in filtered[:100])


async def update_symbols_loop():
    try:
        while True:
            try:
                symbols.clear()
                symbols.update(get_top_100_symbols())
                print(f"[INFO] Symbols updated: {len(symbols)}")
            except Exception as e:
                print("[ERROR] Symbols update failed:", e)

            await asyncio.sleep(SYMBOLS_UPDATE_INTERVAL)
    except asyncio.CancelledError:
        print("[INFO] update_symbols_loop cancelled")


# ─── TELEGRAM UI ─────────────────────────────────────────────────
def start_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖ 10k", callback_data="dec"),
            InlineKeyboardButton("➕ 10k", callback_data="inc"),
        ],
        [
            InlineKeyboardButton(
                "⏸ Выключить" if BOT_ENABLED else "▶️ Включить",
                callback_data="toggle"
            )
        ]
    ])


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚙️ Управление ботом\n\n"
        f"Мин. ликвидация: {MIN_LIQUIDATION_USD}$\n"
        f"Статус: {'ВКЛЮЧЕН' if BOT_ENABLED else 'ВЫКЛЮЧЕН'}"
    )
    await update.message.reply_text(text, reply_markup=start_keyboard())


async def start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MIN_LIQUIDATION_USD, BOT_ENABLED

    query = update.callback_query
    await query.answer()

    if query.data == "inc":
        MIN_LIQUIDATION_USD += 10000
    elif query.data == "dec":
        MIN_LIQUIDATION_USD = max(10000, MIN_LIQUIDATION_USD - 10000)
    elif query.data == "toggle":
        BOT_ENABLED = not BOT_ENABLED

    text = (
        "⚙️ Управление ботом\n\n"
        f"Мин. ликвидация: {MIN_LIQUIDATION_USD}$\n"
        f"Статус: {'ВКЛЮЧЕН' if BOT_ENABLED else 'ВЫКЛЮЧЕН'}"
    )

    await query.edit_message_text(text, reply_markup=start_keyboard())


# ─── SIGNALS ─────────────────────────────────────────────────────
async def send_signal(symbol, side, volume, bot):
    global daily_counter, current_date

    today = datetime.date.today()
    if today != current_date:
        daily_counter = {}
        current_date = today

    daily_counter[symbol] = daily_counter.get(symbol, 0) + 1

    emoji = "🔴" if side == "BUY" else "🟢"
    msg = f"{emoji} {symbol} {volume:,.0f}$ 🔔{daily_counter[symbol]}"

    await bot.send_message(chat_id=CHAT_ID, text=msg)


async def listen_liquidations(app: Application):
    try:
        while True:
            try:
                async with websockets.connect(BINANCE_WS) as ws:
                    async for msg in ws:
                        if not BOT_ENABLED:
                            continue

                        data = json.loads(msg)
                        if not isinstance(data, list):
                            continue

                        for event in data:
                            if not isinstance(event, dict):
                                continue

                            o = event.get("o")
                            if not isinstance(o, dict):
                                continue

                            symbol = o.get("s")
                            if symbol not in symbols:
                                continue

                            try:
                                price = float(o.get("p", 0))
                                qty = float(o.get("q", 0))
                            except (TypeError, ValueError):
                                continue

                            volume = price * qty
                            if volume < MIN_LIQUIDATION_USD:
                                continue

                            await send_signal(symbol, o.get("S"), volume, app.bot)

            except Exception as e:
                print("[ERROR] WebSocket:", e)
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        print("[INFO] listen_liquidations cancelled")


# ─── LIFECYCLE ───────────────────────────────────────────────────
async def post_init(app: Application):
    background_tasks.append(asyncio.create_task(update_symbols_loop()))
    background_tasks.append(asyncio.create_task(listen_liquidations(app)))


async def post_shutdown(app: Application):
    for task in background_tasks:
        task.cancel()

    await asyncio.gather(*background_tasks, return_exceptions=True)
    print("[INFO] Background tasks stopped")


# ─── ENTRY POINT ─────────────────────────────────────────────────
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(start_callback))

    app.run_polling()


if __name__ == "__main__":
    main()

