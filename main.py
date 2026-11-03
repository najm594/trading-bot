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
