import asyncio
import json
import os
import aiohttp
import websockets

from telegram import (
    Update,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE_REST = "https://fapi.binance.com"
BINANCE_WS = "wss://fstream.binance.com/ws"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"

TOP_LIMIT = 200
SYMBOL_REFRESH_SEC = 1800
MARKETCAP_REFRESH_SEC = 7 * 24 * 60 * 60

min_liq_usd = 20_000
marketcap_filter = 20  # 0 = без фильтра

symbols = set()
tasks = {}

top50_marketcap = []
dynamic_blacklist = set()
recent_events = set()

# ================= TELEGRAM UI =================
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📊 Статус", "⚙️ Настройки"],
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def settings_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["-5k", "+5k"],
            ["Все", "-20 кап", "-50 кап"],
            ["🔙 Назад"],
        ],
        resize_keyboard=True,
        is_persistent=True
    )



def status_text():
    if marketcap_filter == 0:
        cap_text = "Все пары"
    else:
        cap_text = f"без топ {marketcap_filter}"

    return (
        f"⚙️ Ликвидации Binance Futures\n\n"
        f"Мин. сумма: {min_liq_usd:,}$\n"
        f"Фильтр капитализации: {cap_text}"
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        status_text(),
        reply_markup=main_keyboard()
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

        top50_marketcap = [
            f"{coin['symbol'].upper()}USDT"
            for coin in data
            if isinstance(coin, dict) and "symbol" in coin
        ]

        print("[INFO] Top 50 marketcap updated")

    except Exception as e:
        print("[MARKETCAP ERROR]", e)


async def rebuild_blacklist():
    global dynamic_blacklist

    if marketcap_filter == 0:
        dynamic_blacklist = set()
    else:
        dynamic_blacklist = set(top50_marketcap[:marketcap_filter])

    print("\n==============================")
    print(f"MARKETCAP FILTER: {marketcap_filter if marketcap_filter else 'OFF'}")
    print(f"Excluded: {len(dynamic_blacklist)}")
    print("==============================\n")


async def weekly_marketcap_update():
    while True:
        await asyncio.sleep(MARKETCAP_REFRESH_SEC)
        await load_top50_marketcap()
        await rebuild_blacklist()

# ================= TEXT HANDLER =================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global min_liq_usd, marketcap_filter, symbols

    text = update.message.text

    # ===== Главное меню =====

    if text == "📊 Статус":
        await update.message.reply_text(
            status_text(),
            reply_markup=main_keyboard()
        )
        return

    if text == "⚙️ Настройки":
        await update.message.reply_text(
            "⚙️ Меню настроек",
            reply_markup=settings_keyboard()
        )
        return

    if text == "🔙 Назад":
        await update.message.reply_text(
            status_text(),
            reply_markup=main_keyboard()
        )
        return

    # ===== Изменение суммы =====

    if text == "+5k":
        min_liq_usd += 5000
        symbols.clear()

    elif text == "-5k":
        min_liq_usd = max(1000, min_liq_usd - 5000)
        symbols.clear()

    # ===== Фильтр капитализации =====

    elif text == "Все":
        marketcap_filter = 0
        await rebuild_blacklist()
        symbols.clear()

    elif text == "-20 кап":
        marketcap_filter = 20
        await rebuild_blacklist()
        symbols.clear()

    elif text == "-50 кап":
        marketcap_filter = 50
        await rebuild_blacklist()
        symbols.clear()

    else:
        return

    # После изменения остаёмся в меню настроек
    await update.message.reply_text(
        status_text(),
        reply_markup=settings_keyboard()
    )

# ================= TOP 200 =================

async def fetch_top_200():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BINANCE_REST}/fapi/v1/ticker/24hr") as r:

                if r.status != 200:
                    print("BINANCE ERROR STATUS:", r.status)
                    text = await r.text()
                    print(text)
                    return set()

                data = await r.json()

        if not isinstance(data, list):
            print("Unexpected Binance response:", data)
            return set()

        pairs = [
            x for x in data
            if isinstance(x, dict)
            and x.get("symbol", "").endswith("USDT")
            and x["symbol"] not in dynamic_blacklist
        ]

        pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)

        return {x["symbol"].lower() for x in pairs[:TOP_LIMIT]}

    except Exception as e:
        print("FETCH_TOP_200 ERROR:", e)
        return set()

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

                    o = json.loads(msg).get("o")
                    if not o:
                        continue

                    usd = float(o["p"]) * float(o["q"])
                    if usd < min_liq_usd:
                        continue

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
        new_symbols = await fetch_top_200()

        sorted_list = sorted(new_symbols)

        print("\n==============================")
        print("TOP 200 BY 24H VOLUME (USDT)")
        print(f"Total: {len(sorted_list)}")
        for s in sorted_list:
            print(s.upper())
        print("==============================\n")

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
    print("=== STARTING LIQUIDATION BOT ===")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()









