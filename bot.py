import os
import time
import requests
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask

# منفذ وهمي عشان Render المجاني ما يطفي البوت
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running"

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "")
FINNHUB_API_KEY = os.getenv("MARKETDATA_TOKEN", "") or os.getenv("FINNHUB_API_KEY", "") or os.getenv("FINNHUB_TOKEN", "")
LAST_CHAT_ID = 0

print("BOT_TOKEN found:", bool(TELEGRAM_TOKEN))
print("FINNHUB found:", bool(FINNHUB_API_KEY))

COMPANIES = [
    "AAPL","NVDA","AMD","INTC","MU","QCOM","CRM","UBER","ABNB","SHOP",
    "SQ","PYPL","COIN","HOOD","PLTR","SOFI","NIO","RIVN","F","GM",
    "BAC","C","WFC","XOM","PFE","BA","DAL","UAL","AAL","LUV",
    "M","BBY","TGT","MRNA","MARA","RIOT","LCID","CVX","AVGO","MSFT"
]

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print("send error:", e)

def send_to_me(text):
    global LAST_CHAT_ID
    if LAST_CHAT_ID:
        send_telegram(LAST_CHAT_ID, text)

def get_quote(symbol):
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=10).json()
        return r
    except Exception as e:
        print("price error:", e)
        return {}

def get_candle(symbol):
    try:
        now = int(time.time())
        url = f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}&resolution=D&from={now-7*86400}&to={now}&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=10).json()
        if r.get("s") == "ok" and len(r.get("v", [])) >= 2:
            return r
        return {}
    except:
        return {}

def is_market_time():
    saudi = datetime.now(timezone(timedelta(hours=3)))
    if saudi.weekday() >= 5:
        return False
    h = saudi.hour + saudi.minute/60
    return 17 <= h < 24

def scan_market():
    if not is_market_time():
        return "⏰ السوق مقفل الان\nالبوت يشتغل وقت التداول بس: من 5 المغرب لـ 12 الليل، الاثنين للجمعة"
    strong, medium, rebound = [], [], []
    for sym in COMPANIES:
        q = get_quote(sym)
        price = q.get("c", 0)
        prev = q.get("pc", 0)
        if price == 0 or prev == 0:
            continue
        if not (50 <= price <= 200):
            continue
        change = (price - prev) / prev * 100
        candle = get_candle(sym)
        vol_ratio = 1.0
        liquidity = 0
        support = 0
        if candle:
            vols = candle.get("v", [])
            lows = candle.get("l", [])
            if len(vols) >= 2 and vols[-2] > 0:
                vol_ratio = vols[-1] / vols[-2]
            if vols:
                liquidity = price * vols[-1]
            if lows:
                support = min(lows[-3:])
        if liquidity < 10000000:
            continue
        if change >= 3 and vol_ratio >= 1.5:
            target = round(price * 1.04, 2)
            stop = round(price * 0.97, 2)
            strong.append(f"🔥 {sym}\nالسعر: ${price}\nالتغير: +{round(change,2)}% | الحجم: {round(vol_ratio,2)}x\n🎯 الهدف: ${target}\n🛑 الوقف: ${stop}")
        elif change >= 1.5 and vol_ratio >= 1.2:
            target = round(price * 1.025, 2)
            stop = round(price * 0.98, 2)
            medium.append(f"⚡ {sym}\nالسعر: ${price}\nالتغير: +{round(change,2)}% | الحجم: {round(vol_ratio,2)}x\n🎯 الهدف: ${target}\n🛑 الوقف: ${stop}")
        elif support > 0:
            dist = (price - support) / support * 100
            if 0 <= dist <= 2 and change > 0.3 and vol_ratio >= 1.2:
                target = round(price * 1.03, 2)
                stop = round(support * 0.99, 2)
                rebound.append(f"🔄 {sym}\nالسعر: ${price}\nالدعم: ${round(support,2)} | بعده: {round(dist,2)}%\nالتغير: +{round(change,2)}% | الحجم: {round(vol_ratio,2)}x\n🎯 الهدف: ${target}\n🛑 الوقف: ${stop} (تحت الدعم)")
        time.sleep(0.3)
    msg = "📊 فحص السوق الأمريكي (50$ - 200$):\n\n"
    if strong:
        msg += "🔥 فرص قوية:\n" + "\n\n".join(strong) + "\n\n"
    if medium:
        msg += "⚡ فرص متوسطة:\n" + "\n\n".join(medium) + "\n\n"
    if rebound:
        msg += "🔄 فرص ارتداد من الدعم:\n" + "\n\n".join(rebound) + "\n\n"
    if not strong and not medium and not rebound:
        msg += "لا توجد فرص قوية الان حسب الحجم والسيولة"
    return msg

def poll_telegram():
    global LAST_CHAT_ID
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=20"
            r = requests.get(url, timeout=30).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                chat = msg.get("chat", {}).get("id")
                text = msg.get("text", "").strip().lower()
                if chat:
                    if LAST_CHAT_ID != chat:
                        LAST_CHAT_ID = chat
                    if text == "/start":
                        send_telegram(chat, "✅ البوت شغال\nارسل /scan عشان تفحص السوق\nارسل /status عشان SPY و QQQ")
                    elif text == "/status":
                        spy = get_quote("SPY").get("c", 0)
                        qqq = get_quote("QQQ").get("c", 0)
                        send_telegram(chat, f"📊 الأسعار:\nSPY: ${spy}\nQQQ: ${qqq}")
                    elif text == "/scan":
                        send_telegram(chat, "🔍 جاري فحص 40 شركة...")
                        send_telegram(chat, scan_market())
                    elif text == "/price":
                        spy = get_quote("SPY").get("c", 0)
                        qqq = get_quote("QQQ").get("c", 0)
                        send_telegram(chat, f"📊 الأسعار:\nSPY: ${spy}\nQQQ: ${qqq}")
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)

def alerts_loop():
    while True:
        time.sleep(600)
        if LAST_CHAT_ID and is_market_time():
            try:
                send_to_me("⏰ فحص تلقائي:\n" + scan_market())
            except Exception as e:
                print(e)

if __name__ == "__main__":
    print("Bot started...")
    threading.Thread(target=poll_telegram, daemon=True).start()
    threading.Thread(target=alerts_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
