import os
import re
import sys
import math
import time
import logging
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

# ============ الإعدادات ============
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# كل الأزواج الظاهرة في MetaTrader (يمكن تغييرها من Render عبر المتغير SYMBOLS)
DEFAULT_SYMBOLS = (
    "EURUSD,GBPUSD,USDJPY,USDCAD,USDCHF,NZDUSD,AUDUSD,AUDNZD,AUDCAD,AUDCHF,AUDJPY,"
    "CADCHF,CADJPY,CHFJPY,EURAUD,EURCAD,EURCHF,EURGBP,EURJPY,EURNZD,GBPCHF,GBPJPY,NZDJPY"
)
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]

INTERVAL = os.getenv("INTERVAL", "15m")          # 15m أو 30m
INTERVAL_SECONDS = {"15m": 900, "30m": 1800}.get(INTERVAL, 900)
SL_ATR = float(os.getenv("SL_ATR", "1.5"))       # وقف الخسارة = 1.5 × ATR
TP_ATR = float(os.getenv("TP_ATR", "3.0"))       # الهدف = 3 × ATR (نسبة 1:2)
STATUS_REPORT = os.getenv("STATUS_REPORT", "0") == "1"   # تقرير فحص مع كل شمعة
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100000"))  # رصيد الحساب بالدولار
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))                  # نسبة المخاطرة لكل صفقة %
CONTRACT_SIZE = 100000                                          # حجم اللوت القياسي
MAX_TRADE_SECONDS = 24 * 3600                    # الصفقة المتتبَّعة تنتهي بعد 24 ساعة سوق

RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RSI_BUY_MAX = 70
RSI_SELL_MIN = 30

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

