import os
import json
import asyncio
import logging
import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import yfinance as yf

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ================== الإعدادات ==================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATA_FILE = os.getenv("DATA_FILE", "state.json")

NY = ZoneInfo("America/New_York")

TICKERS = [
    "SPY", "QQQ", "IWM",
    "NVDA", "TSLA", "META",
    "AAPL", "AMD", "AMZN", "MU"
]

MAX_PER_DAY = 10
SCAN_INTERVAL = 60

# وقت نيويورك
START_SCAN = time(9, 40)
STOP_NEW_TRADES = time(15, 30)
CLOSE_ALL_TIME = time(15, 55)

TP1 = 0.30
TP2 = 0.60
SL = 0.35

MIN_OPEN_INTEREST = 100
MIN_OPTION_VOLUME = 10
MAX_SPREAD_PERCENT = 20
MIN_OPTION_PRICE = 0.10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger(__name__)

# ================== التخزين ==================

STATE = {
    "date": "",
    "chats": [],
    "active": {},
    "history": [],
    "sent_today": 0,
    "last_signal": {}
}


def save_state():
    try:
        folder = os.path.dirname(DATA_FILE)
        if folder:
            os.makedirs(folder, exist_ok=True)

        temp_file = DATA_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)

        os.replace(temp_file, DATA_FILE)

    except Exception:
        log.exception("Could not save state")


def load_state():
    global STATE

    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)

            STATE.update(saved)

    except Exception:
        log.exception("Could not load state")

    today = datetime.now(NY).date().isoformat()

    if STATE.get("date") != today:
        STATE["date"] = today
        STATE["sent_today"] = 0
        STATE["last_signal"] = {}

        # لا نمسح الصفقات المفتوحة بصمت.
        # تتم مراجعتها عند إغلاق الجلسة.

    save_state()


def reset_day_if_needed():
    today = datetime.now(NY).date().isoformat()

    if STATE.get("date") != today:
        STATE["date"] = today
        STATE["sent_today"] = 0
        STATE["last_signal"] = {}
        save_state()


# ================== الوقت ==================

def market_time():
    return datetime.now(NY)


def is_scan_time():
    now = market_time().time()
    return START_SCAN <= now <= STOP_NEW_TRADES


def is_market_day():
    return market_time().weekday() < 5


# ================== رسائل تيليجرام ==================

async def send_all(app, message):
    chats = list(STATE.get("chats", []))

    for chat_id in chats:
        try:
            await app.bot.send_message(
                chat_id=int(chat_id),
                text=message
            )
        except Exception:
            log.exception("Telegram send error: %s", chat_id)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if chat_id not in STATE["chats"]:
        STATE["chats"].append(chat_id)
        save_state()

    await update.message.reply_text(
        "تم تفعيل اشتراكك في تنبيهات الأوبشن اليومية.\n"
        "البوت يبحث عن عقود تنتهي اليوم فقط (0DTE).\n\n"
        "استخدم /help لمعرفة الأوامر."
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if chat_id in STATE["chats"]:
        STATE["chats"].remove(chat_id)
        save_state()

    await update.message.reply_text("تم إيقاف التنبيهات لهذا الحساب.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"حالة البوت: يعمل\n"
        f"تاريخ السوق: {market_time().date()}\n"
        f"الفرص المرسلة اليوم: {STATE['sent_today']}/{MAX_PER_DAY}\n"
        f"الصفقات النشطة: {len(STATE['active'])}\n"
        f"المشتركون: {len(STATE['chats'])}"
    )

    await update.message.reply_text(text)


async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = STATE.get("active", {})

    if not active:
        await update.message.reply_text("لا توجد صفقات نشطة حاليًا.")
        return

    lines = ["الصفقات النشطة:"]

    for trade in active.values():
        lines.append(
            f"{trade['ticker']} {trade['type']} "
            f"Strike: {trade['strike']} "
            f"Entry: {trade['entry']:.2f} "
            f"Expiry: {trade['expiry']}"
        )

    await update.message.reply_text("\n".join(lines))


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = STATE.get("history", [])[-10:]

    if not history:
        await update.message.reply_text("لا يوجد سجل صفقات حتى الآن.")
        return

    lines = ["آخر الصفقات:"]

    for trade in reversed(history):
        lines.append(
            f"{trade['ticker']} {trade['type']} | "
            f"{trade.get('result', 'غير محدد')} | "
            f"{trade.get('exit_price', 0):.2f}"
        )

    await update.message.reply_text("\n".join(lines))


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_scan_time():
        await update.message.reply_text(
            "الفحص اليدوي متاح خلال وقت البحث في جلسة السوق الأمريكية فقط."
        )
        return

    await update.message.reply_text("تم طلب فحص السوق. ستصلك التنبيهات إذا ظهرت فرصة مطابقة.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - تفعيل التنبيهات\n"
        "/stop - إيقاف التنبيهات\n"
        "/status - حالة البوت\n"
        "/active - الصفقات النشطة\n"
        "/history - سجل الصفقات\n"
        "/scan - طلب فحص السوق\n"
        "/help - المساعدة"
    )


# ================== بيانات الأسهم ==================

def get_candles(ticker, interval):
    try:
        df = yf.Ticker(ticker).history(
            period="5d",
            interval=interval,
            auto_adjust=False
        )

        if df is None or df.empty:
            return None

        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

        if len(df) < 25:
            return None

        # حذف آخر شمعة لتجنب استخدام شمعة لم يكتمل تكوينها.
        df = df.iloc[:-1].copy()

        if df.empty:
            return None

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        df.index = df.index.tz_convert(NY)

        # بيانات جلسة اليوم فقط
        today = market_time().date()
        df = df[df.index.date == today]

        if len(df) < 22:
            return None

        return df

    except Exception:
        log.exception("Candle error: %s %s", ticker, interval)
        return None


def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, 0.000001)

    return 100 - (100 / (1 + rs))


