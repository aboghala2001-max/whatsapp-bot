import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import time
import threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============ الإعدادات ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

SAUDI_TZ = ZoneInfo("Asia/Riyadh")
US_EASTERN = ZoneInfo("America/New_York")

# الشروط العامة
PRICE_MIN = 5
PRICE_MAX = 200
NET_INCOME_MIN = 0
MAX_GAIN_DAY = 8.0  # لا يرسل اذا صاعد اكثر من 8% اليوم

# عدد التنبيهات
MAX_ALERTS_STRONG = 3
MAX_ALERTS_MEDIUM = 3
MAX_ALERTS_BOUNCE = 3

# الفحص كل كم دقيقة
SCAN_INTERVAL_MIN = 5

# ============ الحالة ============
alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
last_sent = {}  # ticker -> آخر وقت ارسل فيه
active_trades = {}  # ticker -> trade info
last_scan_report = "لا يوجد فحص بعد"
last_debug_report = "لا يوجد تشخيص بعد"
last_scan_time = None
is_scanning = False
last_update_id = 0

# ============ أدوات ============
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("No Telegram Token")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print(f"Telegram Error: {e}")

def send_to_me(text):
    if MY_CHAT_ID:
        send_telegram(MY_CHAT_ID, text)

def get_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def is_market_open():
    now_et = datetime.now(US_EASTERN)
    if now_et.weekday() >= 5:
        return False
    market_open = dtime(9, 30)
    market_close = dtime(16, 0)
    return market_open <= now_et.time() <= market_close

# ============ جلب الأسهم ============
def get_all_us_stocks():
    try:
        url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=30).json()
        tickers = [x["symbol"] for x in r if "." not in x["symbol"] and "-" not in x["symbol"]]
        return tickers
    except Exception as e:
        print(f"Finnhub Error: {e}")
        return []

