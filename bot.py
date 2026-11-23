import os
import asyncio
import yfinance as yf
print("YFINANCE VERSION:", yf.__version__)
print("YFINANCE VERSION:", yf.__version__)
import logging
import threading
import math
import time as time_module

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd
import yfinance as yf

from telegram import Update
from telegram.ext import Application, CommandHandler


# =====================================================
# 1. الإعدادات
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

NY_TZ = ZoneInfo("America/New_York")
SA_TZ = ZoneInfo("Asia/Riyadh")

TICKERS = [
    "SPY", "QQQ", "IWM", "DIA",
    "NVDA", "TSLA", "META", "AAPL", "AMD",
    "MSFT", "GOOGL", "AMZN", "MU", "AVGO",
    "ORCL", "PLTR", "ARM", "CRM",
    "COIN", "NFLX", "HOOD", "SOFI",
    "UBER", "SMCI",
    "JPM", "XOM", "BA", "DIS", "GLD", "TLT"
]

MAX_PER_DAY = 10
SCAN_INTERVAL = 60

# فترة تشغيل البوت بتوقيت السعودية
SAUDI_START = time(17, 0)
SAUDI_END = time(0, 0)

# جلسة السوق الأمريكية بتوقيت نيويورك
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# شروط العقد
MIN_OPTION_PRICE = 0.10
MIN_OPEN_INTEREST = 100
MIN_OPTION_VOLUME = 10
MAX_SPREAD_PERCENT = 20
MAX_OPTION_TRADE_AGE_MINUTES = 20

# شروط الاستراتيجية المخففة
MIN_VOLUME_RATIO = 0.5

CALL_RSI_MIN = 45
CALL_RSI_MAX = 75

PUT_RSI_MIN = 25
PUT_RSI_MAX = 55

# الأهداف والوقف
TAKE_PROFIT_1 = 0.30
TAKE_PROFIT_2 = 0.60
STOP_LOSS = -0.35

MAX_5M_CANDLE_AGE_MINUTES = 15
MAX_15M_CANDLE_AGE_MINUTES = 25

YAHOO_RETRIES = 3
YAHOO_RETRY_DELAY = 2


# =====================================================
# 2. السجلات وحالة البوت
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("AboghalaBot")

subscribers = set()
active_trades = {}

sent_today = set()
strong_seen_today = set()
medium_seen_today = set()

daily_count = 0
current_day = None

last_scan_time = None
last_scan_summary = {}
last_scan_errors = []

yahoo_status = {
    "ok": False,
    "message": "لم يتم الاختبار بعد"
}

scan_lock = asyncio.Lock()


# =====================================================
# 3. الوقت وإدارة اليوم
# =====================================================

def now_ny():
    return datetime.now(NY_TZ)


def now_saudi():
    return datetime.now(SA_TZ)


def reset_daily_data_if_needed():

    global current_day
    global daily_count
    global sent_today
    global strong_seen_today
    global medium_seen_today

    # اليوم التشغيلي مرتبط بتاريخ نيويورك
    today = now_ny().date()

    if current_day != today:

        current_day = today
        daily_count = 0

        sent_today = set()
        strong_seen_today = set()
        medium_seen_today = set()

        logger.info(
            "تم تصفير إحصائيات اليوم الجديد: %s",
            today
        )


def inside_saudi_schedule():

    current = now_saudi().time()

    # الفترة من 17:00 إلى 00:00
    # أضفنا دقيقة سماح عند منتصف الليل
    return (
        current >= SAUDI_START
        or current < time(0, 1)
    )


def market_is_open():

    now = now_ny()

    if now.weekday() >= 5:
        return False

    return MARKET_OPEN <= now.time() < MARKET_CLOSE


def market_is_open_for_scanning():

    return (
        inside_saudi_schedule()
        and market_is_open()
    )


# =====================================================
# 4. أدوات مساعدة
# =====================================================

def safe_float(value):

    try:

        result = float(value)

        if not math.isfinite(result):
            return None

        return result

    except (TypeError, ValueError):
        return None


def split_message(text, limit=3500):

    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""

    for line in text.split("\n"):

        if len(current) + len(line) + 1 > limit:

            if current:
                chunks.append(current)

            current = line

        else:

            current += ("\n" if current else "") + line

    if current:
        chunks.append(current)

    return chunks


async def reply_long(update, text):

    for chunk in split_message(text):
        await update.message.reply_text(chunk)


def yahoo_call(function, description="Yahoo"):

    last_error = None

    for attempt in range(YAHOO_RETRIES):

        try:
            return function()

        except Exception as e:

            last_error = e

            logger.warning(
                "%s: المحاولة %s/%s فشلت: %s",
                description,
                attempt + 1,
                YAHOO_RETRIES,
                str(e)[:180]
            )

            if attempt < YAHOO_RETRIES - 1:

                time_module.sleep(
                    YAHOO_RETRY_DELAY * (attempt + 1)
                )

    raise RuntimeError(
        f"{description}: فشلت جميع المحاولات: "
        f"{str(last_error)[:200]}"
    )


