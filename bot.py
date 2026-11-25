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
from flask import Flask

# ============ Flask وهمي عشان Render ============
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running ✅"

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ============ الإعدادات ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

SAUDI_TZ = ZoneInfo("Asia/Riyadh")
US_EASTERN = ZoneInfo("America/New_York")

PRICE_MIN = 5
PRICE_MAX = 200
NET_INCOME_MIN = 0
MAX_GAIN_DAY = 8.0

MAX_ALERTS_STRONG = 3
MAX_ALERTS_MEDIUM = 3
MAX_ALERTS_BOUNCE = 3
SCAN_INTERVAL_MIN = 5

alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
last_sent = {}
active_trades = {}
last_scan_report = "لا يوجد فحص بعد"
last_debug_report = "لا يوجد تشخيص بعد"
last_scan_time = None
is_scanning = False
last_update_id = 0

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("No Telegram Token")
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
    rsi = 100 - (100 / (1 + rs))
    return rsi

def is_market_open():
    now_et = datetime.now(US_EASTERN)
    if now_et.weekday() >= 5:
        return False
    return dtime(9, 30) <= now_et.time() <= dtime(16, 0)

def get_all_us_stocks():
    try:
        url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=30).json()
        tickers = [x["symbol"] for x in r if "." not in x["symbol"] and "-" not in x["symbol"]]
        return tickers
    except Exception as e:
        print(f"Finnhub Error: {e}")
        return []

def check_stock(ticker):
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price or not (PRICE_MIN <= price <= PRICE_MAX):
            return None, f"{ticker}: السعر {price} خارج النطاق 5-200"
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
        prev_day = df_1d.iloc[-2]
        last_day = df_1d.iloc[-1]
        day_change_pct = ((last_day["Close"] - prev_day["Close"]) / prev_day["Close"]) * 100
        if day_change_pct > MAX_GAIN_DAY:
            return None, f"{ticker}: صاعد {day_change_pct:.1f}% اكثر من 8%"
        df_1d["EMA20"] = df_1d["Close"].ewm(span=20).mean()
        df_1d["EMA50"] = df_1d["Close"].ewm(span=50).mean()
        df_1d["EMA200"] = df_1d["Close"].ewm(span=200).mean()
        df_1h["RSI"] = get_rsi(df_1h["Close"])
        ema20 = df_1d["EMA20"].iloc[-1]
        ema50 = df_1d["EMA50"].iloc[-1]
        ema200 = df_1d["EMA200"].iloc[-1] if not pd.isna(df_1d["EMA200"].iloc[-1]) else price
        rsi_1h = df_1h["RSI"].iloc[-1]
        avg_vol_20 = df_1d["Volume"].tail(20).mean()
        curr_vol = last_day["Volume"]
        vol_ratio = curr_vol / avg_vol_20 if avg_vol_20 else 0
        dollar_volume = last_day["Close"] * curr_vol
        prev_high = prev_day["High"]

        # 1. قوية
        if (market_cap > 5_000_000_000 and dollar_volume > 50_000_000 and vol_ratio > 2.0
            and price > ema20 and price > ema50
            and 50 <= rsi_1h <= 65
            and last["Close"] > prev_high):
            return {"
