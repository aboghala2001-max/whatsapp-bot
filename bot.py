import os, time, asyncio, threading
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- سيرفر Render ---
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

# شرط 2 - الشركات
TICKERS = ["SPY","QQQ","IWM","INDX","NVDA","TSLA","META","AAPL","AMD","AMZN","MU"]
# INDX = مؤشر، شموعه من NDX وعقده من QQQ
INDX_MAP = {"INDX": {"candle": "^NDX", "option": "QQQ"}}

# شرط 9 - 10 فرص
MAX_PER_DAY = 10
# شرط 8 - لا تكرار
ACTIVE = {}  # ticker -> opportunity
HISTORY = [] # كل فرص اليوم
SENT_TODAY = 0
TODAY = ""
CHAT_IDS = set()
LAST_DEBUG = {}

# جلسة Yahoo بمتصفح حقيقي
try:
    from curl_cffi import requests as cffi_requests
    CFFI = cffi_requests.Session(impersonate="chrome")
except:
    CFFI = None

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
    # شرط 1 - 4 عصراً لـ 12 صباحاً بتوقيت السعودية
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
        if len(df) > 1: df = df.iloc[:-1]  # اخر شمعة مغلقة فقط
        return df
    except:
        return None

def get_option_atm(ticker, price, is_call):
    # شرط 5 - ATM : اقرب سترايك للسعر
    opt_sym = INDX_MAP.get(ticker, {}).get("option", ticker)
    if price < 200:
        strike = float(round(price))
    else:
        strike = float(round(price / 5) * 5)

    # جلب العقد
    if CFFI is not None:
        try:
            url0 = f"https://query2.finance.yahoo.com/v7/finance/options?symbols={opt_sym}"
            r0 = CFFI.get(url0, timeout=15).json()
            res0 = r0.get("optionChain", {}).get("result", [])
            if res0:
                for exp_ts in res0[0].get("expirationDates", [])[:3]:
                    url = f"https://query2.finance.yahoo.com/v7/finance/options?symbols={opt_sym}&date={exp_ts}"
                    data = CFFI.get(url, timeout=15).json()
                    res = data.get("optionChain", {}).get("result", [])
                    if not res: continue
                    opts = res[0].get("options", [])
                    if not opts: continue
                    lst = opts[0].get("calls" if is_call else "puts", [])
                    if not lst: continue
                    best = min(lst, key=lambda x: abs(float(x.get("strike",0))-strike))
                    bid = float(best.get("bid",0) or 0); ask = float(best.get("ask",0) or 0)
                    last = float(best.get("lastPrice",0) or 0)
                    mid = (bid+ask)/2 if bid>0 and ask>0 else last
                    if mid <= 0: continue
                    # شرط 6 - سبريد ضيق
                    spread = ask - bid
                    spread_pct = (spread / mid * 100) if mid>0 else 999
                    if spread > 0.25 or spread_pct > 15:
                        LAST_DEBUG[ticker] = f"سبريد واسع {spread:.2f}$ ({spread_pct:.0f}%) - مرفوض"
                        return None
                    return {"entry": round(mid,2), "bid": round(bid,2), "ask": round(ask,2),
                            "strike": float(best["strike"]), "expiry": datetime.fromtimestamp(exp_ts, tz=NY).strftime("%Y-%m-%d"),
                            "spread": round(spread,2)}
        except Exception as e:
            print("opt err", ticker, e)
    return None

def analyze(ticker):
    reset_daily()
    df5 = get_candles(ticker, "5m")
    df15 = get_candles(ticker, "15m")
    if df5 is None or len(df5) < 25 or df15 is None or len(df15) < 10:
        LAST_DEBUG[ticker] = "ما فيه بيانات كافية"
        return None

    c0 = float(df5["Close"].iloc[-1])
    c1 = float(df5["Close"].iloc[-2])
    # قمة وقاع اخر 20 شمعة 5د = اخر 100 دقيقة
    prev_high = float(df5["High"].iloc[-21:-1].max())
    prev_low = float(df5["Low"].iloc[-21:-1].min())

    # شرط 3 + 4 - اختراق + ثبات
    # الثبات = شمعتين مغلقات فوق القمة / تحت القاع
    is_call = c0 > prev_high and c1 > prev_high
    is_put = c0 < prev_low and c1 < prev_low

    if not is_call and not is_put:
        LAST_DEBUG[ticker] = f"سعر {c0:.2f} | قمة {prev_high:.2f} | قاع {prev_low:.2f} | ما فيه اختراق"
        return None

    direction = "CALL" if is_call else "PUT"
    level = "قمة" if is_call else "قاع"
    breakout_price = prev_high if is_call else prev_low

    opt = get_option_atm(ticker, c0, direction == "CALL")
    if opt is None:
        if ticker not in LAST_DEBUG or "سبريد" not in LAST_DEBUG[ticker]:
            LAST_DEBUG[ticker] = f"سعر {c0:.2f} | ما فيه عقد ATM"
        return None

    entry = opt["entry"]
    # شرط 11 - اهداف ووقف
    tp1 = round(entry * 1.30, 2)  # +30%
    tp2 = round(entry * 1.60, 2)  # +60%
    sl = round(entry * 0.65, 2)   # -35%
    # مدة الفرصة - 60 دقيقة او لين نهاية الجلسة
    dur = "60 دقيقة"

    return {
        "ticker": ticker, "direction": direction, "price": round(c0,2),
        "breakout": round(breakout_price,2), "level": level,
        "strike": opt["strike"], "entry": entry, "bid": opt["bid"], "ask": opt["ask"],
        "spread": opt["spread"], "expiry": opt["expiry"],
        "tp1": tp1, "tp2": tp2, "sl": sl, "dur": dur,
        "sent_at": datetime.now(SA), "status": "نشطة", "result": "-"
    }