# =====================================================
# 5. اختبار Yahoo
# =====================================================

def test_yahoo_connection():

    global yahoo_status

    try:

        stock = yf.Ticker("SPY")

        expirations = yahoo_call(
            lambda: stock.options,
            "اختبار تواريخ Yahoo"
        )

        if not expirations:
            raise RuntimeError(
                "Yahoo لم يرجع تواريخ انتهاء لعقود SPY"
            )

        yahoo_status = {
            "ok": True,
            "message": (
                f"الاتصال يعمل، تواريخ الانتهاء: "
                f"{len(expirations)}"
            )
        }

        return True

    except Exception as e:

        yahoo_status = {
            "ok": False,
            "message": str(e)[:250]
        }

        logger.error(
            "فشل اختبار Yahoo: %s",
            str(e)[:250]
        )

        return False


# =====================================================
# 6. حساب RSI
# =====================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, 0.000001)

    return 100 - (100 / (1 + rs))


# =====================================================
# 7. تجهيز الشموع والمؤشرات
# =====================================================

def prepare_candles(ticker, interval):

    stock = yf.Ticker(ticker)

    df = yahoo_call(
        lambda: stock.history(
            period="5d",
            interval=interval,
            auto_adjust=False,
            prepost=False
        ),
        f"شموع {ticker} {interval}"
    )

    if df is None or df.empty:
        raise ValueError("لم تصل بيانات الشموع")

    df = df.copy()

    required = [
        "Open", "High", "Low", "Close", "Volume"
    ]

    missing = [
        col for col in required
        if col not in df.columns
    ]

    if missing:
        raise ValueError(
            f"أعمدة ناقصة: {missing}"
        )

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(
            "تنسيق وقت الشموع غير صالح"
        )

    if df.index.tz is None:
        df.index = df.index.tz_localize(NY_TZ)
    else:
        df.index = df.index.tz_convert(NY_TZ)

    df = df.sort_index()
    df = df[required].copy()
    df = df.dropna(subset=required)

    df = df[
        (df["Close"] > 0)
        & (df["Volume"] >= 0)
    ]

    if df.empty:
        raise ValueError(
            "بيانات الشموع غير صالحة"
        )

    now = now_ny()

    minutes = 5 if interval == "5m" else 15

    candle_close_times = (
        df.index + timedelta(minutes=minutes)
    )

    df = df.loc[
        candle_close_times <= now - timedelta(seconds=30)
    ]

    if df.empty:
        raise ValueError(
            "لا توجد شموع مكتملة"
        )

    last_candle_close = (
        df.index[-1] + timedelta(minutes=minutes)
    )

    age_minutes = (
        now - last_candle_close
    ).total_seconds() / 60

    max_age = (
        MAX_5M_CANDLE_AGE_MINUTES
        if interval == "5m"
        else MAX_15M_CANDLE_AGE_MINUTES
    )

    if age_minutes > max_age:

        raise ValueError(
            f"بيانات {interval} قديمة "
            f"({age_minutes:.1f} دقيقة)"
        )

    df["EMA20"] = df["Close"].ewm(
        span=20,
        adjust=False
    ).mean()

    df["RSI"] = calculate_rsi(
        df["Close"],
        14
    )

    df["Volume_Avg"] = df["Volume"].rolling(
        20,
        min_periods=20
    ).mean()

    df["Volume_Ratio"] = (
        df["Volume"]
        / df["Volume_Avg"].replace(
            0,
            float("nan")
        )
    )

    today = now.date()

    today_df = df[
        df.index.date == today
    ].copy()

    if today_df.empty:
        raise ValueError(
            "لا توجد شموع لليوم الحالي"
        )

    typical_price = (
        today_df["High"]
        + today_df["Low"]
        + today_df["Close"]
    ) / 3

    cumulative_volume = (
        today_df["Volume"].cumsum()
    )

    if cumulative_volume.iloc[-1] <= 0:
        raise ValueError(
            "حجم التداول غير صالح لحساب VWAP"
        )

    today_df["VWAP"] = (
        (typical_price * today_df["Volume"]).cumsum()
        / cumulative_volume
    )

    return df, today_df


# =====================================================
# 8. تحليل السهم بشروط مخففة
# =====================================================

