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

# الشركات مثل ما هي + XSP
TICKERS = ["AAPL","MSFT","NVDA","TSLA","META","AMZN","GOOGL","NFLX","AMD","AVGO","PLTR","COIN","MSTR","SMCI","ARM","MU","QCOM","INTC","BA","DIS","JPM","BAC","XOM","CVX","SPY","QQQ","XSP","IWM","DIA","AAL","UAL","NKE","SOFI","HOOD","MARA","RIOT","RBLX"]

# المؤشرات منفصلة في العرض فقط - التحليل نفسه للكل
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
        c0 = float(close.iloc[-1]); c1 = float(close.iloc[-2])
        mom15 = (c0-c1)/c1*100
        mom1h = (c0-float(close.iloc[-5]))/float(close.iloc[-5])*100 if len(close)>=5 else 0
        rsi = get_rsi(close)
        vwap = float((close*vol).cumsum().iloc[-1] / (vol.cumsum().iloc[-1]+1e-9))
        avg_vol = float(vol.iloc[-20:-1].mean())
        vol_ratio = float(vol.iloc[-1]/(avg_vol+1e-9))

        # الشروط المتفق عليها - نفسها للشركات والمؤشرات
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

        # مدة الفرصة حسب سرعة الزخم، مو حسب القوة
        # القوية ممكن تكون 15 د، والخفيفة ممكن تكون ساعة
        if abs(mom15) >= 0.50 and vol_ratio >= 2.0:
            duration = "15 د"
        elif abs(mom1h) >= 0.80:
            duration = "ساعة"
        else:
            duration = "30 د"

        strike = round(c0)
        tk = yf.Ticker(ticker)
        try:
            exps = tk.options
            today_str = date.today().isoformat()
            future_exps = [e for e in exps if e > today_str]
            expiry = future_exps[0] if future_exps else exps[0]
        except:
            expiry = None
        if not expiry:
            return None, None
        entry = 2.5 if strength=="قوية" else 2.0
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
        # ابو 0.17 موجود - ما حذفنا الرخيص لانه فيه فايدة
        t1 = entry*1.5; t2 = entry*2.0; stop = entry*0.7
        trade = {"ticker":ticker,"direction":direction,"strike":strike,"entry":entry,"t1":t1,"t2":t2,"stop":stop,"expiry":expiry,"t1_hit":False,"strength":strength,"duration":duration}

        # شكل الفرصة - بس اللي تستفيد منه
        msg = NL.join([
            "فرصة " + strength + " " + direction + " " + ticker,
            "الشركة: " + ticker,
            "السترايك: " + str(int(strike)),
            "السعر: " + format(entry, ".2f") + "$",
            "الهدف الاول: " + format(t1, ".2f") + "$ (+50%)",
            "الهدف الثاني: " + format(t2, ".2f") + "$ (+100%)",
            "الوقف: " + format(stop, ".2f") + "$ (-30%)",
            "مدة الفرصة: " + duration,
            "انتهاء الخيار: " + str(expiry)
        ])
        return trade, msg
    except:
        return None, None

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_CHAT_ID
    AUTO_CHAT_ID = update.effective_chat.id
    check_daily_reset()
    await update.message.reply_text("تم تشغيل المسح التلقائي - قوية + متوسطة كل دقيقتين - خفيفة كل 15 دقيقة - وقت السوق 9:30 - 4:00 نيويورك")

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب: /s META")
        return
    t = context.args[0].upper()
    trade, msg = check_signal(t)
    if not trade:
        await update.message.reply_text("ما فيه فرصة قوية على " + t + " الحين")
    else:
        await update.message.reply_text(msg)

