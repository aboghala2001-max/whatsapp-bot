import os, asyncio, threading
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running")
    def log_message(self, *a): pass
def keep_alive():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever()
threading.Thread(target=keep_alive, daemon=True).start()

TOKEN = os.getenv("BOT_TOKEN")
SA = pytz.timezone("Asia/Riyadh")
NY = pytz.timezone("America/New_York")

TICKERS = ["SPY","QQQ","IWM","INDX","NVDA","TSLA","META","AAPL","AMD","AMZN","MU"]
INDX_MAP = {"INDX": {"candle": "^NDX", "option": "QQQ"}}

MAX_PER_DAY = 10
ACTIVE = {}
HISTORY = []
SENT_TODAY = 0
TODAY = ""
CHAT_IDS = set()
LAST_DEBUG = {}

def today_sa():
    return datetime.now(SA).strftime("%Y-%m-%d")

def reset_daily():
    global SENT_TODAY, TODAY
    d = today_sa()
    if TODAY != d:
        TODAY = d
        SENT_TODAY = 0
        ACTIVE.clear()
        HISTORY.clear()

def in_session():
    now = datetime.now(SA)
    return 16 <= now.hour < 24

def get_candles(ticker, interval):
    sym = INDX_MAP.get(ticker, {}).get("candle", ticker)
    try:
        df = yf.download(sym, period="2d", interval=interval, progress=False, auto_adjust=False)
        if df is None or df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        if len(df) > 1: df = df.iloc[:-1]
        return df
    except:
        return None

