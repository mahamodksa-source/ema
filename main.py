import os
import time
import requests
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple

# ================= إعدادات =================
CATEGORY = os.getenv("BYBIT_CATEGORY", "linear")       # "linear" = USDT Perp
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "60"))    # 60 = فريم ساعة
EMA_FAST = int(os.getenv("EMA_FAST", "9"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "21"))
ORDERBOOK_DEPTH = int(os.getenv("ORDERBOOK_DEPTH", "20"))
DELTA_ABS_MIN = float(os.getenv("DELTA_ABS_MIN", "0"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "75"))    # زِدها إذا كثّرت الرموز
ONCE = os.getenv("ONCE", "0") == "1"

# رموز التداول:
# - ALL = يجلب كل رموز USDT-Perp
# - أو قائمة CSV: "BTCUSDT,ETHUSDT"
SYMBOLS_ENV = os.getenv("SYMBOLS", "ALL")
MAX_SYMBOLS = int(os.getenv("MAX_SYMBOLS", "80"))       # حد أمان لمنع الإفراط

# تيليجرام
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BYBIT_BASE = "https://api.bybit.com"   # v5

def tg_send(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram not configured. MESSAGE:", message)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=12
        )
    except Exception as e:
        print("Telegram error:", e)

# -------- Bybit Helpers --------
def get_usdt_perp_symbols() -> List[str]:
    """يجلب كل رموز USDT-Perp (category=linear) القابلة للتداول."""
    out = []
    cursor = None
    while True:
        params = {"category": CATEGORY, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BYBIT_BASE}/v5/market/instruments-info", params=params, timeout=20)
        r.raise_for_status()
        res = r.json().get("result", {})
        rows = res.get("list", []) or []
        for x in rows:
            sym = x.get("symbol", "")
            quote = x.get("quoteCoin", "")
            status = x.get("status", "")
            if quote == "USDT" and status == "Trading":
                out.append(sym)
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
    out = sorted(set(out))
    if MAX_SYMBOLS and len(out) > MAX_SYMBOLS:
        print(f"[INFO] تقليص الرموز إلى أول {MAX_SYMBOLS} لتجنّب الضغط.")
        out = out[:MAX_SYMBOLS]
    return out

def get_symbols_from_env() -> List[str]:
    if SYMBOLS_ENV.strip().upper() == "ALL":
        return get_usdt_perp_symbols()
    return [s.strip().upper() for s in SYMBOLS_ENV.split(",") if s.strip()]

def bybit_get_klines(symbol: str, limit=200) -> pd.DataFrame:
    params = {"category": CATEGORY, "symbol": symbol, "interval": str(INTERVAL_MIN), "limit": str(limit)}
    r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=params, timeout=15)
    r.raise_for_status()
    data = r.json().get("result", {}).get("list", [])
    data = list(reversed(data))  # أحدث أولاً -> نقلبه
    df = pd.DataFrame(data, columns=["start","open","high","low","close","volume","turnover"])
    df["start"] = pd.to_datetime(df["start"].astype(np.int64), unit="ms", utc=True)
    for col in ["open","high","low","close","volume","turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def bybit_orderbook(symbol: str) -> Tuple[float, float, float]:
    params = {"category": CATEGORY, "symbol": symbol, "limit": "50"}
    r = requests.get(f"{BYBIT_BASE}/v5/market/orderbook", params=params, timeout=12)
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
    closes = df["close"]
    df["ema_fast"] = ema(closes, EMA_FAST)
    df["ema_slow"] = ema(closes, EMA_SLOW)
    if len(df) < 3:
        return None, None, None, None, None
    e_fast_prev = float(df["ema_fast"].iloc[-2])
    e_slow_prev = float(df["ema_slow"].iloc[-2])
    e_fast_now  = float(df["ema_fast"].iloc[-1])
    e_slow_now  = float(df["ema_slow"].iloc[-1])
    crossed_up = e_fast_prev <= e_slow_prev and e_fast_now >  e_slow_now
    crossed_dn = e_fast_prev >= e_slow_prev and e_fast_now <  e_slow_now
    if crossed_up:
        return "bull", e_fast_now, e_slow_now, e_fast_prev, e_slow_prev
    if crossed_dn:
        return "bear", e_fast_now, e_slow_now, e_fast_prev, e_slow_prev
    return None, e_fast_now, e_slow_now, e_fast_prev, e_slow_prev

def ok_with_delta(direction: str, delta: float) -> bool:
    if abs(delta) < DELTA_ABS_MIN:
        return False
    if direction == "bull":
        return delta <= 0      # دلتا سالبة = ضغط طلبات
    else:
        return delta >= 0      # دلتا موجبة = ضغط عروض

def format_msg(symbol, direction, delta, bid_qty, ask_qty, e_fast_now, e_slow_now):
    arrow = "📈" if direction == "bull" else "📉"
    sign = "إيجابي" if delta > 0 else ("سلبي" if delta < 0 else "محايد")
    return (
        f"{arrow} <b>{symbol}</b> | تقاطع EMA {EMA_FAST}/{EMA_SLOW} على فريم {INTERVAL_MIN} دقيقة\n"
        f"• الاتجاه: {'صعودي' if direction=='bull' else 'هبوطي'}\n"
        f"• EMA{EMA_FAST}: {e_fast_now:.6f} / EMA{EMA_SLOW}: {e_slow_now:.6f}\n"
        f"• Delta Orderbook Top{ORDERBOOK_DEPTH}: {delta:.4f} ({sign})  — Bid:{bid_qty:.2f} Ask:{ask_qty:.2f}"
    )

# -------- التشغيل --------
def scan_symbol(symbol: str, last_seen: Dict[str, pd.Timestamp]):
    try:
        df = bybit_get_klines(symbol, limit=max(EMA_SLOW * 3, 200))
        candle_time = df["start"].iloc[-1]
        if last_seen.get(symbol) == candle_time:
            return  # لا نكرر نفس الشمعة
        direction, e_fast_now, e_slow_now, _, _ = detect_cross(df)
        if direction:
            delta, bid_qty, ask_qty = bybit_orderbook(symbol)
            if ok_with_delta(direction, delta):
                msg = format_msg(symbol, direction, delta, bid_qty, ask_qty, e_fast_now, e_slow_now)
                print(msg)
                tg_send(msg)
            else:
                print(f"[{symbol}] تقاطع بدون تأكيد دلتا: {direction} Δ={delta:.3f}")
        last_seen[symbol] = candle_time
    except Exception as e:
        print(f"[{symbol}] Error:", e)

def run_once(symbols: List[str]):
    last_seen = {}
    for sym in symbols:
        scan_symbol(sym, last_seen)

def run_forever(symbols: List[str]):
    last_seen: Dict[str, pd.Timestamp] = {}
    while True:
        for i, sym in enumerate(symbols, 1):
            scan_symbol(sym, last_seen)
            time.sleep(0.4)  # راحة قصيرة بين الطلبات (مهم للحدود)
        time.sleep(POLL_SECONDS)

def main():
    symbols = get_symbols_from_env()
    print(f"[INFO] عدد الرموز: {len(symbols)} | مثال: {symbols[:10]}")
    if ONCE:
        run_once(symbols)
    else:
        run_forever(symbols)

if __name__ == "__main__":
    main()
