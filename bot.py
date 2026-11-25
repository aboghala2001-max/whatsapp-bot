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
MAX_GAIN_DAY = 8.0

# عدد التنبيهات باليوم
MAX_ALERTS_STRONG = 3
MAX_ALERTS_MEDIUM = 3
MAX_ALERTS_BOUNCE = 3

SCAN_INTERVAL_MIN = 5

# ============ الحالة ============
alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
last_sent = {}
active_trades = {}
last_scan_report = "لا يوجد فحص بعد"
last_debug_report = "لا يوجد تشخيص بعد"
last_scan_time = None
is_scanning = False

# ============ أدوات ============
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("No Token")
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
    return 100 - (100 / (1 + rs))

def is_market_open():
    now_et = datetime.now(US_EASTERN)
    if now_et.weekday() >= 5:
        return False
    return dtime(9, 30) <= now_et.time() <= dtime(16, 0)

def get_all_us_stocks():
    try:
        url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=30).json()
        return [x["symbol"] for x in r if "." not in x["symbol"] and "-" not in x["symbol"]]
    except Exception as e:
        print(f"Finnhub Error: {e}")
        return []

# ============ فحص السهم الواحد ============
def check_stock(ticker):
    try:
        tk = yf.Ticker(ticker)
        info = tk.info

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price or not (PRICE_MIN <= price <= PRICE_MAX):
            return None, f"{ticker}: السعر {price} خارج 5-200"

        market_cap = info.get("marketCap", 0) or 0
        if market_cap < 1_000_000_000:
            return None, f"{ticker}: قيمة سوقية صغيرة {market_cap/1e9:.2f}B"

        net_income = info.get("netIncomeToCommon")
        if net_income is None or net_income <= NET_INCOME_MIN:
            return None, f"{ticker}: الشركة خسرانة"

        df_1h = tk.history(period="5d", interval="1h")
        df_1d = tk.history(period="3mo", interval="1d")
        if len(df_1h) < 20 or len(df_1d) < 50:
            return None, f"{ticker}: بيانات ناقصة"

        last = df_1h.iloc[-1]
        prev_day = df_1d.iloc[-2]
        last_day = df_1d.iloc[-1]

        day_change_pct = ((last_day["Close"] - prev_day["Close"]) / prev_day["Close"]) * 100
        if day_change_pct > MAX_GAIN_DAY:
            return None, f"{ticker}: صاعد {day_change_pct:.1f}% اكثر من 8%"

        df_1d["EMA20"] = df_1d["Close"].ewm(span=20).mean()
        df_1d["EMA50"] = df_1d["Close"].ewm(span=50).mean()
        df_1d["EMA200"] = df_1d["Close"].ewm(span=200).mean()
        df_1h["RSI"] = get_rsi(df_1h["Close"])

        ema20 = df_1d["EMA20"].iloc[-1]
        ema50 = df_1d["EMA50"].iloc[-1]
        ema200 = df_1d["EMA200"].iloc[-1] if not pd.isna(df_1d["EMA200"].iloc[-1]) else price
        rsi_1h = df_1h["RSI"].iloc[-1]

        avg_vol_20 = df_1d["Volume"].tail(20).mean()
        curr_vol = last_day["Volume"]
        vol_ratio = curr_vol / avg_vol_20 if avg_vol_20 else 0
        dollar_volume = last_day["Close"] * curr_vol
        prev_high = prev_day["High"]

        # ===== 1. فرصة قوية - اختراق =====
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

        # ===== 2. فرصة متوسطة - اختراق هادي =====
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

        # ===== 3. فرصة ارتداد - من الدعم =====
        support = None
        for level in [ema50, ema200, df_1d["Low"].tail(60).min()]:
            touches = ((df_1d["Low"].tail(60) <= level * 1.01) & (df_1d["Low"].tail(60) >= level * 0.99)).sum()
            if touches >= 3 or abs(price - level) / level < 0.01:
                support = level
                break

        is_at_support = support is not None and abs(price - support) / support < 0.015
        is_green = last["Close"] > last["Open"]
        has_tail = (min(last["Open"], last["Close"]) - last["Low"]) > (last["High"] - last["Low"]) * 0.4 if (last["High"] - last["Low"]) > 0 else False

        if (market_cap > 1_000_000_000 and dollar_volume > 20_000_000
            and vol_ratio > 1.5
            and is_at_support
            and 30 <= rsi_1h <= 45
            and (is_green or has_tail)):
            return {
                "ticker": ticker, "kind": "bounce", "price": price,
                "target": price * 1.053, "stop": price * 0.98,
                "rsi": rsi_1h, "vol_ratio": vol_ratio, "dollar_vol": dollar_volume,
                "market_cap": market_cap, "day_change": day_change_pct,
                "support": support
            }, "OK"

        return None, f"{ticker}: لا تنطبق شروط | RSI {rsi_1h:.0f} | Vol x{vol_ratio:.1f} | $Vol {dollar_volume/1e6:.0f}M | Price {price:.2f} | MC {market_cap/1e9:.1f}B"

    except Exception as e:
        return None, f"{ticker}: خطأ {e}"