def get_signal(ticker):
    df5 = get_candles(ticker, "5m")
    df15 = get_candles(ticker, "15m")

    if df5 is None or df15 is None:
        return None

    if len(df5) < 22 or len(df15) < 22:
        return None

    # فريم 5 دقائق
    last = df5.iloc[-1]
    previous = df5.iloc[-2]

    df5["RSI"] = calculate_rsi(df5["Close"])
    rsi = float(df5["RSI"].iloc[-1])

    # متوسط الحجم السابق، دون احتساب الشمعة الحالية
    avg_volume = float(df5["Volume"].iloc[-21:-1].mean())
    current_volume = float(last["Volume"])

    if avg_volume <= 0:
        return None

    volume_ratio = current_volume / avg_volume

    # VWAP لشموع اليوم
    typical_price = (
        df5["High"] + df5["Low"] + df5["Close"]
    ) / 3

    cumulative_volume = df5["Volume"].cumsum()

    vwap = (
        (typical_price * df5["Volume"]).cumsum()
        / cumulative_volume.replace(0, 1)
    ).iloc[-1]

    # فريم 15 دقيقة
    df15["EMA20"] = df15["Close"].ewm(
        span=20,
        adjust=False
    ).mean()

    close15 = float(df15["Close"].iloc[-1])
    ema20 = float(df15["EMA20"].iloc[-1])

    close5 = float(last["Close"])
    high_previous = float(previous["High"])
    low_previous = float(previous["Low"])

    # شروط CALL
    call_ok = (
        close5 > high_previous
        and close5 > float(vwap)
        and close15 > ema20
        and 50 < rsi < 75
        and volume_ratio >= 1.2
    )

    # شروط PUT
    put_ok = (
        close5 < low_previous
        and close5 < float(vwap)
        and close15 < ema20
        and 25 < rsi < 50
        and volume_ratio >= 1.2
    )

    if call_ok:
        return "CALL"

    if put_ok:
        return "PUT"

    return None


# ================== عقود الأوبشن 0DTE ==================

def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default

        return float(value)

    except Exception:
        return default


