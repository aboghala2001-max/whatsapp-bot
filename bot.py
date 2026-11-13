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
active_trades = {}
trade_history = []
BEST_TICKERS = ["NVDA","TSLA","AAPL","MSFT","AMZN","META","AMD","NFLX","GOOGL","AVGO","PLTR","COIN",
                "JPM","V","MA","BAC","GS","UNH","JNJ","LLY","ABBV","WMT","COST","HD","PG","DIS",
                "XOM","CAT","ORCL","CRM","ADBE","SPY","QQQ"]

def get_expiry_daily():
    # كل الفرص يومية - نفس يوم التداول فقط
    today = datetime.date.today()
    # اذا اليوم سبت 14 -> الاثنين 16، اذا أحد -> الاثنين 16، غيره نفس اليوم
    entry = today
    while entry.weekday() >= 5:
        entry += datetime.timedelta(days=1)
    return entry

def get_market_trend():
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
    try:
        now_et = datetime.datetime.now(ZoneInfo("America/New_York"))
        if now_et.weekday() >= 5: return True, "السوق مسكر - ويكند"
        mins = now_et.hour * 60 + now_et.minute
        if mins < 575 or mins >= 960: return True, "السوق مسكر"
        if 570 <= mins < 575: return True, "أول 5 دقايق - كذب"
        if 720 <= mins < 780: return True, "وقت غداء - بطيء"
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
    sup_n = float(daily['Low'].tail(2).min())
    res_n = float(daily['High'].tail(2).max())
    if sup_n > price or res_n < price or abs(sup_n - price) / price > 0.08 or abs(res_n - price) / price > 0.08:
        recent = hist.tail(40)
        sup_n = float(recent['Low'].min())
        res_n = float(recent['High'].max())
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

    # فقط فرص يومية - ألغينا يومين و 3 أيام
    duration = "15 - 30 دقيقة"
    cont_type = "short"
    # إذا ما فيه استمرارية قصيرة، نعتبرها انتظار، ولا نعطي فرصة طويلة
    cont_short_up = mom15 >= 0.50 and mom1h >= 0 and trend3 > 0
    cont_short_down = mom15 <= -0.50 and mom1h <= 0 and trend3 < 0
    if not (cont_short_up or cont_short_down):
        cont_type = "none"

    expiry = get_expiry_daily()
    expiry_str = expiry.strftime("%Y-%m-%d")
    is_call_guess = mom15 >= 0
    atm = round(price / 5) * 5
    strike = float(atm)
    contract_price, iv = None, 38
    try:
        opt = tk.option_chain(expiry_str)
        chain = opt.calls if is_call_guess else opt.puts
        chain = chain[abs(chain['strike'] - price) <= 10]
        valid = chain[(chain['lastPrice'] <= MAX_CONTRACT_PRICE) & (chain['lastPrice'] > 0.30)]
        if not valid.empty:
            valid = valid.copy()
            valid['dist'] = abs(valid['strike'] - price)
            valid = valid.sort_values('dist')
            row = valid.iloc[0]
            strike = float(row['strike']); contract_price = float(row['lastPrice'])
            iv = float(row.get('impliedVolatility', 0.38)) * 100
    except:
        pass
    if contract_price is None:
        return None
    spy_mom, spy_trend = get_market_trend()
    score = abs(mom15)*3 + abs(trend3)*2 + min(liq,5)*2
    if cont_type != "none": score += 10
    if 45 <= rsi <= 75: score += 5
    if (mom15 > 0 and spy_mom >= 0) or (mom15 < 0 and spy_mom <= 0): score += 5
    if (mom15 > 0 and price > vwap) or (mom15 < 0 and price < vwap): score += 5
    return {"price":price,"rsi":rsi,"vwap":vwap,"mom15":mom15,"mom1h":mom1h,"trend":trend3,"liq":liq,
            "sup_n":sup_n,"sup_f":float(daily['Low'].min()),"res_n":res_n,"res_f":float(daily['High'].max()),
            "strike":strike,"cprice":contract_price,"iv":iv,"delta":0.42,"theta":-0.18,
            "expiry":expiry,"earn":get_earnings(ticker),"is_call_guess":is_call_guess,"duration":duration,"cont_type":cont_type,"score":score,
            "spy_mom":spy_mom,"spy_trend":spy_trend}