def format_msg(d):
    emoji = "🟢" if d["direction"] == "CALL" else "🔴"
    # شرط 11 - رسالة الفرصة بالضبط
    return (
        f"🚨 تنبيه - {emoji} {d['ticker']} - {d['direction']}\n"
        f"━━━━━━━━━━━━━━\n"
        f"الشركة: {d['ticker']}\n"
        f"السترايك: {d['strike']} (ATM)\n"
        f"المدة: {d['dur']}\n"
        f"━━━━━━━━━━━━━━\n"
        f"اختراق {d['level']}: {d['breakout']}$\n"
        f"سعر السهم: {d['price']}$\n"
        f"انتهاء: {d['expiry']}\n"
        f"━━━━━━━━━━━━━━\n"
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
    # متابعة الفرص النشطة - هل وصلت هدف او وقف
    for t, d in list(ACTIVE.items()):
        if d["status"] != "نشطة": continue
        # انتهت المدة 60 د
        if (datetime.now(SA) - d["sent_at"]).total_seconds() > 3600:
            d["status"] = "انتهت"; d["result"] = "انتهت المدة"
            del ACTIVE[t]
            continue
        opt = get_option_atm(t, d["price"], d["direction"] == "CALL")
        if not opt: continue
        cur = opt["entry"]
        if cur >= d["tp2"]:
            d["status"] = "انتهت"; d["result"] = "✅ هدف ثاني +60%"; del ACTIVE[t]
            await send_all(app, f"✅ {t} {d['direction']} وصل الهدف الثاني +60% | دخول {d['entry']}$ -> {cur}$")
        elif cur >= d["tp1"]:
            d["result"] = "✅ هدف اول +30%"
            await send_all(app, f"✅ {t} {d['direction']} وصل الهدف الاول +30% | دخول {d['entry']}$ -> {cur}$")
            # نخليه يكمل للهدف الثاني
        elif cur <= d["sl"]:
            d["status"] = "انتهت"; d["result"] = "❌ وقف -35%"; del ACTIVE[t]
            await send_all(app, f"❌ {t} {d['direction']} ضرب الوقف -35% | دخول {d['entry']}$ -> {cur}$")

async def scanner(app):
    sent_summary = False
    while True:
        try:
            reset_daily()
            now_sa = datetime.now(SA)
            # شرط 10 - تقييم نهاية الجلسة الساعة 12 صباحاً
            if now_sa.hour == 0 and now_sa.minute < 5 and not sent_summary and HISTORY:
                total = len(HISTORY)
                wins = sum(1 for h in HISTORY if "✅" in h["result"])
                losses = sum(1 for h in HISTORY if "❌" in h["result"])
                rate = round(wins / total * 100, 0) if total else 0
                txt = f"📊 تقييم جلسة {TODAY}\n━━━━━━━━━━━━━━\nإجمالي الفرص: {total}/10\n✅ نجحت: {wins}\n❌ فشلت: {losses}\nنسبة النجاح: {rate}%\n━━━━━━━━━━━━━━\n"
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
                    if t in ACTIVE: continue  # شرط 8
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
    await update.message.reply_text("✅ بوت نظيف شغال\nتداول يومي 4 عصراً - 12 صباحاً بتوقيت السعودية\n10 فرص في اليوم | عقود ATM | سبريد ضيق\n/s - فحص شركة\n/status - حالة اليوم")

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("اكتب: /s NVDA"); return
    t = context.args[0].upper()
    if t not in TICKERS:
        await update.message.reply_text(f"{t} مو في القائمة"); return
    if t in ACTIVE:
        await update.message.reply_text(f"⚠️ {t} عندها فرصة نشطة، ما نرسل ثانية لين تنتهي"); return
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
