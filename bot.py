import os, yfinance as yf, datetime, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from zoneinfo import ZoneInfo

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", port), H).serve_forever()
threading.Thread(target=keep_alive, daemon=True).start()

MAX_CONTRACT_PRICE = 2.10
COMMISSION = 4.58
SUCCESS_MOVE = 0.20
REVERSAL_PCT = 0.40
active_trades = {}
BEST_TICKERS = ["NVDA","TSLA","AAPL","MSFT","AMZN","META","AMD","NFLX","GOOGL","AVGO","PLTR","COIN",
                "JPM","V","MA","BAC","GS","UNH","JNJ","LLY","ABBV","WMT","COST","HD","PG","DIS",
                "XOM","CAT","ORCL","CRM","ADBE","SPY","QQQ"]

def get_expiry():
    today = datetime.date.today()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0: days_ahead = 7
    return today + datetime.timedelta(days=days_ahead)

def get_market_trend():
    # اتجاه السوق العام SPY
    try:
        spy = yf.Ticker("SPY").history(period="5d", interval="15m")
        if spy.empty: return 0, 0
        mom = (spy['Close'].iloc[-1] / spy['Close'].iloc[-4] - 1) * 100 if len(spy) > 4 else 0
        daily = yf.Ticker("SPY").history(period="5d", interval="1d")
        trend = (daily['Close'].iloc[-1] / daily['Close'].iloc[-4] - 1) * 100 if len(daily) >= 4 else 0
        return mom, trend
    except:
        return 0, 0

def is_bad_time():
    # وقت السوق بتوقيت نيويورك
    try:
        now_et = datetime.datetime.now(ZoneInfo("America/New_York"))
        # ويكند
        if now_et.weekday() >= 5: return True, "السوق مسكر - ويكند"
        mins = now_et.hour * 60 + now_et.minute
        # قبل الافتتاح وبعده
        if mins < 575 or mins >= 960: return True, "السوق مسكر"
        # أول 5 دقايق 9:30 - 9:35
        if 570 <= mins < 575: return True, "أول 5 دقايق - كذب"
        # وقت الغداء 12:00 - 13:00
        if 720 <= mins < 780: return True, "وقت غداء - بطيء"
        # آخر نص ساعة 15:30 - 16:00
        if mins >= 930: return True, "آخر نص ساعة - خطير"
        return False, ""
    except:
        return False, ""

def get_earnings(ticker):
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if cal is not None and 'Earnings Date' in cal:
            ed = cal['Earnings Date']
            if isinstance(ed, list): ed = ed[0]
            days = (ed.date() - datetime.date.today()).days if hasattr(ed, 'date') else 99
            if 0 <= days <= 3:
                return f"⚠️ أرباح بعد {days} يوم"
            return "✅ مافي أرباح"
        return "✅ مافي أرباح"
    except:
        return "✅ مافي أرباح"