def format_msg(ticker, d):
    stop = d['cprice'] * 0.70
    t1 = d['cprice'] * 1.50
    t2 = d['cprice'] * 2.0
    bad, bad_reason = is_bad_time()
    # كل الشروط لازم تتوفر، ولا ما يعطي CALL ولا PUT
    is_call = d['cont_type'] == "short" and d['mom15'] >= 0 and d['trend'] > 0 and d['liq'] >= 1.2 and d['price'] > d['vwap'] and d['spy_mom'] >= 0 and not bad
    is_put = d['cont_type'] == "short" and d['mom15'] <= 0 and d['trend'] < 0 and d['liq'] >= 1.2 and d['price'] < d['vwap'] and d['spy_mom'] <= 0 and not bad
    if d['cprice'] > MAX_CONTRACT_PRICE: dec = "تخطي"
    elif is_call: dec = "CALL"
    elif is_put: dec = "PUT"
    else: dec = "انتظار ⚠️"
    m = f"📊 {ticker}\n"
    m += f"سعر السهم {d['price']:.1f}$ | سترايك {d['strike']:.0f}$ - {dec}\n"
    m += f"السعر {d['cprice']:.2f}$ - ماكس 2.10$\n"
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
                profit = (cur - tr['entry']) / tr['entry'] * 100
                trade_history.append({'ticker': tr['ticker'], 'type': tr['type'], 'result': 'win', 'profit': profit})
                await context.bot.send_message(chat_id, f"🎯🎯 {tr['ticker']} وصل الهدف الثاني +100% ({cur:.2f}$) - بيع العقد كامل 🎉\n\n📋 تقييم الفرصة:\nالسهم: {tr['ticker']} {tr['type']}\nالدخول: {tr['entry']:.2f}$ - الخروج: {cur:.2f}$\nالنتيجة: ربح +{profit:.0f}% ✅\nالتقييم: فرصة ناجحة")
                del active_trades[chat_id]
                continue
            if cur <= tr['stop']:
                profit = (cur - tr['entry']) / tr['entry'] * 100
                trade_history.append({'ticker': tr['ticker'], 'type': tr['type'], 'result': 'loss', 'profit': profit})
                await context.bot.send_message(chat_id, f"🛑 {tr['ticker']} وصل الوقف -30% ({cur:.2f}$) - اطلع فورا\n\n📋 تقييم الفرصة:\nالسهم: {tr['ticker']} {tr['type']}\nالدخول: {tr['entry']:.2f}$ - الخروج: {cur:.2f}$\nالنتيجة: خسارة {profit:.0f}% ❌\nالتقييم: فرصة فاشلة")
                del active_trades[chat_id]
        except: pass

async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("اكتب: /s NVDA"); return
    ticker = context.args[0].upper()
    d = analyze(ticker)
    if not d: await update.message.reply_text("ما لقيت عقد بسعر <= 2.10$ وقريب من السعر، لا تدخل - جرب بعد شوي"); return
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
    await update.message.reply_text("🔍 أدور لك على أقوى 5 فرص يومية مستمرة في 34 شركة قيادية...")
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
        await update.message.reply_text("مافي فرص يومية مستمرة قوية الحين، كلها بطيئة وحرقناها 🔥")
        return
    out = "🔥 أقوى 5 فرص يومية مستمرة:\n\n"
    for i, (sc, t, m) in enumerate(results[:5], 1):
        out += f"#{i} {m}\n\n"
    await update.message.reply_text(out.strip())

async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not trade_history:
        await update.message.reply_text("لسه ما انتهت أي فرصة، جرب الاثنين وشوف النتايج هنا")
        return
    wins = [x for x in trade_history if x['result'] == 'win']
    losses = [x for x in trade_history if x['result'] == 'loss']
    total = len(trade_history)
    win_rate = len(wins) / total * 100 if total else 0
    avg_profit = sum(x['profit'] for x in trade_history) / total if total else 0
    m = f"📊 تقييم البوت:\n\nعدد الفرص: {total}\nناجحة: {len(wins)} ✅\nفاشلة: {len(losses)} ❌\nنسبة النجاح: {win_rate:.0f}%\nمتوسط الربح: {avg_profit:.0f}%\n\nالتفاصيل:\n"
    for x in trade_history:
        m += f"{x['ticker']} {x['type']} - {x['profit']:.0f}% {'✅' if x['result']=='win' else '❌'}\n"
    if win_rate >= 60: m += f"\n🎉 البوت ناجح، تقدر تدخل بفلوس حقيقية بحذر"
    elif win_rate >= 40: m += f"\n⚠️ البوت متوسط، جرب أكثر قبل الفلوس"
    else: m += f"\n🛑 البوت ضعيف، لا تدخل بفلوس حقيقية"
    await update.message.reply_text(m)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_trades.pop(update.effective_chat.id, None)
    await update.message.reply_text("مسحت كل شي، نبدأ من جديد ✅")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("s", s_cmd))
app.add_handler(CommandHandler("best", best_cmd))
app.add_handler(CommandHandler("results", results_cmd))
app.add_handler(CommandHandler("clear", clear_cmd))
app.job_queue.run_repeating(check_targets, interval=60, first=10)
app.run_polling()
