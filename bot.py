import os, asyncio, threading, requests, csv, io, re
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

def clean_symbol(s):
    return re.sub(r'[^A-Za-z]', '', s.upper().strip())

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains=[]; losses=[]
    for i in range(1, period+1):
        diff = closes[-i] - closes[-i-1]
        gains.append(max(diff,0)); losses.append(max(-diff,0))
    ag=sum(gains)/period; al=sum(losses)/period
    if al==0: return 100
    rs=ag/al
    return 100 - (100/(1+rs))

def get_data_stooq(symbol):
    url=f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    r=requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
    if "Date" not in r.text: return None
    reader=csv.DictReader(io.StringIO(r.text))
    rows=[x for x in reader if x['Close']!='N/D']
    if len(rows)<20: return None
    return rows

def get_data_yahoo(symbol):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=3mo&interval=1d"
    r=requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
    j=r.json()
    res=j['chart']['result'][0]
    q=res['indicators']['quote'][0]
    rows=[]
    for c,h,l,v in zip(q['close'], q['high'], q['low'], q['volume']):
        if c is None: continue
        rows.append({'Close':str(c),'High':str(h),'Low':str(l),'Volume':str(v or 0)})
    if len(rows)<20: return None
    return rows

def get_analysis(raw):
    try:
        symbol=clean_symbol(raw)
        if not symbol: return "اكتب السهم كذا: /s NVDA"
        rows=None
        try: rows=get_data_stooq(symbol)
        except: rows=None
        if not rows:
            try: rows=get_data_yahoo(symbol)
            except: rows=None
        if not rows:
            return f"ما لقيت السهم {symbol} ❌\nجرب: /s AAPL"

        closes=[float(x['Close']) for x in rows]
        highs=[float(x['High']) for x in rows]
        lows=[float(x['Low']) for x in rows]
        vols=[float(x.get('Volume',0) or 0) for x in rows]

        price=closes[-1]; prev=closes[-2]
        change=price-prev; change_pct=(change/prev)*100
        sma20=sum(closes[-20:])/20
        sma50=sum(closes[-50:])/50 if len(closes)>=50 else sum(closes)/len(closes)
        rsi=calc_rsi(closes)
        support=min(lows[-20:]); resistance=max(highs[-20:])
        change_5d=((closes[-1]-closes[-6])/closes[-6])*100 if len(closes)>=6 else 0
        change_1m=((closes[-1]-closes[-22])/closes[-22])*100 if len(closes)>=22 else 0

        # --- السيولة ---
        avg_vol=sum(vols[-20:-1])/19 if len(vols)>=20 else sum(vols)/len(vols)
        today_vol=vols[-1]
        vol_ratio=(today_vol/avg_vol*100) if avg_vol>0 else 100

        if change_pct>0 and today_vol>avg_vol:
            liq="🟢 السيولة داخلة شراء - قوة شرائية"
            liq_dir="شراء"
        elif change_pct<0 and today_vol>avg_vol:
            liq="🔴 السيولة خارجة بيع - ضغط بيع"
            liq_dir="بيع"
        elif today_vol<avg_vol*0.7:
            liq="⚪ سيولة ضعيفة - السوق متردد"
            liq_dir="متردد"
        else:
            liq="🟡 سيولة طبيعية"
            liq_dir="طبيعي"

        # --- CALL / PUT ---
        dist_support=((price-support)/price)*100
        dist_resist=((resistance-price)/price)*100

        if price>sma20 and price>sma50 and rsi>=50 and rsi<70 and change_pct>0:
            option="🟢 اشتري CALL"
            option_reason="السعر فوق المتوسطات + RSI ايجابي + سيولة شراء"
            entry=f"الدخول: فوق {price:.2f}$"
            target=f"الهدف: {resistance:.2f}$"
            stop=f"وقف: {sma20:.2f}$"
            duration="1 - 3 أيام - فرصة سريعة"
            if vol_ratio>150: duration="1 - 2 يوم - زخم قوي جدا، لا تطول"
            elif rsi>60: duration="2 - 5 أيام"
            else: duration="5 - 10 أيام - فرصة سوينق"
        elif price<sma20 and price<sma50 and rsi<50 and change_pct<0:
            option="🔴 اشتري PUT"
            option_reason="السعر تحت المتوسطات + RSI سلبي + سيولة بيع"
            entry=f"الدخول: تحت {price:.2f}$"
            target=f"الهدف: {support:.2f}$"
            stop=f"وقف: {sma20:.2f}$"
            duration="1 - 3 أيام - فرصة سريعة"
            if vol_ratio>150: duration="1 - 2 يوم - زخم قوي جدا، لا تطول"
            elif rsi<40: duration="2 - 5 أيام"
            else: duration="5 - 10 أيام - فرصة سوينق"
        elif rsi<=30 and dist_support<3:
            option="🟢 CALL انعكاس - بحذر"
            option_reason=f"RSI {rsi:.0f} تشبع بيع + السعر قريب من الدعم {support:.2f}$"
            entry=f"الدخول: من الدعم {support:.2f}$"
            target=f"الهدف: {sma20:.2f}$"
            stop=f"وقف: كسر {support:.2f}$"
            duration="1 - 3 أيام - مضاربة سريعة فقط"
        elif rsi>=70 and dist_resist<3:
            option="🔴 PUT انعكاس - بحذر"
            option_reason=f"RSI {rsi:.0f} تشبع شراء + السعر قريب من المقاومة {resistance:.2f}$"
            entry=f"الدخول: من المقاومة {resistance:.2f}$"
            target=f"الهدف: {sma20:.2f}$"
            stop=f"وقف: اختراق {resistance:.2f}$"
            duration="1 - 3 أيام - مضاربة سريعة فقط"
        else:
            option="⏸️ لا تدخل الحين"
            option_reason="لا يوجد اتجاه واضح، انتظر اختراق او كسر"
            entry=f"انتظر اختراق {resistance:.2f}$ للـ CALL او كسر {support:.2f}$ للـ PUT"
            target="-"
            stop="-"
            duration="لا توجد فرصة واضحة"

        if rsi>=70: rsi_txt=f"{rsi:.0f} - تشبع شراء ⚠️"
        elif rsi<=30: rsi_txt=f"{rsi:.0f} - تشبع بيع 🟢"
        else: rsi_txt=f"{rsi:.0f} - طبيعي"

        if price>sma20 and price>sma50: trend="صاعد 📈"
        elif price<sma20 and price<sma50: trend="هابط 📉"
        else: trend="متذبذب ↔️"

        return f"""📊 تحليل {symbol}

💰 السعر: {price:.2f}$
📈 اليوم: {change:+.2f}$ ({change_pct:+.2f}%)
📅 5 أيام: {change_5d:+.2f}%
📅 شهر: {change_1m:+.2f}%

🔍 الاتجاه: {trend}
📏 متوسط 20: {sma20:.2f}$ | 50: {sma50:.2f}$
📊 RSI: {rsi_txt}

🟢 دعم: {support:.2f}$ ({dist_support:.1f}%)
🔴 مقاومة: {resistance:.2f}$ ({dist_resist:.1f}%)

💧 السيولة:
{liq}
📊 حجم اليوم vs المتوسط: {vol_ratio:.0f}%

🎯 قرار الأوبشن:
{option}
💡 السبب: {option_reason}
📍 {entry}
🎯 {target}
🛑 {stop}
⏳ مدة الفرصة: {duration}
"""
    except Exception as e:
        return f"صار خطأ، جرب مرة ثانية: /s {raw}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("شغال ✅\nارسل /s NVDA")
async def s_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ارسل كذا: /s NVDA"); return
    await update.message.reply_text(get_analysis(context.args[0]))
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip()
    parts=text.split()
    if parts[0].lower() in ["/s","s"] and len(parts)>1:
        await update.message.reply_text(get_analysis(parts[1]))

app=ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("s", s_cmd))
app.add_handler(MessageHandler(filters.TEXT, handle_text))
app.run_polling()
