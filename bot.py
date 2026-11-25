import os, time, requests, threading
from datetime import datetime, date, time as dtime
import zoneinfo

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "")
MARKETDATA_TOKEN = os.getenv("MARKETDATA_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
RIYADH = zoneinfo.ZoneInfo("Asia/Riyadh")

# 37 سهم - 35 شركة + SPY + QQQ
UNIVERSE = ["SPY","QQQ","NVDA","AAPL","AMZN","META","GOOGL","TSLA","AMD","AVGO","PLTR","COIN","MSTR","SMCI","ARM","MU","NFLX","MSFT","SOFI","PFE","ET","INTC","BAC","F","UBER","HOOD","MARA","RIOT","CHWY","LCID","RIVN","AFRM","DKNG","PENN","X","CLF","FCX"]

sent_today = {}
counts = {"strong": 0, "medium": 0, "bounce": 0}
today_date = date.today()

def reset_daily():
    global sent_today, counts, today_date
    if date.today() != today_date:
        sent_today = {}; counts = {"strong": 0, "medium": 0, "bounce": 0}; today_date = date.today()

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN or not chat_id: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except: pass

def send_to_me(text):
    if CHAT_ID: send_telegram(CHAT_ID, text)

def md_get(path, params={}):
    try:
        p = {"token": MARKETDATA_TOKEN}; p.update(params)
        r = requests.get(f"https://api.marketdata.app/v1/{path}", params=p, timeout=15).json()
        if isinstance(r, list): return r[0] if r else {}
        return r
    except: return {}

def get_quote(sym):
    return md_get(f"stocks/quotes/{sym}/")

def get_candles(sym, res="D", count=60):
    return md_get(f"stocks/candles/{res}/{sym}/", {"count": count})

def ema(prices, n):
    if len(prices) < n: return 0
    k = 2/(n+1); e = prices[0]
    for p in prices[1:]: e = p*k + e*(1-k)
    return e

def rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains, losses = 0, 0
    for i in range(1, period+1):
        diff = prices[-i] - prices[-i-1]
        if diff > 0: gains += diff
        else: losses += -diff
    rs = gains / (losses if losses else 1)
    return 100 - (100/(1+rs))

def classify(sym, q, candles_d, candles_1h):
    price = q.get("last", 0) or q.get("close", 0) or 0
    if not price: return None
    # السعر 5$-200$ للشركات، SP