# ============ الفحص الرئيسي لكل سهم ============
def check_stock(ticker):
    try:
        tk = yf.Ticker(ticker)
        info = tk.info

        # --- الشروط العامة ---
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price or not (PRICE_MIN <= price <= PRICE_MAX):
            return None, f"{ticker}: السعر {price} خارج النطاق 5-200"

        market_cap = info.get("marketCap", 0) or 0
        if market_cap < 1_000_000_000:  # اقل شي 1B للكل
            return None, f"{ticker}: قيمة سوقية صغيرة {market_cap/1e9:.2f}B"

        net_income = info.get("netIncomeToCommon")
        if net_income is None or net_income <= NET_INCOME_MIN:
            return None, f"{ticker}: الشركة خسرانة"

        # البيانات
        df_1h = tk.history(period="5d", interval="1h")
        df_1d = tk.history(period="3mo", interval="1d")
        if len(df_1h) < 20 or len(df_1d) < 50:
            return None, f"{ticker}: بيانات ناقصة"

        last = df_1h.iloc[-1]
        prev_day = df_1d.iloc[-2]
        last_day = df_1d.iloc[-1]

        # صعود اليوم
        day_change_pct = ((last_day["Close"] - prev_day["Close"]) / prev_day["Close"]) * 100
        if day_change_pct > MAX_GAIN_DAY:
            return None, f"{ticker}: صاعد {day_change_pct:.1f}% اكثر من 8%"

        # المؤشرات
        df_1d["EMA20"] = df_1d["Close"].ewm(span=20).mean()
        df_1d["EMA50"] = df_1d["Close"].ewm(span=50).mean()
        df_1d["EMA200"] = df_1d["Close"].ewm(span=200).mean()
        df_1h["RSI"] = get_rsi(df_1h["Close"])

        ema20 = df_1d["EMA20"].iloc[-1]
        ema50 = df_1d["EMA50"].iloc[-1]
        ema200 = df_1d["EMA200"].iloc[-1] if not pd.isna(df_1d["EMA200"].iloc[-1]) else price
        rsi_1h = df_1h["RSI"].iloc[-1]

        # السيولة والحجم
        avg_vol_20 = df_1d["Volume"].tail(20).mean()
        curr_vol = last_day["Volume"]
        vol_ratio = curr_vol / avg_vol_20 if avg_vol_20 else 0
        dollar_volume = last_day["Close"] * curr_vol

        prev_high = prev_day["High"]
        prev_low = prev_day["Low"]

        # ============ 1. فرصة قوية - اختراق ============
        if (market_cap > 5_000_000_000 and dollar_volume > 50_000_000 and vol_ratio > 2.0
            and price > ema20 and price > ema50
            and 50 <= rsi_1h <= 65
            and last["Close"] > prev_high):
            return {
                "ticker": ticker, "kind": "strong", "price": price,
                "target": price * 1.10, "stop": price * 0.95,
                "rsi": rsi_1h, "vol_ratio": vol_ratio, "dollar_vol": dollar_volume,
                "market_cap": market_cap, "day_change": day_change_pct
            }, "OK"

        # ============ 2. فرصة متوسطة - اختراق هادي ============
        if (market_cap > 1_000_000_000 and 10_000_000 <= dollar_volume <= 50_000_000
            and vol_ratio > 1.3
            and price > ema20
            and 45 <= rsi_1h <= 68
            and last["Close"] >= prev_high * 0.98):
            return {
                "ticker": ticker, "kind": "medium", "price": price,
                "target": price * 1.06, "stop": price * 0.963,
                "rsi": rsi_1h, "vol_ratio": vol_ratio, "dollar_vol": dollar_volume,
                "market_cap": market_cap, "day_change": day_change_pct
            }, "OK"

        # ============ 3. فرصة ارتداد - من الدعم ============
        support = None
        lows = df_1d["Low"].tail(60)
        for level in [ema50, ema200, lows.min()]:
            touches = ((df_1d["Low"].tail(60) <= level * 1.01) & (df_1d["Low"].tail(60) >= level * 0.99)).sum()
            if touches >= 3 or abs(price - level) / level < 0.01:
                support = level
                break

        is_at_support = support is not None and abs(price - support) / support < 0.015
        is_green_candle = last["Close"] > last["Open"]
        has_long_tail = (min(last["Open"], last["Close"]) - last["Low"]) > (last["High"] - last["Low"]) * 0.4 if (last["High"] - last["Low"]) > 0 else False

        if (market_cap > 1_000_000_000 and dollar_volume > 20_000_000
            and vol_ratio > 1.5
            and is_at_support
            and 30 <= rsi_1h <= 45
            and (is_green_candle or has_long_tail)):
            return {
                "ticker": ticker, "kind": "bounce", "price": price,
                "target": price * 1.053, "stop": price * 0.98,
                "rsi": rsi_1h, "vol_ratio": vol_ratio, "dollar_vol": dollar_volume,
                "market_cap": market_cap, "day_change": day_change_pct,
                "support": support
            }, "OK"

        return None, f"{ticker}: لا تنطبق شروط أي نوع | RSI {rsi_1h:.0f} | Vol x{vol_ratio:.1f} | $Vol {dollar_volume/1e6:.0f}M | Price {price:.2f} | MC {market_cap/1e9:.1f}B"

    except Exception as e:
        return None, f"{ticker}: خطأ {e}"

# ============ إضافة صفقة نشطة ============
def add_active(trade):
    ticker = trade["ticker"]
    active_trades[ticker] = {
        **trade,
        "entry_time": datetime.now(SAUDI_TZ),
        "entry_price": trade["price"]
    }

# ============ تنسيق الرسالة ============
def format_message(trade):
    kind = trade["kind"]
    if kind == "strong":
        title = "🔥 فرصة قوية - اختراق"
        action = "تدخل بمبلغ كبير، بدون ما تشوف الشارت"
        risk = "عالية"
    elif kind == "medium":
        title = "⚡ فرصة متوسطة - اختراق هادي"
        action = "مبلغ متوسط، لازم تشوف الشارت وتتأكد من الدعم"
        risk = "متوسطة"
    else:
        title = "🛡️ فرصة ارتداد - من الدعم"
        action = "مبلغ متوسط، تأكد ان الدعم ما انكسر"
        risk = "منخفضة"

    return f"""
{title}
<b>{trade['ticker']}</b>

💰 الدخول: {trade['price']:.2f}$
🎯 الهدف: {trade['target']:.2f}$ (+{((trade['target']/trade['price']-1)*100):.1f}%)
🛑 الوقف: {trade['stop']:.2f}$ ({((trade['stop']/trade['price']-1)*100):.1f}%)

📊 RSI (1H): {trade['rsi']:.0f}
📈 الحجم: x{trade['vol_ratio']:.1f}
💵 السيولة: {trade['dollar_vol']/1e6:.0f}M
🏢 القيمة السوقية: {trade['market_cap']/1e9:.1f}B
📅 صعود اليوم: {trade['day_change']:.1f}%
⚠️ المخاطرة: {risk}

👉 {action}
"""