def analyze_ticker(ticker):

    result = {
        "ticker": ticker,
        "signal": None,
        "grade": "لا دخول",
        "reason": "",
        "details": "",
        "underlying": None
    }

    try:

        df5, today5 = prepare_candles(
            ticker,
            "5m"
        )

        # فريم 15 دقيقة للتأكيد المساعد فقط
        df15 = None

        try:

            df15, today15 = prepare_candles(
                ticker,
                "15m"
            )

        except Exception as e:

            logger.warning(
                "%s: تعذر تجهيز فريم 15 دقيقة: %s",
                ticker,
                str(e)[:120]
            )

        if len(df5) < 22:

            result["reason"] = (
                "بيانات 5 دقائق غير كافية"
            )

            return result

        c5 = df5.iloc[-1]

        close5 = safe_float(c5["Close"])
        ema5 = safe_float(c5["EMA20"])
        rsi5 = safe_float(c5["RSI"])
        volume_ratio = safe_float(
            c5["Volume_Ratio"]
        )

        vwap = safe_float(
            today5.iloc[-1]["VWAP"]
        )

        if df15 is not None and len(df15) >= 22:

            c15 = df15.iloc[-1]

            close15 = safe_float(c15["Close"])
            ema15 = safe_float(c15["EMA20"])

        else:

            close15 = None
            ema15 = None

        values = [
            close5,
            ema5,
            rsi5,
            volume_ratio,
            vwap
        ]

        if any(value is None for value in values):

            result["reason"] = (
                "المؤشرات ناقصة أو غير صالحة"
            )

            return result

        if volume_ratio <= 0:

            result["reason"] = (
                "حجم التداول غير صالح"
            )

            return result

        result["underlying"] = close5

        call_15m_support = (
            close15 is not None
            and ema15 is not None
            and close15 > ema15
        )

        put_15m_support = (
            close15 is not None
            and ema15 is not None
            and close15 < ema15
        )

        details = (
            f"السعر={close5:.2f} | "
            f"VWAP={vwap:.2f} | "
            f"EMA20={ema5:.2f} | "
            f"RSI={rsi5:.1f} | "
            f"Volume={volume_ratio:.2f}x | "
        )

        if close15 is not None and ema15 is not None:

            details += (
                f"15m={close15:.2f}/{ema15:.2f}"
            )

        else:

            details += "15m=غير متاح"

        result["details"] = details

        # -------------------------------------------------
        # الاتجاه الأساسي يعتمد على 5 دقائق
        # 15 دقيقة تأكيد مساعد وليس شرطًا إلزاميًا
        # -------------------------------------------------

        call_direction = (
            close5 > vwap
            or close5 > ema5
        )

        put_direction = (
            close5 < vwap
            or close5 < ema5
        )

        enough_volume = (
            volume_ratio >= MIN_VOLUME_RATIO
        )

        call_signal = (
            call_direction
            and enough_volume
            and CALL_RSI_MIN <= rsi5 <= CALL_RSI_MAX
        )

        put_signal = (
            put_direction
            and enough_volume
            and PUT_RSI_MIN <= rsi5 <= PUT_RSI_MAX
        )

        if call_signal and not put_signal:

            result["signal"] = "CALL"
            result["grade"] = "قوي"

            confirmation = (
                "15m يؤكد الاتجاه"
                if call_15m_support
                else "15m لا يؤكد الاتجاه، لكنه ليس شرطًا إلزاميًا"
            )

            result["reason"] = (
                "CALL: تحقق اتجاه 5 دقائق "
                f"وحجم التداول وRSI. {confirmation}"
            )

            return result

        if put_signal and not call_signal:

            result["signal"] = "PUT"
            result["grade"] = "قوي"

            confirmation = (
                "15m يؤكد الاتجاه"
                if put_15m_support
                else "15m لا يؤكد الاتجاه، لكنه ليس شرطًا إلزاميًا"
            )

            result["reason"] = (
                "PUT: تحقق اتجاه 5 دقائق "
                f"وحجم التداول وRSI. {confirmation}"
            )

            return result

        if call_signal and put_signal:

            result["reason"] = (
                "الاتجاه غير واضح"
            )

            return result

        failed_call = []
        failed_put = []

        if not call_direction:
            failed_call.append(
                "السعر تحت VWAP وEMA20"
            )

        if not put_direction:
            failed_put.append(
                "السعر فوق VWAP وEMA20"
            )

        if rsi5 < CALL_RSI_MIN or rsi5 > CALL_RSI_MAX:
            failed_call.append(
                "RSI خارج نطاق CALL"
            )

        if rsi5 < PUT_RSI_MIN or rsi5 > PUT_RSI_MAX:
            failed_put.append(
                "RSI خارج نطاق PUT"
            )

        if not enough_volume:

            failed_call.append(
                f"حجم التداول أقل من {MIN_VOLUME_RATIO}x"
            )

            failed_put.append(
                f"حجم التداول أقل من {MIN_VOLUME_RATIO}x"
            )

        result["reason"] = (
            "CALL: " + ", ".join(failed_call)
            + "\nPUT: " + ", ".join(failed_put)
            + "\n" + details
        )

        return result

    except Exception as e:

        result["reason"] = (
            f"خطأ في التحليل: {str(e)[:180]}"
        )

        return result


