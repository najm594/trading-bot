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

def check_market():
    try:
        # جلب أسعار جميع العملات من باينانس دفعة واحدة
        url = "https://api.binance.com/api/v3/ticker/price"
        response = requests.get(url)
        all_tickers = response.json()
        
        print(f"Total pairs fetched: {len(all_tickers)}")
        
        for ticker in all_tickers:
            symbol = ticker['symbol']
            
            # نختار العملات المقابلة لـ USDT فقط كمثال
            if symbol.endswith("USDT"):
                price = float(ticker['price'])
                
                # يمكنك وضع شرطك هنا لفحص كل العملات
                # مثلاً: إذا أردت تنبيهاً لعملة معينة بشروط محددة
                # if symbol == "BTCUSDT" and price > 100000:
                #     send_telegram_message(f"🚨 فرصة على {symbol}: السعر {price}")
                
    except Exception as e:
        print(f"Error fetching market data: {e}")

def main():
    print("Bot is running...")
    send_telegram_message("🚀 تم تشغيل بوت التداول بنجاح لمراقبة جميع العملات!")
    
    while True:
        check_market()
        # الانتظار لمدة 15 دقيقة قبل الدورة التالية
        time.sleep(900)

if __name__ == "__main__":
    main()