async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("جاري فحص كل الشركات...")
    stock_strong = []
    stock_light = []
    index_strong = []
    index_light = []
    for t in TICKERS:
        trade, msg = check_signal(t)
        if not trade:
            continue
        is_index = t in INDICES
        if trade['strength'] == "خفيفة":
            if is_index:
                index_light.append(msg)
            else:
                stock_light.append(msg)
        else:
            if is_index:
                index_strong.append(msg)
            else:
                stock_strong.append(msg)

    if stock_strong:
        await update.message.reply_text("--- فرص الشركات ---")
        for m in stock_strong:
            await update.message.reply_text(m)
            await asyncio.sleep(0.5)
    if stock_light:
        await update.message.reply_text("--- فرص الشركات - خفيفة 5 فقط ---")
        for m in stock_light[:5]:
            await update.message.reply_text(m)
            await asyncio.sleep(0.5)
    if index_strong:
        await update.message.reply_text("--- فرص المؤشرات SPY QQQ XSP ---")
        for m in index_strong:
            await update.message.reply_text(m)
            await asyncio.sleep(0.5)
    if index_light:
        await update.message.reply_text("--- فرص المؤشرات - خفيفة ---")
        for m in index_light[:5]:
            await update.message.reply_text(m)
            await asyncio.sleep(0.5)

    if not stock_strong and not stock_light and not index_strong and not index_light:
        await update.message.reply_text("ما فيه فرص الحين")

async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not results:
        await update.message.reply_text("ما فيه نتائج اليوم بعد")
        return
    lines = ["نتائج اليوم:"]
    for r in results:
        icon = "WIN" if r['result']=="win" else "LOSE"
        lines.append(icon + " " + r['direction'] + " " + r['ticker'] + " | السترايك: " + str(int(r['strike'])) + " | السعر: " + format(r['entry'], ".2f") + "$ -> " + format(r['exit'], ".2f") + "$ (" + format(r['pct'], "+.0f") + "%)")
    await update.message.reply_text(NL.join(lines))

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ACTIVE.clear()
    await update.message.reply_text("تم مسح الصفقات المسجلة")

