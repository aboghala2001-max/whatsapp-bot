import os, threading, logging
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

WATCHLIST = {}
ALERTED = {}

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
        sma200 = float(close.rolling(200).mean().iloc[-1])
        rsi = float(rsi_calc(close).iloc[-1])
        vwap = float(close.tail(20).mean())
        mom15 = float((close.iloc[-1] / close.iloc[-16] - 1) * 100) if len(close) > 16 else 0
        support = float(close.tail(30).min())
        resist = float(close.tail(30).max())

        # --- منطق الفرص القوية ---
        # CALL قوية جدا
        if price > vwap and 45 <= rsi <= 65 and mom15 > 0.5 and price > sma50:
            option = "CALL 📈"
            strength = "قوية جدا 🔥"
            duration = "15 - 30 دقيقة"
            entry = price
            target = price * 1.02
            stop = price * 0.99
            reason = "فوق VWAP + RSI متوازن + زخم صاعد"
        # CALL قوية
        elif price > vwap and 40 <= rsi <= 68 and mom15 > 0:
            option = "CALL 📈"
            strength = "قوية 💪"
            duration = "30 - 60 دقيقة"
            entry = price
            target = price * 1.015
            stop = price * 0.988
            reason = "فوق VWAP + زخم صاعد"
        # PUT قوية جدا
        elif price < vwap and 35 <= rsi <= 55 and mom15 < -0.5 and price < sma50:
            option = "PUT 📉"
            strength = "قوية جدا 🔥"
            duration = "15 - 30 دقيقة"
            entry = price
            target = price * 0.98
            stop = price * 1.01
            reason = "تحت VWAP + RSI نازل + زخم هابط"
        # PUT قوية
        elif price < vwap and 32 <= rsi <= 60 and mom15 < 0:
            option = "PUT 📉"
            strength = "قوية 💪"
            duration = "30 - 60 دقيقة"
            entry = price
            target = price * 0.985
            stop = price * 1.012
            reason = "تحت VWAP + زخم هابط"
        # متوسطة
        elif rsi > 70 or rsi < 30:
            option = "انتظار ⚠️"
            strength = "متوسطة"
            duration = "60 - 120 دقيقة"
            entry = price
            target = resist if rsi < 30 else support
            stop = support if rsi < 30 else resist
            reason = "متشبع - انتظر ارتداد"
        else:
            option = "انتظار ⚠️"
            strength = "ضعيفة"
            duration = "لا تدخل"
            entry = price
            target = resist
            stop = support
            reason = "لا يوجد تأكيد"

        return {'price': price, 'change': change, 'rsi': rsi, 'vwap': vwap, 'mom15': mom15, 'option': option, 'strength': strength, 'duration': duration, 'entry': entry, 'target': target, 'stop': stop, 'reason': reason, 'support': support, 'resist': resist}
    except Exception as e:
        print(f"Error {ticker}: {e}")
        return None

def analyze(ticker):
    sig = get_signal(ticker)
    if not sig: return None
    if "انتظار" in sig['option']:
        return f"📊 *{ticker.upper()}*\n💰 السعر: ${sig['price']:.2f} ({sig['change']:+.2f}%)\n🎯 {sig['option']} - {sig['strength']}\n⏱️ المدة: {sig['duration']}\n📉 RSI: {sig['rsi']:.0f} | VWAP: ${sig['vwap']:.1f}\n⚡ زخم: {sig['mom15']:+.2f}%\n💡 السبب: {sig['reason']}\n🛡️ دعم: ${sig['support']:.1f} | 🎯 مقاومة: ${sig['resist']:.1f}"
    return f"🚨 *فرصة {sig['strength']}*\n📊 *{ticker.upper()}*\n🎯 {sig['option']}\n💰 دخول: ${sig['entry']:.2f}\n🎯 هدف: ${sig['target']:.2f}\n🛑 وقف: ${sig['stop']:.2f}\n⏱️ المدة: {sig['duration']}\n📉 RSI: {sig['rsi']:.0f} | VWAP: ${sig['vwap']:.1f}\n⚡ زخم: {sig['mom15']:+.2f}%\n💡 السبب: {sig['reason']}\n🛡️ دعم: ${sig['support']:.1f} | 🎯 مقاومة: ${sig['resist']:.1f}"

async def auto_alert(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, symbols in list(WATCHLIST.items()):
        for sym in symbols:
            sig = get_signal(sym)
            if not sig: continue
            if sig['strength'] not in ["قوية جدا 🔥", "قوية 💪"]:
                continue
            key = f"{chat_id}_{sym}_{sig['option']}"
            last_price = ALERTED.get(key, 0)
            if abs(sig['price'] - last_price) < 1:
                continue
            ALERTED[key] = sig['price']
            msg = f"🚨 فرصة {sig['strength']}\n{sym} - {sig['option']}\n💰 دخول: ${sig['entry']:.2f}\n🎯 هدف: ${sig['target']:.2f} | 🛑 وقف: ${sig['stop']:.2f}\n⏱️ المدة: {sig['duration']}\n💡 {sig['reason']}"
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg)
            except Exception as e:
                print(e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("هلا فيك 👋\nارسل /s AAPL لتحليل أي سهم\nالفرص القوية بس هي اللي تجيك تنبيه")

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ارسل كذا: /s NVDA"); return
    ticker = context.args[0].upper()
    await update.message.reply_text(f"جالس أحلل {ticker} ... ⏳")
    res = analyze(ticker)
    if not res: await update.message.reply_text("ما لقيت السهم")
    else: await update.message.reply_text(res, parse_mode="Markdown")

async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for t in ["AAPL","NVDA","TSLA","MSFT"]:
        res = analyze(t)
        if res: await update.message.reply_text(res, parse_mode="Markdown")

def run_bot():
    if not TOKEN:
        print("❌ BOT_TOKEN missing"); return
    print("✅ Bot starting polling...")
    app_ = ApplicationBuilder().token(TOKEN).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CommandHandler("s", stock))
    app_.add_handler(CommandHandler("watch", watch))
    if app_.job_queue:
        app_.job_queue.run_repeating(auto_alert, interval=300, first=10)
    app_.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=lambda: app_web.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    run_bot()
