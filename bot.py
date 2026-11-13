import os, threading, logging, time, json, asyncio
import yfinance as yf
import pandas as pd
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
TOKEN = os.environ.get("BOT_TOKEN")
app_web = Flask(__name__)

@app_web.route('/')
def home(): return "Bot is running"

DEFAULT_SYMBOLS = ["AMZN","NVDA","AAPL","TSLA","MSFT","GOOGL","QQQ","SPY","META","XSP"]

CONTRACTS = 2
COMMISSION_PER_CONTRACT = 0.99
COMMISSION_MIN = 1.99
VAT = 0.15
COST_TOTAL = (max(COMMISSION_MIN, COMMISSION_PER_CONTRACT * CONTRACTS) * 2) * (1 + VAT)
TARGET_NET_PROFIT = 5.0 * CONTRACTS
MIN_PROFIT_TOTAL = COST_TOTAL + TARGET_NET_PROFIT
MIN_PREMIUM_MOVE = MIN_PROFIT_TOTAL / (CONTRACTS * 100)
DELTA = 0.35
MIN_STOCK_MOVE = MIN_PREMIUM_MOVE / DELTA
REVERSAL_PCT = 0.004
TIMEOUT_SEC = 3600  # 60 دقيقة بدل 30

VOLATILITY = {
    "SPY": (0.005, 0.0035),
    "QQQ": (0.005, 0.0035),
    "MSFT": (0.005, 0.0035),
    "AAPL": (0.006, 0.004),
    "GOOGL": (0.006, 0.004),
    "XSP": (0.005, 0.0035),
    "AMZN": (0.008, 0.005),
    "NVDA": (0.009, 0.005),
    "META": (0.008, 0.005),
    "TSLA": (0.011, 0.007),
}
def get_tp_sl(ticker):
    return VOLATILITY.get(ticker.upper(), (0.007, 0.0045))

def load_json(name, default):
    try:
        if os.path.exists(name):
            with open(name, 'r') as f:
                data = json.load(f)
                return data if data is not None else default
    except Exception as e:
        print(f"Load {name} error: {e}")
    return default

def save_json(name, data):
    try:
        with open(name, 'w') as f: json.dump(data, f)
    except Exception as e:
        print(f"Save {name} error: {e}")

WATCHLIST = {int(k): v for k, v in load_json("watchlist.json", {}).items()}
ALERTED = {}
ACTIVE_SIGNALS = {k: v for k, v in load_json("active.json", {}).items()}
HISTORY = load_json("history.json", [])