def get_0dte_contract(ticker, option_type):
    try:
        stock = yf.Ticker(ticker)

        today = market_time().date().isoformat()
        expirations = stock.options

        # لا نختار أي تاريخ آخر غير تاريخ اليوم.
        if today not in expirations:
            return None

        chain = stock.option_chain(today)

        options = chain.calls if option_type == "CALL" else chain.puts

        if options is None or options.empty:
            return None

        stock_price = safe_float(
            stock.history(period="1d", interval="1m")["Close"].dropna().iloc[-1]
        )

        if stock_price <= 0:
            return None

        options = options.copy()

        options["bid_num"] = options["bid"].apply(safe_float)
        options["ask_num"] = options["ask"].apply(safe_float)
        options["volume_num"] = options["volume"].apply(safe_float)
        options["oi_num"] = options["openInterest"].apply(safe_float)

        options["spread_pct"] = (
            (options["ask_num"] - options["bid_num"])
            / options["ask_num"].replace(0, pd.NA)
        ) * 100

        options = options[
            (options["bid_num"] > 0)
            & (options["ask_num"] > 0)
            & (options["ask_num"] >= options["bid_num"])
            & (options["volume_num"] >= MIN_OPTION_VOLUME)
            & (options["oi_num"] >= MIN_OPEN_INTEREST)
            & (options["spread_pct"] <= MAX_SPREAD_PERCENT)
        ]

        if options.empty:
            return None

        # اختيار أقرب Strike للسعر الحالي من العقود التي اجتازت الفلاتر.
        options["distance"] = (
            options["strike"] - stock_price
        ).abs()

        selected = options.sort_values("distance").iloc[0]

        bid = safe_float(selected["bid_num"])
        ask = safe_float(selected["ask_num"])

        if ask < MIN_OPTION_PRICE:
            return None

        return {
            "ticker": ticker,
            "type": option_type,
            "expiry": today,
            "strike": safe_float(selected["strike"]),
            "contract": str(selected["contractSymbol"]),
            "bid": bid,
            "ask": ask,
            "entry": ask,
            "volume": int(safe_float(selected["volume_num"])),
            "open_interest": int(safe_float(selected["oi_num"])),
            "stock_price": stock_price,
        }

    except Exception:
        log.exception("Option chain error: %s", ticker)
        return None


# ================== فتح الصفقات ==================

async def open_trade(app, ticker, option_type):
    if STATE["sent_today"] >= MAX_PER_DAY:
        return

    if any(
        trade["ticker"] == ticker
        for trade in STATE["active"].values()
    ):
        return

    today = market_time().date().isoformat()

    last_signal = STATE["last_signal"].get(ticker)

    if last_signal == today:
        return

    contract = await asyncio.to_thread(
        get_0dte_contract,
        ticker,
        option_type
    )

    if not contract:
        return

    trade_id = (
        f"{ticker}_{option_type}_{contract['strike']}_{today}"
    )

    entry = contract["entry"]

    trade = {
        **contract,
        "id": trade_id,
        "entry": entry,
        "entry_time": market_time().isoformat(),
        "tp1": round(entry * (1 + TP1), 2),
        "tp2": round(entry * (1 + TP2), 2),
        "sl": round(entry * (1 - SL), 2),
        "tp1_sent": False,
        "status": "ACTIVE",
    }

    STATE["active"][trade_id] = trade
    STATE["sent_today"] += 1
    STATE["last_signal"][ticker] = today

    save_state()

    message = (
        f"فرصة أوبشن يومية جديدة\n\n"
        f"السهم: {ticker}\n"
        f"النوع: {option_type}\n"
        f"Strike: {contract['strike']}\n"
        f"الانتهاء: {today} (0DTE)\n"
        f"رمز العقد: {contract['contract']}\n\n"
        f"سعر الدخول التقديري (Ask): {entry:.2f}\n"
        f"TP1: {trade['tp1']:.2f} (+30%)\n"
        f"TP2: {trade['tp2']:.2f} (+60%)\n"
        f"SL: {trade['sl']:.2f} (-35%)\n\n"
        f"حجم العقد: {contract['volume']}\n"
        f"Open Interest: {contract['open_interest']}\n\n"
        f"هذه إشارة تنبيه وليست أمر شراء."
    )

    await send_all(app, message)

    log.info("New trade: %s", trade_id)


# ================== متابعة الصفقات ==================

def get_current_option_quote(trade):
    try:
        stock = yf.Ticker(trade["ticker"])

        chain = stock.option_chain(trade["expiry"])

        options = (
            chain.calls
            if trade["type"] == "CALL"
            else chain.puts
        )

        selected = options[
            options["contractSymbol"] == trade["contract"]
        ]

        if selected.empty:
            return None

        row = selected.iloc[0]

        bid = safe_float(row["bid"])
        ask = safe_float(row["ask"])

        if bid <= 0:
            return None

        # استخدام Bid كتقدير محافظ لقيمة البيع.
        return {
            "bid": bid,
            "ask": ask
        }

    except Exception:
        log.exception("Quote error: %s", trade["contract"])
        return None