# ============ الفحص الكامل ============
def main_loop(manual=False):
    global last_scan_report, last_debug_report, last_scan_time, is_scanning
    if is_scanning:
        return "الفحص شغال الحين، انتظر"
    is_scanning = True

    tickers = get_all_us_stocks()
    found = []
    debug_lines = []

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(check_stock, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                trade, msg = fut.result()
                if trade:
                    kind = trade["kind"]
                    limit = {"strong": MAX_ALERTS_STRONG, "medium": MAX_ALERTS_MEDIUM, "bounce": MAX_ALERTS_BOUNCE}[kind]
                    if alerts_count[kind] >= limit:
                        continue

                    if ticker in last_sent:
                        last_time = last_sent[ticker]
                        if (datetime.now(SAUDI_TZ) - last_time).total_seconds() < 3600:
                            continue

                    alerts_count[kind] += 1
                    last_sent[ticker] = datetime.now(SAUDI_TZ)
                    add_active(trade)
                    found.append(trade)
                    send_to_me(format_message(trade))
                else:
                    debug_lines.append(msg)
            except Exception as e:
                debug_lines.append(f"{ticker}: خطأ {e}")

    last_scan_time = datetime.now(SAUDI_TZ)
    last_scan_report = f"آخر فحص: {last_scan_time.strftime('%H:%M:%S')} - وجد {len(found)} فرصة - قوية {alerts_count['strong']}/3 - متوسطة {alerts_count['medium']}/3 - ارتداد {alerts_count['bounce']}/3"
    last_debug_report = "\n".join(debug_lines[:50])
    is_scanning = False
    return last_scan_report

# ============ أوامر التليجرام ============
def poll_telegram():
    global last_update_id
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            r = requests.get(url, params={"timeout": 30, "offset": last_update_id + 1}, timeout=35).json()
            for upd in r.get("result", []):
                last_update_id = upd["update_id"]
                msg = upd.get("message", {})
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat", {}).get("id")

                if text == "افحص":
                    send_telegram(chat, "🔍 جاري الفحص الحين...")
                    res = main_loop(manual=True)
                    send_telegram(chat, f"✅ خلص الفحص: {res}\n\n{last_scan_report}")

                elif text == "الحالة":
                    send_telegram(chat, f"""📊 الحالة

🔥 قوية: {alerts_count['strong']}/3
⚡ متوسطة: {alerts_count['medium']}/3
🛡️ ارتداد: {alerts_count['bounce']}/3

{last_scan_report}
آخر فحص: {last_scan_time}""")

                elif text == "الفرص":
                    if not active_trades:
                        send_telegram(chat, "لا يوجد فرص نشطة اليوم")
                    else:
                        txt = "📋 الفرص النشطة:\n\n"
                        for t, tr in active_trades.items():
                            txt += f"{t} - دخول {tr['price']:.2f}$ - هدف {tr['target']:.2f}$ - وقف {tr['stop']:.2f}$\n"
                        send_telegram(chat, txt)
        except Exception as e:
            print(f"Poll Error: {e}")
        time.sleep(2)

# ============ التشغيل ============
def daily_reset():
    global alerts_count, last_sent, active_trades
    while True:
        now = datetime.now(SAUDI_TZ)
        # تصفير كل يوم الساعة 12 الليل بتوقيت السعودية
        if now.hour == 0 and now.minute == 0:
            alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
            last_sent = {}
            active_trades = {}
            time.sleep(61)
        time.sleep(30)

def scheduler():
    while True:
        if is_market_open():
            main_loop(manual=False)
        time.sleep(SCAN_INTERVAL_MIN * 60)

if __name__ == "__main__":
    send_to_me("✅ Bot ON - 5-200$ - from 5:30")
    threading.Thread(target=poll_telegram, daemon=True).start()
    threading.Thread(target=daily_reset, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    while True:
        time.sleep(60)
