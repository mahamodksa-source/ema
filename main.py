import os
import time
import math
import requests
import numpy as np
import pandas as pd

# ===== إعدادات قابلة للتعديل =====
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")               # مثال: BTCUSDT
CATEGORY = os.getenv("BYBIT_CATEGORY", "linear")      # linear لعقود USDT
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "60"))   # 60 = فريم ساعة
EMA_FAST = int(os.getenv("EMA_FAST", "9"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "21"))
ORDERBOOK_DEPTH = int(os.getenv("ORDERBOOK_DEPTH", "20"))
DELTA_ABS_MIN = float(os.getenv("DELTA_ABS_MIN", "0"))   # حد أدنى مطلق للدلتا (اختياري)
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))      # كل كم ثانية نتحقق
ONCE = os.getenv("ONCE", "0") == "1"                      # لو 1 ينفّذ فحصًا واحدًا ويخرج

# تيليجرام
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BYBIT_BASE = "https://api.bybit.com"   # v5

def tg_send(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram not configured. MESSAGE:", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

def bybit_get_klines(limit=200):
    """
    يجلب شموع الفريم المحدد (ساعة) لعقود USDT (category=linear).
    نستخدم آخر شمعتين مغلقتين للتحقق من التقاطع.
    """
    params = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "interval": str(INTERVAL_MIN),
        "limit": str(limit)
    }
    r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=params, timeout=15)
    r.raise_for_status()
    data = r.json().get("result", {}).get("list", [])
    # Bybit تُعيد أحدث شمعة أولًا؛ نقلب الترتيب ليصير تصاعدي
    data = list(reversed(data))
    # الأعمدة: [start, open, high, low, close, volume, turnover]
    df = pd.DataFrame(data, columns=["start","open","high","low","close","volume","turnover"])
    # نحوّل للأرقام
    df["start"] = pd.to_datetime(df["start"].astype(np.int64), unit="ms", utc=True)
    for col in ["open","high","low","close","volume","turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def bybit_orderbook():
    """
    يجلب دفتر الأوامر (Top 50) ثم نجمع أول ORDERBOOK_DEPTH مستويات.
    الدلتا = (مجموع العروض) - (مجموع الطلبات).
    موجب كبير -> ضغط بيع / سالب كبير -> ضغط شراء.
    """
    params = {"category": CATEGORY, "symbol": SYMBOL, "limit": "50"}
    r = requests.get(f"{BYBIT_BASE}/v5/market/orderbook", params=params, timeout=15)
    r.raise_for_status()
    res = r.json().get("result", {})
    bids = res.get("b", [])  # [price, size]
    asks = res.get("a", [])
    top_bids = bids[:ORDERBOOK_DEPTH]
    top_asks = asks[:ORDERBOOK_DEPTH]
    bid_qty = sum(float(x[1]) for x in top_bids)
    ask_qty = sum(float(x[1]) for x in top_asks)
    delta = ask_qty - bid_qty
    return delta, bid_qty, ask_qty

def detect_cross(df: pd.DataFrame):
    """
    نستخدم آخر شمعتين مغلقتين:
    - تقاطع صعودي: EMA9 كان تحت EMA21 ثم أصبح فوق.
    - تقاطع هبوطي: العكس.
    نعيد ('bull' | 'bear' | None) بالإضافة إلى قيم EMAs.
    """
    closes = df["close"]
    df["ema_fast"] = ema(closes, EMA_FAST)
    df["ema_slow"] = ema(closes, EMA_SLOW)
    if len(df) < 3:
        return None, None, None, None, None

    # نأخذ الشمعتين قبل الأخيرة والأخيرة (المغلقتين)
    e_fast_prev = float(df["ema_fast"].iloc[-2])
    e_slow_prev = float(df["ema_slow"].iloc[-2])
    e_fast_now = float(df["ema_fast"].iloc[-1])
    e_slow_now = float(df["ema_slow"].iloc[-1])

    crossed_up = e_fast_prev <= e_slow_prev and e_fast_now > e_slow_now
    crossed_dn = e_fast_prev >= e_slow_prev and e_fast_now < e_slow_now

    if crossed_up:
        return "bull", e_fast_now, e_slow_now, e_fast_prev, e_slow_prev
    if crossed_dn:
        return "bear", e_fast_now, e_slow_now, e_fast_prev, e_slow_prev
    return None, e_fast_now, e_slow_now, e_fast_prev, e_slow_prev

def format_msg(direction, delta, bid_qty, ask_qty, e_fast_now, e_slow_now):
    arrow = "📈" if direction == "bull" else "📉"
    delta_txt = f"{delta:.4f}"
    sign = "إيجابي" if delta > 0 else ("سلبي" if delta < 0 else "محايد")
    return (
        f"{arrow} <b>تقاطع EMA {EMA_FAST}/{EMA_SLOW}</b> على {SYMBOL} (فريم {INTERVAL_MIN} دقيقة)\n"
        f"• الاتجاه: {'صعودي' if direction=='bull' else 'هبوطي'}\n"
        f"• EMA{EMA_FAST} الآن: {e_fast_now:.4f}\n"
        f"• EMA{EMA_SLOW} الآن: {e_slow_now:.4f}\n"
        f"• Delta Orderbook (Top {ORDERBOOK_DEPTH}): {delta_txt} ({sign})\n"
        f"• BidQty: {bid_qty:.4f} | AskQty: {ask_qty:.4f}\n"
        f"— Bybit • ساعة"
    )

def should_confirm_with_delta(direction, delta):
    """
    منطق بسيط لتأكيد الإشارة بالدلتا:
    - صعودي: نفضّل delta سالب أو <= حد أدنى مطلق (يعني ضغط طلبات) *ملاحظة: تعريف الدلتا هنا ask-bid*
      إذا أردتها بالعكس عدّل الدالة.
    - هبوطي: نفضّل delta موجب (ضغط عروض).
    - يمكن ضبط حد أدنى مطلق DELTA_ABS_MIN.
    """
    if abs(delta) < DELTA_ABS_MIN:
        return False
    if direction == "bull":
        return delta <= 0
    else:  # bear
        return delta >= 0

def check_once():
    df = bybit_get_klines(limit=max(EMA_SLOW * 3, 200))
    direction, e_fast_now, e_slow_now, _, _ = detect_cross(df)
    if not direction:
        print("لا يوجد تقاطع على آخر شمعة مغلقة.")
        return

    delta, bid_qty, ask_qty = bybit_orderbook()
    if should_confirm_with_delta(direction, delta):
        msg = format_msg(direction, delta, bid_qty, ask_qty, e_fast_now, e_slow_now)
        print(msg)
        tg_send(msg)
    else:
        print("تم رصد تقاطع لكن الدلتا لم تؤكد الإشارة:", direction, delta)

def main():
    if ONCE:
        check_once()
        return

    last_candle_time = None
    while True:
        try:
            df = bybit_get_klines(limit=max(EMA_SLOW * 3, 200))
            # آخر شمعة مغلقة هي آخر عنصر
            candle_time = df["start"].iloc[-1]
            # لا نكرّر الإشعار لنفس الشمعة
            if last_candle_time is None or candle_time != last_candle_time:
                direction, e_fast_now, e_slow_now, _, _ = detect_cross(df)
                if direction:
                    delta, bid_qty, ask_qty = bybit_orderbook()
                    if should_confirm_with_delta(direction, delta):
                        msg = format_msg(direction, delta, bid_qty, ask_qty, e_fast_now, e_slow_now)
                        print(msg)
                        tg_send(msg)
                    else:
                        print("تقاطع بدون تأكيد دلتا:", direction, delta)
                last_candle_time = candle_time
            else:
                print("بانتظار إغلاق شمعة جديدة...")
        except Exception as e:
            print("Error:", e)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
