import os
import sys
import time
import logging
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

# ============ الإعدادات ============
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# العملات الرئيسية (يمكن تغييرها من Render عبر المتغير SYMBOLS)
DEFAULT_SYMBOLS = "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,EURGBP,EURJPY,GBPJPY"
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]

INTERVAL = os.getenv("INTERVAL", "15m")          # 15m أو 30m
INTERVAL_SECONDS = {"15m": 900, "30m": 1800}.get(INTERVAL, 900)
SL_ATR = float(os.getenv("SL_ATR", "1.5"))   # وقف الخسارة = 1.5 × ATR
TP_ATR = float(os.getenv("TP_ATR", "3.0"))   # الهدف = 3 × ATR (نسبة 1:2)
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
            q = res["indicators"]["quote"][0]
            rows = [(t, c, h, l) for t, c, h, l in zip(ts, q["close"], q["high"], q["low"])
                    if c is not None and h is not None and l is not None]
            rows = rows[:-1]  # حذف الشمعة الحالية غير المكتملة
            return ([x[1] for x in rows], rows[-1][0],
                    [x[2] for x in rows], [x[3] for x in rows])
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


def atr(highs, lows, closes, period=14):
    """متوسط المدى الحقيقي (Wilder) لقياس تقلب السوق."""
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    a = sum(trs[:period]) / period
    for t in trs[period:]:
        a = (a * (period - 1) + t) / period
    return a


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
    closes, last_ts, highs, lows = fetch_closes(symbol)
    if len(closes) < MACD_SLOW + MACD_SIGNAL + 20:
        return None

    # السوق مغلق (عطلة نهاية الأسبوع): آخر شمعة قديمة
    if time.time() - last_ts > INTERVAL_SECONDS * 3:
        return {"closed": True}

    r = rsi(closes)
    a = atr(highs, lows, closes)
    m0, s0, m1, s1 = macd(closes)
    side = None
    if m0 <= s0 and m1 > s1 and r < RSI_BUY_MAX:
        side = "BUY"
    elif m0 >= s0 and m1 < s1 and r > RSI_SELL_MIN:
        side = "SELL"
    return {"closed": False, "side": side, "price": closes[-1], "rsi": r,
            "macd": m1, "signal": s1, "ts": last_ts, "atr": a}


NAMES = {
    "EURUSD": "يورو/دولار", "GBPUSD": "جنيه إسترليني/دولار", "USDJPY": "دولار/ين ياباني",
    "USDCHF": "دولار/فرنك سويسري", "AUDUSD": "دولار أسترالي/دولار",
    "USDCAD": "دولار/دولار كندي", "NZDUSD": "دولار نيوزيلندي/دولار",
    "EURGBP": "يورو/جنيه إسترليني", "EURJPY": "يورو/ين ياباني", "GBPJPY": "جنيه إسترليني/ين ياباني",
}
SIDE_AR = {"BUY": "شراء", "SELL": "بيع"}
INTERVAL_AR = {"15m": "15 دقيقة", "30m": "30 دقيقة"}


def fmt_price(symbol, price):
    return f"{price:.3f}" if "JPY" in symbol else f"{price:.5f}"


def seconds_to_next_candle():
    now = time.time()
    return INTERVAL_SECONDS - (now % INTERVAL_SECONDS) + 5


# ============ خادم صحة (يطلبه Render للخدمات المجانية) ============
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("خادم الصحة يعمل على المنفذ %d", port)


def keep_alive():
    """يزور رابط الخدمة كل 10 دقائق حتى لا تنام."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return

    def loop():
        while True:
            time.sleep(600)
            try:
                requests.get(url, timeout=15)
            except Exception as e:
                log.warning("keep-alive فشل: %s", e)

    threading.Thread(target=loop, daemon=True).start()


def main():
    if not TOKEN or not CHAT_ID:
        log.error("المتغيران TELEGRAM_TOKEN و TELEGRAM_CHAT_ID مطلوبان.")
        sys.exit(1)

    start_health_server()
    keep_alive()
    log.info("Bot is running... %s", SYMBOLS)
    send_telegram_message(
        f"🚀 تم تشغيل بوت التداول\nالعملات ({len(SYMBOLS)}): {', '.join(SYMBOLS)}\nالإطار: {INTERVAL_AR.get(INTERVAL, INTERVAL)}"
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
                report.append(f"{sym}: {fmt_price(sym, res['price'])} | RSI {res['rsi']:.1f} | {SIDE_AR.get(side, '-')}")

                if side and last_signal.get(sym) != (side, res["ts"]):
                    icon = "🟢" if side == "BUY" else "🔴"
                    p, a = res["price"], res["atr"]
                    sign = 1 if side == "BUY" else -1
                    sl = p - sign * SL_ATR * a
                    tp = p + sign * TP_ATR * a
                    ratio = TP_ATR / SL_ATR
                    send_telegram_message(
                        f"{icon} إشارة {SIDE_AR[side]}\n"
                        f"{sym} - {NAMES.get(sym, sym)}\n"
                        f"الإطار: {INTERVAL_AR.get(INTERVAL, INTERVAL)}\n"
                        f"الدخول: {fmt_price(sym, p)}\n"
                        f"وقف الخسارة: {fmt_price(sym, sl)}\n"
                        f"الهدف: {fmt_price(sym, tp)}\n"
                        f"نسبة المخاطرة إلى العائد: 1:{ratio:g}\n"
                        f"مؤشر RSI: {res['rsi']:.1f}\n"
                        f"⏱ الإشارة صالحة لشمعة واحدة فقط"
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