# =====================================================
# 9. جلب عقد أوبشن 0DTE
# =====================================================

def get_0dte_contract(
    ticker,
    signal,
    underlying_price
):

    stock = yf.Ticker(ticker)

    today_string = (
        now_ny().date().isoformat()
    )

    expirations = yahoo_call(
        lambda: stock.options,
        f"تواريخ انتهاء {ticker}"
    )

    if not expirations:

        raise ValueError(
            "Yahoo لم يرجع تواريخ انتهاء"
        )

    if today_string not in expirations:

        raise ValueError(
            f"لا يوجد عقد ينتهي اليوم {today_string}"
        )

    chain = yahoo_call(
        lambda: stock.option_chain(today_string),
        f"سلسلة خيارات {ticker}"
    )

    options = (
        chain.calls
        if signal == "CALL"
        else chain.puts
    )

    if options is None or options.empty:

        raise ValueError(
            "سلسلة العقود فارغة"
        )

    options = options.copy()

    required_columns = [
        "contractSymbol",
        "strike",
        "bid",
        "ask",
        "volume",
        "openInterest",
        "lastTradeDate"
    ]

    missing = [
        col for col in required_columns
        if col not in options.columns
    ]

    if missing:

        raise ValueError(
            f"بيانات العقد ناقصة: {missing}"
        )

    for col in [
        "strike",
        "bid",
        "ask",
        "volume",
        "openInterest"
    ]:

        options[col] = pd.to_numeric(
            options[col],
            errors="coerce"
        )

    options = options.dropna(
        subset=required_columns
    )

    if options.empty:

        raise ValueError(
            "لا توجد عقود ببيانات مكتملة"
        )

    options["lastTradeDate"] = pd.to_datetime(
        options["lastTradeDate"],
        utc=True,
        errors="coerce"
    )

    options = options.dropna(
        subset=["lastTradeDate"]
    )

    if options.empty:

        raise ValueError(
            "لا توجد تواريخ تداول صالحة للعقود"
        )

    now_utc = datetime.now(timezone.utc)

    age = (
        now_utc - options["lastTradeDate"]
    ).dt.total_seconds() / 60

    options = options[
        (age >= -2)
        & (age <= MAX_OPTION_TRADE_AGE_MINUTES)
    ].copy()

    if options.empty:

        raise ValueError(
            "آخر تداول للعقود أقدم من "
            f"{MAX_OPTION_TRADE_AGE_MINUTES} دقيقة"
        )

    options = options[
        (options["bid"] > 0)
        & (options["ask"] > 0)
        & (options["ask"] >= options["bid"])
        & (options["ask"] >= MIN_OPTION_PRICE)
        & (options["openInterest"] >= MIN_OPEN_INTEREST)
        & (options["volume"] >= MIN_OPTION_VOLUME)
    ].copy()

    if options.empty:

        raise ValueError(
            "لا توجد عقود تحقق شروط السعر والسيولة"
        )

    midpoint = (
        options["ask"] + options["bid"]
    ) / 2

    options["spread_pct"] = (
        (options["ask"] - options["bid"])
        / midpoint
    ) * 100

    options = options[
        options["spread_pct"] <= MAX_SPREAD_PERCENT
    ].copy()

    if options.empty:

        raise ValueError(
            "فروق أسعار العقود مرتفعة"
        )

    underlying_price = safe_float(
        underlying_price
    )

    if (
        underlying_price is None
        or underlying_price <= 0
    ):

        raise ValueError(
            "سعر السهم الأساسي غير صالح"
        )

    options["distance"] = (
        options["strike"] - underlying_price
    ).abs()

    options = options.sort_values(
        by=["distance", "spread_pct"],
        ascending=[True, True]
    )

    contract = options.iloc[0]

    bid = safe_float(contract["bid"])
    ask = safe_float(contract["ask"])
    strike = safe_float(contract["strike"])
    spread = safe_float(contract["spread_pct"])

    if any(
        value is None
        for value in [bid, ask, strike, spread]
    ):

        raise ValueError(
            "بيانات العقد المختار غير صالحة"
        )

    if bid <= 0 or ask <= 0 or ask < bid:

        raise ValueError(
            "سعر العقد غير صالح"
        )

    if ask < MIN_OPTION_PRICE:

        raise ValueError(
            "سعر العقد أقل من الحد الأدنى"
        )

    entry = ask

    if entry <= 0:

        raise ValueError(
            "سعر الدخول غير صالح"
        )

    return {
        "symbol": ticker,
        "type": signal,
        "contract": str(contract["contractSymbol"]),
        "strike": strike,
        "expiration": today_string,
        "bid": bid,
        "ask": ask,
        "entry": entry,
        "spread": spread,
        "volume": int(contract["volume"]),
        "open_interest": int(contract["openInterest"]),
        "underlying": underlying_price
    }


# =====================================================
# 10. رسالة الفرصة
# =====================================================

