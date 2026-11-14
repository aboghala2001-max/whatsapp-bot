import yfinance as yf
import datetime
import pytz
import os
import telebot
from flask import Flask
import threading

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running"

TICKERS = ["NVDA","TSLA","META","AAPL","AMD","PLTR","COIN","MSFT","AMZN","GOOGL","NFLX","AVGO","MU","SMCI","ARM","SHOP","SQ","PYPL","UBER","ABNB","CRWD","PANW","MRVL","INTC","QCOM","TXN","ADBE","ORCL","CRM","BA","DIS","NKE","XOM"]
MIN_PRICE = 0.50
MAX_PRICE = 2.50

def check_time():
    tz = pytz.timezone("Asia/Riyadh")
    now = datetime.datetime.now(tz)
    mins = now.hour * 60 + now.minute
    open_time = 16*60+30
    close_time = 24*60
    if now.weekday() >= 5:
        return False, "ويكند - السوق مسكر"
    if open_time <= mins < close_time:
        return True, "مفتوح"
    return False, "خارج وقت التداول - البوت يفتح 4:30 العصر"

def check_signal(ticker):
    ok, msg = check_time()
    if not ok:
        return f"⏳ انتظار - {msg}"
    try:
        stock = yf.Ticker(ticker)
        df15 = stock.history(period="1d", interval="15m")
        df1h = stock.history(period="2d", interval="60m")
        df3d = stock.history(period="5d", interval="1d")
        if len(df15) < 2 or len(df1h) < 2 or len(df3d) < 3:
            return "⏳ انتظار - ما فيه بيانات كافية"
        price = float(df15['Close'].iloc[-1])
        mom15 = (df15['Close'].iloc[-1] - df15['Close'].iloc[-2]) / df15['Close'].iloc[-2] * 100
        mom1h = (df1h['Close'].iloc[-1] - df1h['Close'].iloc[-2]) / df1h['Close'].iloc[-2] * 100
        trend3d = (df3d['Close'].iloc[-1] - df3d['Close'].iloc[-3]) / df3d['Close'].iloc[-3] * 100
        typical = (df15['High'] + df15['Low'] + df15['Close']) / 3
        vwap = float((typical * df15['Volume']).sum() / df15['Volume'].sum())
        spy = yf.Ticker("SPY").history(period="1d", interval="15m")
        spy_mom = (spy['Close'].iloc[-1] - spy['Close'].iloc[-2]) / spy['Close'].iloc[-2] * 100
        avg_vol = float(df15['Volume'].mean())
        last_vol = float(df15['Volume'].iloc[-1])
        vol_ok = last_vol >= avg_vol * 1.2
        delta = df15['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not rsi.isna().all() else 50
        direction = None
        if mom15 >= 0.50 and mom1h > 0 and trend3d > 0 and price > vwap and spy_mom > 0 and vol_ok:
            direction = "CALL"
        elif mom15 <= -0.50 and mom1h < 0 and trend3d < 0 and price < vwap and spy_mom < 0 and vol_ok:
            direction = "PUT"
        else:
            return f"⏳ انتظار ⚠️\n{ticker} | سعر {price:.2f}$ | زخم 15د {mom15:.2f}% | VWAP {vwap:.2f}$ | SPY {spy_mom:.2f}% | RSI {rsi_val:.0f} | حجم {'✅' if vol_ok else '❌'}"
        try:
            exps = stock.options
            if not exps:
                return f"✅ {direction} {ticker}\nسعر {price:.2f}$ | RSI {rsi_val:.0f} | زخم {mom15:.2f}%"
            opt = stock.option_chain(exps[0])
            chain = opt.calls if direction == "CALL" else opt.puts
            chain = chain[(chain['lastPrice'] >= MIN_PRICE) & (chain['lastPrice'] <= MAX_PRICE)]
            if chain.empty:
                return f"✅ {direction} {ticker} - إشارة قوية بس ما فيه عقد بين 0.50 و 2.50\nسعر {price:.2f}$ | RSI {rsi_val:.0f}"
            chain = chain.copy()
            chain['diff'] = abs(chain['strike'] - price)
            best = chain.sort_values('diff').iloc[0]
            entry = float(best['lastPrice'])
            stop = entry * 0.70
            t1 = entry * 1.50
            t2 = entry * 2.00
            return (f"✅ {direction} {ticker} {int(best['strike'])} | {exps[0]}\n"
                    f"💰 دخول: {entry:.2f}$\n"
                    f"🛑 وقف: {stop:.2f}$ (-30%)\n"
                    f"🎯 هدف1: {t1:.2f}$ (+50%)\n"
                    f"🎯 هدف2: {t2:.2f}$ (+100%)\n"
                    f"سعر السهم {price:.2f}$ | RSI {rsi_val:.0f} | زخم {mom15:.2f}%")
        except Exception as e:
            return f"✅ {direction} {ticker}\nسعر {price:.2f}$ | RSI {rsi_val:.0f} | زخم {mom15:.2f}%"
    except Exception as e:
        return f"❌ خطأ: {e}"

@bot.message_handler(commands=['s'])
def handle_s(message):
    try:
        ticker = message.text.split()[1].upper()
        bot.reply_to(message, "🔍 جاري الفحص...")
        bot.reply_to(message, check_signal(ticker))
    except:
        bot.reply_to(message, "اكتب كذا: /s META")

@bot.message_handler(commands=['best'])
def handle_best(message):
    bot.reply_to(message, "🔍 جاري فحص كل الأسهم...")
    results = []
    for t in TICKERS:
        r = check_signal(t)
        if r.startswith("✅"):
            results.append(r)
    if results:
        bot.reply_to(message, "\n\n---\n\n".join(results[:5]))
    else:
        bot.reply_to(message, "⏳ ما فيه فرص قوية الحين، انتظر")

@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.reply_to(message, "أهلا 👋\n/s META - فحص سهم\n/best - أفضل الفرص\nالبوت يفتح من 4:30 العصر الى 12:00 الليل")

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    bot.infinity_polling()
