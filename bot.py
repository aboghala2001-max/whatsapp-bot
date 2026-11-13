import os, threading, logging, time
import yfinance as yf
import pandas as pd
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("BOT_TOKEN")
print(f"TOKEN FOUND: {bool(TOKEN)}")

app = Flask(__name__)
@app.route('/')
def home(): return "Bot is alive!"

def rsi_calc(data, period=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def analyze(ticker):
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
    support = float(close.tail(30).min())
    resist = float(close.tail(30).max())
    if price > sma50 > sma200: trend = "صاعد"
    elif price < sma50 < sma200: trend = "هابط"
    else: trend = "متذبذب"
    if rsi > 70: rsi_txt = f"{rsi:.0f} (متشبع)"
    elif rsi < 30: rsi_txt = f"{rsi:.0f} (رخيص)"
    else: rsi_txt = f"{rsi:.0f} (متوازن)"
    if trend.startswith("صاعد") and rsi < 65: advice = "الزبدة: ترند صاعد، دخول مع أي نزول قرب الدعم."
    elif rsi > 70: advice = "الزبدة: السهم حامي، لا تدخل الحين."
    elif trend.startswith("هابط"): advice = "الزبدة: لا تعاكس الترند، انتظار أفضل."
    else: advice = "الزبدة: انتظار، لا دخول ولا خروج الحين."
    return f"السهم: {ticker.upper()}\nالسعر: ${price:.2f} ({change:+.2f}%)\nالاتجاه: {trend}\nRSI: {rsi_txt}\nدعم: ${support:.1f} | مقاومة: ${resist:.1f}\n\n{advice}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("هلا فيك\nارسل /s AAPL لتحليل أي سهم\nارسل /watch لتحليل أسهمك الرئيسية")

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ارسل كذا: /s NVDA"); return
    ticker = context.args[0].upper()
    await update.message.reply_text(f"جالس أحلل {ticker} ...")
    res = analyze(ticker)
    if not res: await update.message.reply_text("ما لقيت السهم، تأكد من الرمز.")
    else: await update.message.reply_text(res)

async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for t in ["AAPL","NVDA","TSLA","MSFT"]:
        res = analyze(t)
        if res: await update.message.reply_text(res)

def run_bot():
    if not TOKEN:
        print("ERROR: BOT_TOKEN is missing")
        return
    while True:
        try:
            print("Bot starting polling...")
            app_ = ApplicationBuilder().token(TOKEN).build()
            app_.add_handler(CommandHandler("start", start))
            app_.add_handler(CommandHandler("s", stock))
            app_.add_handler(CommandHandler("watch", watch))
            app_.run_polling(drop_pending_updates=True, stop_signals=None)
        except Exception as e:
            print(f"Bot crashed, retry in 5 sec: {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    run_bot()
