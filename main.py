import os
import time
import requests

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error sending message: {e}")

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
            
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def check_market():
    # أهم وأقوى العملات الرقمية ذات السيولة العالية
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
    
    for symbol in symbols:
        try:
            # جلب البيانات على إطار 15 دقيقة
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=30"
            response = requests.get(url)
            klines = response.json()
            
            close_prices = [float(k[4]) for k in klines]
            current_price = close_prices[-1]
            rsi = calculate_rsi(close_prices)
            
            # استراتيجية صفقات الصعود (Long) بأهداف صغيرة ومضمونة
            if rsi < 30:
                entry = current_price
                target = round(entry * 1.012, 4) # هدف ربح آمن 1.2%
                stop_loss = round(entry * 0.994, 4) # وقف خسارة ضيق 0.6%
                
                msg = (
                    f"🚨 **فرصة صفقة صعود (LONG)** 🚨\n\n"
                    f"📌 **العملة:** {symbol}\n"
                    f"⏱ **الإطار الزمني:** 15 دقيقة\n"
                    f"🟢 **سعر الدخول:** `{entry}`\n"
                    f"🎯 **الهدف (Take Profit):** `{target}` (+1.2%)\n"
                    f"🛑 **وقف الخسارة (Stop Loss):** `{stop_loss}` (-0.6%)\n"
                    f"⚡ **الرافعة المالية المقترحة:** `5x` (للحفاظ على الأمان والربح المضمون)\n"
                    f"📊 **مؤشر RSI:** `{round(rsi, 2)}` (منطقة تشبع بيعي - فرصة ارتداد)"
                )
                send_telegram_message(msg)
                
            # استراتيجية صفقات الهبوط (Short) بأهداف صغيرة ومضمونة
            elif rsi > 70:
                entry = current_price
                target = round(entry * 0.988, 4) # هدف ربح آمن 1.2%
                stop_loss = round(entry * 1.006, 4) # وقف خسارة ضيق 0.6%
                
                msg = (
                    f"🚨 **فرصة صفقة هبوط (SHORT)** 🚨\n\n"
                    f"📌 **العملة:** {symbol}\n"
                    f"⏱ **الإطار الزمني:** 15 دقيقة\n"
                    f"🔴 **سعر الدخول:** `{entry}`\n"
                    f"🎯 **الهدف (Take Profit):** `{target}` (-1.2%)\n"
                    f"🛑 **وقف الخسارة (Stop Loss):** `{stop_loss}` (+0.6%)\n"
                    f"⚡ **الرافعة المالية المقترحة:** `5x` (للحفاظ على الأمان والربح المضمون)\n"
                    f"📊 **مؤشر RSI:** `{round(rsi, 2)}` (منطقة تشبع شرائي - فرصة تصحيح)"
                )
                send_telegram_message(msg)
                
        except Exception as e:
            print(f"Error checking {symbol}: {e}")

def main():
    print("Bot is running...")
    send_telegram_message("🚀 تم تشغيل بوت التداول الذكي (إطار 15 دقيقة) بنجاح وجاهز لاصطياد الصفقات الآمنة!")
    
    while True:
        check_market()
        time.sleep(900) # الفحص كل 15 دقيقة لتطابق الشموع

if __name__ == "__main__":
    main()