def trade_message(trade):

    entry = trade["entry"]

    target1 = entry * (1 + TAKE_PROFIT_1)
    target2 = entry * (1 + TAKE_PROFIT_2)
    stop = entry * (1 + STOP_LOSS)

    direction = (
        "CALL 🟢"
        if trade["type"] == "CALL"
        else "PUT 🔴"
    )

    return (
        "🚨 فرصة أوبشن\n\n"
        f"الشركة: {trade['symbol']}\n"
        f"الاتجاه: {direction}\n"
        f"الاسترايك: ${trade['strike']:.2f}\n"
        f"السعر: ${entry:.2f}\n\n"
        f"🎯 الهدف 1: ${target1:.2f}\n"
        f"🎯 الهدف 2: ${target2:.2f}\n"
        f"🛑 الوقف: ${stop:.2f}\n\n"
        f"المدة: حتى إغلاق السوق اليوم\n"
        f"الانتهاء: {trade['expiration']} (0DTE)\n\n"
        "⚠️ سعر الدخول تقديري حسب Ask، "
        "والتنبيه تحليلي وليس تنفيذًا للصفقة."
    )


# =====================================================
# 11. إرسال الرسائل للمشتركين
# =====================================================

async def send_to_subscribers(
    application,
    message
):

    if not subscribers:

        logger.warning(
            "لا يوجد مشتركون؛ لم يتم تسليم التنبيه"
        )

        return False

    delivered = False

    for chat_id in list(subscribers):

        try:

            for chunk in split_message(message):

                await application.bot.send_message(
                    chat_id=chat_id,
                    text=chunk
                )

            delivered = True

        except Exception:

            logger.exception(
                "تعذر إرسال رسالة للمشترك %s",
                chat_id
            )

    return delivered


# =====================================================
# 12. فتح فرصة بعقد مؤكد
# =====================================================

async def open_trade(
    application,
    ticker,
    signal,
    contract
):

    global daily_count

    reset_daily_data_if_needed()

    if daily_count >= MAX_PER_DAY:
        return False

    if ticker in sent_today:
        return False

    if ticker in active_trades:
        return False

    trade = {
        **contract,
        "grade": "قوي",
        "opened_at": now_ny(),
        "tp1_sent": False,
        "tp2_sent": False,
        "stop_sent": False
    }

    delivered = await send_to_subscribers(
        application,
        trade_message(trade)
    )

    if not delivered:
        return False

    active_trades[ticker] = trade

    sent_today.add(ticker)
    strong_seen_today.add(ticker)

    daily_count += 1

    logger.info(
        "تم تسليم فرصة %s %s بعقد %s",
        ticker,
        signal,
        contract["contract"]
    )

    return True


# =====================================================
# 13. جلب سعر العقد للمتابعة
# =====================================================

def get_contract_quote(
    ticker,
    expiration,
    contract_symbol
):

    try:

        stock = yf.Ticker(ticker)

        chain = yahoo_call(
            lambda: stock.option_chain(expiration),
            f"متابعة عقد {ticker}"
        )

        all_options = pd.concat(
            [chain.calls, chain.puts],
            ignore_index=True
        )

        if all_options.empty:
            return None

        match = all_options[
            all_options["contractSymbol"].astype(str)
            == str(contract_symbol)
        ]

        if match.empty:
            return None

        contract = match.iloc[0]

        bid = safe_float(
            contract.get("bid")
        )

        ask = safe_float(
            contract.get("ask")
        )

        if bid is None or ask is None:
            return None

        if bid <= 0 or ask <= 0 or ask < bid:
            return None

        return (bid + ask) / 2

    except Exception:

        logger.exception(
            "تعذر جلب سعر العقد %s",
            contract_symbol
        )

        return None


# =====================================================
# 14. متابعة الأهداف والوقف
# =====================================================

