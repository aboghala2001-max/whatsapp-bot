import os, threading, logging, time
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

DEFAULT_SYMBOLS = ["AMZN","NVDA","AAPL","TSLA","MSFT","GOOGL","QQQ","SPY","META","XSP"]
WATCHLIST = {}
ALERTED = {}

# === جديد: منع التكرار + التقييم ===
ACTIVE_SIGNALS = {}  # { "chatid_SYMBOL": {entry,target,stop,option,time} }
HISTORY = []  # كل الفرص اللي خلصت
# HISTORY = [{"symbol": "NVDA", "result": "win"/"loss", "option": "CALL"}]

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
        rsi = float(rsi_calc(close).iloc[-1])
        vwap = float(close.tail(20).mean())
        mom15 = float((close.iloc[-1] / close.iloc[-16] - 1) * 100) if len(close) > 16 else 0
        support = float(close.tail(30).min())
        resist = float(close.tail(30).max())

        if price > vwap and 45 <= rsi <= 65 and mom15 > 0.5 and price > sma50:
            option = "CALL 📈"; strength = "قوية جدا 🔥"; duration = "15 - 30 دقيقة"
            entry = price; target = price * 1.02; stop = price * 0.99
            reason = "فوق VWAP + RSI متوازن + زخم صاعد"
        elif price > vwap and 40 <= rsi <= 68 and mom15 > 0:
            option = "CALL 📈"; strength = "قوية 💪"; duration = "30 - 60 دقيقة"
            entry = price; target = price * 1.015; stop = price * 0.988
            reason = "فوق VWAP + زخم صاعد"
        elif price < vwap and 35 <= rsi <= 55 and mom15 < -0.5 and price < sma50:
            option = "PUT 📉"; strength = "قوية جدا 🔥"; duration = "15 - 30 دقيقة"
            entry = price; target = price * 0.98; stop = price * 1.01
            reason = "تحت VWAP + RSI نازل + زخم هابط"
        elif price < vwap and 32 <= rsi <= 60 and mom15 < 0:
            option = "PUT 📉"; strength = "قوية 💪"; duration = "30 - 60 دقيقة"
            entry = price; target = price * 0.985; stop = price * 1.012
            reason = "تحت VWAP + زخم هابط"
        elif rsi > 70 or rsi < 30:
            option = "انتظار ⚠️"; strength = "متوسطة"; duration = "60 - 120 دقيقة"
            entry = price; target = resist if rsi < 30 else support; stop = support if rsi < 30 else resist
            reason = "متشبع - انتظر ارتداد"
        else:
            option = "انتظار ⚠️"; strength = "ضعيفة"; duration = "لا تدخل"
            entry = price; target = resist; stop = support; reason = "لا يوجد تأكيد"

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
            # 1. شيك هل فيه فرصة شغالة لنفس السهم؟ اذا فيه قيمها
            active_key = f"{chat_id}_{sym}"
            if active_key in ACTIVE_SIGNALS:
                old = ACTIVE_SIGNALS[active_key]
                sig_now = get_signal(sym)
                if not sig_now: continue
                cur = sig_now['price']
                result = None
                # CALL: وصل الهدف او ضرب الستوب
                if "CALL" in old['option']:
                    if cur >= old['target']: result = "win"
                    elif cur <= old['stop']: result = "loss"
                else: # PUT
                    if cur <= old['target']: result = "win"
                    elif cur >= old['stop']: result = "loss"
                # لو مر 30 دقيقة (1800 ثانية) بدون نتيجة = انتهت
                if result is None and (time.time() - old['time'] > 1800):
                    result = "timeout"
                if result:
                    if result in ["win","loss"]:
                        HISTORY.append({"symbol": sym, "result": result, "option": old['option']})
                        emoji = "✅ نجحت" if result == "win" else "❌ فشلت"
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=f"{emoji} - فرصة {sym} خلصت\nدخول: ${old['entry']:.2f} | السعر الحالي: ${cur:.2f}\nالحين اقدر ارسل لك فرصة جديدة على {sym} اذا جت 🔥")
                        except: pass
                    del ACTIVE_SIGNALS[active_key]
                    # لو فشلت او نجحت كمل ولا ترسل فرصة جديدة بنفس اللفة، انتظر اللفة الجاية
                    continue
                else:
                    # فيه فرصة شغالة للحين، لا تكرر
                    continue

            # 2. لو مافيه فرصة شغالة، دور على فرصة جديدة
            sig = get_signal(sym)
            if not sig: continue
            if sig['strength'] not in ["قوية جدا 🔥", "قوية 💪"]:
                continue
            key = f"{chat_id}_{sym}_{sig['option']}"
            last_price = ALERTED.get(key, 0)
            if abs(sig['price'] - last_price) < 1:
                continue
            ALERTED[key] = sig['price']
            # سجلها كفرصة شغالة عشان ما تتكرر
            ACTIVE_SIGNALS[active_key] = {"entry": sig['entry'], "target": sig['target'], "stop": sig['stop'], "option": sig['option'], "time": time.time()}
            msg = f"🚨 فرصة {sig['strength']}\n{sym} - {sig['option']}\n💰 دخول: ${sig['entry']:.2f}\n🎯 هدف: ${sig['target']:.2f} | 🛑 وقف: ${sig['stop']:.2f}\n⏱️ المدة: {sig['duration']}\n💡 {sig['reason']}\n\n⏳ هذي الفرصة شغالة، ما بجيك فرصة ثانية على {sym} الا لما تخلص"
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg)
            except Exception as e:
                print(e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    WATCHLIST[chat_id] = DEFAULT_SYMBOLS.copy()
    await update.message.reply_text("هلا فيك 👋\nسجلتك في التنبيه التلقائي ✅\nبيراقب لك:\nAMZN, NVDA, AAPL, TSLA, MSFT, GOOGL, QQQ, SPY, META, XSP\n\nأي فرصة قوية بتجيك لحالها 🔥\nوتقدر تسأل عن أي سهم بـ /s NVDA\nوتشوف تقييم البوت بـ /stats")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HISTORY:
        await update.message.reply_text("📊 للحين مافيه فرص خلصت عشان اقيمها\nانتظر اول فرصة تخلص وبيجيك التقييم هنا")
        return
    total = len(HISTORY)
    wins = len([h for h in HISTORY if h['result'] == "win"])
    losses = total - wins
    rate = wins / total * 100 if total else 0
    # افضل سهم
    txt = f"📊 *تقييم البوت لنفسه*\n\n"
    txt += f"إجمالي الفرص: {total}\n✅ نجحت: {wins}\n❌ فشلت: {losses}\n🎯 نسبة النجاح: {rate:.0f}%\n\n"
    # حسب السهم
    for sym in DEFAULT_SYMBOLS:
        sh = [h for h in HISTORY if h['symbol'] == sym]
        if sh:
            sw = len([h for h in sh if h['result']=="win"])
            txt += f"{sym}: {sw}/{len(sh)} نجحت ({sw/len(sh)*100:.0f}%)\n"
    # الفرص الشغالة الحين
    if ACTIVE_SIGNALS:
        txt += f"\n⏳ فرص شغالة الحين: {len(ACTIVE_SIGNALS)}"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ارسل كذا: /s NVDA"); return
    ticker = context.args[0].upper()
    await update.message.reply_text(f"جالس أحلل {ticker} ... ⏳")
    res = analyze(ticker)
    if not res: await update.message.reply_text("ما لقيت السهم")
    else: await update.message.reply_text(res, parse_mode="Markdown")

async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for t in DEFAULT_SYMBOLS:
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
    app_.add_handler(CommandHandler("stats", stats))
    if app_.job_queue:
        app_.job_queue.run_repeating(auto_alert, interval=300, first=10)
    app_.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=lambda: app_web.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    run_bot()
