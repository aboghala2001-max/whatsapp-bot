import os, yfinance as yf, pandas as pd
from datetime import datetime, time
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")
TICKERS = ["AAPL","MSFT","NVDA","TSLA","AMD","META","AMZN","GOOGL","NFLX","SPY","QQQ","XSP","AVGO","PLTR","COIN","MSTR"]
CHAT_IDS = set()

def market_open():
    et = pytz.timezone("America/New_York")
    now = datetime.now(et)
    if now.weekday() >= 5: return False
    return time(9,30) <= now.time() <= time(16,0)

def get_real_option(ticker, side):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="1d", interval="1m")
        if hist.empty: return None
        if isinstance(hist.columns, pd.MultiIndex): hist.columns = hist.columns.get_level_values(0)
        underlying = float(hist['Close'].iloc[-1])
        exps = tk.options
        if not exps: return None
        exp = exps[0]
        chain = tk.option_chain(exp)
        df = chain.calls if side == "Call" else chain.puts
        if df.empty: return None
        df = df.copy()
        df['diff'] = abs(df['strike'] - underlying)
        for _, row in df.sort_values('diff').head(5).iterrows():
            bid = float(row['bid']); ask = float(row['ask']); last = float(row['lastPrice'])
            if ask <= 0 or bid <= 0: continue
            spread = (ask - bid) / ask
            if spread > 0.20: continue
            vol = int(row.get('volume', 0) or 0)
            oi = int(row.get('openInterest', 0) or 0)
            if vol < 10 and oi < 50: continue
            return {"strike": float(row['strike']), "exp": exp, "bid": round(bid,2), "ask": round(ask,2), "last": round(last,2), "entry": round(ask,2), "underlying": round(underlying,2), "spread": round(spread*100,1), "volume": vol, "oi": oi}
        return None
    except Exception as e:
        print(f"option error {ticker}: {e}")
        return None

def get_signal(ticker):
    fetch_ticker = "SPY" if ticker == "XSP" else ticker
    try:
        df = yf.download(fetch_ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df.empty or len(df) < 20: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        close = df['Close']; vol = df['Volume']
        vwap = (df['Close']*df['Volume']).cumsum() / df['Volume'].cumsum()
        last = float(close.iloc[-1]); vwap_last = float(vwap.iloc[-1])
        delta = close.diff()
        gain = delta.where(delta>0, 0).rolling(14).mean()
        loss = (-delta.where(delta<0, 0)).rolling(14).mean()
        rs = gain / (loss + 0.0001)
        rsi = float(100 - (100/(1+rs.iloc[-1])))
        vol_ratio = float(vol.iloc[-1] / (vol.rolling(20).mean().iloc[-1] + 1))
        is_up = last > vwap_last
        side = "Call" if is_up else "Put"
        score = 0
        if vol_ratio > 1.5: score += 3
        elif vol_ratio > 1.2: score += 2
        elif vol_ratio > 0.8: score += 1
        if is_up and 55 <= rsi <= 68: score += 3
        elif not is_up and 32 <= rsi <= 45: score += 3
        elif 50 <= rsi <= 70 or 30 <= rsi <= 50: score += 2
        if abs(last - vwap_last)/vwap_last > 0.002: score += 2
        else: score += 1
        if vol_ratio > 1.2: score += 2
        if score >= 8 and vol_ratio > 1.2: typ = "قوية 🔥"
        elif score >= 5: typ = "خفيفة ⚡"
        else: return None
        return {"ticker":ticker,"side":side,"type":typ,"rsi":round(rsi,1),"vol":round(vol_ratio,2),"score":score,"vwap_diff":round((last-vwap_last)/vwap_last*100,2)}
    except Exception as e:
        print(f"signal error {ticker}: {e}")
        return None

def format_msg(s, opt):
    entry = opt['entry']
    if s['type'].startswith("قوية"):
        target = round(entry*1.40,2); stop = round(entry*0.80,2); profit = round((target-entry)*100,0)
    else:
        target = round(entry*1.18,2); stop = round(entry*0.90,2); profit = round((target-entry)*100,0)
    stars = "⭐" * (s['score']//2) if s['score']>=4 else "⭐⭐"
    return (f"🎯 فرصة {s['type']} - {s['ticker']} {s['side']}\n{stars} التقييم: {s['score']}/10\n\nستريك: {opt['strike']} | انتهاء: {opt['exp']}\nسعر السهم: {opt['underlying']}\nBid: {opt['bid']} | Ask: {opt['ask']} | Last: {opt['last']}\nسبريد: {opt['spread']}% | حجم تداول: {opt['volume']} | عقود مفتوحة: {opt['oi']}\n\n✅ اشتري على {entry}\n🎯 بيع على {target} (+{profit}$ لكل عقد)\n🛑 وقف على {stop}\n\nRSI: {s['rsi']} | قوة الحجم: {s['vol']}x | بعيد عن VWAP: {s['vwap_diff']}%\n💡 نصيحة: لا تدخل بأكثر من عقدين اذا راس مالك صغير، ولا تنسى الوقف")

async def scan_and_send(context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    if not market_open(): return 0
    targets = [chat_id] if chat_id else list(CHAT_IDS)
    if not targets: return 0
    sent = 0
    for t in TICKERS:
        s = get_signal(t)
        if not s: continue
        opt = get_real_option(t, s['side'])
        if not opt: continue
        msg = format_msg(s, opt)
        for cid in targets:
            try: await context.bot.send_message(cid, msg)
            except: pass
        sent += 1
        if sent >= 2: break
    return sent

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    if not context.job_queue.jobs():
        context.job_queue.run_repeating(auto_job, interval=900, first=10, name="auto")
    await update.message.reply_text("✅ البوت شغال - النسخة النهائية\n\n🔔 تنبيه تلقائي كل 15 دقيقة\n⭐ تقييم لكل فرصة من 10\n🛡️ فلتر سبريد + حجم\n🕐 يشتغل بس وقت السوق\n💰 سعر حقيقي Bid/Ask/Last\n\nارسل /best للفحص الفوري\nارسل /stop لإيقاف التنبيهات")

async def best(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not market_open():
        await update.message.reply_text("🔴 السوق مسكر الحين. البوت يشتغل من 9:30 صباحا لـ 4 عصرا بتوقيت نيويورك.")
        return
    await update.message.reply_text("🔍 جاري فحص الاسعار الحقيقية...")
    sent = await scan_and_send(context, chat_id=update.effective_chat.id)
    if sent == 0:
        await update.message.reply_text("ما فيه فرص الحين، حتى الخفيفة. السوق متذبذب. التنبيه التلقائي بيرسل لك اول ما تطلع فرصة.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.discard(update.effective_chat.id)
    await update.message.reply_text("🔕 وقفت التنبيهات. ارسل /start لتشغيلها من جديد.")

async def auto_job(context: ContextTypes.DEFAULT_TYPE):
    await scan_and_send(context)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("best", best))
    app.add_handler(CommandHandler("stop", stop))
    print("Bot started - FINAL")
    app.run_polling()

if __name__ == "__main__":
    main()
