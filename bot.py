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
active_alerts = []
last_scan = {"time": None, "found": 0, "details": [], "total": 0}

def is_work_time():
    now = datetime.now(SAUDI_TZ)
    return 17 <= now.hour <= 23

def reset_daily():
    global alerts_count, last_reset_day, active_alerts
    today = datetime.now(SAUDI_TZ).date()
    if last_reset_day != today:
        alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
        active_alerts = []
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
        return ("strong", msg, price, target, stop)
    if (10000000 <= liquidity <= 50000000 and vol_ratio > 1.3 and price > ema20_1h and 45 <= rsi_1h <= 68 and last_close >= prev_high_1d * 0.99):
        target = price * 1.06
        stop = price * 0.963
        msg = "⚡ فرصة متوسطة - " + ticker + " | السعر: " + str(round(price,2)) + "$ | السيولة: " + str(round(liquidity/1000000)) + "M | RSI: " + str(round(rsi_1h)) + " | الهدف: " + str(round(target,2)) + "$ | الوقف: " + str(round(stop,2)) + "$"
        return ("medium", msg, price, target, stop)
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
        return ("bounce", msg, price, target, stop)
    return None

def add_active(ticker, kind, entry, target, stop):
    for a in active_alerts:
        if a["ticker"] == ticker:
            return
    active_alerts.append({"ticker": ticker, "kind": kind, "entry": entry, "target": target, "stop": stop, "time": datetime.now(SAUDI_TZ).strftime("%I:%M %p")})

