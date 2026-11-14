import os, threading, time
from flask import Flask
import yfinance as yf
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running"

TICKERS = ["NVDA","TSLA","META","AAPL","AMD","PLTR","COIN","MSFT","AMZN","GOOGL","NFLX","AVGO","MU","SMCI","ARM","SHOP","SQ","PYPL","UBER","ABNB","CRWD","PANW","MRVL","INTC","QCOM","TXN","ADBE","ORCL","CRM","BA","DIS","NKE","XOM","XSP"]
# قطاع كل سهم
SECTOR = {"NVDA":"XLK","AAPL":"XLK","MSFT":"XLK","AVGO":"XLK","MU":"XLK","SMCI":"XLK","ARM":"XLK","MRVL":"XLK","INTC":"XLK","QCOM":"XLK","TXN":"XLK","ADBE":"XLK","ORCL":"XLK","CRM":"XLK","CRWD":"XLK","PANW":"XLK","TSLA":"XLY","AMZN":"XLY","NFLX":"XLY","SHOP":"XLY","SQ":"XLY","PYPL":"XLY","UBER":"XLY","ABNB":"XLY","DIS":"XLY","NKE":"XLY","META":"XLC","GOOGL":"XLC","PLTR":"XLK","COIN":"XLF","BA":"XLI","XOM":"XLE","XSP":"SPY"}

results = []
active_trade = None