async def monitor_trades(application):

    if not active_trades:
        return

    now = now_ny()

    for ticker in list(active_trades.keys()):

        trade = active_trades.get(ticker)

        if not trade:
            continue

        # إغلاق المتابعة عند إغلاق السوق الأمريكي
        if (
            now.weekday() < 5
            and now.time() >= MARKET_CLOSE
        ):

            await send_to_subscribers(
                application,
                f"⏰ انتهت مدة عقد {ticker}\n"
                f"العقد: {trade['contract']}\n"
                "انتهت متابعة البوت عند إغلاق السوق."
            )

            active_trades.pop(ticker, None)
            continue

        price = await asyncio.to_thread(
            get_contract_quote,
            ticker,
            trade["expiration"],
            trade["contract"]
        )

        if price is None:
            continue

        entry = trade["entry"]

        if entry <= 0:
            continue

        change = (price - entry) / entry

        # وقف الخسارة
        if (
            change <= STOP_LOSS
            and not trade["stop_sent"]
        ):

            trade["stop_sent"] = True

            await send_to_subscribers(
                application,
                "🛑 وقف الخسارة\n\n"
                f"الشركة: {ticker}\n"
                f"العقد: {trade['contract']}\n"
                f"الدخول: ${entry:.2f}\n"
                f"السعر الحالي: ${price:.2f}\n"
                f"التغير: {change * 100:.1f}%"
            )

            active_trades.pop(ticker, None)
            continue

        # الهدف الثاني
        if (
            change >= TAKE_PROFIT_2
            and not trade["tp2_sent"]
        ):

            trade["tp2_sent"] = True

            await send_to_subscribers(
                application,
                "🎯 تحقق الهدف الثاني\n\n"
                f"الشركة: {ticker}\n"
                f"العقد: {trade['contract']}\n"
                f"السعر الحالي: ${price:.2f}\n"
                f"التغير: {change * 100:.1f}%"
            )

        # الهدف الأول
        elif (
            change >= TAKE_PROFIT_1
            and not trade["tp1_sent"]
        ):

            trade["tp1_sent"] = True

            await send_to_subscribers(
                application,
                "✅ تحقق الهدف الأول\n\n"
                f"الشركة: {ticker}\n"
                f"العقد: {trade['contract']}\n"
                f"السعر الحالي: ${price:.2f}\n"
                f"التغير: {change * 100:.1f}%"
            )


# =====================================================
# 15. محرك الفحص
# =====================================================

async def run_scan(
    application,
    manual=False
):

    global last_scan_time
    global last_scan_summary
    global last_scan_errors

    reset_daily_data_if_needed()

    if scan_lock.locked():

        return (
            "يوجد فحص آخر قيد التنفيذ. "
            "انتظر حتى ينتهي."
        )

    async with scan_lock:

        last_scan_time = now_ny()

        summary = {}
        errors = []

        if not inside_saudi_schedule():

            return (
                "خارج وقت تشغيل البوت.\n\n"
                f"الوقت السعودي: "
                f"{now_saudi().strftime('%H:%M:%S')}\n"
                "وقت التشغيل: 17:00–00:00 بتوقيت السعودية."
            )

        if not market_is_open():

            return (
                "السوق الأمريكية مغلقة حاليًا.\n\n"
                f"وقت نيويورك: "
                f"{now_ny().strftime('%H:%M:%S')}\n"
                f"الوقت السعودي: "
                f"{now_saudi().strftime('%H:%M:%S')}\n"
                "لن يتم فتح فرص جديدة خارج جلسة السوق."
            )

        if daily_count >= MAX_PER_DAY:

            return (
                f"وصل البوت إلى الحد الأقصى "
                f"{MAX_PER_DAY} فرص اليوم."
            )

        for ticker in TICKERS:

            reset_daily_data_if_needed()

            if daily_count >= MAX_PER_DAY:
                break

            if not market_is_open():
                break

            if not inside_saudi_schedule():
                break

            if ticker in sent_today:

                summary[ticker] = (
                    "تم إرسال فرصة لهذا السهم اليوم"
                )

                continue

            if ticker in active_trades:

                summary[ticker] = (
                    "توجد فرصة نشطة لهذا السهم"
                )

                continue

            analysis = await asyncio.to_thread(
                analyze_ticker,
                ticker
            )

            grade = analysis["grade"]
            signal = analysis["signal"]

            if not signal:

                summary[ticker] = (
                    f"لا دخول | {analysis['reason']}"
                )

                continue

            if grade == "متوسط":

                medium_seen_today.add(ticker)

                summary[ticker] = (
                    f"فرصة متوسطة {signal}"
                )

                continue

            if grade != "قوي":

                summary[ticker] = (
                    f"لم يتم الإرسال: {grade}"
                )

                continue

            strong_seen_today.add(ticker)

            try:

                contract = await asyncio.to_thread(
                    get_0dte_contract,
                    ticker,
                    signal,
                    analysis["underlying"]
                )

                sent = await open_trade(
                    application,
                    ticker,
                    signal,
                    contract
                )

                if sent:

                    summary[ticker] = (
                        f"تم إرسال {signal} بعقد مؤكد "
                        f"{contract['contract']}"
                    )

                else:

                    summary[ticker] = (
                        "لم يتم التسليم: لا يوجد مشترك "
                        "أو فشل الإرسال أو بلغ الحد اليومي"
                    )

            except Exception as e:

                reason = str(e)[:200]

                errors.append(
                    f"{ticker}: {reason}"
                )

                summary[ticker] = (
                    "إشارة قوية، لكن لم يتم إرسالها "
                    "لعدم تأكيد عقد الأوبشن: "
                    f"{reason}"
                )

                logger.warning(
                    "%s: تعذر تأكيد العقد: %s",
                    ticker,
                    reason
                )

        last_scan_summary = summary
        last_scan_errors = errors

        report = (
            f"انتهى الفحص: "
            f"{now_ny().strftime('%H:%M:%S')} نيويورك\n"
            f"الوقت السعودي: "
            f"{now_saudi().strftime('%H:%M:%S')}\n\n"
            f"الفرص المرسلة بعقود مؤكدة: "
            f"{daily_count}/{MAX_PER_DAY}\n"
            f"الفرص المتوسطة المرصودة: "
            f"{len(medium_seen_today)}\n"
            f"الأسهم التي تم فحصها: {len(summary)}\n"
            f"الصفقات النشطة: {len(active_trades)}\n"
            f"أخطاء جلب العقود: {len(errors)}"
        )

        logger.info(report)

        return report