CUR_AR = {
    "EUR": "يورو", "USD": "دولار أمريكي", "GBP": "جنيه إسترليني", "JPY": "ين ياباني",
    "CHF": "فرنك سويسري", "AUD": "دولار أسترالي", "CAD": "دولار كندي", "NZD": "دولار نيوزيلندي",
}
SIDE_AR = {"BUY": "شراء", "SELL": "بيع"}
INTERVAL_AR = {"15m": "15 دقيقة", "30m": "30 دقيقة"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", stream=sys.stdout)
log = logging.getLogger("bot")

# تتبع الصفقات والنتائج (في الذاكرة، وتبدأ من جديد عند إعادة التشغيل)
open_trades = {}
stats = {"win": 0, "loss": 0, "other": 0, "r": 0.0, "usd": 0.0}
last_price = {}


# ============ تيليجرام ============
def send_telegram_message(message, html=False):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    if html:
        payload["parse_mode"] = "HTML"
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            log.warning("فشل الإرسال (محاولة %d/3): %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))
    if html:  # احتياط: إعادة الإرسال كنص عادي بدون وسوم
        return send_telegram_message(re.sub(r"<[^>]+>", "", message))
    return False


# ============ جلب البيانات ============
def fetch_candles(symbol):
    """يرجع (إغلاق، أوقات، أعلى، أدنى) للشموع المغلقة فقط."""
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
            return ([x[1] for x in rows], [x[0] for x in rows],
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
    closes, tss, highs, lows = fetch_candles(symbol)
    if len(closes) < MACD_SLOW + MACD_SIGNAL + 20:
        return None

    # السوق مغلق (عطلة نهاية الأسبوع): آخر شمعة قديمة
    if time.time() - tss[-1] > INTERVAL_SECONDS * 3:
        return {"closed": True}

    r = rsi(closes)
    a = atr(highs, lows, closes)
    m0, s0, m1, s1 = macd(closes)
    side = None
    if m0 <= s0 and m1 > s1 and r < RSI_BUY_MAX:
        side = "BUY"
    elif m0 >= s0 and m1 < s1 and r > RSI_SELL_MIN:
        side = "SELL"
    last_price[symbol] = closes[-1]
    return {"closed": False, "side": side, "price": closes[-1], "rsi": r, "atr": a,
            "ts": tss[-1], "tss": tss, "highs": highs, "lows": lows}


def pair_name(sym):
    return f"{CUR_AR.get(sym[:3], sym[:3])}/{CUR_AR.get(sym[3:], sym[3:])}"


def fmt_price(symbol, price):
    return f"{price:.3f}" if "JPY" in symbol else f"{price:.5f}"


# ============ حجم الصفقة ============
def price_of(pair):
    if pair not in last_price:
        last_price[pair] = fetch_candles(pair)[0][-1]
    return last_price[pair]


def quote_to_usd(quote):
    """كم دولارًا تساوي وحدة واحدة من العملة المقابلة (الثانية في الزوج)."""
    if quote == "USD":
        return 1.0
    if quote in ("EUR", "GBP", "AUD", "NZD"):
        return price_of(quote + "USD")
    return 1.0 / price_of("USD" + quote)  # JPY, CHF, CAD


def position_size(sym, dist):
    """يحسب اللوت بحيث تكون الخسارة عند الوقف = RISK_PCT من الرصيد."""
    risk_target = ACCOUNT_BALANCE * RISK_PCT / 100
    loss_per_lot = dist * CONTRACT_SIZE * quote_to_usd(sym[3:])
    lots = max(math.floor(risk_target / loss_per_lot * 100) / 100, 0.01)
    risk_usd = lots * loss_per_lot
    pip = 0.01 if "JPY" in sym else 0.0001
    return {"lots": lots, "risk_usd": risk_usd,
            "gain_usd": risk_usd * TP_ATR / SL_ATR,
            "pips_sl": dist / pip, "pips_tp": dist * TP_ATR / SL_ATR / pip}


# ============ تتبع النتائج ============
def stats_line():
    return (f"📈 الحصيلة: رابحة {stats['win']} | خاسرة {stats['loss']} | "
            f"أخرى {stats['other']} | الصافي {stats['r']:+.1f}R (≈ {stats['usd']:+.0f}$)")


def finish_trade(sym, kind, r_value, title):
    t = open_trades.pop(sym)
    usd = r_value * t.get("risk_usd", 0.0)
    stats[kind] += 1
    stats["r"] += r_value
    stats["usd"] += usd
    usd_txt = f" (≈ {usd:+.0f}$)" if t.get("risk_usd") else ""
    send_telegram_message(
        f"{title}\n{sym} - {SIDE_AR[t['side']]}\n"
        f"النتيجة: {r_value:+.1f}R{usd_txt}\n{stats_line()}\n"
        f"(1R = مقدار وقف الخسارة)"
    )


def check_trade(sym, res):
    """يفحص هل وصلت الصفقة المفتوحة للهدف أو وقف الخسارة بعد الإشارة."""
    t = open_trades.get(sym)
    if not t:
        return
    buy = t["side"] == "BUY"
    for ts, hi, lo in zip(res["tss"], res["highs"], res["lows"]):
        if ts <= t["ts"]:
            continue
        hit_sl = lo <= t["sl"] if buy else hi >= t["sl"]
        hit_tp = hi >= t["tp"] if buy else lo <= t["tp"]
        if hit_sl:  # إذا تحقق الاثنان في نفس الشمعة نعتبرها خسارة (تحفظًا)
            finish_trade(sym, "loss", -1.0, "❌ ضُرب وقف الخسارة")
            return
        if hit_tp:
            finish_trade(sym, "win", TP_ATR / SL_ATR, "✅ تحقق الهدف")
            return
    if res["tss"][-1] - t["ts"] > MAX_TRADE_SECONDS:
        sign = 1 if buy else -1
        r_val = sign * (res["price"] - t["entry"]) / abs(t["entry"] - t["sl"])
        finish_trade(sym, "other", r_val, "⏹ انتهت المدة دون هدف أو وقف")


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


def seconds_to_next_candle():
    now = time.time()
    return INTERVAL_SECONDS - (now % INTERVAL_SECONDS) + 5


def main():
    if not TOKEN or not CHAT_ID:
        log.error("المتغيران TELEGRAM_TOKEN و TELEGRAM_CHAT_ID مطلوبان.")
        sys.exit(1)

    start_health_server()
    keep_alive()
    log.info("Bot is running... %s", SYMBOLS)
    send_telegram_message(
        f"🚀 تم تشغيل بوت التداول\n"
        f"عدد الأزواج: {len(SYMBOLS)}\n"
        f"الإطار: {INTERVAL_AR.get(INTERVAL, INTERVAL)}\n"
        f"أول فحص للإحماء فقط (بدون إشارات) لتجنب تكرار إشارات قديمة."
    )

    last_signal = {}  # symbol -> (side, candle_ts)
    warmup = True
    while True:
        report = []
        for sym in SYMBOLS:
            time.sleep(0.5)
            try:
                res = analyze(sym)
                if res is None:
                    report.append(f"{sym}: بيانات غير كافية")
                    continue
                if res["closed"]:
                    report.append(f"{sym}: السوق مغلق")
                    continue

                check_trade(sym, res)

                side = res["side"]
                report.append(f"{sym}: {fmt_price(sym, res['price'])} | RSI {res['rsi']:.1f} | {SIDE_AR.get(side, '-')}")

                if not side or last_signal.get(sym) == (side, res["ts"]):
                    continue
                last_signal[sym] = (side, res["ts"])
                if warmup:
                    continue

                p, a = res["price"], res["atr"]
                if a <= 0:
                    continue
                cur = open_trades.get(sym)
                if cur and cur["side"] == side:
                    log.info("تجاهل إشارة %s على %s (صفقة مفتوحة بنفس الاتجاه)", side, sym)
                    continue
                if cur:  # إشارة معاكسة: نغلق المتتبَّعة عند السعر الحالي
                    sign = 1 if cur["side"] == "BUY" else -1
                    r_val = sign * (p - cur["entry"]) / abs(cur["entry"] - cur["sl"])
                    finish_trade(sym, "other", r_val, "🔁 أُغلقت بإشارة معاكسة")

                sign = 1 if side == "BUY" else -1
                sl = p - sign * SL_ATR * a
                tp = p + sign * TP_ATR * a
                try:
                    size = position_size(sym, SL_ATR * a)
                except Exception as e:
                    log.warning("تعذر حساب الحجم لـ %s: %s", sym, e)
                    size = None
                open_trades[sym] = {"side": side, "entry": p, "sl": sl, "tp": tp, "ts": res["ts"],
                                    "risk_usd": size["risk_usd"] if size else 0.0}
                icon = "🟢" if side == "BUY" else "🔴"
                msg = (
                    f"{icon} إشارة {SIDE_AR[side]}\n"
                    f"{sym} - {pair_name(sym)}\n"
                    f"الإطار: {INTERVAL_AR.get(INTERVAL, INTERVAL)}\n"
                    f"الدخول: <code>{fmt_price(sym, p)}</code>\n"
                    f"وقف الخسارة: <code>{fmt_price(sym, sl)}</code> ({size['pips_sl']:.1f} نقطة)\n"
                    f"الهدف: <code>{fmt_price(sym, tp)}</code> ({size['pips_tp']:.1f} نقطة)\n"
                    if size else
                    f"{icon} إشارة {SIDE_AR[side]}\n"
                    f"{sym} - {pair_name(sym)}\n"
                    f"الإطار: {INTERVAL_AR.get(INTERVAL, INTERVAL)}\n"
                    f"الدخول: <code>{fmt_price(sym, p)}</code>\n"
                    f"وقف الخسارة: <code>{fmt_price(sym, sl)}</code>\n"
                    f"الهدف: <code>{fmt_price(sym, tp)}</code>\n"
                )
                if size:
                    msg += (
                        f"الحجم المقترح: <code>{size['lots']:.2f}</code> لوت\n"
                        f"الخسارة عند الوقف: -{size['risk_usd']:.0f}$ "
                        f"({RISK_PCT:g}% من {ACCOUNT_BALANCE:.0f}$)\n"
                        f"الربح عند الهدف: +{size['gain_usd']:.0f}$\n"
                    )
                msg += (
                    f"نسبة المخاطرة إلى العائد: 1:{TP_ATR / SL_ATR:g}\n"
                    f"مؤشر RSI: {res['rsi']:.1f}\n"
                    f"⏱ الإشارة صالحة لشمعة واحدة فقط\n"
                    f"💡 المس أي رقم لنسخه"
                )
                send_telegram_message(msg, html=True)
                log.info("إشارة %s على %s", side, sym)
            except Exception as e:
                log.exception("خطأ في %s: %s", sym, e)
                report.append(f"{sym}: خطأ")

        warmup = False
        log.info("انتهى الفحص: %s", " || ".join(report))
        if STATUS_REPORT:
            send_telegram_message("📊 تقرير الفحص\n" + "\n".join(report) + "\n" + stats_line())

        time.sleep(seconds_to_next_candle())


if __name__ == "__main__":
    main()
