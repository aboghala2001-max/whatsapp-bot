import os
import requests
import pandas as pd
import yfinance as yf
import time
import threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

SAUDI_TZ = ZoneInfo("Asia/Riyadh")
US_EASTERN = ZoneInfo("America/New_York")

PRICE_MIN = 5
PRICE_MAX = 200
MAX_GAIN_DAY = 8.0
MAX_STRONG = 3
MAX_MEDIUM = 3
MAX_BOUNCE = 3
SCAN_MIN = 5

alerts = {"strong": 0, "medium": 0, "bounce": 0}
last_sent = {}
active = {}
last_report = "no scan yet"
last_debug = "no debug yet"
last_time = None
is_scanning = False

def send_tg(chat_id, text):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print(e)

def send_me(text):
    if MY_CHAT_ID:
        send_tg(MY_CHAT_ID, text)

def get_rsi(s, p=14):
    d = s.diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = (-d.where(d < 0, 0)).rolling(p).mean()
    rs = g / l
    return 100 - (100 / (1 + rs))

def market_open():
    now = datetime.now(US_EASTERN)
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() <= dtime(16, 0)

def get_stocks():
    try:
        u = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        r = requests.get(u, timeout=30).json()
        out = []
        for x in r:
            s = x["symbol"]
            if "." not in s and "-" not in s:
                out.append(s)
        return out
    except Exception as e:
        print(e)
        return []

def check_stock(t):
    try:
        tk = yf.Ticker(t)
        info = tk.info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None, f"{t}: no price"
        if price < PRICE_MIN or price > PRICE_MAX:
            return None, f"{t}: price out"
        cap = info.get("marketCap", 0) or 0
        if cap < 1000000000:
            return None, f"{t}: small cap"
        ni = info.get("netIncomeToCommon")
        if ni is None or ni <= 0:
            return None, f"{t}: loss"
        h1 = tk.history(period="5d", interval="1h")
        d1 = tk.history(period="3mo", interval="1d")
        if len(h1) < 20 or len(d1) < 50:
            return None, f"{t}: no data"
        last = h1.iloc[-1]
        prev = d1.iloc[-2]
        lastd = d1.iloc[-1]
        chg = (lastd["Close"] - prev["Close"]) / prev["Close"] * 100
        if chg > MAX_GAIN_DAY:
            return None, f"{t}: up too much"
        d1["E20"] = d1["Close"].ewm(span=20).mean()
        d1["E50"] = d1["Close"].ewm(span=50).mean()
        d1["E200"] = d1["Close"].ewm(span=200).mean()
        h1["RSI"] = get_rsi(h1["Close"])
        e20 = d1["E20"].iloc[-1]
        e50 = d1["E50"].iloc[-1]
        e200 = d1["E200"].iloc[-1]
        if pd.isna(e200):
            e200 = price
        rsi = h1["RSI"].iloc[-1]
        avgv = d1["Volume"].tail(20).mean()
        curv = lastd["Volume"]
        vr = curv / avgv if avgv else 0
        dvol = lastd["Close"] * curv
        phigh = prev["High"]
        if cap > 5000000000 and dvol > 50000000 and vr > 2.0:
            if price > e20 and price > e50 and 50 <= rsi <= 65:
                if last["Close"] > phigh:
                    return {"t": t, "k": "strong", "p": price, "tg": price*1.10, "st": price*0.95, "rsi": rsi, "vr": vr, "dv": dvol, "cap": cap, "chg": chg}, "OK"
        if cap > 1000000000 and 10000000 <= dvol <= 50000000 and vr > 1.3:
            if price > e20 and 45 <= rsi <= 68:
                if last["Close"] >= phigh*0.98:
                    return {"t": t, "k": "medium", "p": price, "tg": price*1.06, "st": price*0.963, "rsi": rsi, "vr": vr, "dv": dvol, "cap": cap, "chg": chg}, "OK"
        sup = None
        for lvl in [e50, e200, d1["Low"].tail(60).min()]:
            low60 = d1["Low"].tail(60)
            cnt = ((low60 <= lvl*1.01) & (low60 >= lvl*0.99)).sum()
            if cnt >= 3 or abs(price-lvl)/lvl < 0.01:
                sup = lvl
                break
        at_sup = False
        if sup is not None:
            if abs(price-sup)/sup < 0.015:
                at_sup = True
        green = last["Close"] > last["Open"]
        rng = last["High"] - last["Low"]
        tail = False
        if rng > 0:
            if (min(last["Open"], last["Close"]) - last["Low"]) > rng*0.4:
                tail = True
        if cap > 1000000000 and dvol > 20000000 and vr > 1.5:
            if at_sup and 30 <= rsi <= 45 and (green or tail):
                return {"t": t, "k": "bounce", "p": price, "tg": price*1.053, "st": price*0.98, "rsi": rsi, "vr": vr, "dv": dvol, "cap": cap, "chg": chg}, "OK"
        return None, f"{t}: no match"
    except Exception as e:
        return None, f"{t}: err {e}"

