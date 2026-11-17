import os
import time
import asyncio
import threading
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 10000))
NL = "\n"
NY = ZoneInfo("America/New_York")
TICKERS = ["AAPL","MSFT","NVDA","TSLA","META","AMZN","GOOGL","NFLX","AMD","AVGO","PLTR","COIN","MSTR","SMCI","ARM","MU","QCOM","INTC","BA","DIS","JPM","BAC","XOM","CVX","SPY","QQQ","XSP","IWM","DIA","AAL","UAL","NKE","SOFI","HOOD","MARA","RIOT","RBLX"]
INDICES = ["SPY","QQQ","XSP"]
ACTIVE = {}
results = []
DAILY_COUNT = 0
MAX_DAILY = 10
AUTO_CHAT_ID = None
SUMMARY_SENT_DATE = None
LAST_RESET_DATE = None
MARKET_CACHE = {}
CACHE_TIME = 0

app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is running"

def ny_today():
    return datetime.now(NY).date()

def check_daily_reset():
    global DAILY_COUNT, LAST_RESET_DATE, results
    today = ny_today()
    if LAST_RESET_DATE != today:
        LAST_RESET_DATE = today
        DAILY_COUNT = 0
        results = []

def is_trading_hours():
    now = datetime.now(NY).time()
    return dtime(9,0) <= now <= dtime(16,0)

def is_end_of_day():
    now = datetime.now(NY).time()
    return dtime(16,0) <= now <= dtime(16,15)

def load_market():
    global MARKET_CACHE, CACHE_TIME
    if time.time() - CACHE_TIME < 300 and MARKET_CACHE:
        return
    try:
        df = yf.download("SPY QQQ IWM DIA VIX", period="1d", interval="15m", progress=False)
        MARKET_CACHE = df
        CACHE_TIME = time.time()
    except:
        pass

def get_rsi(close, period=14):
    if len(close) < period+1:
        return 50
    delta = close.diff()
    gain = delta.where(delta>0,0).rolling(period).mean()
    loss = -delta.where(delta<0,0).rolling(period).mean()
    rs = gain/(loss+1e-9)
    rsi = 100 - (100/(1+rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50

def safe_float(v):
    try:
        f = float(v)
        if pd.isna(f):
            return 0.0
        return f
    except:
        return 0.0

def get_real_option_info(tr):
    try:
        t = yf.Ticker(tr['ticker'])
        opts = t.option_chain(tr['expiry'])
        chain = opts.calls if tr['direction']=="CALL" else opts.puts
        row = chain[chain['strike']==tr['strike']]
        if len(row)==0:
            return None
        r = row.iloc[0]
        bid = safe_float(r.get('bid', 0))
        ask = safe_float(r.get('ask', 0))
        last = safe_float(r.get('lastPrice', 0))
        if bid > 0 and ask > 0:
            price = bid * 0.5 + ask * 0.5
            spread = 0
            if price > 0:
                spread = ask - bid
                spread = spread / price
            return {"price": price, "bid": bid, "ask": ask, "spread": spread, "source": "BID/ASK"}
        if last > 0:
            return {"price": last, "bid": bid, "ask": ask, "spread": 0, "source": "LAST"}
        return None
    except:
        return None

def get_option_price(tr):
    info = get_real_option_info(tr)
    if info is None:
        return None
    return float(info['price'])

def check_signal(ticker):
    try:
        df = yf.download(ticker, period="2d", interval="15m", progress=False, auto_adjust=True)
        if df is None or len(df) < 20:
            return None, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df['Close']
        vol = df['Volume']
        c0 = float(close.iloc[-1])
        c1 = float(close.iloc[-2])
        if c0 > 2000:
            return None, None
        mom15 = (c0-c1)/c1*100
        mom1h = (c0-float(close.iloc[-5]))/float(close.iloc[-5])*100 if len(close)>=5 else 0
        rsi = get_rsi(close)
        vwap = float((close*vol).cumsum().iloc[-1] / (vol.cumsum().iloc[-1]+1e-9))
        avg_vol = float(vol.iloc[-20:-1].mean())
        vol_ratio = float(vol.iloc[-1]/(avg_vol+1e-9))
        direction = None
        strength = None
        score = 0
        if mom15>0.25 and mom1h>0.4 and rsi>55 and c0>vwap and vol_ratio>1.2:
            direction="CALL"
            score+=3
        elif mom15<-0.25 and mom1h<-0.4 and rsi<45 and c0<vwap and vol_ratio>1.2:
            direction="PUT"
            score+=3
        elif mom15>0.15 and mom1h>0.2 and rsi>52:
            direction="CALL"
            score+=2
        elif mom15<-0.15 and mom1h<-0.2 and rsi<48:
            direction="PUT"
            score+=2
        elif abs(mom15)>0.03:
            direction="CALL" if mom15>0 else "PUT"
            score+=1
        else:
            return None, None
        if score>=3:
            strength="قوية"
        elif score==2:
            strength="متوسطة"
        else:
            strength="خفيفة"
        if abs(mom15) >= 0.50 and vol_ratio >= 2.0:
            duration = "15 د"
        elif abs(mom1h) >= 0.80:
            duration = "ساعة"
        else:
            duration = "30 د"
        tk = yf.Ticker(ticker)
        try:
            exps = tk.options
            today_str = ny_today().isoformat()
            if today_str in exps:
                expiry = today_str
            elif len(exps) > 0:
                expiry = exps[0]
            else:
                return None, None
        except:
            return None, None
        strike = round(c0)
        entry = 2.5 if strength=="قوية" else 2.0
        bid = 0
        ask = 0
        price_source = "LAST"
        try:
            oc = tk.option_chain(expiry)
            chain = oc.calls if direction=="CALL" else oc.puts
            idx = (chain['strike']-strike).abs().idxmin()
            row = chain.loc[[idx]]
            if len(row)>0:
                strike = float(row.iloc[0]['strike'])
                bid = safe_float(row.iloc[0].get('bid', 0))
                ask = safe_float(row.iloc[0].get('ask', 0))
                last = safe_float(row.iloc[0].get('lastPrice', 0))
                if bid > 0 and ask > 0:
                    entry = bid * 0.5 + ask * 0.5
                    price_source = "BID/ASK"
                    spread = 0
                    if entry > 0:
                        spread