def add_active(trade):
    active_trades[trade["ticker"]] = {**trade, "entry_time": datetime.now(SAUDI_TZ), "entry_price": trade["price"]}

# ============ تنسيق رسالة الفرصة ============
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

# ============ متابعة الهدف والوقف - كل دقيقة ============
def check_active_trades():
    while True:
        try:
            if is_market_open() and active_trades:
                for ticker in list(active_trades.keys()):
                    tr = active_trades[ticker]
                    try:
                        curr = yf.Ticker(ticker).history(period="1d", interval="1m").iloc[-1]["Close"]
                        pnl = ((curr / tr['entry_price'] - 1) * 100)
                        if curr >= tr['target']:
                            send_to_me(f"✅ وصل الهدف - <b>{ticker}</b>\n\nدخول: {tr['entry_price']:.2f}$ | الآن: {curr:.2f}$ | {pnl:+.1f}%\n🎯 هدفك كان: {tr['target']:.2f}$\nبيع وخذ ربحك 👏")
                            del active_trades[ticker]
                        elif curr <= tr['stop']:
                            send_to_me(f"❌ ضرب الوقف - <b>{ticker}</b>\n\nدخول: {tr['entry_price']:.2f}$ | الآن: {curr:.2f}$ | {pnl:+.1f}%\n🛑 وقفك كان: {tr['stop']:.2f}$\nاطلع لا تنتظر")
                            del active_trades[ticker]
                    except:
                        pass
        except:
            pass
        time.sleep(60)

# ============ الفحص الكامل ============
def main_loop(manual=False):
    global last_scan_report, last_debug_report, last_scan_time, is_scanning
    if is_scanning:
        return "الفحص شغال الحين، انتظر"
    is_scanning = True
    # شلنا انتظار 30 دقيقة - يرسل من 5:30 على طول

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
                        if (datetime.now(SAUDI_TZ) - last_sent[ticker]).total_seconds() < 3600:
                            continue
                    alerts_count[kind] += 1
                    last_sent[ticker] = datetime.now(SAUDI_TZ)
                    add_active(trade)
                    send_to_me(format_message(trade))
                    found.append(trade)
                else:
                    debug_lines.append(msg)
            except Exception as e:
                debug_lines.append(f"{ticker}: {e}")

    last_scan_time = datetime.now(SAUDI_TZ)
    last_scan_report = f"آخر فحص: {last_scan_time.strftime('%I:%M %p')} | وجد {len(found)} فرصة | فحص {len(tickers)} سهم"
    last_debug_report = "\n".join(debug_lines[:50])
    is_scanning = False
    return f"تم الفحص، وجد {len(found)} فرصة"