async def close_trade(app, trade_id, exit_price, result):
    trade = STATE["active"].pop(trade_id, None)

    if not trade:
        return

    trade["status"] = "CLOSED"
    trade["exit_price"] = exit_price
    trade["result"] = result
    trade["exit_time"] = market_time().isoformat()

    trade["profit_percent"] = round(
        ((exit_price / trade["entry"]) - 1) * 100,
        2
    ) if trade["entry"] > 0 else 0

    STATE["history"].append(trade)
    STATE["history"] = STATE["history"][-500:]

    save_state()

    message = (
        f"إغلاق صفقة {trade['ticker']} {trade['type']}\n\n"
        f"النتيجة: {result}\n"
        f"الدخول: {trade['entry']:.2f}\n"
        f"الخروج التقديري: {exit_price:.2f}\n"
        f"التغير: {trade['profit_percent']}%"
    )

    await send_all(app, message)


async def monitor_active(app):
    active_ids = list(STATE["active"].keys())

    for trade_id in active_ids:
        trade = STATE["active"].get(trade_id)

        if not trade:
            continue

        quote = await asyncio.to_thread(
            get_current_option_quote,
            trade
        )

        if not quote:
            continue

        current = quote["bid"]

        # وقف الخسارة
        if current <= trade["sl"]:
            await close_trade(
                app, trade_id, current, "وقف الخسارة"
            )
            continue

        # الهدف الثاني
        if current >= trade["tp2"]:
            await close_trade(
                app, trade_id, current, "تحقق الهدف الثاني"
            )
            continue

        # الهدف الأول - تنبيه مرة واحدة
        if current >= trade["tp1"] and not trade["tp1_sent"]:
            trade["tp1_sent"] = True
            save_state()

            await send_all(
                app,
                f"تحقق الهدف الأول\n"
                f"{trade['ticker']} {trade['type']}\n"
                f"سعر العقد الحالي (Bid): {current:.2f}\n"
                f"الهدف الأول: +30%\n"
                f"تابع الصفقة؛ لم يتم إغلاقها تلقائيًا."
            )


async def close_all_at_end(app):
    active_ids = list(STATE["active"].keys())

    for trade_id in active_ids:
        trade = STATE["active"].get(trade_id)

        if not trade:
            continue

        quote = await asyncio.to_thread(
            get_current_option_quote,
            trade
        )

        exit_price = quote["bid"] if quote else 0.0

        await close_trade(
            app,
            trade_id,
            exit_price,
            "نهاية جلسة التداول"
        )


# ================== محرك الفحص ==================

async def scanner_loop(app):
    log.info("Scanner started")

    while True:
        try:
            reset_day_if_needed()

            now = market_time()

            if is_market_day():
                # متابعة الصفقات حتى بعد بلوغ الحد اليومي.
                if now.time() < CLOSE_ALL_TIME:
                    await monitor_active(app)

                elif STATE["active"]:
                    await close_all_at_end(app)

                # فتح صفقات جديدة فقط خلال الوقت المحدد.
                if is_scan_time() and STATE["sent_today"] < MAX_PER_DAY:

                    for ticker in TICKERS:
                        if STATE["sent_today"] >= MAX_PER_DAY:
                            break

                        try:
                            signal = await asyncio.to_thread(
                                get_signal,
                                ticker
                            )

                            if signal:
                                await open_trade(
                                    app,
                                    ticker,
                                    signal
                                )

                        except Exception:
                            log.exception("Scan error: %s", ticker)

            await asyncio.sleep(SCAN_INTERVAL)

        except asyncio.CancelledError:
            break

        except Exception:
            log.exception("Scanner loop error")
            await asyncio.sleep(30)


# ================== Render Health Check ==================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()

    log.info("Health server running on port %s", port)


# ================== تشغيل البوت ==================

async def post_init(app):
    load_state()

    app.bot_data["scanner_task"] = asyncio.create_task(
        scanner_loop(app)
    )

    log.info("Telegram bot initialized")


async def post_shutdown(app):
    task = app.bot_data.get("scanner_task")

    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing")

    start_health_server()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("active", active_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("help", help_command))

    log.info("Starting Telegram bot")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
