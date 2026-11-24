import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import time
import threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============ الإعدادات ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

SAUDI_TZ = ZoneInfo("Asia/Riyadh")
US_EASTERN = ZoneInfo("America/New_York")

# الشروط العامة
PRICE_MIN = 5
PRICE_MAX = 200
NET_INCOME_MIN = 0
MAX_GAIN_DAY = 8.0

# عدد التنبيهات باليوم
MAX_ALERTS_STRONG = 3
MAX_ALERTS_MEDIUM = 3
MAX_ALERTS_BOUNCE = 3

SCAN_INTERVAL_MIN = 5

# ============ الحالة ============
alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
last_sent = {}
active_trades = {}
last_scan_report = "لا يوجد فحص بعد"
last_debug_report = "لا يوجد تشخيص بعد"
last_scan_time = None
is_scanning = False

# ============ أدوات ============
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("No Token")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print(f"Telegram Error: {e}")

def send_to_me(text):
    if MY_CHAT_ID:
        send_telegram(MY_CHAT_ID, text)

def get_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def is_market_open():
    now_et = datetime.now(US_EASTERN)
    if now_et.weekday() >= 5:
        return False
    return dtime(9, 30) <= now_et.time() <= dtime(16, 0)

def get_all_us_stocks():
    try:
        url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=30).json()
        return [x["symbol"] for x in r if "." not in x["symbol"] and "-" not in x["symbol"]]
    except Exception as e:
        print(f"Finnhub Error: {e}")
        return []

# ============ فحص السهم الواحد ============
def check_stock(ticker):
    try:
        tk = yf.Ticker(ticker)
        info = tk.info

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price or not (PRICE_MIN <= price <= PRICE_MAX):
            return None, f"{ticker}: السعر {price} خارج 5-200"

        market_cap = info.get("marketCap", 0) or 0
        if market_cap < 1_000_000_000:
            return None, f"{ticker}: قيمة سوقية صغيرة {market_cap/1e9:.2f}B"

        net_income = info.get("netIncomeToCommon")
        if net_income is None or net_income <= NET_INCOME_MIN:
            return None, f"{ticker}: الشركة خسرانة"

        df_1h = tk.history(period="5d", interval="1h")
        df_1d = tk.history(period="3mo", interval="1d")
        if len(df_1h) < 20 or len(df_1d) < 50:
            return None, f"{ticker}: بيانات ناقصة"

        last = df_1h.iloc[-1]
        prev_day = df_