# =====================================================
# 16. محرك الفحص التلقائي
# =====================================================

async def scanner_loop(application):

    logger.info(
        "بدأ محرك الفحص التلقائي: "
        "17:00 إلى 00:00 بتوقيت السعودية"
    )

    while True:

        try:

            reset_daily_data_if_needed()

            # الفحص خلال الوقت السعودي المحدد
            # وبشرط أن تكون السوق الأمريكية مفتوحة
            if market_is_open_for_scanning():

                await run_scan(
                    application,
                    manual=False
                )

            # متابعة الصفقات النشطة
            await monitor_trades(application)

        except asyncio.CancelledError:
            raise

        except Exception:

            logger.exception(
                "خطأ في محرك الفحص"
            )

        await asyncio.sleep(SCAN_INTERVAL)


# =====================================================
# 17. أوامر تيليجرام
# =====================================================

async def start_command(update, context):

    chat_id = update.effective_chat.id

    subscribers.add(chat_id)

    await update.message.reply_text(
        "✅ تم تفعيل اشتراكك في Aboghala Bot.\n\n"
        "البوت يبحث عن فرص CALL وPUT بعقود مؤكدة.\n"
        "تم تخفيف شروط الدخول.\n"
        "لن يرسل فرصة إذا لم يستطع تحديد الاسترايك "
        "وسعر العقد.\n\n"
        "رسالة الفرصة تحتوي على:\n"
        "الشركة، الاتجاه، الاسترايك، السعر، "
        "الهدف الأول والثاني، الوقف، والمدة.\n\n"
        "⏰ وقت تشغيل البوت:\n"
        "من 5:00 مساءً إلى 12:00 منتصف الليل "
        "بتوقيت السعودية.\n\n"
        "الأوامر:\n"
        "/scan - فحص السوق\n"
        "/status - حالة البوت\n"
        "/report - تقرير آخر فحص\n"
        "/debug - تشخيص الأسهم\n"
        "/active - الصفقات النشطة\n"
        "/help - المساعدة\n\n"
        "⚠️ التنبيهات تحليلية وليست تنفيذًا للصفقات."
    )


async def scan_command(update, context):

    await update.message.reply_text(
        "🔎 بدأ الفحص اليدوي.\n"
        "قد يستغرق بعض الوقت..."
    )

    result = await run_scan(
        context.application,
        manual=True
    )

    await update.message.reply_text(result)


async def status_command(update, context):

    reset_daily_data_if_needed()

    last_scan = (
        last_scan_time.strftime("%Y-%m-%d %H:%M:%S")
        if last_scan_time
        else "لم يتم الفحص بعد"
    )

    yahoo_text = (
        "يعمل"
        if yahoo_status["ok"]
        else "تعذر الاتصال"
    )

    saudi_time = now_saudi()
    ny_time = now_ny()

    await update.message.reply_text(
        "📊 حالة Aboghala Bot\n\n"
        "الحالة: يعمل\n\n"
        f"الوقت السعودي: "
        f"{saudi_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"وقت نيويورك: "
        f"{ny_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"آخر محاولة فحص: {last_scan}\n"
        f"اختبار Yahoo: {yahoo_text}\n"
        f"تفاصيل Yahoo: {yahoo_status['message'][:200]}\n\n"
        f"الفرص المرسلة اليوم: {daily_count}/{MAX_PER_DAY}\n"
        f"الفرص المتوسطة المرصودة: {len(medium_seen_today)}\n"
        f"الصفقات النشطة: {len(active_trades)}\n"
        f"المشتركون: {len(subscribers)}\n"
        f"عدد الأسهم: {len(TICKERS)}\n\n"
        "شروط الاتجاه: السعر فوق VWAP أو EMA20 للـCALL، "
        "وتحتهما للـPUT.\n"
        f"حجم التداول الأدنى: {MIN_VOLUME_RATIO}x\n"
        f"RSI CALL: {CALL_RSI_MIN}–{CALL_RSI_MAX}\n"
        f"RSI PUT: {PUT_RSI_MIN}–{PUT_RSI_MAX}\n"
        "فريم 15 دقيقة: تأكيد مساعد فقط.\n\n"
        "وقت تشغيل البوت: 17:00–00:00 السعودية.\n"
        f"البوت داخل فترة التشغيل: "
        f"{'نعم' if inside_saudi_schedule() else 'لا'}\n"
        f"السوق الأمريكية مفتوحة: "
        f"{'نعم' if market_is_open() else 'لا'}\n"
        "نهاية متابعة العقود: إغلاق السوق الأمريكية."
    )


