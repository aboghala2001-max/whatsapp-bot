import os
import time
import asyncio
import threading
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running")
    def log_message(self, *a): pass

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), H).serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()

TOKEN = os.getenv("BOT_TOKEN")

TICKERS = ["AAPL","MSFT","NVDA","TSLA","META","AMZN","GOOGL","AMD","NFLX","AVGO",
"PLTR","COIN","HOOD","SOFI","MSTR","APP","SMCI","ARM","MU","TSM",
"JPM","XOM","BAC","WMT","COST","LLY","UNH","MA","V","CRM",
"SPY","QQQ","DIA","IWM","GLD"]

LAST_DEBUG = {}
SENT_TODAY = {"strong_mid": 0, "light": 0, "date": ""}
ACTIVE = {}
CHAT_IDS = set()
LAST_LIGHT_SENT = 0
NY = pytz.timezone("America/New_York")

# جلسة متصفح حقيقي - عشان Yahoo ما يحظرنا
try:
    from curl_cffi import requests as cffi_requests
    CFFI_SESSION = cffi_requests.Session(impersonate="chrome")
except Exception as e:
    print("curl_cffi not available", e)
    CFFI_SESSION = None

def today_str():
    return datetime.now(NY).strftime("%Y-%m-%d")

def reset_daily():
    d = today_str()
    if SENT_TODAY["date"] != d:
        SENT_TODAY["strong_mid"] = 0
        SENT_TODAY["light"] = 0
        SENT_TODAY["date"] = d
        ACTIVE.clear()

