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

# تعديلات ابو غلا المثبتة
VIX_LIMIT = 25
MAX_DAILY = 10  # 10 فرص في اليوم - طلبك

TICKERS = ["NVDA","TSLA","META","AAPL","AMD","PLTR","COIN","MSFT","AMZN","GOOGL","NFLX","AVGO","MU","SMCI","ARM","SHOP","SQ","PYPL","UBER","ABNB","CRWD","PANW","MRVL","INTC","QCOM","TXN","ADBE","ORCL","CRM","BA","DIS","NKE","XOM","XSP","SPY","QQQ"]
SECTOR = {"NVDA":"XLK","AAPL":"XLK","MSFT":"XLK","AVGO":"XLK","MU":"XLK","SMCI":"XLK","ARM":"XLK","MRVL":"XLK","INTC":"XLK","QCOM":"XLK","TXN":"XLK","ADBE":"XLK","ORCL":"XLK","CRM":"XLK","CRWD":"XLK","PANW":"XLK","TSLA":"XLY","AMZN":"XLY","NFLX":"XLY","SHOP":"XLY","SQ":"XLY","PYPL":"XLY","UBER":"XLY","ABNB":"XLY","DIS":"XLY","NKE":"XLY","META":"XLC","GOOGL":"XLC","PLTR":"XLK","COIN":"XLF","BA":"XLI","XOM":"XLE","XSP":"SPY","SPY":"SPY","QQQ":"QQQ"}

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
        return 570 <= mins <= 960  # 9:30 - 16:00 فقط، بدون فلتر اول 5 دقايق ولا غداء ولا آخر نص ساعة - الغاء رقم 13
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
        if cal is not None and 'Earnings Date' in cal:
            ed = cal['Earnings Date']
            if isinstance(ed, list): ed = ed[0]
            days = (ed.date() - datetime.now().date()).days if hasattr(ed, 'date') else 99
            if 0 <= days <= 3: return f"⚠️ أرباح بعد {days} يوم"
        return ""
    except: return ""

