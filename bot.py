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
    prev_h = float(df['High'].iloc[-2]); prev_l = float(df['Low'].iloc[-2])
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
        vol_mean = float(df15['Volume'].mean())
        vol_now = float(df15['Volume'].iloc[-1])
        recent = df1h.tail(20)
        sup1 = float(recent['Low'].min()); res1 = float(recent['High'].max())
        sup2 = float(df3d['Low'].min()); res2 = float(df3d['High'].max())
        candle = detect_candle(df15)
        direction = None
        strength = ""
        vol_ok_strong = vol_now >= vol_mean * 1.2
        if m15 >= 0.50 and m1h > 0 and price > vwap and vol_ok_strong and 45 <= rsi_val <= 70:
            if (res1 - price) / price * 100 < 0.20:
                direction = None
            else:
                direction = "CALL"
                strength = "قوية" if candle in ["ابتلاع صاعد", "هامر"] else "متوسطة"
        elif m15 <= -0.50 and m1h < 0 and price < vwap and vol_ok_strong and 45 <= rsi_val <= 70:
            if (price - sup1) / price * 100 < 0.20:
                direction = None
            else:
                direction = "PUT"
                strength = "قوية" if candle in ["ابتلاع هابط", "شوتينق ستار"] else "متوسطة"
        if direction is None:
            if m15 >= 0.30 and m1h > 0 and price > vwap and vol_now >= vol_mean * 1.0 and 40 <= rsi_val <= 75:
                if (res1 - price) / price * 100 < 0.10:
                    return None, ""
                direction = "CALL"
                strength = "خفيفة"
            elif m15 <= -0.30 and m1h < 0 and price < vwap and vol_now >= vol_mean * 1.0 and 40 <= rsi_val <= 75:
                if (price - sup1) / price * 100 < 0.10:
                    return None, ""
                direction = "PUT"
                strength = "خفيفة"
            else:
                return None, ""
        exps = stock.options
        if not exps: return None, ""
        opt = stock.option_chain(exps[0])
        chain = opt.calls if direction == "CALL" else opt.puts
        if strength == "خفيفة":
            chain = chain[(chain['lastPrice'] >= 0.30) & (chain['lastPrice'] <= 0.60)]
        else:
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
        d = float(best.get('delta', 0)) if 'delta' in best else 0
        iv = float(best.get('impliedVolatility', 0))*100 if 'impliedVolatility' in best else 0
        bid = float(best.get('bid', entry)); ask = float(best.get('ask', entry))
        spread = (ask - bid) / entry * 100 if entry else 99
        if spread > 20: return None, ""
        stop = entry * 0.70; t1 = entry * 1.50; t2 = entry * 2.00
        be = strike + entry if direction == "CALL" else strike - entry
        total = entry * 100
        profit1 = (t1 - entry) * 100
        profit2 = (t2 - entry) * 100
        earn = get_earnings(ticker)
        candle_txt = candle if candle else "بدون شمعة تأكيد"
        if strength == "قوية": icon = "🔥"
        elif strength == "خفيفة": icon = "🪶"
        else: icon = "⚡"
        trade = {"ticker": ticker, "direction": direction, "strike": strike, "entry": entry, "stop": stop, "t1": t1, "t2": t2, "exp": exps[0], "price": price, "t1_hit": False, "strength": strength}
        parts = []
        parts.append("🚨 فرصة تلقائية")
        parts.append(f"{icon} {strength} | {direction} {ticker} {int(strike)} | {exps[0]}")
        parts.append(f"🕯️ شمعة: {candle_txt}")
        parts.append(f"💰 دخول {entry:.2f}$ | إجمالي {total:.0f}$ | تعادل {be:.2f}$")
        parts.append(f"🛑 وقف {stop:.2f}$ (-30%) | 🎯 {t1:.2f}$ (+50%) / {t2:.2f}$ (+100%)")
        parts.append(f"💵 ربح الهدف الاول {profit1:.0f}$ | ربح الهدف الثاني {profit2:.0f}$")
        parts.append(f"📊 دلتا {d:.2f} | IV {iv:.0f}% | سبريد {spread:.0f}%")
        parts.append(f"📈 RSI {rsi_val:.0f} | VWAP {vwap:.2f}$ | زخم {m15:.2f}% | VIX {vix:.1f}")
        parts.append(f"🌐 SPY {spy_m:.2f}% | QQQ {qqq_m:.2f}% | قطاع {sec_etf} {sec_m:.2f}%")
        parts.append(f"🛡️ دعم {sup1:.2f} / {sup2:.2f} | مقاومة {res1:.2f} / {res2:.2f}")
        parts.append(f"{earn}")
        msg = NL.join(parts).strip()
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
    txt = NL.join(["✅ تم تشغيل التنبيه التلقائي", f"📊 فرص اليوم: {DAILY_COUNT}/{MAX_DAILY}", "🕐 وقت التداول: 9:30 - 16:00 بتوقيت نيويورك - بدون فلتر وقت", "🔥 قوية = مع شمعة تأكيد | ⚡ متوسطة = بدون شمعة | 🪶 خفيفة = شروط اخف ربح 20-60$", "/s META - فحص سهم", "/best - أفضل الفرص", "/results - نسبة النجاح", "/clear - مسح الصفقة"])
    await update.message.reply_text(txt)

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب: /s META"); return
    t = context.args[0].upper()
    await update.message.reply_text("🔍 جاري فحص...")
    load_market()
    trade, msg = check_signal(t)
    await update.message.reply_text(msg if msg else "⏳ ما فيه فرصة قوية ولا خفيفة على " + t)