def rsi_calc(data, period=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_live_price(ticker):
    try:
        df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return float(df['Close'].iloc[-1])
    except Exception as e:
        print(f"Live price {ticker} error: {e}")
        return None

def get_signal(ticker):
    try:
        # ===== التعديل الجديد: شمعات 5 دقايق لآخر 5 ايام =====
        df = yf.download(ticker, period="5d", interval="5m", progress=False, auto_adjust=True)
        if df.empty or len(df) < 50: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        close = df['Close']
        high = df['High']
        low = df['Low']
        vol = df['Volume'] if 'Volume' in df.columns else pd.Series([1]*len(df), index=df.index)
        price = float(close.iloc[-1])
        live = get_live_price(ticker)
        if live: price = live
        prev = float(close.iloc[-2])
        change = (price - prev) / prev * 100
        # RSI على 5 دقايق - يعكس اللحظة الحالية مو اليوم كامل
        rsi = float(rsi_calc(close).iloc[-1])
        # VWAP حقيقي بالفوليوم
        typical = (high + low + close) / 3
        vwap = float((typical * vol).cumsum().iloc[-1] / vol.cumsum().iloc[-1]) if vol.sum() > 0 else float(close.tail(20).mean())
        # SMA 20 على 5 دقايق = ترند آخر 100 دقيقة
        sma20 = float(close.rolling(20).mean().iloc[-1])
        # زخم 15 دقيقة = 3 شمعات 5د، وزخم ساعة = 12 شمعة
        mom15 = float((close.iloc[-1] / close.iloc[-4] - 1) * 100) if len(close) > 4 else 0
        mom60 = float((close.iloc[-1] / close.iloc[-13] - 1) * 100) if len(close) > 13 else 0
        # فوليوم: هل الشمعة الأخيرة أقوى من المتوسط؟
        avg_vol = float(vol.tail(20).mean())
        last_vol = float(vol.iloc[-1])
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0
        support = float(close.tail(60).min())
        resist = float(close.tail(60).max())
        tp, sl = get_tp_sl(ticker)

        # ===== شروط أقوى: لازم زخم + فوليوم + فوق VWAP مع بعض =====
        if price > vwap and price > sma20 and 50 <= rsi <= 68 and mom15 > 0.25 and mom60 > 0.2 and vol_ratio > 0.8:
            option = "CALL 📈"; strength = "قوية جدا 🔥"; duration = "30 - 60 دقيقة"
            entry = price; target = price * (1 + tp); stop = price * (1 - sl)
            reason = f"فوق VWAP الحقيقي + RSI {rsi:.0f} + زخم 15د {mom15:+.2f}% + فوليوم قوي"
        elif price > vwap and 45 <= rsi <= 68 and mom15 > 0.15 and mom60 > 0:
            option = "CALL 📈"; strength = "قوية 💪"; duration = "30 - 60 دقيقة"
            entry = price; target = price * (1 + tp * 0.8); stop = price * (1 - sl)
            reason = f"فوق VWAP + زخم صاعد {mom15:+.2f}%"
        elif price < vwap and price < sma20 and 32 <= rsi <= 50 and mom15 < -0.25 and mom60 < -0.2 and vol_ratio > 0.8:
            option = "PUT 📉"; strength = "قوية جدا 🔥"; duration = "30 - 60 دقيقة"
            entry = price; target = price * (1 - tp); stop = price * (1 + sl)
            reason = f"تحت VWAP الحقيقي + RSI {rsi:.0f} + زخم 15د {mom15:+.2f}% + فوليوم قوي"
        elif price < vwap and 32 <= rsi <= 55 and mom15 < -0.15 and mom60 < 0:
            option = "PUT 📉"; strength = "قوية 💪"; duration = "30 - 60 دقيقة"
            entry = price; target = price * (1 - tp * 0.8); stop = price * (1 + sl)
            reason = f"تحت VWAP + زخم هابط {mom15:+.2f}%"
        elif rsi > 72 or rsi < 28:
            option = "انتظار ⚠️"; strength = "متوسطة"; duration = "60 - 120 دقيقة"
            entry = price; target = resist if rsi < 28 else support; stop = support if rsi < 28 else resist
            reason = "متشبع - انتظر ارتداد"
        else:
            option = "انتظار ⚠️"; strength = "ضعيفة"; duration = "لا تدخل"
            entry = price; target = resist; stop = support; reason = f"لا يوجد تأكيد - زخم 15د {mom15:+.2f}% ضعيف"
        return {'price': price, 'change': change, 'rsi': rsi, 'vwap': vwap, 'mom15': mom15, 'mom60': mom60, 'option': option, 'strength': strength, 'duration': duration, 'entry': entry, 'target': target, 'stop': stop, 'reason': reason, 'support': support, 'resist': resist}
    except Exception as e:
        print(f"Error {ticker}: {e}")
        return None

def analyze(ticker):
    sig = get_signal(ticker)
    if not sig: return None
    if "انتظار" in sig['option']:
        return f"📊 *{ticker.upper()}*\n💰 السعر: ${sig['price']:.2f} ({sig['change']:+.2f}%)\n🎯 {sig['option']} - {sig['strength']}\n⏱️ المدة: {sig['duration']}\n📉 RSI(5د): {sig['rsi']:.0f} | VWAP: ${sig['vwap']:.1f}\n⚡ زخم 15د: {sig['mom15']:+.2f}% | زخم ساعة: {sig['mom60']:+.2f}%\n💡 السبب: {sig['reason']}\n🛡️ دعم: ${sig['support']:.1f} | 🎯 مقاومة: ${sig['resist']:.1f}"
    return f"🚨 *فرصة {sig['strength']}*\n📊 *{ticker.upper()}*\n🎯 {sig['option']}\n💰 دخول: ${sig['entry']:.2f}\n🎯 هدف: ${sig['target']:.2f}\n🛑 وقف: ${sig['stop']:.2f}\n⏱️ المدة: {sig['duration']}\n📉 RSI(5د): {sig['rsi']:.0f} | VWAP: ${sig['vwap']:.1f}\n⚡ زخم 15د: {sig['mom15']:+.2f}% | زخم ساعة: {sig['mom60']:+.2f}%\n💡 السبب: {sig['reason']}\n🛡️ دعم: ${sig['support']:.1f} | 🎯 مقاومة: ${sig['resist']:.1f}"

async def check_and_send(bot):
    global ACTIVE_SIGNALS, HISTORY
    ACTIVE_SIGNALS = {k: v for k, v in load_json("active.json", {}).items()}
    HISTORY = load_json("history.json", [])
    for key in list(ACTIVE_SIGNALS.keys()):
        try:
            parts = key.split("_")
            chat_id = int(parts[0]); sym = parts[1]
        except: continue
        old = ACTIVE_SIGNALS.get(key)
        if not old: continue
        cur = get_live_price(sym)
        if not cur:
            sig_now = get_signal(sym)
            if not sig_now: continue
            cur = sig_now['price']
        entry = old['entry']
        move = cur - entry
        is_call = "CALL" in old['option']
        if not old.get('reversed_alerted'):
            reversed_now = False
            if is_call and move <= -entry * REVERSAL_PCT:
                reversed_now = True
                txt_rev = f"⚠️ انعكاس - عقد {sym} CALL قلب عليك\nدخول: ${entry:.2f} | الحالي: ${cur:.2f} ({move:+.2f}$)\nالعقد قاعد يذوب، فكر تطلع الحين قبل الستوب"
            elif not is_call and move >= entry * REVERSAL_PCT:
                reversed_now = True
                txt_rev = f"⚠️ انعكاس - عقد {sym} PUT قلب عليك\nدخول: ${entry:.2f} | الحالي: ${cur:.2f} ({move:+.2f}$)\nالعقد قاعد يذوب، فكر تطلع الحين قبل الستوب"
            if reversed_now:
                old['reversed_alerted'] = True
                ACTIVE_SIGNALS[key] = old
                save_json("active.json", ACTIVE_SIGNALS)
                try: await bot.send_message(chat_id=chat_id, text=txt_rev)
                except Exception as e: print(f"Reversal send error: {e}")
        result = None; reason_txt = ""
        if is_call:
            if cur >= old['target']: result = "win"; reason_txt = "🎯 وصلت الهدف"
            elif cur <= old['stop']: result = "loss"; reason_txt = "🛑 ضربت الستوب"
        else:
            if cur <= old['target']: result = "win"; reason_txt = "🎯 وصلت الهدف"
            elif cur >= old['stop']: result = "loss"; reason_txt = "🛑 ضربت الستوب"
        if result is None and (time.time() - old.get('time', time.time()) > TIMEOUT_SEC):
            result = "timeout"
        if result:
            if result == "timeout":
                if is_call:
                    if move >= MIN_STOCK_MOVE:
                        result = "win"; reason_txt = f"⏳ خلص وقتها (60 دقيقة) - تحرك {move:+.2f}$ ✅ غطى عمولة {CONTRACTS} عقود"
                    else:
                        result = "loss"; reason_txt = f"⏳ خلص وقتها (60 دقيقة) - تحرك {move:+.2f}$ فقط ❌ ما غطى عمولة {CONTRACTS} عقود (لازم {MIN_STOCK_MOVE:.2f}$)"
                else:
                    if move <= -MIN_STOCK_MOVE:
                        result = "win"; reason_txt = f"⏳ خلص وقتها (60 دقيقة) - تحرك {move:+.2f}$ ✅ غطى عمولة {CONTRACTS} عقود"
                    else:
                        result = "loss"; reason_txt = f"⏳ خلص وقتها (60 دقيقة) - تحرك {move:+.2f}$ فقط ❌ ما غطى عمولة {CONTRACTS} عقود (لازم {MIN_STOCK_MOVE:.2f}$)"
            HISTORY.append({"symbol": sym, "result": result, "option": old['option'], "entry": entry, "exit": cur, "move": move, "time": old.get('time', time.time())})
            save_json("history.json", HISTORY)
            emoji = "✅ نجحت" if result == "win" else "❌ فشلت"
            try:
                await bot.send_message(chat_id=chat_id, text=f"{emoji} - عقد {sym} خلصت\n{reason_txt}\nدخول: ${entry:.2f} | السعر الحالي: ${cur:.2f} | الحركة: {move:+.2f}$\nعمولة سهم لـ {CONTRACTS} عقود: ${COST_TOTAL:.2f}\nالحين اقدر ارسل لك فرصة جديدة على {sym} اذا جت 🔥\nشوف تقييمي بـ /stats")
            except Exception as e: print(f"Send eval error: {e}")
            if key in ACTIVE_SIGNALS:
                del ACTIVE_SIGNALS[key]
            save_json("active.json", ACTIVE_SIGNALS)

async def auto_alert(context: ContextTypes.DEFAULT_TYPE):
    await check_and_send(context.bot)
    for chat_id, symbols in list(WATCHLIST.items()):
        for sym in symbols:
            active_key = f"{chat_id}_{sym}"
            if active_key in ACTIVE_SIGNALS: continue
            sig = get_signal(sym)
            if not sig: continue
            if sig['strength'] not in ["قوية جدا 🔥", "قوية 💪"]: continue
            ACTIVE_SIGNALS[active_key] = {"entry": sig['entry'], "target": sig['target'], "stop": sig['stop'], "option": sig['option'], "time": time.time(), "reversed_alerted": False}
            save_json("active.json", ACTIVE_SIGNALS)
            msg = f"🚨 فرصة عقود {sig['strength']}\n{sym} - {sig['option']}\n💰 دخول السهم: ${sig['entry']:.2f}\n🎯 هدف: ${sig['target']:.2f} | 🛑 وقف: ${sig['stop']:.2f}\n⏱️ المدة: {sig['duration']}\n💡 {sig['reason']}\n\n📊 للعقود ({CONTRACTS} عقود): لازم السهم يتحرك {MIN_STOCK_MOVE:.2f}$ عشان تغطي عمولة سهم + ربح\n⚠️ اذا انعكس عليك 0.4% بجيك تنبيه انعكاس\n\n⏳ هذي الفرصة شغالة، ما بجيك فرصة ثانية على {sym} الا لما تخلص"
            try: await context.bot.send_message(chat_id=chat_id, text=msg)
            except Exception as e: print(e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    WATCHLIST[chat_id] = DEFAULT_SYMBOLS.copy()
    save_json("watchlist.json", WATCHLIST)
    await check_and_send(context.bot)
    await update.message.reply_text("هلا فيك 👋\nسجلتك في التنبيه التلقائي ✅\nبيراقب لك:\nAMZN, NVDA, AAPL, TSLA, MSFT, GOOGL, QQQ, SPY, META, XSP\n\nأي فرصة قوية بتجيك لحالها 🔥\nوتقدر تسأل عن أي سهم بـ /s NVDA\nوتشوف تقييم البوت بـ /stats")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_and_send(context.bot)
    HISTORY_NOW = load_json("history.json", [])
    if not HISTORY_NOW:
        await update.message.reply_text("📊 للحين مافيه فرص خلصت عشان اقيمها\nانتظر اول فرصة تخلص وبيجيك التقييم هنا")
        return
    total = len(HISTORY_NOW)
    wins = len([h for h in HISTORY_NOW if h.get('result') == "win"])
    losses = total - wins
    rate = wins / total * 100 if total else 0
    txt = f"📊 *تقييم البوت لنفسه - عقود*\n\n"
    txt += f"إجمالي الفرص: {total}\n✅ نجحت: {wins}\n❌ فشلت: {losses}\n🎯 نسبة النجاح: {rate:.0f}%\n"
    txt += f"عمولة سهم لـ {CONTRACTS} عقود: ${COST_TOTAL:.2f} ذهاب وعودة\nشرط النجاح: حركة {MIN_STOCK_MOVE:.2f}$ على الاقل\n\n"
    for sym in DEFAULT_SYMBOLS:
        sh = [h for h in HISTORY_NOW if h.get('symbol') == sym]
        if sh:
            sw = len([h for h in sh if h.get('result')=="win"])
            txt += f"{sym}: {sw}/{len(sh)} نجحت ({sw/len(sh)*100:.0f}%)\n"
    ACTIVE_NOW = load_json("active.json", {})
    if ACTIVE_NOW:
        txt += f"\n⏳ فرص شغالة الحين: {len(ACTIVE_NOW)}"
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

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global HISTORY, ACTIVE_SIGNALS
    HISTORY = []; ACTIVE_SIGNALS = {}
    save_json("history.json", []); save_json("active.json", {})
    await update.message.reply_text("✅ مسحت كل التقييم والفرص الشغالة، نبدأ من جديد")

def run_bot():
    if not TOKEN:
        print("❌ BOT_TOKEN missing"); return
    print("✅ Bot starting polling...")
    app_ = ApplicationBuilder().token(TOKEN).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CommandHandler("s", stock))
    app_.add_handler(CommandHandler("watch", watch))
    app_.add_handler(CommandHandler("stats", stats))
    app_.add_handler(CommandHandler("clear", clear_history))
    if app_.job_queue:
        app_.job_queue.run_repeating(auto_alert, interval=300, first=10)
        print("✅ job_queue OK")
    else:
        print("⚠️ job_queue missing - using threading fallback")
        def loop():
            time.sleep(10)
            while True:
                try:
                    async def run_once():
                        class FakeContext: pass
                        ctx = FakeContext(); ctx.bot = app_.bot
                        await auto_alert(ctx)
                    asyncio.run(run_once())
                except Exception as e:
                    print(f"Loop error: {e}")
                time.sleep(60)
        threading.Thread(target=loop, daemon=True).start()
    app_.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=lambda: app_web.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    run_bot()