async def auto_scan(app_bot):
    global DAILY_COUNT, SUMMARY_SENT_DATE
    last_light_check = 0
    while True:
        await asyncio.sleep(120)
        if not AUTO_CHAT_ID: continue
        check_daily_reset()
        if is_end_of_day() and SUMMARY_SENT_DATE != date.today():
            SUMMARY_SENT_DATE = date.today()
            for t in list(ACTIVE.keys()):
                tr = ACTIVE[t]
                p = get_option_price(tr)
                if p is not None:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    res = "win" if pct > 0 else "lose"
                    results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": res})
                del ACTIVE[t]
            wins = sum(1 for r in results if r['result'] == "win")
            lines = []
            lines.append("ملخص نهاية الجلسة")
            lines.append("فرص اليوم: " + str(DAILY_COUNT) + "/" + str(MAX_DAILY))
            lines.append("نجاح: " + str(wins) + " | فشل: " + str(len(results)-wins))
            if results:
                lines.append("")
                for r in results:
                    icon = "WIN" if r['result'] == "win" else "LOSE"
                    lines.append(icon + " " + r['direction'] + " " + r['ticker'] + " | السترايك: " + str(int(r['strike'])) + " | السعر: " + format(r['entry'], ".2f") + "$ -> " + format(r['exit'], ".2f") + "$ (" + format(r['pct'], "+.0f") + "%)")
            else:
                lines.append("ما كان فيه فرص انتهت اليوم")
            try: await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=NL.join(lines))
            except: pass
            continue
        if not is_trading_hours(): continue
        if DAILY_COUNT >= MAX_DAILY: continue
        is_light_time = (time.time() - last_light_check) >= 900
        if is_light_time:
            last_light_check = time.time()
        try:
            load_market()
            for t in list(ACTIVE.keys()):
                tr = ACTIVE[t]
                p = get_option_price(tr)
                if p is None: continue
                if p >= tr['t2']:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "win"})
                    txt2 = NL.join([
                        "نجاح كامل " + tr['direction'] + " " + t,
                        "الشركة: " + t,
                        "السترايك: " + str(int(tr['strike'])),
                        "السعر: " + format(tr['entry'], ".2f") + "$ -> " + format(p, ".2f") + "$ (+" + format(pct, ".0f") + "%)",
                        "الهدف الاول: " + format(tr['t1'], ".2f") + "$",
                        "الهدف الثاني: " + format(tr['t2'], ".2f") + "$ - وصل",
                        "الوقف: " + format(tr['stop'], ".2f") + "$",
                        "مدة الفرصة: " + tr.get('duration',''),
                        "انتهاء الخيار: " + str(tr['expiry'])
                    ])
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    del ACTIVE[t]
                elif p >= tr['t1'] and not tr.get('t1_hit'):
                    tr['t1_hit'] = True
                    tr['stop'] = tr['entry']
                    txt2 = NL.join([
                        "هدف اول " + tr['direction'] + " " + t,
                        "الشركة: " + t,
                        "السترايك: " + str(int(tr['strike'])),
                        "السعر: " + format(tr['entry'], ".2f") + "$ -> " + format(p, ".2f") + "$ (+50%)",
                        "الهدف الاول: " + format(tr['t1'], ".2f") + "$ - وصل",
                        "الهدف الثاني: " + format(tr['t2'], ".2f") + "$ - باقي",
                        "الوقف: " + format(tr['entry'], ".2f") + "$ - صار على الدخول",
                        "مدة الفرصة: " + tr.get('duration',''),
                        "انتهاء الخيار: " + str(tr['expiry'])
                    ])
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                elif p <= tr['stop']:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    if tr.get('t1_hit'):
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "win"})
                        txt2 = NL.join([
                            "انتهت بربح " + tr['direction'] + " " + t,
                            "الشركة: " + t,
                            "السترايك: " + str(int(tr['strike'])),
                            "السعر: " + format(tr['entry'], ".2f") + "$ -> " + format(p, ".2f") + "$ (" + format(pct, "+.0f") + "%)",
                            "الهدف الاول: " + format(tr['t1'], ".2f") + "$ - وصل قبل",
                            "الهدف الثاني: " + format(tr['t2'], ".2f") + "$",
                            "الوقف: " + format(tr['entry'], ".2f") + "$ - ضرب على الدخول",
                            "مدة الفرصة: " + tr.get('duration',''),
                            "انتهاء الخيار: " + str(tr['expiry'])
                        ])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    else:
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "lose"})
                        txt2 = NL.join([
                            "فشل " + tr['direction'] + " " + t,
                            "الشركة: " + t,
                            "السترايك: " + str(int(tr['strike'])),
                            "السعر: " + format(tr['entry'], ".2f") + "$ -> " + format(p, ".2f") + "$ (" + format(pct, ".0f") + "%)",
                            "الهدف الاول: " + format(tr['t1'], ".2f") + "$",
                            "الهدف الثاني: " + format(tr['t2'], ".2f") + "$",
                            "الوقف: " + format(tr['stop'], ".2f") + "$ - ضرب",
                            "مدة الفرصة: " + tr.get('duration',''),
                            "انتهاء الخيار: " + str(tr['expiry'])
                        ])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    del ACTIVE[t]
            for t in TICKERS:
                if DAILY_COUNT >= MAX_DAILY: break
                if t in ACTIVE:
                    trade, msg = check_signal(t)
                    if trade and ACTIVE[t]['direction'] != trade['direction']:
                        old = ACTIVE[t]
                        txt2 = NL.join([
                            "تنبيه انعكاس " + t,
                            "الشركة: " + t,
                            "كان: " + old['direction'] + " السترايك: " + str(int(old['strike'])) + " السعر: " + format(old['entry'], ".2f") + "$",
                            "الحين: " + trade['direction'] + " السترايك: " + str(int(trade['strike'])) + " السعر: " + format(trade['entry'], ".2f") + "$",
                            "الهدف الاول: " + format(trade['t1'], ".2f") + "$ | الهدف الثاني: " + format(trade['t2'], ".2f") + "$ | الوقف: " + format(trade['stop'], ".2f") + "$",
                            "مدة الفرصة: " + trade.get('duration',''),
                            "الفرصة الاولى انتهت"
                        ])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                        ACTIVE[t] = trade
                        DAILY_COUNT += 1
                    continue
                trade, msg = check_signal(t)
                if not trade: continue
                if trade['strength'] == "خفيفة" and not is_light_time:
                    continue
                ACTIVE[t] = trade
                DAILY_COUNT += 1
                await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=msg + NL + "فرصة " + str(DAILY_COUNT) + "/" + str(MAX_DAILY) + " اليوم")
        except: pass

async def main():
    tg_app = Application.builder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(CommandHandler("s", s_cmd))
    tg_app.add_handler(CommandHandler("best", best_cmd))
    tg_app.add_handler(CommandHandler("results", results_cmd))
    tg_app.add_handler(CommandHandler("clear", clear_cmd))
    asyncio.create_task(auto_scan(tg_app))
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
