import os
import time
import requests
import pandas as pd
import ccxt

# =========================
# إعدادات من متغيرات البيئة
# =========================
EXCHANGE = "bybit"  # نحدد البورصة Bybit
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "1m")  # 1m, 5m, 15m
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================
# Telegram
# =========================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

# =========================
# CCXT Exchange (Bybit)
# =========================
exchange = getattr(ccxt, EXCHANGE)({
    "enableRateLimit": True,
})

def get_ohlcv(symbol, timeframe="1m", limit=100):
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
# Check Signals
# =========================
def check_signals():
    df = get_ohlcv(SYMBOL, TIMEFRAME)
    df["EMA9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["EMA21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["Signal"] = (df["EMA9"] > df["EMA21"]).astype(int)
    df["Cross"] = df["Signal"].diff()

    last_cross = df.iloc[-1]["Cross"]
    buy_volume, sell_volume = get_orderbook(SYMBOL)

    if last_cross == 1 and buy_volume > sell_volume:
        send_telegram(f"🚀 تقاطع EMA9 فوق EMA21 على {SYMBOL}\n📊 المشترين: {buy_volume:.2f} > البائعين: {sell_volume:.2f}")
    elif last_cross == -1 and sell_volume > buy_volume:
        send_telegram(f"📉 تقاطع EMA9 تحت EMA21 على {SYMBOL}\n📊 البائعين: {sell_volume:.2f} > المشترين: {buy_volume:.2f}")

# =========================
# Loop
# =========================
if __name__ == "__main__":
    while True:
        try:
            check_signals()
        except Exception as e:
            send_telegram(f"❌ خطأ: {e}")
        time.sleep(60)  # كل دقيقة
