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
from telegram.error import BadRequest


# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE_REST = "https://fapi.binance.com"
BINANCE_WS = "wss://fstream.binance.com/ws"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"

TOP_LIMIT = 100
SYMBOL_REFRESH_SEC = 1800
MARKETCAP_REFRESH_SEC = 7 * 24 * 60 * 60  # 7 дней


bot_enabled = True
min_liq_usd = 20_000
marketcap_filter = 20  # по умолчанию TOP 20


symbols = set()
tasks = {}

top50_marketcap = []
dynamic_blacklist = set()

# 🔥 анти-дубль
recent_events = set()


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
        ],
        [
            InlineKeyboardButton("–20 кап", callback_data="cap20"),
            InlineKeyboardButton("–50 кап", callback_data="cap50"),
        ]
    ])


def status_text():
    return (
        f"⚙️ *Ликвидации Binance Futures*\n\n"
        f"Статус: *{'ВКЛЮЧЕН' if bot_enabled else 'ВЫКЛЮЧЕН'}*\n"
        f"Мин. сумма: *{min_liq_usd:,}$*\n"
        f"Исключаем по капитализации: *топ {marketcap_filter}*"
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        status_text(),
        parse_mode="Markdown",
        reply_markup=keyboard()
    )


# ================= MARKETCAP =================

async def load_top50_marketcap():
    global top50_marketcap

    try:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 50,
            "page": 1,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(COINGECKO_URL, params=params) as r:
                data = await r.json()

        if not isinstance(data, list):
            print("[MARKETCAP ERROR] Unexpected response")
            return

        top50_marketcap = [
            f"{coin['symbol'].upper()}USDT"
            for coin in data
            if isinstance(coin, dict) and "symbol" in coin
        ]

        print("[INFO] Top 50 marketcap updated")

    except Exception as e:
        print("[MARKETCAP LOAD ERROR]", e)


async def rebuild_blacklist():
    global dynamic_blacklist

    dynamic_blacklist = set(top50_marketcap[:marketcap_filter])

    print("\n==============================")
    print(f"MARKETCAP FILTER: TOP {marketcap_filter}")
    print(f"Total: {len(dynamic_blacklist)}")
    for s in sorted(dynamic_blacklist):
        print(s)
    print("==============================\n")


async def weekly_marketcap_update():
    while True:
        await asyncio.sleep(MARKETCAP_REFRESH_SEC)
        await load_top50_marketcap()


# ================= BUTTONS =================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_enabled, min_liq_usd, marketcap_filter, symbols

    q = update.callback_query
    await q.answer()

    if q.data == "toggle":
        bot_enabled = not bot_enabled

    elif q.data == "inc":
        min_liq_usd += 5000

    elif q.data == "dec":
        min_liq_usd = max(1000, min_liq_usd - 5000)

    elif q.data == "cap20":
        marketcap_filter = 20
        await rebuild_blacklist()
        symbols.clear()

    elif q.data == "cap50":
        marketcap_filter = 50
        await rebuild_blacklist()
        symbols.clear()

    try:
        await q.edit_message_text(
            status_text(),
            parse_mode="Markdown",
            reply_markup=keyboard()
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


# ================= TOP 100 ПО ОБЪЕМУ =================

async def fetch_top_100():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BINANCE_REST}/fapi/v1/ticker/24hr") as r:
            data = await r.json()

    if not isinstance(data, list):
        return set()

    pairs = [
        x for x in data
        if x.get("symbol", "").endswith("USDT")
        and x["symbol"] not in dynamic_blacklist
    ]

    pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)

    return {x["symbol"].lower() for x in pairs[:TOP_LIMIT]}


# ================= FORCE ORDER =================

def coinglass_url(symbol: str):
    return f"https://www.coinglass.com/tv/Binance_{symbol.upper()}"


async def listen_symbol(app: Application, symbol: str):
    global recent_events

    url = f"{BINANCE_WS}/{symbol}@forceOrder"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                async for msg in ws:

                    if not bot_enabled:
                        continue

                    o = json.loads(msg).get("o")
                    if not o:
                        continue

                    usd = float(o["p"]) * float(o["q"])
                    if usd < min_liq_usd:
                        continue

                    # 🔥 создаём уникальный ID события
                    event_id = f"{o['s']}_{o['T']}_{usd}"

                    if event_id in recent_events:
                        continue

                    recent_events.add(event_id)

                    if len(recent_events) > 1000:
                        recent_events.clear()

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
        except Exception:
            await asyncio.sleep(5)


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

        await asyncio.sleep(SYMBOL_REFRESH_SEC)


# ================= POST INIT =================

async def post_init(app: Application):
    print("\n==============================")
    print("🚀 BOT STARTED")
    print("==============================\n")

    await load_top50_marketcap()
    await rebuild_blacklist()

    asyncio.create_task(symbol_manager(app))
    asyncio.create_task(weekly_marketcap_update())


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
