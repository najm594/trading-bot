import os
import sys
import time
import logging
import requests

# ============ الإعدادات ============
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
INTERVAL = os.getenv("INTERVAL", "15m")        # 15m أو 30m
INTERVAL_SECONDS = {"15m": 900, "30m": 1800}.get(INTERVAL, 900)
STATUS_REPORT = os.getenv("STATUS_REPORT", "0") == "1"  # تقرير فحص مع كل شمعة
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RSI_BUY_MAX = 70      # لا شراء إذا كان RSI أعلى منه
RSI_SELL_MIN = 30     # لا بيع إذا كان RSI أدنى منه

# مصدر بيانات عام لا يحتاج مفتاح (يعمل على Render بدون حظر جغرافي)
KLINES_URL = "https://data-api.binance.vision/api/v3/klines"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("bot")


# ============ تيليجرام ============
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}  # بدون Markdown لتجنب أخطاء التحليل
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
def fetch_closes():
    """يرجع أسعار الإغلاق للشموع المغلقة فقط."""
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": 200}
    r = requests.get(KLINES_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    data = data[:-1]  # حذف الشمعة الحالية غير المكتملة
    return [float(c[4]) for c in data]


# ============ المؤشرات ============
def ema(values, period):
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out  # يبدأ من الفهرس period-1


def rsi(closes, period=RSI_PERIOD):
    """RSI بطريقة Wilder القياسية."""
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
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def macd(closes):
    """يرجع (macd_prev, signal_prev, macd_now, signal_now)."""
    fast = ema(closes, MACD_FAST)
    slow = ema(closes, MACD_SLOW)
    offset = MACD_SLOW - MACD_FAST
    macd_line = [f - s for f, s in zip(fast[offset:], slow)]
    sig = ema(macd_line, MACD_SIGNAL)
    m = macd_line[-len(sig):]
    return m[-2], sig[-2], m[-1], sig[-1]


# ============ منطق الإشارة ============
def analyze():
    closes = fetch_closes()
    if len(closes) < MACD_SLOW + MACD_SIGNAL + 20:
        return None, 0, 0, 0, 0
    r = rsi(closes)
    m0, s0, m1, s1 = macd(closes)
    price = closes[-1]

    side = None
    if m0 <= s0 and m1 > s1 and r < RSI_BUY_MAX:
        side = "BUY"
    elif m0 >= s0 and m1 < s1 and r > RSI_SELL_MIN:
        side = "SELL"
    return side, price, r, m1, s1


def seconds_to_next_candle():
    """ينتظر حتى إغلاق الشمعة التالية + 3 ثوانٍ للتأكد من اكتمالها."""
    now = time.time()
    return INTERVAL_SECONDS - (now % INTERVAL_SECONDS) + 3


def main():
    if not TOKEN or not CHAT_ID:
        log.error("المتغيران TELEGRAM_TOKEN و TELEGRAM_CHAT_ID مطلوبان.")
        sys.exit(1)

    log.info("Bot is running...")
    send_telegram_message(
        f"🚀 تم تشغيل بوت التداول\nالزوج: {SYMBOL} | الإطار: {INTERVAL}"
    )

    last_signal = None
    while True:
        try:
            side, price, r, m1, s1 = analyze()
            if STATUS_REPORT:
                send_telegram_message(
                    f"📊 فحص {SYMBOL} ({INTERVAL})\n"
                    f"السعر: {price:.2f}\nRSI: {r:.1f}\n"
                    f"MACD: {m1:.4f} | Signal: {s1:.4f}\n"
                    f"الإشارة: {side or 'لا شيء'}"
                )
            if side:
                if side != last_signal:  # منع تكرار نفس الإشارة
                    icon = "🟢" if side == "BUY" else "🔴"
                    send_telegram_message(
                        f"{icon} إشارة {side}\n"
                        f"{SYMBOL} ({INTERVAL})\n"
                        f"السعر: {price:.2f}\n"
                        f"RSI: {r:.1f}"
                    )
                    last_signal = side
                    log.info("إشارة %s عند %.2f", side, price)
            else:
                log.info("لا توجد إشارة.")
        except Exception as e:
            log.exception("خطأ أثناء التحليل: %s", e)

        time.sleep(seconds_to_next_candle())


if __name__ == "__main__":
    main()
