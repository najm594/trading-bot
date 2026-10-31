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

def main():
    print("Bot is running...")
    send_telegram_message("🚀 **تم تشغيل بوت التداول بنجاح!**\nمرحباً بك، البوت يعمل الآن وجاهز لمراقبة السوق.")
    
    while True:
        time.sleep(900)

if __name__ == "__main__":
    main()
