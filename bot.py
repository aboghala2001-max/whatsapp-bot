import os
import time
import asyncio
import threading
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 10000))
NL = "\n"
NY = ZoneInfo("America/New_York")

TICKERS = ["AAPL","MSFT","NVDA","TSLA","META","AMZN","GOOGL","NFLX","AMD","AVGO","PLTR","COIN","MSTR","SMCI","ARM","MU","QCOM","INTC","BA","DIS","JPM","BAC","XOM","CVX","SPY","QQQ","IWM","DIA","AAL","UAL","NKE","SOFI","HOOD","MARA","RIOT","RBLX"]

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

def check_daily_reset():
    global DAILY_COUNT, LAST_RESET_DATE, results
    today = date.today()
    if LAST_RESET_DATE != today:
        LAST_RESET_DATE = today
        DAILY_COUNT = 0
        results = []

def is_trading_hours():
    now = datetime.now(NY).time()
    return dtime(9,30) <= now <= dtime(16,0)

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

def get_option_price(tr):
    try:
        t = yf.Ticker(tr['ticker'])
        opts = t.option_chain(tr['expiry'])
        chain = opts.calls if tr['direction']=="CALL" else opts.puts
        row = chain[chain['strike']==tr['strike']]
        if len(row)==0:
            return None
        return float(row.iloc[0]['lastPrice'])
    except:
        return None

def check_signal(ticker):
    try:
        df = yf.download(ticker, period="2d", interval="15m", progress=False, auto_adjust=True)
        if df is None or len(df) < 20:
            return None, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df['Close']
        vol = df['Volume']
        c0 = float(close.iloc[-1]); c1 = float(close.iloc[-2]); c2 = float(close.iloc[-3])
        mom15 = (c0-c1)/c1*100
        mom1h = (c0-float(close.iloc[-5]))/float(close.iloc[-5])*100 if len(close)>=5 else 0
        rsi = get_rsi(close)
        vwap = float((close*vol).cumsum().iloc[-1] / (vol.cumsum().iloc[-1]+1e-9))
        avg_vol = float(vol.iloc[-20:-1].mean())
        vol_ratio = float(vol.iloc[-1]/(avg_vol+1e-9))
        high = float(df['High'].iloc[-20:].max()); low = float(df['Low'].iloc[-20:].min())
        dist_high = (high-c0)/c0*100; dist_low = (c0-low)/c0*100
        direction = None; strength = None; score = 0
        if mom15>0.25 and mom1h>0.4 and rsi>55 and c0>vwap and vol_ratio>1.2:
            direction="CALL"; score+=3
        elif mom15<-0.25 and mom1h<-0.4 and rsi<45 and c0<vwap and vol_ratio>1.2:
            direction="PUT"; score+=3
        elif mom15>0.15 and mom1h>0.2 and rsi>52:
            direction="CALL"; score+=2
        elif mom15<-0.15 and mom1h<-0.2 and rsi<48:
            direction="PUT"; score+=2
        elif abs(mom15)>0.08:
            direction="CALL" if mom15>0 else "PUT"; score+=1
        else:
            return None, None
        if score>=3: strength="قوية"
        elif score==2: strength="متوسطة"
        else: strength="خفيفة"
        icon = "🔥" if strength=="قوية" else "⚡" if strength=="متوسطة" else "🪶"
        strike = round(c0)
        # خيار قريب من السعر، اسبوعي تقريبا
        tk = yf.Ticker(ticker)
        try:
            exps = tk.options
            expiry = exps[0] if exps else None
        except:
            expiry = None
        if not expiry:
            return None, None
        # سعر تقريبي للخيار
        entry = 2.5 if strength=="قوية" else 2.0
        # نحاول نجيب سعر حقيقي
        try:
            oc = tk.option_chain(expiry)
            chain = oc.calls if direction=="CALL" else oc.puts
            row = chain.iloc[(chain['strike']-strike).abs().argsort()[:1]]
            if len(row)>0:
                strike = float(row.iloc[0]['strike'])
                entry = float(row.iloc[0]['lastPrice'])
                if entry<=0: entry=2.0
        except:
            pass
        t1 = entry*1.5; t2 = entry*2.0; stop = entry*0.7
        trade = {"ticker":ticker,"direction":direction,"strike":strike,"entry":entry,"t1":t1,"t2":t2,"stop":stop,"expiry":expiry,"t1_hit":False,"strength":strength}
        msg = NL.join([
            f"{icon} فرصة {strength} {direction} {ticker} {int(strike)}",
            f"💰 دخول: {entry:.2f}$ | وقف: {stop:.2f}$ (-30%)",
            f"🎯 هدف اول: {t1:.2f}$ (+50%) | هدف ثاني: {t2:.2f}$ (+100%)",
            f"📊 زخم 15د: {mom15:+.2f}% | زخم ساعة: {mom1h:+.2f}% | RSI: {rsi:.0f} | حجم: {vol_ratio:.1f}x",
            f"📅 انتهاء: {expiry}",
            f"⚠️ خذ جزء عند الهدف الاول، الوقف بيتحرك للدخول"
        ])
        return trade, msg
    except Exception as e:
        return None, None

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_CHAT_ID
    AUTO_CHAT_ID = update.effective_chat.id
    check_daily_reset()
    await update.message.reply_text("✅ تم تشغيل المسح التلقائي\n🔥 قوية + ⚡ متوسطة كل دقيقتين\n🪶 خفيفة كل 15 دقيقة\n📊 وقت السوق 9:30 - 4:00 نيويورك")

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب: /s META")
        return
    t = context.args[0].upper()
    trade, msg = check_signal(t)
    if not trade:
        await update.message.reply_text(f"ما فيه فرصة قوية على {t} الحين")
    else:
        await update.message.reply_text(msg)

async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 جاري فحص كل الشركات...")
    found = []
    for t in TICKERS:
        trade, msg = check_signal(t)
        if trade:
            found.append(msg)
    if not found:
        await update.message.reply_text("ما فيه فرص الحين")
    else:
        await update.message.reply_text(NL+NL.join(found[:10]))

async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not results:
        await update.message.reply_text("ما فيه نتائج اليوم بعد")
        return
    lines = ["📊 نت
