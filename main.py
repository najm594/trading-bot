import os
import sys
import time
import logging
import requests

# ============ الإعدادات ============
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# العملات الرئيسية (يمكن تغييرها من Render عبر المتغير SYMBOLS)
DEFAULT_SYMBOLS = "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD"
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]

INTERVAL = os.getenv("INTERVAL", "15m")          # 15m أو 30m
INTERVAL_SECONDS = {"15m": 900, "30m": 1800}.get(INTERVAL, 900)
STATUS_REPORT = os.getenv("STATUS_REPORT", "0") == "1"   # تقرير فحص مع كل شمعة

RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RSI_BUY_MAX = 70
RSI_SELL_MIN = 30

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", stream=sys.stdout)
log = logging.getLogger("bot")


# ============ تيليجرام ============
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            log.warning("فشل الإرسال (محاولة %d/3): %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))
    return False


# ============ جلب البيانات ============
def fetch_closes(symbol):
    """يرجع (أسعار الإغلاق، وقت آخر شمعة مغلقة) للشموع المغلقة فقط."""
    params = {"interval": INTERVAL, "range": "5d"}
    url = CHART_URL.format(ticker=f"{symbol}=X")
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res["timestamp"]
            closes = res["indicators"]["quote"][0]["close"]
            pairs = [(t, c) for t, c in zip(ts, closes) if c is not None]
            pairs = pairs[:-1]  # حذف الشمعة الحالية غير المكتملة
            return [c for _, c in pairs], pairs[-1][0]
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"فشل جلب بيانات {symbol}: {last_err}")


# ============ المؤشرات ============
def ema(values, period):
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes, period=RSI_PERIOD):
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def macd(closes):
    fast = ema(closes, MACD_FAST)
    slow = ema(closes, MACD_SLOW)
    offset = MACD_SLOW - MACD_FAST
    macd_line = [f - s for f, s in zip(fast[offset:], slow)]
    sig = ema(macd_line, MACD_SIGNAL)
    m = macd_line[-len(sig):]
    return m[-2], sig[-2], m[-1], sig[-1]


# ============ التحليل ============
def analyze(symbol):
    closes, last_ts = fetch_closes(symbol)
    if len(closes) < MACD_SLOW + MACD_SIGNAL + 20:
        return None

    # السوق مغلق (عطلة نهاية الأسبوع): آخر شمعة قديمة
    if time.time() - last_ts > INTERVAL_SECONDS * 3:
        return {"closed": True}

    r = rsi(closes)
    m0, s0, m1, s1 = macd(closes)
    side = None
    if m0 <= s0 and m1 > s1 and r < RSI_BUY_MAX:
        side = "BUY"
    elif m0 >= s0 and m1 < s1 and r > RSI_SELL_MIN:
        side = "SELL"
    return {"closed": False, "side": side, "price": closes[-1], "rsi": r,
            "macd": m1, "signal": s1, "ts": last_ts}


def fmt_price(symbol, price):
    return f"{price:.3f}" if "JPY" in symbol else f"{price:.5f}"


def seconds_to_next_candle():
    now = time.time()
    return INTERVAL_SECONDS - (now % INTERVAL_SECONDS) + 5


def main():
    if not TOKEN or not CHAT_ID:
        log.error("المتغيران TELEGRAM_TOKEN و TELEGRAM_CHAT_ID مطلوبان.")
        sys.exit(1)

    log.info("Bot is running... %s", SYMBOLS)
    send_telegram_message(
        f"🚀 تم تشغيل بوت التداول\nالعملات: {', '.join(SYMBOLS)}\nالإطار: {INTERVAL}"
    )

    last_signal = {}  # symbol -> (side, candle_ts)
    while True:
        report = []
        for sym in SYMBOLS:
            try:
                res = analyze(sym)
                if res is None:
                    report.append(f"{sym}: بيانات غير كافية")
                    continue
                if res["closed"]:
                    report.append(f"{sym}: السوق مغلق")
                    continue

                side = res["side"]
                report.append(f"{sym}: {fmt_price(sym, res['price'])} | RSI {res['rsi']:.1f} | {side or '-'}")

                if side and last_signal.get(sym) != (side, res["ts"]):
                    icon = "🟢" if side == "BUY" else "🔴"
                    send_telegram_message(
                        f"{icon} إشارة {side}\n"
                        f"{sym} ({INTERVAL})\n"
                        f"السعر: {fmt_price(sym, res['price'])}\n"
                        f"RSI: {res['rsi']:.1f}"
                    )
                    last_signal[sym] = (side, res["ts"])
                    log.info("إشارة %s على %s", side, sym)
            except Exception as e:
                log.exception("خطأ في %s: %s", sym, e)
                report.append(f"{sym}: خطأ")
            time.sleep(0.5)

        log.info("انتهى الفحص: %s", " || ".join(report))
        if STATUS_REPORT:
            send_telegram_message("📊 تقرير الفحص\n" + "\n".join(report))

        time.sleep(seconds_to_next_candle())


if __name__ == "__main__":
    main()