def mom(df):
    return (df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2] * 100 if len(df) >= 2 else 0

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
        if len(df15) < 2 or len(df1h) < 2 or len(df3d) < 3: return None, "⏳ ما فيه بيانات"
        price = float(df15['Close'].iloc[-1])
        m15 = mom(df15); m1h = mom(df1h)
        trend3d = (df3d['Close'].iloc[-1] - df3d['Close'].iloc[-3]) / df3d['Close'].iloc[-3] * 100
        typical = (df15['High'] + df15['Low'] + df15['Close']) / 3
        vwap = float((typical * df15['Volume']).sum() / df15['Volume'].sum())
        spy = yf.Ticker("SPY").history(period="1d", interval="15m")
        qqq = yf.Ticker("QQQ").history(period="1d", interval="15m")
        spy_m = mom(spy); qqq_m = mom(qqq)
        vix_df = yf.Ticker("^VIX").history(period="1d", interval="15m")
        vix = float(vix_df['Close'].iloc[-1]) if len(vix_df) else 20
        if vix > 25: return None, f"⏳ {ticker} مرفوض - VIX عالي {vix:.1f}"
        sec_etf = SECTOR.get(ticker, "SPY")
        sec_df = yf.Ticker(sec_etf).history(period="1d", interval="15m")
        sec_m = mom(sec_df)
        delta = df15['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / loss))
        rsi_val = float(rsi.iloc[-1]) if not rsi.isna().all() else 50
        avg_vol = float(df15['Volume'].mean()); last_vol = float(df15['Volume'].iloc[-1])
        vol_ok = last_vol >= avg_vol * 1.2
        recent = df1h.tail(20)
        sup1 = float(recent['Low'].min()); res1 = float(recent['High'].max())
        sup2 = float(df3d['Low'].min()); res2 = float(df3d['High'].max())

        direction = None
        if m15 >= 0.50 and m1h > 0 and trend3d > 0 and price > vwap and spy_m > 0 and qqq_m > 0 and sec_m > 0 and vol_ok and 45 <= rsi_val <= 70:
            direction = "CALL"
        elif m15 <= -0.50 and m1h < 0 and trend3d < 0 and price < vwap and spy_m < 0 and qqq_m < 0 and sec_m < 0 and vol_ok and 45 <= rsi_val <= 70:
            direction = "PUT"
        else:
            reasons = []
            if not (spy_m > 0 and qqq_m > 0) and not (spy_m < 0 and qqq_m < 0): reasons.append("توجه السوق متضارب")
            if not (45 <= rsi_val <= 70): reasons.append(f"RSI {rsi_val:.0f}")
            if not vol_ok: reasons.append("سيولة ضعيفة")
            return None, f"⏳ {ticker} انتظار - {', '.join(reasons) if reasons else f'زخم {m15:.2f}%'} | سعر {price:.2f}$ | RSI {rsi_val:.0f}"

        if direction == "CALL" and (res1 - price) / price * 100 < 0.20: return None, f"⏳ {ticker} مرفوض - مقاومة قريبة {res1:.2f}$"
        if direction == "PUT" and (price - sup1) / price * 100 < 0.20: return None, f"⏳ {ticker} مرفوض - دعم قريب {sup1:.2f}$"

        exps = stock.options
        if not exps: return None, "❌ ما فيه اوبشن"
        opt = stock.option_chain(exps[0])
        chain = opt.calls if direction == "CALL" else opt.puts
        # سعر العقد من 0.30 الى 2.50
        chain = chain[(chain['lastPrice'] >= 0.30) & (chain['lastPrice'] <= 2.50)]
        if 'volume' in chain.columns: chain = chain[chain['volume'].fillna(0) >= 100]
        if 'openInterest' in chain.columns: chain = chain[chain['openInterest'].fillna(0) >= 500]
        if chain.empty: return None, f"✅ {direction} {ticker} إشارة قوية بس ما فيه عقد قوي (سيولة / OI)"
        chain = chain.copy()
        chain['diff'] = abs(chain['strike'] - price)
        chain = chain[chain['diff'] / price * 100 <= 8]
        if chain.empty: return None, f"⏳ {ticker} ما فيه سترايك قريب"
        if 'delta' in chain.columns: chain = chain[(chain['delta'].abs() >= 0.30) & (chain['delta'].abs() <= 0.60)]
        if chain.empty: return None, f"⏳ {ticker} مرفوض - دلتا خارج النطاق"
        best = chain.sort_values('diff').iloc[0]
        entry = float(best['lastPrice']); strike = float(best['strike'])
        th = float(best.get('theta', 0)) if 'theta' in best else 0
        if abs(th) > 0.05: return None, f"⏳ {ticker} مرفوض - ثيتا عالي {th:.3f}"
        d = float(best.get('delta', 0)) if 'delta' in best else 0
        iv = float(best.get('impliedVolatility', 0))*100 if 'impliedVolatility' in best else 0
        bid = float(best.get('bid', entry)); ask = float(best.get('ask', entry))
        spread = (ask - bid) / entry * 100 if entry else 99
        if spread > 20: return None, f"⏳ {ticker} مرفوض - سبريد عالي {spread:.0f}%"
        stop = entry * 0.70; t1 = entry * 1.50; t2 = entry * 2.00
        be = strike + entry if direction == "CALL" else strike - entry
        total = entry * 100
        earn = get_earnings(ticker)
        trade = {"ticker": ticker, "direction": direction, "strike": strike, "entry": entry, "stop": stop, "t1": t1, "t2": t2, "exp": exps[0]}
        msg = f"✅ {direction} {ticker} {int(strike)} | {exps[0]}\n💰 دخول {entry:.2f}$ | إجمالي {total:.0f}$ | تعادل {be:.2f}$\n🛑 وقف {stop:.2f}$ (-30%) | 🎯 {t1:.2f}$ (+50%) / {t2:.2f}$ (+100%)\n📊 دلتا {d:.2f} | ثيتا {th:.3f} | IV {iv:.0f}% | سبريد {spread:.0f}%\n📈 RSI {rsi_val:.0f} | VWAP {vwap:.2f}$ | زخم {m15:.2f}% | VIX {vix:.1f}\n🌐 SPY {spy_m:.2f}% | QQQ {qqq_m:.2f}% | قطاع {sec_etf} {sec_m:.2f}%\n🛡️ دعم {sup1:.2f} / {sup2:.2f} | مقاومة {res1:.2f} / {res2:.2f}\n{earn}".strip()
        return trade, msg
    except Exception as e: return None, f"❌ خطأ {ticker}: {e}"

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا 👋\n/s META - فحص سهم\n/best - أفضل الفرص القوية\n/results - نسبة النجاح\n/clear - مسح الصفقة المسجلة")
async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_trade
    if not context.args:
        await update.message.reply_text("اكتب: /s META"); return
    t = context.args[0].upper()
    await update.message.reply_text("🔍 جاري فحص أقوى الفرص...")
    trade, msg = check_signal(t)
    if trade: active_trade = trade
    await update.message.reply_text(msg)
async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 جاري فحص 35 سهم بأقوى الفلاتر...")
    out = []
    for t in TICKERS:
        trade, msg = check_signal(t)
        if trade: out.append(msg)
    await update.message.reply_text("\n\n---\n\n".join(out[:3]) if out else "⏳ ما فيه فرص قوية الحين - الفلاتر رفضت الكل، وهذا ممتاز، معناته ما ندخل فرص ضعيفة")
async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not results:
        await update.message.reply_text("📊 ما فيه نتائج مسجلة بعد"); return
    wins = sum(1 for r in results if r == "win"); total = len(results)
    await update.message.reply_text(f"📊 النتائج:\nإجمالي: {total}\nنجاح: {wins} ({wins/total*100:.0f}%)\nخسارة: {total-wins}")
async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_trade
    active_trade = None
    await update.message.reply_text("✅ تم مسح الصفقة المسجلة")

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start_cmd))
    app_bot.add_handler(CommandHandler("s", s_cmd))
    app_bot.add_handler(CommandHandler("best", best_cmd))
    app_bot.add_handler(CommandHandler("results", results_cmd))
    app_bot.add_handler(CommandHandler("clear", clear_cmd))
    app_bot.run_polling()
