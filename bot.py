import os, datetime, pytz, threading
print("=== BOT START ===", flush=True)
TOKEN = os.getenv("BOT_TOKEN")
print("TOKEN EXISTS:", bool(TOKEN), "LEN:", len(TOKEN) if TOKEN else 0, flush=True)

import telebot, yfinance as yf
from flask import Flask
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running"

bot = telebot.TeleBot(TOKEN)
print("TELEBOT OK", flush=True)

TICKERS = ["NVDA","TSLA","META","AAPL","AMD","PLTR","COIN","MSFT","AMZN","GOOGL","NFLX","AVGO","MU","SMCI","ARM","SHOP","SQ","PYPL","UBER","ABNB","CRWD","PANW","MRVL","INTC","QCOM","TXN","ADBE","ORCL","CRM","BA","DIS","NKE","XOM"]
MIN_PRICE = 0.50
MAX_PRICE = 2.50

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
        chain = chain[(chain['lastPrice'] >= MIN_PRICE) & (chain['lastPrice'] <= MAX_PRICE)]
        if chain.empty: return f"✅ {direction} {ticker} - إشارة قوية بس ما فيه عقد بين 0.50 و 2.50"
        chain = chain.copy(); chain['diff'] = abs(chain['strike'] - price)
        best = chain.sort_values('diff').iloc[0]
        entry = float(best['lastPrice']); stop = entry * 0.70; t1 = entry * 1.50; t2 = entry * 2.00
        return f"✅ {direction} {ticker} {int(best['strike'])} | {exps[0]}\n💰 دخول: {entry:.2f}$\n🛑 وقف: {stop:.2f}$ (-30%)\n🎯 هدف1: {t1:.2f}$ (+50%)\n🎯 هدف2: {t2:.2f}$ (+100%)\nسعر {price:.2f}$ | RSI {rsi_val:.0f} | زخم {mom15:.2f}%"
    except Exception as e: return f"❌ خطأ: {e}"

@bot.message_handler(commands=['s'])
def handle_s(m):
    try:
        t = m.text.split()[1].upper(); bot.reply_to(m, "🔍 جاري الفحص..."); bot.reply_to(m, check_signal(t))
    except: bot.reply_to(m, "اكتب: /s META")
@bot.message_handler(commands=['best'])
def handle_best(m):
    bot.reply_to(m, "🔍 جاري فحص كل الأسهم...")
    res = [check_signal(t) for t in TICKERS]
    res = [r for r in res if r.startswith("✅")]
    bot.reply_to(m, "\n\n---\n\n".join(res[:5]) if res else "⏳ ما فيه فرص قوية الحين")
@bot.message_handler(commands=['start'])
def handle_start(m): bot.reply_to(m, "أهلا 👋\n/s META - فحص سهم\n/best - أفضل الفرص\nيفتح 4:30 العصر الى 12 الليل")

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    print("POLLING START", flush=True)
    bot.infinity_polling()