# ============ تصفير يومي ============
def daily_reset():
    global alerts_count, active_trades
    while True:
        now = datetime.now(SAUDI_TZ)
        if now.hour == 0 and now.minute == 0:
            alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
            active_trades = {}
            last_sent.clear()
            send_to_me("🔄 تم تصفير عداد اليوم، نبدأ يوم جديد")
            time.sleep(61)
        time.sleep(30)

# ============ أوامر التليجرام ============
def handle_command(chat_id, text):
    if text == "/start":
        send_telegram(chat_id, "أهلاً بك! بوت الفرص شغال ✅\nاكتب /help لتشوف الأوامر")
    elif text == "/help":
        send_telegram(chat_id, "الأوامر:\n/scan - فحص الحين\n/status - حالة البوت وكم فرصة باقي\n/report - تقرير آخر فحص\n/debug - تشخيص ليش ما طلعت فرصة\n/active - الصفقات النشطة اليوم\n/id - رقمك\n/help - المساعدة")
    elif text == "/id":
        send_telegram(chat_id, f"رقمك: {chat_id}")
    elif text == "/scan":
        send_telegram(chat_id, "جار الفحص...")
        send_telegram(chat_id, main_loop(manual=True))
    elif text == "/status":
        send_telegram(chat_id, f"""📊 حالة البوت:
آخر فحص: {last_scan_time.strftime('%I:%M %p') if last_scan_time else 'لا يوجد'}
السوق مفتوح: {'نعم' if is_market_open() else 'لا'}

فرص قوية اليوم: {alerts_count['strong']}/{MAX_ALERTS_STRONG}
فرص متوسطة: {alerts_count['medium']}/{MAX_ALERTS_MEDIUM}
فرص ارتداد: {alerts_count['bounce']}/{MAX_ALERTS_BOUNCE}

باقي لك: {MAX_ALERTS_STRONG - alerts_count['strong']} قوية + {MAX_ALERTS_MEDIUM - alerts_count['medium']} متوسطة + {MAX_ALERTS_BOUNCE - alerts_count['bounce']} ارتداد = {9 - sum(alerts_count.values())} فرص
صفقات نشطة: {len(active_trades)}
""")
    elif text == "/report":
        send_telegram(chat_id, last_scan_report)
    elif text == "/debug":
        send_telegram(chat_id, f"تشخيص آخر فحص:\n{last_debug_report[:3000]}")
    elif text == "/active":
        if not active_trades:
            send_telegram(chat_id, "لا يوجد صفقات نشطة اليوم")
        else:
            msg = "📈 الصفقات النشطة اليوم:\n\n"
            for t, tr in active_trades.items():
                try:
                    curr = yf.Ticker(t).history(period="1d", interval="1m").iloc[-1]["Close"]
                    pnl = ((curr / tr['entry_price'] - 1) * 100)
                    msg += f"{t}: دخول {tr['entry_price']:.2f} | الآن {curr:.2f} | {pnl:+.1f}% | هدف {tr['target']:.2f} | وقف {tr['stop']:.2f}\n\n"
                except:
                    msg += f"{t}: دخول {tr['entry_price']:.2f} | هدف {tr['target']:.2f} | وقف {tr['stop']:.2f}\n\n"
            send_telegram(chat_id, msg)
    else:
        send_telegram(chat_id, "ما فهمت، اكتب /help")

def telegram_polling():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            r = requests.get(url, timeout=35).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    handle_command(upd["message"]["chat"]["id"], upd["message"].get("text", ""))
        except Exception as e:
            print(f"Polling Error: {e}")
            time.sleep(5)

# ============ التشغيل ============
if __name__ == "__main__":
    threading.Thread(target=daily_reset, daemon=True).start()
    threading.Thread(target=telegram_polling, daemon=True).start()
    threading.Thread(target=check_active_trades, daemon=True).start()
    send_to_me("✅ البوت اشتغل - السعر 5$-200$ - يرسل من 5:30 - مع تنبيه الهدف والوقف")
    while True:
        if is_market_open():
            main_loop()
        time.sleep(SCAN_INTERVAL_MIN * 60)