def analyze(ticker):
    tk = yf.Ticker(ticker)
    hist = tk.history(period="5d", interval="15m")
    daily = tk.history(period="5d", interval="1d")
    if hist.empty or daily.empty: return None
    price = float(hist['Close'].iloc[-1])
    delta = hist['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean().iloc[-1]
    loss = -delta.where(delta < 0, 0).rolling(14).mean().iloc[-1]
    rsi = 100 - (100 / (1 + gain / loss)) if loss != 0 else 50
    vwap = float((hist['Close'] * hist['Volume']).sum() / hist['Volume'].sum()) if hist['Volume'].sum() else price
    mom15 = (hist['Close'].iloc[-1] / hist['Close'].iloc[-4] - 1) * 100 if len(hist) > 4 else 0
    mom1h = (hist['Close'].iloc[-1] / hist['Close'].iloc[-5] - 1) * 100 if len(hist) > 5 else 0
    trend3 = (daily['Close'].iloc[-1] / daily['Close'].iloc[-4] - 1) * 100 if len(daily) >= 4 else 0
    vol = float(hist['Volume'].iloc[-1]); vol_avg = float(hist['Volume'].mean())
    liq = vol / vol_avg if vol_avg else 0
    sup_n = float(daily['Low'].tail(2).min()); res_n = float(daily['High'].tail(2).max())
    sup_f = float(daily['Low'].min()); res_f = float(daily['High'].max())
    expiry = get_expiry()
    expiry_str = expiry.strftime("%Y-%m-%d")
    is_call_guess = mom15 >= 0
    strike = round(price + 2.5) if is_call_guess else round(price - 2.5)
    contract_price, iv, delta_g, theta = 1.85, 38, 0.42, -0.18
    try:
        opt = tk.option_chain(expiry_str)
        chain = opt.calls if is_call_guess else opt.puts
        valid = chain[(chain['lastPrice'] <= MAX_CONTRACT_PRICE) & (chain['lastPrice'] > 0.30)]
        if not valid.empty:
            row = valid.iloc[(valid['strike'] - price).abs().argsort()[:1]].iloc[0]
            strike = float(row['strike']); contract_price = float(row['lastPrice'])
            iv = float(row.get('impliedVolatility', 0.38)) * 100
            delta_g = 0.42 if abs(strike - price) < 3 else 0.30
            theta = -0.18 if abs(strike - price) < 3 else -0.12
    except: pass
    cont_short_up = mom15 >= 0.50 and mom1h >= 0 and trend3 > 0
    cont_short_down = mom15 <= -0.50 and mom1h <= 0 and trend3 < 0
    cont_long_up = abs(trend3) >= 2 and trend3 > 0 and mom15 >= 0 and mom1h >= 0
    cont_long_down = abs(trend3) >= 2 and trend3 < 0 and mom15 <= 0 and mom1h <= 0
    if cont_short_up or cont_short_down:
        duration = "15 - 30 دقيقة"
        cont_type = "short"
    elif cont_long_up or cont_long_down:
        duration = "يومين - 3 أيام"
        cont_type = "long"
    else:
        duration = "15 - 30 دقيقة"
        cont_type = "none"
    spy_mom, spy_trend = get_market_trend()
    score = abs(mom15)*3 + abs(trend3)*2 + min(liq,5)*2
    if cont_type != "none": score += 10
    if 45 <= rsi <= 75: score += 5
    # بونص اذا مع السوق
    if (mom15 > 0 and spy_mom >= 0) or (mom15 < 0 and spy_mom <= 0): score += 5
    # بونص اذا مع VWAP
    if (mom15 > 0 and price > vwap) or (mom15 < 0 and price < vwap): score += 5
    return {"price":price,"rsi":rsi,"vwap":vwap,"mom15":mom15,"mom1h":mom1h,"trend":trend3,"liq":liq,
            "sup_n":sup_n,"sup_f":sup_f,"res_n":res_n,"res_f":res_f,
            "strike":strike,"cprice":contract_price,"iv":iv,"delta":delta_g,"theta":theta,
            "expiry":expiry,"earn":get_earnings(ticker),"is_call_guess":is_call_guess,"duration":duration,"cont_type":cont_type,"score":score,
            "spy_mom":spy_mom,"spy_trend":spy_trend}

def format_msg(ticker, d):
    stop = d['cprice'] * 0.70
    t1 = d['cprice'] * 1.50
    t2 = d['cprice'] * 2.0
    # فلتر الوقت
    bad, bad_reason = is_bad_time()
    # CALL: مستمرة + فوق VWAP + السوق صاعد
    is_call = d['cont_type'] in ("short","long") and d['mom15'] >= 0 and d['trend'] > 0 and d['liq'] >= 1.2 and d['price'] > d['vwap'] and d['spy_mom'] >= 0 and not bad
    # PUT: مستمرة + تحت VWAP + السوق هابط
    is_put = d['cont_type'] in ("short","long") and d['mom15'] <= 0 and d['trend'] < 0 and d['liq'] >= 1.2 and d['price'] < d['vwap'] and d['spy_mom'] <= 0 and not bad
    if d['cprice'] > MAX_CONTRACT_PRICE: dec = "تخطي"
    elif is_call: dec = "CALL"
    elif is_put: dec = "PUT"
    else: dec = "انتظار ⚠️"
    m = f"📊 {ticker}\n"
    m += f"سترايك {d['strike']:.0f}$ - {dec}\n"
    m += f"السعر {d['cprice']:.2f}$\n"
    m += f"التاريخ {d['expiry'].strftime('%d %b')} - المدة {d['duration']}\n"
    m += f"الدعم {d['sup_n']:.1f}$ | المقاومة {d['res_n']:.1f}$\n"
    m += f"الوقف {stop:.2f}$\n"
    m += f"الهدف الأول {t1:.2f}$\n"
    m += f"الهدف الثاني {t2:.2f}$"
    return m, is_call, is_put

async def check_targets(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, tr in list(active_trades.items()):
        try:
            tk = yf.Ticker(tr['ticker'])
            expiry_str = tr['expiry'].strftime("%Y-%m-%d")
            opt = tk.option_chain(expiry_str)
            chain = opt.calls if tr['type'] == 'CALL' else opt.puts
            row = chain[chain['strike'] == tr['strike']]
            if row.empty: continue
            cur = float(row.iloc[0]['lastPrice'])
            if not tr.get('t1_hit') and cur >= tr['t1']:
                tr['t1_hit'] = True
                await context.bot.send_message(chat_id, f"🎯 {tr['ticker']} وصل الهدف الأول +50% ({cur:.2f}$) - حرك وقفك على الدخول")
            if not tr.get('t2_hit') and cur >= tr['t2']:
                tr['t2_hit'] = True
                await context.bot.send_message(chat_id, f"🎯🎯 {tr['ticker']} وصل الهدف الثاني +100% ({cur:.2f}$) - بيع العقد كامل 🎉")
                del active_trades[chat_id]
                continue
            if cur <= tr['stop']:
                await context.bot.send_message(chat_id, f"🛑 {tr['ticker']} وصل الوقف -30% ({cur:.2f}$) - اطلع فورا")
                del active_trades[chat_id]
        except: pass

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب: /s NVDA"); return
    ticker = context.args[0].upper()
    d = analyze(ticker)
    if not d: await update.message.reply_text("ما لقيت بيانات"); return
    msg, is_call, is_put = format_msg(ticker, d)
    await update.message.reply_text(msg)
    if is_call or is_put:
        active_trades[update.effective_chat.id] = {
            'ticker': ticker, 'strike': d['strike'], 'entry': d['cprice'],
            't1': d['cprice']*1.5, 't2': d['cprice']*2.0, 'stop': d['cprice']*0.70,
            'type': 'CALL' if is_call else 'PUT', 'expiry': d['expiry'],
            't1_hit': False, 't2_hit': False
        }

async def best_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bad, bad_reason = is_bad_time()
    if bad:
        await update.message.reply_text(f"⏸️ {bad_reason} - لا تداول الحين")
        return
    await update.message.reply_text("🔍 أدور لك على أقوى الفرص المستمرة في 34 شركة قيادية...")
    results = []
    for t in BEST_TICKERS:
        try:
            d = analyze(t)
            if not d: continue
            msg, is_call, is_put = format_msg(t, d)
            if is_call or is_put:
                results.append((d['score'], t, msg))
        except: pass
    results.sort(reverse=True)
    if not results:
        await update.message.reply_text("مافي فرص مستمرة قوية الحين، كلها بطيئة وحرقناها 🔥")
        return
    out = "🔥 أقوى 3 فرص مستمرة:\n\n"
    for i, (sc, t, m) in enumerate(results[:3], 1):
        out += f"#{i} {m}\n\n"
    await update.message.reply_text(out.strip())

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_trades.pop(update.effective_chat.id, None)
    await update.message.reply_text("مسحت كل شي، نبدأ من جديد ✅")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("s", s_cmd))
app.add_handler(CommandHandler("best", best_cmd))
app.add_handler(CommandHandler("clear", clear_cmd))
app.job_queue.run_repeating(check_targets, interval=60, first=10)
app.run_polling()
