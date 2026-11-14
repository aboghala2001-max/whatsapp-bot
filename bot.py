import os, datetime, pytz, threading, asyncio
from flask import Flask
import yfinance as yf
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")
print("TOKEN EXISTS:", bool(TOKEN), flush=True)

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running"

TICKERS = ["NVDA","TSLA","META","AAPL","AMD","PLTR","COIN","MSFT","AMZN","GOOGL","NFLX","AVGO","MU","SMCI","ARM","SHOP","SQ","PYPL","UBER","ABNB","CRWD","PANW","MRVL","INTC","QCOM","TXN","ADBE","ORCL","CRM","BA","DIS","NKE","XOM"]

def check_time():
    tz = pytz.timezone("Asia/Riyadh")
    now = datetime.datetime.now(tz)
    mins = now.hour * 60 + now.minute
    if now.weekday() >= 5: return False, "ويكند"
    if 16*60+30 <= mins < 24*60: return True, "مفتوح"
    return False, "خارج الوقت - يفتح 4:30 العصر الى 12 الليل"

def check_signal(ticker):
    ok, msg = check_time()
    if not ok: return f"⏳ انتظار - {msg}"
    try:
        stock = yf.Ticker(ticker)
        df15 = stock.history(period="1d", interval="15m")
        df1h = stock.history(period="2d", interval="60m")
        df3d = stock.history(period="5d", interval="1d")
        if len(df15) < 2 or len(df1h) < 2 or len(df3d) < 3: return "⏳ انتظار - ما فيه بيانات"
        price = float(df15['Close'].iloc[-1])
        mom15 = (df15['Close'].iloc[-1] - df15['Close'].iloc[-2]) / df15['Close'].iloc[-2] * 100
        mom1h = (df1h['Close'].iloc[-1] - df1h['Close'].iloc[-2]) / df1h['Close'].iloc[-2] * 100
        trend3d = (df3d['Close'].iloc[-1] - df3d['Close'].iloc[-3]) / df3d['Close'].iloc[-3] * 100
        typical = (df15['High'] + df15['Low'] + df15['Close']) / 3
        vwap = float((typical * df15['Volume']).sum() / df15['Volume'].sum())
        spy = yf.Ticker("SPY").history(period="1d", interval="15m")
        spy_mom = (spy['Close'].iloc[-1] - spy['Close'].iloc[-2]) / spy['Close'].iloc[-2] * 100
        avg_vol = float(df15['Volume'].mean()); last_vol = float(df15['Volume'].iloc[-1])
        vol_ok = last_vol >= avg_vol * 1.2
        delta = df15['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / loss))
        rsi_val = float(rsi.iloc[-1]) if not rsi.isna().all() else 50
        direction = None
        if mom15 >= 0.50 and mom1h > 0 and trend3d > 0 and price > vwap and spy_mom > 0 and vol_ok: direction = "CALL"
        elif mom15 <= -0.50 and mom1h < 0 and trend3d < 0 and price < vwap and spy_mom < 0 and vol_ok: direction = "PUT"
        else: return f"⏳ انتظار ⚠️\n{ticker} | سعر {price:.2f}$ | زخم {mom15:.2f}% | VWAP {vwap:.2f}$ | SPY {spy_mom:.2f}% | RSI {rsi_val:.0f}"
        exps = stock.options
        if not exps: return f"✅ {direction} {ticker}\nسعر {price:.2f}$ | RSI {rsi_val:.0f}"
        opt = stock.option_chain(exps[0])
        chain = opt.calls if direction == "CALL" else opt.puts
        chain = chain[(chain['lastPrice'] >= 0.50) & (chain['lastPrice'] <= 2.50)]
        if chain.empty: return f"✅ {direction} {ticker} - إشارة قوية بس ما فيه عقد بين 0.50 و 2.50"
        chain = chain.copy(); chain['diff'] = abs(chain['strike'] - price)
        best = chain.sort_values('diff').iloc[0]
        entry = float(best['lastPrice']); stop = entry * 0.70; t1 = entry * 1.50; t2 = entry * 2.00
        return f"✅ {direction} {ticker} {int(best['strike'])} | {exps[0]}\n💰 دخول: {entry:.2f}$\n🛑 وقف: {stop:.2f}$ (-30%)\n🎯 هدف1: {t1:.2f}$ (+50%)\n🎯 هدف2: {t2:.2f}$ (+100%)\nسعر {price:.2f}$ | RSI {rsi_val:.0f} | زخم {mom15:.2f}%"
    except Exception as e: return f"❌ خطأ: {e}"

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا 👋\n/s META - فحص سهم\n/best - أفضل الفرص\nيفتح 4:30 العصر الى 12 الليل")

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب: /s META"); return
    t = context.args[0].upper()
    await update.message.reply_text("🔍 جاري الفحص...")
    await update.message.reply_text(check_signal(t))

async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 جاري فحص كل الأسهم...")
    res = [check_signal(t) for t in TICKERS]
    res = [r for r in res if r.startswith("✅")]
    await update.message.reply_text("\n\n---\n\n".join(res[:5]) if res else "⏳ ما فيه فرص قوية الحين")

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start_cmd))
    app_bot.add_handler(CommandHandler("s", s_cmd))
    app_bot.add_handler(CommandHandler("best", best_cmd))
    print("POLLING START", flush=True)
    app_bot.run_polling()
