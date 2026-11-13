import os, asyncio, threading, requests, csv, io
asyncio.set_event_loop(asyncio.new_event_loop())
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

app_web = Flask(__name__)
@app_web.route('/')
def home(): return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()
TOKEN = os.environ.get("BOT_TOKEN")

def calc_rsi(closes, period=14):
    if len(closes) < period+1:
        return 50
    gains, losses = [], []
    for i in range(1, period+1):
        diff = closes[-i] - closes[-i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains)/period
    avg_loss = sum(losses)/period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def get_analysis(symbol):
    try:
        symbol = symbol.upper().strip()
        # Stooq - شغال بدون حظر
        url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        if "Date" not in r.text or len(r.text) < 100:
            return f"ما لقيت السهم {symbol} ❌\nجرب: /s AAPL\nتأكد انه سهم أمريكي"
        
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        rows = [x for x in rows if x['Close'] != 'N/D']
        if len(rows) < 10:
            return f"ما لقيت بيانات كافية لـ {symbol}"

        closes = [float(x['Close']) for x in rows]
        highs = [float(x['High']) for x in rows]
        lows = [float(x['Low']) for x in rows]

        price = closes[-1]
        prev = closes[-2]
        change = price - prev
        change_pct = (change / prev) * 100

        sma20 = sum(closes[-20:]) / min(20, len(closes))
        sma50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 50 else sum(closes)/len(closes)
        rsi = calc_rsi(closes)

        support = min(lows[-20:])
        resistance = max(highs[-20:])
        change_5d = ((closes[-1] - closes[-6]) / closes[-6]) * 100 if len(closes) >= 6 else 0
        change_1m = ((closes[-1] - closes[-22]) / closes[-22]) * 100 if len(closes) >= 22 else 0

        if price > sma20 and price > sma50 and rsi >= 50:
            trend = "صاعد 📈"
            decision = "الاتجاه العام صاعد، الشراء مع أي نزول للدعم أفضل"
        elif price < sma20 and price < sma50 and rsi < 50:
            trend = "هابط 📉"
            decision = "الاتجاه العام هابط، لا تشتري الا اذا اخترق المقاومة"
        else:
            trend = "متذبذب ↔️"
            decision = "السهم متذبذب بين الدعم والمقاومة، انتظر اختراق"

        if rsi >= 70:
            rsi_txt = f"{rsi:.0f} - تشبع شراء ⚠️"
        elif rsi <= 30:
            rsi_txt = f"{rsi:.0f} - تشبع بيع 🟢"
        else:
            rsi_txt = f"{rsi:.0f} - طبيعي"

        if abs(change_pct) >= 3:
            power = "حركة قوية اليوم 🔥"
        elif abs(change_pct) >= 1:
            power = "حركة متوسطة"
        else:
            power = "حركة هادئة"

        msg = f"""📊 تحليل {symbol}

💰 السعر: {price:.2f}$
📈 اليوم: {change:+.2f}$ ({change_pct:+.2f}%) - {power}
📅 5 أيام: {change_5d:+.2f}%
📅 شهر: {change_1m:+.2f}%

🔍 الاتجاه: {trend}
📏 متوسط 20 يوم: {sma20:.2f}$
📏 متوسط 50 يوم: {sma50:.2f}$
📊 RSI: {rsi_txt}

🟢 دعم: {support:.2f}$
🔴 مقاومة: {resistance:.2f}$

💡 الخلاصة:
{decision}
"""
        return msg
    except Exception as e:
        return f"صار خطأ، جرب مرة ثانية: /s {symbol}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("شغال ✅\nارسل /s NVDA")

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ارسل كذا: /s NVDA")
        return
    msg = get_analysis(context.args[0])
    await update.message.reply_text(msg)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if parts[0].lower() in ["/s", "s"] and len(parts) > 1:
        msg = get_analysis(parts[1])
        await update.message.reply_text(msg)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("s", s_cmd))
app.add_handler(MessageHandler(filters.TEXT, handle_text))
app.run_polling()
