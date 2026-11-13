import os, yfinance as yf, datetime, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

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

def get_expiry():
    today = datetime.date.today()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0: days_ahead = 7
    return today + datetime.timedelta(days=days_ahead)

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
    return {"price":price,"rsi":rsi,"vwap":vwap,"mom15":mom15,"mom1h":mom1h,"trend":trend3,"liq":liq,
            "sup_n":sup_n,"sup_f":sup_f,"res_n":res_n,"res_f":res_f,
            "strike":strike,"cprice":contract_price,"iv":iv,"delta":delta_g,"theta":theta,
            "expiry":expiry,"earn":get_earnings(ticker),"is_call_guess":is_call_guess}

def format_msg(ticker, d):
    breakeven = d['strike'] + d['cprice'] + (COMMISSION/100) if d['is_call_guess'] else d['strike'] - d['cprice'] - (COMMISSION/100)
    stop = d['cprice'] * 0.70
    t1 = d['cprice'] * 1.50
    t2 = d['cprice'] * 2.0
    total = d['cprice'] * 100 + COMMISSION
    success_price = d['cprice'] + SUCCESS_MOVE
    is_call = d['mom15'] >= 0.50 and d['liq'] >= 1.2 and d['trend'] > 0
    is_put = d['mom15'] <= -0.50 and d['liq'] >= 1.2 and d['trend'] < 0
    if d['cprice'] > MAX_CONTRACT_PRICE: dec = "تخطي - غالي"
    elif is_call: dec = "CALL - ادخل"
    elif is_put: dec = "PUT - ادخل"
    else: dec = "انتظار ⚠️"
    theta_warn = "ثيتا عالي!" if d['theta'] < -0.25 else "ثيتا مريح"
    # 6 أسطر فقط - مختصر
    m = f"📊 {ticker} ${d['price']:.2f} ({d['mom15']:+.2f}%) | 🎯 {dec} | ⏱️ {d['expiry'].strftime('%d %b')}\n"
    m += f"📉 RSI {d['rsi']:.0f} | VWAP ${d['vwap']:.1f} | زخم {d['mom1h']:+.2f}% | اتجاه {d['trend']:+.2f}% | سيولة {d['liq']:.1f}x | دلتا {d['delta']:.2f} | ثيتا {d['theta']:.2f} | IV {d['iv']:.0f}%\n"
    m += f"💰 سترايك {d['strike']:.0f}$ | عقد {d['cprice']:.2f}$ | إجمالي 1 عقد {total:.0f}$ شامل عمولة | تعادل {breakeven:.2f}$\n"
    m += f"🛡️ دعم {d['sup_n']:.1f}$ / {d['sup_f']:.1f}$ | 🎯 مقاومة {d['res_n']:.1f}$ / {d['res_f']:.1f}$\n"
    m += f"🛑 وقف {stop:.2f}$ (-30%) | 🎯 هدف1 {t1:.2f}$ (+50%) | 🎯 هدف2 {t2:.2f}$ (+100%) | ✅ نجاح {success_price:.2f}$ (+0.20$)\n"
    m += f"{d['earn']} | {theta_warn} | انعكاس 0.4% | 🔔 تنبيه عند الهدف والوقف"
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

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_trades.pop(update.effective_chat.id, None)
    await update.message.reply_text("مسحت كل شي، نبدأ من جديد ✅")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("s", s_cmd))
app.add_handler(CommandHandler("clear", clear_cmd))
app.job_queue.run_repeating(check_targets, interval=60, first=10)
app.run_polling()