def fmt(tr):
    k = tr["k"]
    if k == "strong":
        title = "STRONG"
        act = "big amount"
        risk = "high"
    elif k == "medium":
        title = "MEDIUM"
        act = "medium amount, check chart"
        risk = "medium"
    else:
        title = "BOUNCE"
        act = "medium amount, check support"
        risk = "low"
    pct_t = (tr["tg"]/tr["p"]-1)*100
    pct_s = (tr["st"]/tr["p"]-1)*100
    return f"{title} {tr['t']}\nEntry: {tr['p']:.2f}\nTarget: {tr['tg']:.2f} (+{pct_t:.1f}%)\nStop: {tr['st']:.2f} ({pct_s:.1f}%)\nRSI: {tr['rsi']:.0f} Vol: x{tr['vr']:.1f} Liq: {tr['dv']/1e6:.0f}M Cap: {tr['cap']/1e9:.1f}B Day: {tr['chg']:.1f}% Risk: {risk}\n{act}"

def check_active():
    while True:
        try:
            if market_open() and active:
                for t in list(active.keys()):
                    tr = active[t]
                    try:
                        cur = yf.Ticker(t).history(period="1d", interval="1m").iloc[-1]["Close"]
                        pnl = (cur/tr["p"]-1)*100
                        if cur >= tr["tg"]:
                            send_me(f"TARGET HIT {t} Entry {tr['p']:.2f} Now {cur:.2f} {pnl:+.1f}%")
                            del active[t]
                        elif cur <= tr["st"]:
                            send_me(f"STOP HIT {t} Entry {tr['p']:.2f} Now {cur:.2f} {pnl:+.1f}%")
                            del active[t]
                    except:
                        pass
        except:
            pass
        time.sleep(60)

def main_loop(manual=False):
    global last_report, last_debug, last_time, is_scanning
    if is_scanning:
        return "scanning now"
    is_scanning = True
    tickers = get_stocks()
    found = []
    deb = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(check_stock, t): t for t in tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                tr, msg = fut.result()
                if tr:
                    k = tr["k"]
                    lim = {"strong": MAX_STRONG, "medium": MAX_MEDIUM, "bounce": MAX_BOUNCE}[k]
                    if alerts[k] >= lim:
                        continue
                    if t in last_sent:
                        dt = (datetime.now(SAUDI_TZ) - last_sent[t]).total_seconds()
                        if dt < 3600:
                            continue
                    alerts[k] += 1
                    last_sent[t] = datetime.now(SAUDI_TZ)
                    active[t] = tr
                    send_me(fmt(tr))
                    found.append(tr)
                else:
                    deb.append(msg)
            except Exception as e:
                deb.append(f"{t}: {e}")
    last_time = datetime.now(SAUDI_TZ)
    last_report = f"Last: {last_time.strftime('%I:%M %p')} Found {len(found)} Checked {len(tickers)}"
    last_debug = "\n".join(deb[:50])
    is_scanning = False
    return f"Done found {len(found)}"

def daily_reset():
    global alerts, active
    while True:
        now = datetime.now(SAUDI_TZ)
        if now.hour == 0 and now.minute == 0:
            alerts = {"strong": 0, "medium": 0, "bounce": 0}
            active = {}
            last_sent.clear()
            send_me("Reset done")
            time.sleep(61)
        time.sleep(30)

def handle(chat_id, text):
    if text == "/start":
        send_tg(chat_id, "Bot ON. /help")
    elif text == "/help":
        send_tg(chat_id, "/scan /status /report /debug /active /id /help")
    elif text == "/id":
        send_tg(chat_id, f"ID: {chat_id}")
    elif text == "/scan":
        send_tg(chat_id, "Scanning...")
        send_tg(chat_id, main_loop(True))
    elif text == "/status":
        s = f"Last: {last_time}\nOpen: {market_open()}\nStrong {alerts['strong']}/{MAX_STRONG}\nMedium {alerts['medium']}/{MAX_MEDIUM}\nBounce {alerts['bounce']}/{MAX_BOUNCE}\nActive: {len(active)}"
        send_tg(chat_id, s)
    elif text == "/report":
        send_tg(chat_id, last_report)
    elif text == "/debug":
        send_tg(chat_id, last_debug[:3000])
    elif text == "/active":
        if not active:
            send_tg(chat_id, "No active")
        else:
            m = "Active:\n"
            for t, tr in active.items():
                try:
                    cur = yf.Ticker(t).history(period="1d", interval="1m").iloc[-1]["Close"]
                    pnl = (cur/tr["p"]-1)*100
                    m += f"{t} {tr['p']:.2f} Now {cur:.2f} {pnl:+.1f}% TG {tr['tg']:.2f} ST {tr['st']:.2f}\n"
                except:
                    m += f"{t} {tr['p']:.2f} TG {tr['tg']:.2f} ST {tr['st']:.2f}\n"
            send_tg(chat_id, m)
    else:
        send_tg(chat_id, "Use /help")

def polling():
    off = 0
    while True:
        try:
            u = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={off}&timeout=30"
            r = requests.get(u, timeout=35).json()
            for upd in r.get("result", []):
                off = upd["update_id"] + 1
                if "message" in upd:
                    handle(upd["message"]["chat"]["id"], upd["message"].get("text", ""))
        except Exception as e:
            print(e)
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=daily_reset, daemon=True).start()
    threading.Thread(target=polling, daemon=True).start()
    threading.Thread(target=check_active, daemon=True).start()
    send_me("Bot ON - 5-200$ - from 5:30")
    while True:
        if market_open():
            main_loop()
        time.sleep(SCAN_MIN * 60)