def get_option_atm(ticker, price, is_call):
    # يدور عقد ATM وبس - ما له دخل التاريخ
    opt_sym = INDX_MAP.get(ticker, {}).get("option", ticker)
    strike = float(round(price))
    try:
        tk = yf.Ticker(opt_sym)
        exps = tk.options
        if not exps: return None
        exp = exps[0]  # أقرب عقد متوفر، بدون فلترة تاريخ
        chain = tk.option_chain(exp)
        df_opt = chain.calls if is_call else chain.puts
        if df_opt.empty: return None
        df_opt = df_opt.copy()
        df_opt["diff"] = abs(df_opt["strike"] - strike)
        best = df_opt.sort_values("diff").iloc[0]
        bid = float(best.get("bid", 0) or 0)
        ask = float(best.get("ask", 0) or 0)
        last = float(best.get("lastPrice", 0) or 0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
        if mid <= 0: return None
        spread = ask - bid if bid > 0 and ask > 0 else 0
        return {"entry": round(mid,2), "bid": round(bid,2), "ask": round(ask,2),
                "strike": float(best["strike"]), "expiry": exp,
                "spread": round(spread,2)}
    except Exception as e:
        print("opt err", ticker, e)
        return None

def analyze(ticker):
    reset_daily()
    df5 = get_candles(ticker, "5m")
    df15 = get_candles(ticker, "15m")
    if df5 is None or df5.empty or df15 is None or df15.empty:
        LAST_DEBUG[ticker] = "ما فيه بيانات"
        return None

    c5 = float(df5["Close"].iloc[-1])
    h5_prev = float(df5["High"].iloc[-2])
    l5_prev = float(df5["Low"].iloc[-2])

    c15 = float(df15["Close"].iloc[-1])
    h15_prev = float(df15["High"].iloc[-2])
    l15_prev = float(df15["Low"].iloc[-2])

    # الشرط: 5د أو 15د يكفي
    is_call = c5 > h5_prev or c15 > h15_prev
    is_put = c5 < l5_prev or c15 < l15_prev

    if not is_call and not is_put:
        LAST_DEBUG[ticker] = f"سعر 5د {c5:.2f} | قمة {h5_prev:.2f} | قاع {l5_prev:.2f} | ما فيه اختراق"
        return None

    direction = "CALL" if is_call else "PUT"
    level = "قمة" if is_call else "قاع"
    breakout_price = h5_prev if is_call else l5_prev
    price = c5

    opt = get_option_atm(ticker, price, direction == "CALL")
    if opt is None:
        LAST_DEBUG[ticker] = f"سعر {price:.2f} | ما فيه عقد ATM"
        return None

    entry = opt["entry"]
    tp1 = round(entry * 1.30, 2)
    tp2 = round(entry * 1.60, 2)
    sl = round(entry * 0.65, 2)
    dur = "حتى نهاية الجلسة"

    return {
        "ticker": ticker, "direction": direction, "price": round(price,2),
        "breakout": round(breakout_price,2), "level": level,
        "strike": opt["strike"], "entry": entry, "bid": opt["bid"], "ask": opt["ask"],
        "spread": opt["spread"], "expiry": opt["expiry"],
        "tp1": tp1, "tp2": tp2, "sl": sl, "dur": dur,
        "sent_at": datetime.now(SA), "status": "نشطة", "result": "-"
    }

def format_msg(d):
    emoji = "🟢" if d["direction"] == "CALL" else "🔴"
    return (
        f"🚨 تنبيه - {emoji} {d['ticker']} - {d['direction']}\n"
        f"الشركة: {d['ticker']} | السترايك: {d['strike']} | المدة: {d['dur']}\n"
        f"اختراق {d['level']}: {d['breakout']}$\n"
        f"سعر السهم: {d['price']}$\n"
        f"انتهاء: {d['expiry']}\n"
        f"دخول: {d['entry']}$ | Bid {d['bid']} / Ask {d['ask']} | سبريد {d['spread']}$\n"
        f"الهدف الاول: {d['tp1']}$ (+30%)\n"
        f"الهدف الثاني: {d['tp2']}$ (+60%)\n"
        f"الوقف: {d['sl']}$ (-35%)"
    )

async def send_all(app, text):
    for cid in list(CHAT_IDS):
        try: await app.bot.send_message(chat_id=cid, text=text)
        except: pass

async def check_active(app):
    for t, d in list(ACTIVE.items()):
        if d["status"] != "نشطة": continue
        if not in_session():
            d["status"] = "انتهت"; d["result"] = "انتهت الجلسة"; del ACTIVE[t]; continue
        opt = get_option_atm(t, d["price"], d["direction"] == "CALL")
        if not opt: continue
        cur = opt["entry"]
        if cur >= d["tp2"]:
            d["status"] = "انتهت"; d["result"] = "✅ هدف ثاني +60%"; del ACTIVE[t]
            await send_all(app, f"✅ {t} {d['direction']} وصل الهدف الثاني +60% | دخول {d['entry']}$ -> {cur}$")
        elif cur >= d["tp1"]:
            d["result"] = "✅ هدف اول +30%"
            await send_all(app, f"✅ {t} {d['direction']} وصل الهدف الاول +30% | دخول {d['entry']}$ -> {cur}$")
        elif cur <= d["sl"]:
            d["status"] = "انتهت"; d["result"] = "❌ وقف -35%"; del ACTIVE[t]
            await send_all(app, f"❌ {t} {d['direction']} ضرب الوقف -35% | دخول {d['entry']}$ -> {cur}$")

async def scanner(app):
    sent_summary = False
    while True:
        try:
            reset_daily()
            now_sa = datetime.now(SA)
            if now_sa.hour == 0 and now_sa.minute < 5 and not sent_summary and HISTORY:
                total = len(HISTORY)
                wins = sum(1 for h in HISTORY if "✅" in h["result"])
                losses = sum(1 for h in HISTORY if "❌" in h["result"])
                rate = round(wins / total * 100, 0) if total else 0
                txt = f"📊 تقييم جلسة {TODAY}\nإجمالي الفرص: {total}/10\n✅ نجحت: {wins}\n❌ فشلت: {losses}\nنسبة النجاح: {rate}%\n"
                for h in HISTORY:
                    txt += f"{h['ticker']} {h['direction']} سترايك {h['strike']} - {h['result']}\n"
                await send_all(app, txt)
                sent_summary = True
            if now_sa.hour == 16:
                sent_summary = False

            if in_session() and SENT_TODAY < MAX_PER_DAY:
                await check_active(app)
                for t in TICKERS:
                    if SENT_TODAY >= MAX_PER_DAY: break
                    if t in ACTIVE: continue
                    d = analyze(t)
                    if not d: continue
                    ACTIVE[t] = d
                    HISTORY.append(d)
                    SENT_TODAY += 1
                    await send_all(app, format_msg(d))
        except Exception as e:
            print("scanner", e)
        await asyncio.sleep(120)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    await update.message.reply_text("✅ بوت شغال على شروطك الـ 11\n/s - فحص شركة\n/status - حالة اليوم")

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("اكتب: /s NVDA"); return
    t = context.args[0].upper()
    if t not in TICKERS:
        await update.message.reply_text(f"{t} مو في القائمة"); return
    if t in ACTIVE:
        await update.message.reply_text(f"⚠️ {t} عندها فرصة نشطة"); return
    d = analyze(t)
    if d:
        ACTIVE[t] = d; HISTORY.append(d)
        global SENT_TODAY; SENT_TODAY += 1
        await update.message.reply_text(format_msg(d))
    else:
        await update.message.reply_text(f"❌ {t} - ما فيه اختراق\n{LAST_DEBUG.get(t,'')}")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_daily()
    await update.message.reply_text(f"📊 اليوم: {SENT_TODAY}/10 فرص | نشطة: {len(ACTIVE)} | {list(ACTIVE.keys())}")

async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(scanner(app))

def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("s", s_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    print("Bot started")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
