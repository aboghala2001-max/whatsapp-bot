import os, threading, logging, time
import yfinance as yf
import pandas as pd
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("BOT_TOKEN")
app_web = Flask(__name__)
@app_web.route('/')
def home(): return "Bot is running"

DEFAULT_SYMBOLS = ["AMZN","NVDA","AAPL","TSLA","MSFT","GOOGL","QQQ","SPY","META","XSP"]
WATCHLIST = {}
ALERTED = {}

# === جديد: منع التكرار + التقييم ===
ACTIVE_SIGNALS = {}
HISTORY = []

def rsi_calc(data, period=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_signal(ticker):
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        close = df['Close']
        price = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        change = (price - prev) / prev * 100
        sma50 = float(close.rolling(50).mean().iloc[-1])
        rsi = float(rsi_calc(close).iloc[-1])
        vwap = float(close.tail(20).mean())
        mom15 = float((close.iloc[-1] / close.iloc[-16] - 1) * 100) if len(close) > 16 else 0
        support = float(close.tail(30).min())
        resist = float(close.tail(30).max())

        if price > vwap and 45 <= rsi <= 65 and mom15 > 0.5 and price > sma50:
            option = "CALL 📈"; strength = "قوية جدا 🔥"; duration = "15 - 30 دقيقة"
            entry = price; target = price * 1.02; stop = price * 0.99
            reason = "فوق VWAP + RSI متوازن + زخم صاعد"
        elif price > vwap and 40 <= rsi <= 68 and mom15 > 0:
            option = "CALL 📈"; strength = "قوية 💪"; duration = "30 - 60 دقيقة"
            entry = price; target = price * 1.015; stop = price * 0.988
            reason = "فوق VWAP + زخم صاعد"
        elif price < vwap and 35 <= rsi <= 55 and mom15 < -0.5 and price < sma50:
            option = "PUT 📉"; strength = "قوية جدا 🔥"; duration = "15 - 30 دقيقة"
            entry = price; target = price * 0.98; stop = price * 1.01
            reason = "تحت VWAP + RSI نازل + زخم هابط"
        elif price < vwap and 32 <= rsi <= 60 and mom15 < 0:
            option = "PUT 📉"; strength = "قوية 💪"; duration = "30 - 60 دقيقة"
            entry = price; target = price * 0.985; stop = price * 1.012
            reason = "تحت VWAP + زخم هابط"
        elif rsi > 70 or rsi < 30:
            option = "انتظار ⚠️"; strength = "متوسطة"; duration = "60