async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    check_daily_reset()
    await update.message.reply_text("🔍 جاري فحص كل الشركات...")
    load_market()
    out = []
    for t in TICKERS:
        _, m = check_signal(t)
        if m: out.append(m)
        if len(out) >= 3: break
    sep = NL + NL + "---" + NL + NL
    await update.message.reply_text(sep.join(out[:3]) if out else "⏳ ما فيه فرص قوية ولا خفيفة الحين")

async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    check_daily_reset()
    if not results:
        await update.message.reply_text(f"📊 نتائج اليوم: {DAILY_COUNT}/{MAX_DAILY}" + NL + "ما انتهت اي فرصة بعد"); return
    wins = sum(1 for r in results if r['result'] == "win")
    lines = []
    lines.append(f"📊 نتائج اليوم: {DAILY_COUNT}/{MAX_DAILY}")
    lines.append(f"✅ نجاح: {wins} | ❌ فشل: {len(results)-wins}")
    lines.append("")
    for r in results:
        icon = "✅" if r['result'] == "win" else "❌"
        lines.append(f"{icon} {r['direction']} {r['ticker']} {int(r['strike'])} | دخول {r['entry']:.2f}$ -> خروج {r['exit']:.2f}$ ({r['pct']:+.0f}%)")
    await update.message.reply_text(NL.join(lines))

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
            lines = []
            lines.append("🏁 ملخص نهاية الجلسة")
            lines.append(f"📊 فرص اليوم: {DAILY_COUNT}/{MAX_DAILY}")
            lines.append(f"✅ نجاح: {wins} | ❌ فشل: {len(results)-wins}")
            if results:
                lines.append("")
                for r in results:
                    icon = "✅" if r['result'] == "win" else "❌"
                    lines.append(f"{icon} {r['direction']} {r['ticker']} {int(r['strike'])} | {r['entry']:.2f}$ -> {r['exit']:.2f}$ ({r['pct']:+.0f}%)")
            else:
                lines.append("ما كان فيه فرص انتهت اليوم")
            txt = NL.join(lines)
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
                    txt2 = NL.join([f"✅ نجاح كامل {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ (+{pct:.0f}%)", "🎯 وصل الهدف الثاني +100%، الفرصة انتهت"])
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    del ACTIVE[t]
                elif p >= tr['t1'] and not tr.get('t1_hit'):
                    tr['t1_hit'] = True
                    tr['stop'] = tr['entry']
                    txt2 = NL.join([f"🎯 هدف اول {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> الحين {p:.2f}$ (+50%)", f"⚠️ خذ جزء من ربحك، الوقف صار على الدخول، نكمل للهدف الثاني {tr['t2']:.2f}$ (+100%)"])
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                elif p <= tr['stop']:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    if tr.get('t1_hit'):
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "win"})
                        txt2 = NL.join([f"✅ انتهت بربح {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ ({pct:+.0f}%)", "وصل الهدف الاول قبل، ورجع للوقف على الدخول، الفرصة انتهت"])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    else:
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "lose"})
                        txt2 = NL.join([f"❌ فشل {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ ({pct:.0f}%)", "🛑 ضرب الوقف، الفرصة انتهت"])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    del ACTIVE[t]
            for t in TICKERS:
                if DAILY_COUNT >= MAX_DAILY: break
                if t in ACTIVE:
                    trade, msg = check_signal(t)
                    if trade and ACTIVE[t]['direction'] != trade['direction']:
                        old = ACTIVE[t]
                        txt2 = NL.join([f"🔄 تنبيه انعكاس {t}", f"كان: {old['direction']} {int(old['strike'])} دخول {old['entry']:.2f}$", f"الحين: {trade['direction']} {int(trade['strike'])} دخول {trade['entry']:.2f}$", "⚠️ الفرصة الاولى انتهت"])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                        ACTIVE[t] = trade
                        DAILY_COUNT += 1
                    continue
                trade, msg = check_signal(t)
                if not trade: continue
                ACTIVE[t] = trade
                DAILY_COUNT += 1
                await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=msg + NL + f"📊 فرصة {DAILY_COUNT}/{MAX_DAILY} اليوم")
        except: pass

async def post_init(app):
    asyncio.create_task(auto_scan(app))

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    app_bot = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app_bot.add_handler(CommandHandler("start", start_cmd))
    app_bot.add_handler(CommandHandler("s", s_cmd))
    app_bot.add_handler(CommandHandler("best", best_cmd))
    app_bot.add_handler(CommandHandler("results", results_cmd))
    app_bot.add_handler(CommandHandler("clear", clear_cmd))
    app_bot.run_polling()