def get_candles(ticker):
    try:
        df = yf.download(ticker, period="2d", interval="15m", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        if len(df) > 1:
            df = df.iloc[:-1]
        return df
    except Exception as e:
        print("candles err", ticker, e)
        return None

def _parse_opt_list(lst, strike):
    if not lst:
        return None
    best = min(lst, key=lambda x: abs(float(x.get("strike", 0)) - strike))
    bid = float(best.get("bid", 0) or 0)
    ask = float(best.get("ask", 0) or 0)
    last = float(best.get("lastPrice", 0) or 0)
    price = (bid + ask) / 2 if bid > 0 and ask > 0 else (last if last > 0 else ask)
    if price <= 0:
        return None
    return {
        "entry": round(price, 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "strike": float(best["strike"]),
        "vol": int(best.get("volume", 0) or 0),
        "oi": int(best.get("openInterest", 0) or 0),
    }

def get_option_price(ticker, strike, is_call):
    import json
    # الطريقة 1: Yahoo بمتصفح حقيقي curl_cffi
    if CFFI_SESSION is not None:
        try:
            url0 = f"https://query2.finance.yahoo.com/v7/finance/options?symbols={ticker}"
            r0 = CFFI_SESSION.get(url0, timeout=15)
            data0 = r0.json()
            res0 = data0.get("optionChain", {}).get("result", [])
            if res0:
                exps = res0[0].get("expirationDates", [])
                for exp_ts in exps[:8]:
                    try:
                        url = f"https://query2.finance.yahoo.com/v7/finance/options?symbols={ticker}&date={exp_ts}"
                        r = CFFI_SESSION.get(url, timeout=15)
                        data = r.json()
                        res = data.get("optionChain", {}).get("result", [])
                        if not res:
                            continue
                        opts = res[0].get("options", [])
                        if not opts:
                            continue
                        lst = opts[0].get("calls" if is_call else "puts", [])
                        out = _parse_opt_list(lst, strike)
                        if out:
                            out["expiry"] = datetime.fromtimestamp(exp_ts, tz=NY).strftime("%Y-%m-%d")
                            return out
                    except Exception as e:
                        print("opt cffi err", ticker, e)
                        continue
        except Exception as e:
            print("opt cffi main err", ticker, e)

    # الطريقة 2: yfinance احتياط - مع نفس الجلسة اذا توفرت
    try:
        if CFFI_SESSION is not None:
            tk = yf.Ticker(ticker, session=CFFI_SESSION)
        else:
            tk = yf.Ticker(ticker)
        exps = tk.options
        if not exps:
            return None
        for exp in exps[:8]:
            try:
                chain = tk.option_chain(exp)
                df = chain.calls if is_call else chain.puts
                if df is None or df.empty:
                    continue
                df = df.copy()
                df["diff"] = (df["strike"] - strike).abs()
                row = df.sort_values("diff").iloc[0]
                bid = float(row.get("bid", 0) or 0)
                ask = float(row.get("ask", 0) or 0)
                last = float(row.get("lastPrice", 0) or 0)
                price = (bid + ask) / 2 if bid > 0 and ask > 0 else (last if last > 0 else ask)
                if price <= 0:
                    continue
                return {
                    "entry": round(price, 2),
                    "bid": round(bid, 2),
                    "ask": round(ask, 2),
                    "strike": float(row["strike"]),
                    "vol": int(row.get("volume", 0) or 0),
                    "oi": int(row.get("openInterest", 0) or 0),
                    "expiry": exp,
                }
            except Exception as e:
                print("opt yf err", ticker, e)
                continue
    except Exception as e:
        print("opt err", ticker, e)
    return None

def analyze(ticker):
    reset_daily()
    df = get_candles(ticker)
    if df is None or len(df) < 10:
        LAST_DEBUG[ticker] = "ما فيه بيانات"
        return None

    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values
    vols = df["Volume"].values
    last_closed_time = df.index[-1]

    c0 = float(closes[-1])
    c1 = float(closes[-2])
    mom15 = (c0 / c1 - 1) * 100 if c1 else 0
    c_1h = float(closes[-5]) if len(closes) >= 5 else c1
    mom1h = (c0 / c_1h - 1) * 100 if c_1h else 0

    deltas = np.diff(closes[-15:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = gains.mean() if len(gains) else 0
    avg_loss = losses.mean() if len(losses) else 0
    if avg_loss == 0:
        rsi = 100 if avg_gain > 0 else 50
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    tp = (highs[-20:] + lows[-20:] + closes[-20:]) / 3
    vwap = float(np.sum(tp * vols[-20:]) / np.sum(vols[-20:])) if np.sum(vols[-20:]) > 0 else c0
    above_vwap = c0 > vwap
    avg_vol = float(np.mean(vols[-10:-1])) if len(vols) > 2 else 1
    vol_ratio = float(vols[-1] / avg_vol) if avg_vol > 0 else 0

    if -0.10 <= mom15 <= 0.10:
        is_up = mom1h > 0
        is_down = mom1h < 0
        neutral = True
    else:
        is_up = mom15 > 0 and mom1h > 0
        is_down = mom15 < 0 and mom1h < 0
        neutral = False

    if not is_up and not is_down:
        LAST_DEBUG[ticker] = f"سعر {c0:.2f} | mom15 {mom15:.3f}% | mom1h {mom1h:.3f}% | RSI {rsi:.0f} | فوليوم {vol_ratio:.2f}x | اتجاه متعاكس - لا"
        return None

    direction = "CALL" if is_up else "PUT"
    amom15 = abs(mom15)
    amom1h = abs(mom1h)

    if amom15 >= 0.25 and amom1h >= 0.4 and ((is_up and rsi >= 55 and above_vwap) or (is_down and rsi <= 45 and not above_vwap)):
        level = "قوية"
        score = 10
    elif amom15 >= 0.15 and amom1h >= 0.2 and ((is_up and rsi >= 52) or (is_down and rsi <= 48)):
        level = "متوسطة"
        score = 7
    elif amom15 >= 0.01 or neutral:
        level = "خفيفة"
        score = 4
    else:
        LAST_DEBUG[ticker] = f"سعر {c0:.2f} | mom15 {mom15:.3f}% | mom1h {mom1h:.3f}% | RSI {rsi:.0f} | حركة ضعيفة - لا"
        return None

    if direction == "CALL":
        strike = float(np.ceil(c0)) if c0 < 100 else float(np.ceil(c0 / 5) * 5)
    else:
        strike = float(np.floor(c0)) if c0 < 100 else float(np.floor(c0 / 5) * 5)

    opt = get_option_price(ticker, strike, direction == "CALL")
    if opt is None:
        LAST_DEBUG[ticker] = f"سعر {c0:.2f} | ما فيه اوبشن للسترايك {strike}"
        return None
    used_expiry = opt["expiry"]

    entry = opt["entry"]
    if entry <= 0 or pd.isna(entry):
        entry = 2.0

    contract_price = entry * 100
    if contract_price > 250:
        LAST_DEBUG[ticker] = f"سعر السهم {c0:.2f} | سعر العقد {contract_price:.0f}$ - فوق 250$ - مرفوض"
        return None

    if level == "قوية":
        tp1 = round(entry * 1.5, 2)
        tp2 = round(entry * 2.0, 2)
        sl = round(entry * 0.7, 2)
        dur = "30 د - ساعة"
        dur_min = 60
        prob = 78
    elif level == "متوسطة":
        tp1 = round(entry * 1.5, 2)
        tp2 = round(entry * 2.0, 2)
        sl = round(entry * 0.7, 2)
        dur = "15 - 30 د"
        dur_min = 30
        prob = 65
    else:
        tp1 = round(entry * 1.3, 2)
        tp2 = round(entry * 1.6, 2)
        sl = round(entry * 0.75, 2)
        dur = "15 د"
        dur_min = 15
        prob = 52

    now_ny = datetime.now(NY)
    try:
        closed_ny = last_closed_time.tz_convert(NY) if hasattr(last_closed_time, 'tz_convert') else last_closed_time
        closed_str = closed_ny.strftime("%H:%M")
    except:
        closed_str = ""
    expire_time = (now_ny + timedelta(minutes=dur_min)).strftime("%H:%M")

    data = {
        "ticker": ticker, "direction": direction, "level": level, "score": score,
        "price": round(c0, 2), "strike": opt["strike"], "entry": entry,
        "bid": opt["bid"], "ask": opt["ask"], "tp1": tp1, "tp2": tp2, "sl": sl,
        "contract": round(contract_price, 0), "prob": prob, "dur": dur,
        "dur_min": dur_min, "expire_time": expire_time, "closed_str": closed_str,
        "mom15": round(mom15, 3), "mom1h": round(mom1h, 3), "rsi": round(rsi, 0),
        "vwap": round(vwap, 2), "vol": round(vol_ratio, 2),
        "vol_opt": opt["vol"], "oi": opt["oi"], "expiry": used_expiry
    }
    LAST_DEBUG[ticker] = f"سعر {c0:.2f} | عقد {contract_price:.0f}$ | mom15 {mom15:.3f}% | mom1h {mom1h:.3f}% | RSI {rsi:.0f} | {level} {direction}"
    return data

def format_msg(d):
    emoji = "🟢" if d["direction"] == "CALL" else "🔴"
    lvl = "🔥 قوية" if d["level"] == "قوية" else ("⚡ متوسطة" if d["level"] == "متوسطة" else "💡 خفيفة")
    return (
        f"{emoji} {d['ticker']} - {d['direction']} - {lvl} {d['score']}/10\n"
        f"سعر السهم: {d['price']}$ (اخر شمعة مغلقة 15د: {d['closed_str']} بتوقيت نيويورك)\n"
        f"سترايك: {d['strike']}\n"
        f"انتهاء: {d['expiry']}\n"
        f"دخول: {d['entry']}$ | Bid {d['bid']} / Ask {d['ask']}\n"
        f"سعر العقد: {d['contract']}$\n"
        f"هدف اول: {d['tp1']}$ (+50%)\n"
        f"هدف ثاني: {d['tp2']}$ (+100%)\n"
        f"وقف: {d['sl']}$ (-30%)\n"
        f"⏳ مدة الفرصة: {d['dur']} | تنتهي الساعة {d['expire_time']} بتوقيت نيويورك\n"
        f"الاحتمال: {d['prob']}%\n"
        f"mom15 {d['mom15']}% | mom1h {d['mom1h']}% | RSI {d['rsi']} | VWAP {d['vwap']}\n"
        f"فوليوم اوبشن: {d['vol_opt']} | OI: {d['oi']}"
    )

async def send_all(app, text):
    for cid in list(CHAT_IDS):
        try:
            await app.bot.send_message(chat_id=cid, text=text)
        except Exception as e:
            print("send err", e)

async def scanner(app):
    global LAST_LIGHT_SENT
    while True:
        try:
            reset_daily()
            now_ny = datetime.now(NY)
            if now_ny.weekday() < 5 and 9 <= now_ny.hour < 16:
                for t in TICKERS:
                    d = analyze(t)
                    if not d:
                        continue
                    if d["ticker"] in ACTIVE:
                        continue
                    if d["level"] in ["قوية", "متوسطة"]:
                        if SENT_TODAY["strong_mid"] >= 10:
                            continue
                        SENT_TODAY["strong_mid"] += 1
                    else:
                        if SENT_TODAY["light"] >= 5:
                            continue
                        if time.time() - LAST_LIGHT_SENT < 900:
                            continue
                        LAST_LIGHT_SENT = time.time()
                        SENT_TODAY["light"] += 1
                    ACTIVE[d["ticker"]] = d
                    await send_all(app, format_msg(d))
                if now_ny.hour >= 16:
                    ACTIVE.clear()
            if now_ny.weekday() < 5 and now_ny.hour == 16 and now_ny.minute == 5:
                await send_all(app, f"📊 ملخص اليوم: قوية+متوسطة {SENT_TODAY['strong_mid']}/10 | خفيفة {SENT_TODAY['light']}/5")
                await asyncio.sleep(60)
        except Exception as e:
            print("scanner error", e)
        await asyncio.sleep(120)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    reset_daily()
    await update.message.reply_text(
        "✅ البوت شغال\n"
        "قوية + متوسطة 10 في اليوم - خفيفة 5 في اليوم\n"
        "فلتر: سعر العقد تحت 250$ (سعر السهم ما له فلتر)\n"
        "الاوامر:\n"
        "/best - افضل الفرص الحين\n"
        "/s NVDA - فحص شركة\n"
        "/status - حالة اليوم\n"
        "/clear - مسح المتابعة"
    )

async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    await update.message.reply_text("🔍 افحص كل الشركات...")
    results = []
    for t in TICKERS:
        d = analyze(t)
        if d:
            results.append(d)
    if not results:
        await update.message.reply_text("ما فيه فرص الحين - السوق هادي او كل الفرص مرفوضة بفلتر العقد")
        return
    order = {"قوية": 0, "متوسطة": 1, "خفيفة": 2}
    results.sort(key=lambda x: order.get(x["level"], 3))
    for d in results[:10]:
        await update.message.reply_text(format_msg(d))

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("اكتب: /s NVDA")
        return
    t = context.args[0].upper()
    d = analyze(t)
    if d:
        await update.message.reply_text(format_msg(d))
    else:
        await update.message.reply_text(f"❌ {t} - ما فيه فرصة\n{LAST_DEBUG.get(t, '')}")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_daily()
    await update.message.reply_text(f"📊 اليوم: قوية+متوسطة {SENT_TODAY['strong_mid']}/10 | خفيفة {SENT_TODAY['light']}/5 | نشطة: {len(ACTIVE)}")

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ACTIVE.clear()
    await update.message.reply_text("✅ تم المسح")

async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(scanner(app))

def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("best", best_cmd))
    app.add_handler(CommandHandler("s", s_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    print("Bot started")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
