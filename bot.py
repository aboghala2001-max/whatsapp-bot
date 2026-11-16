import os, threading, time, asyncio
from flask import Flask
import yfinance as yf
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime, date
import pytz

TOKEN = os.getenv("BOT_TOKEN")
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running"

NL = chr(10)
VIX_LIMIT = 25
MAX_DAILY = 10

TICKERS = ["NVDA","TSLA","META","AAPL","AMD","PLTR","COIN","MSFT","AMZN","GOOGL","NFLX","AVGO","MU","SMCI","ARM","SHOP","SQ","PYPL","UBER","ABNB","CRWD","PANW","MRVL","INTC","QCOM","TXN","ADBE","ORCL","CRM","BA","DIS","NKE","XOM","XSP","SPY","QQQ"]
SECTOR = {"NVDA":"XLK","AAPL":"XLK","MSFT":"XLK","AVGO":"XLK","MU":"XLK","SMCI":"XLK","ARM":"XLK","MRVL":"XLK","INTC":"XLK","QCOM":"XLK","TXN":"XLK","ADBE":"XLK","ORCL":"XLK","CRM":"XLK","CRWD":"XLK","PANW":"XLK","TSLA":"XLY","AMZN":"XLY","NFLX":"XLY","SHOP":"XLY","SQ":"XLY","PYPL":"XLY","UBER":"XLY","ABNB":"XLY","DIS":"XLY","NKE":"XLY","META":"XLC","GOOGL":"XLC","PLTR":"XLK","COIN":"XLF","BA":"XLI","XOM":"XLE","XSP":"SPY","SPY":"SPY","QQQ":"QQQ"}
NO_LIGHT = ["SPY","QQQ","XSP"]

results = []
ACTIVE = {}
MARKET = {}
LAST_MARKET = 0
AUTO_CHAT_ID = None
DAILY_COUNT = 0
DAILY_DATE = None
SUMMARY_SENT_DATE = None

def is_trading_hours():
    try:
        ny = pytz.timezone("America/New_York")
        now = datetime.now(ny)
        if now.weekday() >= 5: return False
        mins = now.hour * 60 + now.minute
        return 570 <= mins <= 960
    except: return True

def is_end_of_day():
    try:
        ny = pytz.timezone("America/New_York")
        now = datetime.now(ny)
        if now.weekday() >= 5: return False
        mins = now.hour * 60 + now.minute
        return 960 <= mins <= 975
    except: return False

def check_daily_reset():
    global DAILY_COUNT, DAILY_DATE, SUMMARY_SENT_DATE
    today = date.today()
    if DAILY_DATE != today:
        DAILY_DATE = today
        DAILY_COUNT = 0
        ACTIVE.clear()
        results.clear()
        SUMMARY_SENT_DATE = None

def mom(df):
    return (df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2] * 100 if len(df) >= 2 else 0

def detect_candle(df):
    if len(df) < 2: return ""
    prev_o = float(df['Open'].iloc[-2]); prev_c = float(df['Close'].iloc[-2])
    o = float(df['Open'].iloc[-1]); c = float(df['Close'].iloc[-1])
    h = float(df['High'].iloc[-1]); l = float(df['Low'].iloc[-1])
    body = abs(c - o)
    rng = h - l
    if rng == 0: return ""
    if prev_c < prev_o and c > o and o <= prev_c and c >= prev_o:
        return "ابتلاع صاعد"
    if prev_c > prev_o and c < o and o >= prev_c and c <= prev_o:
        return "ابتلاع هابط"
    lower = min(o, c) - l
    upper = h - max(o, c)
    if c > o and lower > body * 2 and upper < body * 1.0 and body / rng < 0.35:
        return "هامر"
    if c < o and upper > body * 2 and lower < body * 1.0 and body / rng < 0.35:
        return "شوتينق ستار"
    return ""

def load_market():
    global LAST_MARKET
    if time.time() - LAST_MARKET < 300 and MARKET: return
    try:
        MARKET['spy'] = mom(yf.Ticker("SPY").history(period="1d", interval="15m"))
        MARKET['qqq'] = mom(yf.Ticker("QQQ").history(period="1d", interval="15m"))
        v = yf.Ticker("^VIX").history(period="1d", interval="15m")
        MARKET['vix'] = float(v['Close'].iloc[-1]) if len(v) else 20
        for etf in ["XLK","XLY","XLC","XLF","XLI","XLE","SPY"]:
            MARKET[etf] = mom(yf.Ticker(etf).history(period="1d", interval="15m"))
        LAST_MARKET = time.time()
    except: pass

def get_earnings(ticker):
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is not None and 'Earnings Date
