import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
import asyncio
import urllib.request
import urllib.parse
import json

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SAUDI_TZ = pytz.timezone("Asia/Riyadh")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def log_message(self, *args):
        pass

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

def send_msg(text, chat_id=None):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id or CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Sent OK: {resp.status}")
            return True
    except Exception as e:
        print(f"Send FAILED: {e}")
        return False

WATCHLIST = ["PLTR", "AMD", "MU", "HOOD", "SOFI", "INTC", "SNAP", "U", "DKNG", "AFRM", "UPST", "OPEN", "RBLX", "SHOP", "SQ", "PYPL", "TSLA", "RIVN", "LCID", "NIO", "QS", "CHPT", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF", "CCL", "AAL", "UAL", "DAL", "NCLH", "ET", "F", "BAC", "WBD", "PARA", "CPNG", "NU"]

MAX_ALERTS = {"strong": 3, "medium": 3, "bounce": 3}
last_sent = {}
alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
last_reset_day = None

def is_work_time():
    now = datetime.now(SAUDI_TZ)
    return 17 <= now.hour <= 23

def reset_daily():
    global alerts_count, last_reset_day
    today = datetime.now(SAUDI_TZ).date()
    if last_reset_day != today:
        alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
        last_reset_day = today

def get_data(ticker):
    try:
        df_1h = yf.download(ticker, period="5d", interval="1h", progress=False)
        df_1d = yf.download(ticker, period="60d", interval="1d", progress=False)
        if df_1h.empty or df_1d.empty:
            return None
        if isinstance(df_1h.columns, pd.MultiIndex):
            df_1h.columns = df_1h.columns.get_level_values(0)
        if isinstance(df_1d.columns, pd.MultiIndex):
            df_1d.columns = df_1d.columns.get_level_values(0)
        return df_1h, df_1d
    except Exception as e:
        print(f"Data error {ticker}: {e}")
        return None

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def check_stock(ticker):
    data = get_data(ticker)
    if not data:
        return None
    df_1h, df_1d = data
    price = float(df_1h["Close"].iloc[-1])
    if not (5 <= price <= 300):
        return None
    ema20_1h = float(df_1h["Close"].ewm(span=20).mean().iloc[-1])
    ema50_1h = float(df_1h["Close"].ewm(span=50).mean().iloc[-1])
    ema50_1d = float(df_1d["Close"].ewm(span=50).mean().iloc[-1])
    ema200_1d = float(df_1d["Close"].ewm(span=200).mean().iloc[-1])
    rsi_1h = float(calc_rsi(df_1h["Close"]).iloc[-1])
    vol_today = float(df_1h["Volume"].iloc[-1])
    vol_avg = float(df_1h["Volume"].tail(20).mean())
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0
    liquidity = price * vol_today * 6
    last_close = float(df_1h["Close"].iloc[-1])
    last_open = float(df_1h["Open"].iloc[-1])
    last_low = float(df_1h["Low"].iloc[-1])
    prev_high_1d = float(df_1d["High"].iloc[-2])
    open_today = float(df_1d["Open"].iloc[-1])
    day_change = ((last_close - open_today) / open_today) * 100
    if day_change > 8:
        return None
    if (liquidity > 50000000 and vol_ratio > 2.0 and price > ema20_1h and price > ema50_1h and 50 <= rsi_1h <= 65 and last_close > prev_high_1d):
        target = price * 1.10
        stop = price * 0.95
        msg = "🔥 فرصة قوية - " + ticker + " | السعر: " + str(round(price,2)) + "$ | السيولة: " + str(round(liquidity/1000000)) + "M | RSI: " + str(round(rsi_1h)) + " | الهدف: " + str(round(target,2)) + "$ | الوقف: " + str(round(stop,2)) + "$"
        return ("strong", msg)
    if (10000000 <= liquidity <= 50000000 and vol_ratio > 1.3 and price > ema20_1h and 45 <= rsi_1h <= 68 and last_close >= prev_high_1d * 0.99):
        target = price * 1.06
        stop = price * 0.963
        msg = "⚡ فرصة متوسطة - " + ticker + " | السعر: " + str(round(price,2)) + "$ | السيولة: " + str(round(liquidity/1000000)) + "M | RSI: " + str(round(rsi_1h)) + " | الهدف: " + str(round(target,2)) + "$ | الوقف: " + str(round(stop,2)) + "$"
        return ("medium", msg)
    near_support = abs(price - ema50_1d) / ema50_1d < 0.01 or abs(price - ema200_1d) / ema200_1d < 0.01
    low_20 = float(df_1d["Low"].tail(20).min())
    near_low = abs(price - low_20) / low_20 < 0.015
    is_green_after_support = last_close > last_open
    has_long_tail = (last_close - last_low) > (last_close - last_open) * 1.5 if (last_close - last_open) != 0 else False
    if (liquidity > 20000000 and vol_ratio > 1.5 and (near_support or near_low) and 30 <= rsi_1h <= 45 and (is_green_after_support or has_long_tail)):
        support = ema50_1d if abs(price - ema50_1d) < abs(price - ema200_1d) else ema200_1d
        if near_low and not near_support:
            support = low_20
        target = price * 1.053
        stop = price * 0.98
        msg = "🛡️ فرصة ارتداد - " + ticker + " | السعر: " + str(round(price,2)) + "$ | الدعم: " + str(round(support,2)) + "$ | RSI: " + str(round(rsi_1h)) + " | الهدف: " + str(round(target,2)) + "$ | الوقف: " + str(round(stop,2)) + "$"
        return ("bounce", msg)
    return None

last_update_id = 0

def get_updates():
    global last_update_id
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?timeout=5&offset={last_update_id+1}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("result", [])
    except Exception as e:
        print(f"getUpdates error: {e}")
        return []

async def listen_commands():
    global last_update_id
    while True:
        for upd in get_updates():
            last_update_id = upd["update_id"]
            msg = upd.get("message", {})
            text = (msg.get("text") or "").strip()
            chat = str(msg.get("chat", {}).get("id", ""))
            if not text:
                continue
            print(f"Received: {text} from {chat}")
            if text in ["/start", "start", "السلام عليكم", "هلا"]:
                send_msg("✅ هلا فيك! البوت شغال.\nارسل /الحالة عشان تشوف الحالة.", chat)
            elif text in ["/status", "/الحالة", "الحالة", "حالة"]:
                now = datetime.now(SAUDI_TZ)
                work = "داخل وقت العمل (5م-12ص) ✅" if is_work_time() else "خارج وقت العمل (5م-12ص) ⏸️ - الفرص متوقفة، بس انا ارد عليك"
                send_msg(f"📊 الحالة:\nالوقت: {now.strftime('%I:%M %p')}\n{work}\nفرص قوية اليوم: {alerts_count['strong']}/3\nفرص متوسطة: {alerts_count['medium']}/3\nفرص ارتداد: {alerts_count['bounce']}/3\nعدد الأسهم المراقبة: {len(WATCHLIST)}", chat)
            elif text in ["/id", "رقمي", "ايدي"]:
                send_msg(f"رقمك هو: {chat}", chat)
            else:
                send_msg("وصلتني رسالتك 👍\nاكتب /الحالة عشان تشوف حالة البوت.", chat)
        await asyncio.sleep(2)

async def main_loop():
    while True:
        reset_daily()
        if is_work_time():
            for ticker in WATCHLIST:
                if ticker in last_sent:
                    minutes_passed = (datetime.now(SAUDI_TZ) - last_sent[ticker]).total_seconds() / 60
                    if minutes_passed < 60:
                        continue
                result = check_stock(ticker)
                if result:
                    kind, msg = result
                    if alerts_count[kind] < MAX_ALERTS[kind]:
                        send_msg(msg)
                        alerts_count[kind] += 1
                        last_sent[ticker] = datetime.now(SAUDI_TZ)
                        print(f"Sent {kind} {ticker}")
                await asyncio.sleep(1)
            print("انتهت الجولة، انتظار 5 دقايق...")
            await asyncio.sleep(300)
        else:
            print("خارج وقت العمل 5م - 12ص، انتظار فرص...")
            await asyncio.sleep(300)

async def test_start():
    send_msg("✅ البوت اشتغل واتصل فيك بنجاح")
    await asyncio.gather(main_loop(), listen_commands())

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    asyncio.run(test_start())
