import asyncio
import json
import websockets

BINANCE_WS = "wss://fstream.binance.com/stream"


async def listen_force_orders():
    while True:
        try:
            async with websockets.connect(
                BINANCE_WS,
                ping_interval=20,
                ping_timeout=20
            ) as ws:

                print("[INFO] WS connected")

                # подписка
                await ws.send(json.dumps({
                    "method": "SUBSCRIBE",
                    "params": ["!forceOrder@arr"],
                    "id": 1
                }))

                print("[INFO] forceOrder subscribed")

                async for msg in ws:
                    data = json.loads(msg)

                    # служебные ответы Binance
                    if isinstance(data, dict) and "result" in data:
                        continue

                    # stream API
                    payload = data.get("data")

                    if not isinstance(payload, list):
                        continue

                    # 🔴 ВАЖНО: ЛОГИРУЕМ СЫРОЕ СОБЫТИЕ
                    for event in payload:
                        o = event.get("o")
                        if not isinstance(o, dict):
                            continue

                        symbol = o.get("s")
                        side = o.get("S")
                        price = o.get("p")
                        qty = o.get("q")

                        try:
                            volume = float(price) * float(qty)
                        except Exception:
                            volume = "?"

                        print(
                            "[RAW FORCEORDER]",
                            symbol,
                            side,
                            "price:", price,
                            "qty:", qty,
                            "usd:", volume
                        )

        except Exception as e:
            print("[ERROR] WS error:", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(listen_force_orders())