def get_current_price(ticker):
    try:
        df = yf.download(ticker, period="1d", interval="1h", progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return float(df["Close"].iloc[-1])
    except:
        return None

def debug_one(ticker):
    try:
        data = get_data(ticker)
        if not data:
            return f"{ticker}: ❌ ما جت بيانات"
        df_1h, df_1d = data
        price = float(df_1h["Close"].iloc[-1])
        ema20 = float(df_1h["Close"].ewm(span=20).mean().iloc[-1])
        rsi = float(calc_rsi(df_1h["Close"]).iloc[-1])
        vol_today = float(df_1h["Volume"].iloc[-1])
        vol_avg = float(df_1h["Volume"].tail(20).mean())
        vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0
        liq = price * vol_today * 6
        open_today = float(df_1d["Open"].iloc[-1])
        day_change = ((price - open_today) / open_today) * 100 if open_today else 0
        res = check_stock(ticker)
        if res:
            return f"{ticker}: ✅ {res[0]} | سعر {round(price,2)}$ | RSI {round(rsi)} | Vol x{round(vol_ratio,1)} | Liq {round(liq/1000000)}M | تغير {round(day_change,1)}%"
        else:
            reason = []
            if not (5 <= price <= 300):
                reason.append("سعر خارج النطاق")
            if day_change > 8:
                reason.append(f"مرتفع {round(day_change,1)}% >8%")
            if price < ema20:
                reason.append("تحت EMA20")
            if not (30 <= rsi <= 68):
                reason.append(f"RSI {round(rsi)} خارج النطاق")
            if vol_ratio < 1.3:
                reason.append(f"حجم ضعيف x{round(vol_ratio,1)}")
            if liq < 10000000:
                reason.append(f"سيولة ضعيفة {round(liq/1000000)}M")
            if not reason:
                reason.append("ما طابق شروط الاختراق/الارتداد")
            return f"{ticker}: ⏸️ سعر {round(price,2)}$ | RSI {round(rsi)} | Vol x{round(vol_ratio,1)} | Liq {round(liq/1000000)}M | {', '.join(reason)}"
    except Exception as e:
        return f"{ticker}: ❌ خطأ {e}"

HELP_TEXT = "🤖 أوامر البوت:\n\n/scan - افحص الأسهم الحين\n/status - حالة البوت\n/report - تقرير آخر فحص\n/debug - تشخيص الأسهم (ليش ما طلعت فرصة)\n/active - الصفقات النشطة اليوم\n/help - المساعدة"

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
                send_msg("✅ هلا فيك! البوت شغال.\n\n" + HELP_TEXT, chat)
            elif text in ["/help", "مساعدة", "المساعدة", "help"]:
                send_msg(HELP_TEXT, chat)
            elif text in ["/status", "/الحالة", "الحالة", "حالة", "status"]:
                now = datetime.now(SAUDI_TZ)
                work = "داخل وقت العمل (5م-12ص) ✅" if is_work_time() else "خارج وقت العمل (5م-12ص) ⏸️ - الفرص التلقائية متوقفة، بس الأوامر شغالة"
                last_scan_txt = f"آخر فحص: {last_scan['time']}" if last_scan["time"] else "آخر فحص: ما صار فحص للحين"
                send_msg(f"📊 حالة البوت:\nالوقت: {now.strftime('%I:%M %p')}\n{work}\n{last_scan_txt}\nفرص قوية اليوم: {alerts_count['strong']}/3\nفرص متوسطة: {alerts_count['medium']}/3\nفرص ارتداد: {alerts_count['bounce']}/3\nصفقات نشطة: {len(active_alerts)}\nعدد الأسهم المراقبة: {len(WATCHLIST)}", chat)
            elif text in ["/id", "رقمي", "ايدي"]:
                send_msg(f"رقمك هو: {chat}", chat)
            elif text in ["/scan", "/فحص", "فحص", "افحص", "افحص لي", "/check", "check", "scan"]:
                send_msg("🔍 طيب، جاري فحص الـ 40 سهم الحين... بياخذ دقيقة تقريباً، انتظرني.", chat)
                found = 0
                details = []
                for ticker in WATCHLIST:
                    try:
                        result = check_stock(ticker)
                        if result:
                            kind, m, entry, target, stop = result
                            send_msg(m, chat)
                            add_active(ticker, kind, entry, target, stop)
                            details.append(f"{ticker} - {kind} - دخول {round(entry,2)}$")
                            found += 1
                    except Exception as e:
                        print(f"Scan error {ticker}: {e}")
                    await asyncio.sleep(1)
                last_scan["time"] = datetime.now(SAUDI_TZ).strftime("%I:%M %p")
                last_scan["found"] = found
                last_scan["details"] = details
                last_scan["total"] = len(WATCHLIST)
                if found == 0:
                    send_msg("✅ خلص الفحص، ما فيه فرصة مطابقة لشروطك الحين.", chat)
                else:
                    send_msg(f"✅ خلص الفحص، لقيت {found} فرصة.", chat)
            elif text in ["/report", "/تقرير", "تقرير", "تقرير الفحص", "report"]:
                if not last_scan["time"]:
                    send_msg("📭 ما صار فحص للحين.\nاكتب /scan عشان افحص لك.", chat)
                else:
                    txt = f"📋 تقرير الفحص:\nوقت الفحص: {last_scan['time']}\nفحصت: {last_scan['total']} سهم\nلقيت: {last_scan['found']} فرصة\n\n"
                    if last_scan["details"]:
                        txt += "\n".join(last_scan["details"])
                    else:
                        txt += "ما فيه فرصة مطابقة."
                    txt += f"\n\nفرص اليوم المرسلة: قوية {alerts_count['strong']}/3 | متوسطة {alerts_count['medium']}/3 | ارتداد {alerts_count['bounce']}/3"
                    send_msg(txt, chat)
            elif text in ["/debug", "/تشخيص", "تشخيص", "تشخيص الأسهم", "debug"]:
                send_msg("🛠️ طيب، جاري تشخيص الـ 40 سهم... بياخذ دقيقتين، بيرسلها لك على دفعات.", chat)
                batch = []
                for i, ticker in enumerate(WATCHLIST, 1):
                    line = debug_one(ticker)
                    batch.append(line)
                    if len(batch) == 10 or i == len(WATCHLIST):
                        send_msg("🔍 تشخيص:\n" + "\n".join(batch), chat)
                        batch = []
                    await asyncio.sleep(1)
                send_msg("✅ خلص التشخيص.", chat)
            elif text in ["/active", "/الفرص", "/فرص", "الفرص النشطة", "فرص نشطة", "الفرص", "فرص", "active", "الصفقات النشطة", "صفقات", "الفرص النشطه"]:
                if not active_alerts:
                    send_msg("📭 ما فيه صفقات نشطة اليوم للحين.\nاكتب /scan عشان افحص لك.", chat)
                else:
                    txt = f"📈 الصفقات النشطة اليوم ({len(active_alerts)}):\n\n"
                    for a in active_alerts:
                        cur = get_current_price(a["ticker"])
                        if cur is not None:
                            pct = ((cur - a["entry"]) / a["entry"]) * 100
                            if cur >= a["target"]:
                                st = "✅ حقق الهدف"
                            elif cur <= a["stop"]:
                                st = "❌ ضرب الوقف"
                            else:
                                st = "⏳ نشطة"
                            txt += f"{a['ticker']} - {st}\nدخول: {round(a['entry'],2)}$ | الحالي: {round(cur,2)}$ | {round(pct,2)}%\nهدف: {round(a['target'],2)}$ | وقف: {round(a['stop'],2)}$ | {a['time']}\n\n"
                        else:
                            txt += f"{a['ticker']}\nدخول: {round(a['entry'],2)}$ | هدف: {round(a['target'],2)}$ | وقف: {round(a['stop'],2)}$ | {a['time']}\n\n"
                    send_msg(txt, chat)
            elif text in ["/نتيجة", "/نتيجه", "نتيجة", "نتيجه", "نتيجة الفرصة", "نتيجة الفرص", "النتائج", "نتائج", "/result", "result"]:
                if not active_alerts:
                    send_msg("📭 ما فيه فرص اليوم عشان احسب نتيجتها.\nاكتب /scan عشان افحص لك.", chat)
                else:
                    send_msg("📊 جاري حساب نتيجة الفرص... ثواني.", chat)
                    txt = "📊 نتيجة الفرص اليوم:\n\n"
                    wins = 0
                    losses = 0
                    for a in active_alerts:
                        cur = get_current_price(a["ticker"])
                        if cur is None:
                            txt += f"{a['ticker']} - ما قدرت اجيب سعره الحين\n\n"
                            continue
                        pct = ((cur - a["entry"]) / a["entry"]) * 100
                        if cur >= a["target"]:
                            status = "✅ حقق الهدف"
                            wins += 1
                        elif cur <= a["stop"]:
                            status = "❌ ضرب الوقف"
                            losses += 1
                        else:
                            status = "⏳ للحين نشطة"
                        txt += f"{a['ticker']} - {status}\nدخول: {round(a['entry'],2)}$ | الحالي: {round(cur,2)}$ | {round(pct,2)}%\nهدف: {round(a['target'],2)}$ | وقف: {round(a['stop'],2)}$\n\n"
                        await asyncio.sleep(1)
                    txt += f"الملخص: ✅ {wins} هدف | ❌ {losses} وقف | ⏳ {len(active_alerts)-wins-losses} نشطة"
                    send_msg(txt, chat)
            else:
                send_msg("وصلتني رسالتك 👍\n\n" + HELP_TEXT, chat)
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
                    kind, msg, entry, target, stop = result
                    if alerts_count[kind] < MAX_ALERTS[kind]:
                        send_msg(msg)
                        alerts_count[kind] += 1
                        last_sent[ticker] = datetime.now(SAUDI_TZ)
                        add_active(ticker, kind, entry, target, stop)
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
