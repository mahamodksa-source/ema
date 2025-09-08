import os
import time
import requests
import pandas as pd
import ccxt

# =========================
# إعدادات من متغيرات البيئة
# =========================
EXCHANGE = "bybit"
TIMEFRAME = os.getenv("TIMEFRAME", "1h")  # فريم الساعة
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================
# Telegram
# =========================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

# =========================
# CCXT Bybit
# =========================
exchange = getattr(ccxt, EXCHANGE)({
    "enableRateLimit": True,
})

def get_ohlcv(symbol, timeframe="1h", limit=100):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
    df["close"] = df["close"].astype(float)
    return df

def get_orderbook(symbol, limit=50):
    ob = exchange.fetch_order_book(symbol, limit=limit)
    bids = ob['bids']
    asks = ob['asks']
    buy_volume = sum([b[1] for b in bids]) if bids else 0
    sell_volume = sum([a[1] for a in asks]) if asks else 0
    return buy_volume, sell_volume

# =========================
# Check Signal per Symbol
# =========================
def check_symbol(symbol):
    df = get_ohlcv(symbol, TIMEFRAME)
    df["EMA9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["EMA21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["Signal"] = (df["EMA9"] > df["EMA21"]).astype(int)
    df["Cross"] = df["Signal"].diff()

    last_cross = df.iloc[-1]["Cross"]
    buy_volume, sell_volume = get_orderbook(symbol)

    if last_cross == 1 and buy_volume > sell_volume:
        send_telegram(f"🚀 تقاطع صعودي EMA9 فوق EMA21 على {symbol}\n📊 المشترين: {buy_volume:.2f} > البائعين: {sell_volume:.2f}")
    elif last_cross == -1 and sell_volume > buy_volume:
        send_telegram(f"📉 تقاطع هبوطي EMA9 تحت EMA21 على {symbol}\n📊 البائعين: {sell_volume:.2f} > المشترين: {buy_volume:.2f}")

# =========================
# Main Loop
# =========================
if __name__ == "__main__":
    while True:
        try:
            markets = exchange.load_markets()
            usdt_pairs = [s for s in markets if s.endswith("/USDT")]

            for symbol in usdt_pairs:
                try:
                    check_symbol(symbol)
                except Exception as e:
                    print(f"خطأ في {symbol}: {e}")

        except Exception as e:
            send_telegram(f"❌ خطأ رئيسي: {e}")

        time.sleep(60 * 60)  # يفحص كل ساعة
