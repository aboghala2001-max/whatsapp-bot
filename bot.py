import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
import asyncio
import urllib.request
import urllib.parse

# ========== الاعدادات العامة ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# وقت العمل بتوقيت السعودية: من 5 عصراً الى 12 منتصف الليل
SAUDI_TZ = pytz.timezone("Asia/Riyadh")

# ========== سيرفر وهمي عشان Render المجاني ما يطفي البوت ==========
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def log_message(self, *args):
        pass

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

# ========== ارسال تيليجرام مباشر - بدون مكتبة - ما يعلق ابداً ==========
def send_msg(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Sent OK: {resp.status}")
            return True
    except Exception as e:
        print(f"Send FAILED: {e}")
        return False

# ========== قائمة الشركات - 40 شركة الأكثر حركة يومية - تحت 300$ ==========
WATCHLIST = [
    "PLTR", "AMD", "MU", "HOOD", "SOFI", "INTC", "SNAP", "U",
    "DKNG", "AFRM", "UPST", "OPEN", "RBLX", "SHOP", "SQ", "PYPL",
    "TSLA", "RIVN", "LCID", "NIO", "QS", "CHPT",
    "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF",
    "CCL", "AAL", "UAL", "DAL", "NCLH",
    "ET", "F", "BAC", "WBD", "PARA", "CPNG", "NU"
]

MAX_ALERTS = {"strong": 3, "medium": 3, "bounce": 3}
last_sent = {}
alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
last_reset_day = None

def is_work_time():
    now = datetime.now(SAUDI_TZ)
    return 17 <= now.hour <= 23

def reset_daily():
    global alerts_count, last_reset_day
    today = datetime.now(SAUDI_TZ).date()
    if last_reset_day != today:
        alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
        last_reset_day = today

def get_data(ticker):
    try:
        df_1h = yf.download(ticker, period="5d", interval="1h", progress=False)
        df_1d = yf.download(ticker, period="60d", interval="1d", progress=False)
        if df_1h.empty or df_1d.empty:
            return None
        if isinstance(df_1h.columns, pd.MultiIndex):
            df_1h.columns = df_1h.columns.get_level_values(0)
        if isinstance(df_1d.columns, pd.MultiIndex):
            df_1d.columns = df_1d.columns.get_level_values(0)
        return df_1h, df_1d
    except Exception as e:
        print(f"Data error {ticker}: {e}")
        return None

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def check_stock(ticker):
    data = get_data(ticker)
    if not data:
        return None
    df_1h, df_1d = data
    price = float(df_1h["Close"].iloc[-1])
    if not (5 <= price <= 300):
        return None
    ema20_1h = float(df_1h["Close"].ewm(span=20).mean().iloc[-1])
    ema50_1h = float(df_1h["Close"].ewm(span=50).mean().iloc[-1])
    ema50_1d = float(df_1d["Close"].ewm(span=50).mean().iloc[-1])
    ema200_1d = float(df_1d["Close"].ewm(span=200).mean().iloc[-1])
    rsi_1h = float(calc_rsi(df_1h["Close"]).iloc[-1])
    vol_today = float(df_1h["Volume"].iloc[-1])
    vol_avg = float(df_1h["Volume"].tail(20).mean())
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0
    liquidity = price * vol_today * 6
    last_close = float(df_1h["Close"].iloc[-1])
    last_open = float(df_1h["Open"].iloc[-1])
    last_low = float(df_1h["Low"].iloc[-1])
    prev_high_1d = float(df_1d["High"].iloc[-2])
    open_today = float(df_1d["Open"].iloc[-1])
    day_change = ((last_close - open_today) / open_today) * 100
    if day_change > 8:
        return None

    # 1. فرصة قوية
    if (liquidity > 50_000_000 and vol_ratio > 2.0 and
        price > ema20_1h and price > ema50_1h and
        50 <= rsi_1h <= 65 and last_close > prev_high_1d):
        target = price * 1.10
        stop = price * 0.95
        return ("strong", f"🔥 <b>فرصة قوية - {ticker}</b>\n"
                f"السعر: {price:.2f}$ | السيولة: {liquidity/1e6:.0f}M | الحجم: {vol_ratio:.1f}x\n"
                f"RSI: {rsi_1