def check_signal(ticker):
    try:
        stock = yf.Ticker(ticker)
        df15 = stock.history(period="1d", interval="15m")
        df1h = stock.history(period="2d", interval="60m")
        df3d = stock.history(period="5d", interval="1d")
        if len(df15) < 2 or len(df1h) < 2 or len(df3d) < 3: return None, ""
        price = float(df15['Close'].iloc[-1])
        m15 = mom(df15); m1h = mom(df1h)
        trend3d = (df3d['Close'].iloc[-1] - df3d['Close'].iloc[-3]) / df3d['Close'].iloc[-3] * 100
        typical = (df15['High'] + df15['Low'] + df15['Close']) / 3
        vwap = float((typical * df15['Volume']).sum() / df15['Volume'].sum())
        spy_m = MARKET.get('spy', 0); qqq_m = MARKET.get('qqq', 0); vix = MARKET.get('vix', 20)
        if vix > VIX_LIMIT: return None, ""
        sec_etf = SECTOR.get(ticker, "SPY"); sec_m = MARKET.get(sec_etf, 0)
        delta = df15['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / loss))
        rsi_val = float(rsi.iloc[-1]) if not rsi.isna().all() else 50
        vol_ok = float(df15['Volume'].iloc[-1]) >= float(df15['Volume'].mean()) * 1.2
        recent = df1h.tail(20)
        sup1 = float(recent['Low'].min()); res1 = float(recent['High'].max())
        sup2 = float(df3d['Low'].min()); res2 = float(df3d['High'].max())
        direction = None
        if m15 >= 0.50 and m1h > 0 and trend3d > 0 and price > vwap and spy_m > 0 and qqq_m > 0 and sec_m > 0 and vol_ok and 45 <= rsi_val <= 70:
            direction = "CALL"
        elif m15 <= -0.50 and m1h < 0 and trend3d < 0 and price < vwap and spy_m < 0 and qqq_m < 0 and sec_m < 0 and vol_ok and 45 <= rsi_val <= 70:
            direction = "PUT"
        else: return None, ""
        if direction == "CALL" and (res1 - price) / price * 100 < 0.20: return None, ""
        if direction == "PUT" and (price - sup1) / price * 100 < 0.20: return None, ""
        exps = stock.options
        if not exps: return None, ""
        opt = stock.option_chain(exps[0])
        chain = opt.calls if direction == "CALL" else opt.puts
        # تعديلك: من 0.30 الى 2.50
        chain = chain[(chain['lastPrice'] >= 0.30) & (chain['lastPrice'] <= 2.50)]
        if 'volume' in chain.columns: chain = chain[chain['volume'].fillna(0) >= 100]
        if 'openInterest' in chain.columns: chain = chain[chain['openInterest'].fillna(0) >= 500]
        if chain.empty: return None, ""
        chain = chain.copy()
        chain['diff'] = abs(chain['strike'] - price)
        chain = chain[chain['diff'] / price * 100 <= 8]
        if chain.empty: return None, ""
        if 'delta' in chain.columns: chain = chain[(chain['delta'].abs() >= 0.30) & (chain['delta'].abs() <= 0.60)]
        if chain.empty: return None, ""
        best = chain.sort_values('diff').iloc[0]
        entry = float(best['lastPrice']); strike = float(best['strike'])
        th = float(best.get('theta', 0)) if 'theta' in best else 0
        if abs(th) > 0.05: return None, ""
        d = float(best.get('delta', 0)) if 'delta' in best else 0
        iv = float(best.get('impliedVolatility', 0))*100 if 'impliedVolatility' in best else 0
        bid = float(best.get('bid', entry)); ask = float(best.get('ask', entry))
        spread = (ask - bid) / entry * 100 if entry else 99
        if spread > 20: return None, ""
        stop = entry * 0.70; t1 = entry * 1.50; t2 = entry * 2.00
        be = strike + entry if direction == "CALL" else strike - entry
        total = entry * 100
        earn = get_earnings(ticker)
        trade = {"ticker": ticker, "direction": direction, "strike": strike, "entry": entry, "stop": stop, "t1": t1, "t2": t2, "exp": exps[0], "price": price, "t1_hit": False}
        msg = f"🚨 فرصة تلقائية\n✅ {direction} {ticker} {int(strike)} | {exps[0]}\n💰 دخول {entry:.2f}$ | إجمالي {total:.0f}$ | تعادل {be:.2f}$\n🛑 وقف {stop:.2f}$ (-30%) | 🎯 {t1:.2f}$ (+50%) / {t2:.2f}$ (+100%)\n📊 دلتا {d:.2f} | ثيتا {th:.3f} | IV {iv:.0f}% | سبريد {spread:.0f}%\n📈 RSI {rsi_val:.0f} | VWAP {vwap:.2f}$ | زخم {m15:.2f}% | VIX {vix:.1f}\n🌐 SPY {spy_m:.2f}% | QQQ {qqq_m:.2f}% | قطاع {sec_etf} {sec_m:.2f}%\n🛡️ دعم {sup1:.2f} / {sup2:.2f} | مقاومة {res1:.2f} / {res2:.2f}\n{earn}".strip()
        return trade, msg
    except: return None, ""

def get_option_price(trade):
    try:
        stock = yf.Ticker(trade['ticker'])
        opt = stock.option_chain(trade['exp'])
        chain = opt.calls if trade['direction'] == "CALL" else opt.puts
        row = chain[chain['strike'] == trade['strike']]
        if row.empty: return None
        return float(row.iloc[0]['lastPrice'])
    except: return None

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_CHAT_ID
    AUTO_CHAT_ID = update.effective_chat.id
    check_daily_reset()
    await update.message.reply_text(f"✅ تم تشغيل التنبيه التلقائي\n📊 فرص اليوم: {DAILY_COUNT}/{MAX_DAILY}\n🕐 وقت التداول: 9:30 - 16:00 بتوقيت نيويورك - بدون فلتر وقت\n/s META - فحص سهم\n/best - أفضل الفرص\n/results - نسبة النجاح\n/clear - مسح الصفقة")

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب: /s META"); return
    t = context.args[0].upper()
    await update.message.reply_text("🔍 جاري فحص...")
    load_market()
    trade, msg = check_signal(t)
    await update.message.reply_text(msg if msg else "⏳ ما فيه فرصة قوية على " + t)

async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    check_daily_reset()
    await update.message.reply_text("🔍 جاري فحص كل الشركات...")
    load_market()
    out = []
    for t in TICKERS:
        _, m = check_signal(t)
        if m: out.append(m)
        if len(out) >= 3: break
    await update.message.reply_text("\n\n---\n\n".join(out[:3]) if out else "⏳ ما فيه فرص قوية الحين")

async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    check_daily_reset()
    if not results:
        await update.message.reply_text(f"📊 نتائج اليوم: {DAILY_COUNT}/{MAX_DAILY}\nما انتهت اي فرصة بعد"); return
    wins = sum(1 for r in results if r['result'] == "win")
    txt = f"📊 نتائج اليوم: {DAILY_COUNT}/{MAX_DAILY}\n✅ نجاح: {wins} | ❌ فشل: {len(results)-wins}\n\n"
    for r in results:
        icon = "✅" if r['result'] == "win" else "❌"
        txt += f"{icon} {r['direction']} {r['ticker']} {int(r['strike'])} | دخول {r['entry']:.2f}$ -> خروج {r['exit']:.2f}$ ({r['pct']:+.0f}%)\n"
    await update.message.reply_text(txt)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ACTIVE.clear()
    await update.message.reply_text("✅ تم مسح الصفقة المسجلة، الحين اقدر ارسل فرص جديدة")

async def auto_scan(app_bot):
    global DAILY_COUNT, SUMMARY_SENT_DATE
    while True:
        await asyncio.sleep(900)
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
            txt = f"🏁 ملخص نهاية الجلسة\n📊 فرص اليوم: {DAILY_COUNT}/{MAX_DAILY}\n✅ نجاح: {wins} | ❌ فشل: {len(results)-wins}\n"
            if results:
                txt += "\n"
                for r in results:
                    icon = "✅" if r['result'] == "win" else "❌"
                    txt += f"{icon} {r['direction']} {r['ticker']} {int(r['strike'])} | {r['entry']:.2f}$ -> {r['exit']:.2f}$ ({r['pct']:+.0f}%)\n"
            else:
                txt += "ما كان فيه فرص انتهت اليوم"
            try: await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt)
            except: pass
            continue
        if not is_trading_hours(): continue
        if DAILY_COUNT >= MAX_DAILY: continue
        try:
            load_market()
            for t in list(ACTIVE.keys()):
                tr = ACTIVE[t]
                p = get_option_price(tr)
                if p is None: continue
                if p >= tr['t2']:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "win"})
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=f"✅ نجاح كامل {tr['direction']} {t} {int(tr['strike'])}\n💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ (+{pct:.0f}%)\n🎯 وصل الهدف الثاني +100%، الفرصة انتهت")
                    del ACTIVE[t]
                elif p >= tr['t1'] and not tr.get('t1_hit'):
                    tr['t1_hit'] = True
                    tr['stop'] = tr['entry']
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=f"🎯 هدف اول {tr['direction']} {t} {int(tr['strike'])}\n💰 دخول {tr['entry']:.2f}$ -> الحين {p:.2f}$ (+50%)\n⚠️ خذ جزء من ربحك، الوقف صار على الدخول، نكمل للهدف الثاني {tr['t2']:.2f}$ (+100%)")
                elif p <= tr['stop']:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    if tr.get('t1_hit'):
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "win"})
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=f"✅ انتهت بربح {tr['direction']} {t} {int(tr['strike'])}\n💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ ({pct:+.0f}%)\nوصل الهدف الاول قبل، ورجع للوقف على الدخول، الفرصة انتهت")
                    else:
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "lose"})
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=f"❌ فشل {tr['direction']} {t} {int(tr['strike'])}\n💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ ({pct:.0f}%)\n🛑 ضرب الوقف، الفرصة انتهت")
                    del ACTIVE[t]
            for t in TICKERS:
                if DAILY_COUNT >= MAX_DAILY: break
                if t in ACTIVE:
                    trade, msg = check_signal(t)
                    if trade and ACTIVE[t]['direction'] != trade['direction']:
                        old = ACTIVE[t]
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=f"🔄 تنبيه انعكاس {t}\nكان: {old['direction']} {int(old['strike'])} دخول {old['entry']:.2f}$\nالحين: {trade['direction']} {int(trade['strike'])} دخول {trade['entry']:.2f}$\n⚠️ الفرصة الاولى انتهت")
                        ACTIVE[t] = trade
                        DAILY_COUNT += 1
                    continue
                trade, msg = check_signal(t)
                if not trade: continue
                ACTIVE[t] = trade
                DAILY_COUNT += 1
                await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=msg + f"\n📊 فرصة {DAILY_COUNT}/{MAX_DAILY} اليوم")
        except: pass

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start_cmd))
    app_bot.add_handler(CommandHandler("s", s_cmd))
    app_bot.add_handler(CommandHandler("best", best_cmd))
    app_bot.add_handler(CommandHandler("results", results_cmd))
    app_bot.add_handler(CommandHandler("clear", clear_cmd))
    loop = asyncio.get_event_loop()
    loop.create_task(auto_scan(app_bot))
    app_bot.run_polling()