async def report_command(update, context):

    if not last_scan_time:

        await update.message.reply_text(
            "لم يتم تنفيذ أي فحص حتى الآن. استخدم /scan."
        )

        return

    lines = [
        "📋 تقرير آخر فحص",
        f"الوقت بنيويورك: "
        f"{last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"الوقت السعودي: "
        f"{now_saudi().strftime('%Y-%m-%d %H:%M:%S')}",
        f"الفرص المرسلة: {daily_count}/{MAX_PER_DAY}",
        f"الفرص المتوسطة: {len(medium_seen_today)}",
        ""
    ]

    for ticker, result in last_scan_summary.items():
        lines.append(
            f"{ticker}: {result}"
        )

    if last_scan_errors:

        lines.append(
            "\nأخطاء جلب العقود:"
        )

        lines.extend(last_scan_errors)

    await reply_long(
        update,
        "\n".join(lines)
    )


async def debug_command(update, context):

    if not last_scan_summary:

        await update.message.reply_text(
            "لا توجد تفاصيل فحص حتى الآن.\n"
            "استخدم /scan أثناء جلسة السوق."
        )

        return

    lines = [
        "🔬 تشخيص الأسهم\n",
        "شروط الدخول مخففة.",
        f"حجم التداول الأدنى: {MIN_VOLUME_RATIO}x",
        f"RSI CALL: {CALL_RSI_MIN}–{CALL_RSI_MAX}",
        f"RSI PUT: {PUT_RSI_MIN}–{PUT_RSI_MAX}",
        "الاتجاه يعتمد على فريم 5 دقائق.",
        "فريم 15 دقيقة تأكيد مساعد وليس شرطًا إلزاميًا.",
        "لا يتم إرسال فرصة إلا بعد تأكيد عقد الأوبشن.",
        "وقت التشغيل: 17:00–00:00 السعودية."
    ]

    for ticker, reason in last_scan_summary.items():

        lines.append(
            f"\n{ticker}: {reason}"
        )

    await reply_long(
        update,
        "\n".join(lines)
    )


async def active_command(update, context):

    if not active_trades:

        await update.message.reply_text(
            "لا توجد صفقات نشطة بعقود مؤكدة حاليًا."
        )

        return

    lines = [
        "📌 الصفقات النشطة"
    ]

    for ticker, trade in active_trades.items():

        entry = trade["entry"]

        lines.append(
            f"\n{ticker} {trade['type']}\n"
            f"الاسترايك: ${trade['strike']:.2f}\n"
            f"سعر الدخول: ${entry:.2f}\n"
            f"الهدف الأول: ${entry * 1.30:.2f}\n"
            f"الهدف الثاني: ${entry * 1.60:.2f}\n"
            f"الوقف: ${entry * 0.65:.2f}\n"
            f"الانتهاء: {trade['expiration']}"
        )

    await reply_long(
        update,
        "\n".join(lines)
    )


async def help_command(update, context):

    await update.message.reply_text(
        "/start - تفعيل التنبيهات\n"
        "/scan - فحص السوق الآن\n"
        "/status - حالة البوت\n"
        "/report - تقرير آخر فحص\n"
        "/debug - تشخيص الأسهم\n"
        "/active - الصفقات النشطة\n"
        "/help - المساعدة\n\n"
        "وقت التشغيل: 5 مساءً إلى 12 صباحًا "
        "بتوقيت السعودية."
    )


# =====================================================
# 18. بدء المهام
# =====================================================

async def post_init(application):

    asyncio.create_task(
        asyncio.to_thread(
            test_yahoo_connection
        )
    )

    application.create_task(
        scanner_loop(application)
    )

    logger.info(
        "تم بدء مهام Aboghala Bot"
    )


# =====================================================
# 19. خادم Render
# =====================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path in ("/", "/health"):

            body = b"Aboghala Bot is running"

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.end_headers()

            self.wfile.write(body)

        else:

            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    logger.info(
        "Health server listening on port %s",
        port
    )

    server.serve_forever()


# =====================================================
# 20. تشغيل البوت
# =====================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "لم يتم العثور على BOT_TOKEN "
            "في Environment Variables"
        )

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("scan", scan_command)
    )

    application.add_handler(
        CommandHandler("status", status_command)
    )

    application.add_handler(
        CommandHandler("report", report_command)
    )

    application.add_handler(
        CommandHandler("debug", debug_command)
    )

    application.add_handler(
        CommandHandler("active", active_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    logger.info(
        "Aboghala Bot is starting..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
