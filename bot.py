import os
import asyncio
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
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# =====================================================
# 1. الإعدادات
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

TZ = ZoneInfo("America/New_York")

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

START_SCAN = time(9, 40)
STOP_NEW_TRADES = time(15, 30)
CLOSE_ALL_TIME = time(15, 55)

MIN_OPTION_PRICE = 0.10
MIN_OPEN_INTEREST = 100
MIN_OPTION_VOLUME = 10
MAX_SPREAD_PERCENT = 20

TAKE_PROFIT_1 = 0.30
TAKE_PROFIT_2 = 0.60
STOP_LOSS = -0.35

# أقصى عمر مسموح لآخر تداول في العقد
MAX_OPTION_TRADE_AGE_MINUTES = 20

# أقصى عمر مسموح لآخر شمعة مكتملة
MAX_5M_CANDLE_AGE_MINUTES = 15
MAX_15M_CANDLE_AGE_MINUTES = 25

# عدد محاولات الاتصال بـ Yahoo
YAHOO_RETRIES = 3

# مهلة بين المحاولات
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
    return datetime.now(TZ)


def reset_daily_data_if_needed():

    global current_day
    global daily_count
    global sent_today
    global strong_seen_today
    global medium_seen_today

    today = now_ny().date()

    if current_day != today:

        current_day = today

        daily_count = 0

        sent_today = set()

        strong_seen_today = set()

        medium_seen_today = set()

        logger.info(
            "تم تصفير إحصائيات اليوم الجديد"
        )


def market_is_open_for_scanning():

    now = now_ny()

    if now.weekday() >= 5:
        return False

    return START_SCAN <= now.time() <= STOP_NEW_TRADES


def market_is_open():

    now = now_ny()

    if now.weekday() >= 5:
        return False

    return time(9, 30) <= now.time() <= time(16, 0)


# =====================================================
# 4. أدوات مساعدة
# =====================================================

def is_finite(value):

    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


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

    """
    تنفيذ طلب Yahoo مع إعادة المحاولة.
    نترك yfinance يدير Cookie وCrumb داخلياً.
    """

    last_error = None

    for attempt in range(YAHOO_RETRIES):

        try:

            result = function()

            return result

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
# 5. اختبار اتصال Yahoo
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
                f"الاتصال يعمل، تواريخ الانتهاء المتاحة: "
                f"{len(expirations)}"
            )
        }

        logger.info(
            "اختبار Yahoo ناجح"
        )

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

    rs = avg_gain / avg_loss.replace(
        0,
        0.000001
    )

    return 100 - (100 / (1 + rs))


# =====================================================
# 7. جلب الشموع وتجهيز المؤشرات
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

        raise ValueError(
            "لم تصل بيانات الشموع من Yahoo"
        )

    df = df.copy()

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    missing = [
        col for col in required
        if col not in df.columns
    ]

    if missing:

        raise ValueError(
            f"أعمدة الشموع ناقصة: {missing}"
        )

    if not isinstance(df.index, pd.DatetimeIndex):

        raise ValueError(
            "تنسيق وقت الشموع غير صالح"
        )

    if df.index.tz is None:

        df.index = df.index.tz_localize(TZ)

    else:

        df.index = df.index.tz_convert(TZ)

    df = df.sort_index()

    df = df[required].copy()

    df = df.dropna(
        subset=required
    )

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

    # Yahoo يعرض وقت بداية الشمعة.
    # نحتفظ بالشموع المكتملة فقط.

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

    # التأكد من حداثة البيانات

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

    # EMA20

    df["EMA20"] = df["Close"].ewm(
        span=20,
        adjust=False
    ).mean()

    # RSI

    df["RSI"] = calculate_rsi(
        df["Close"],
        14
    )

    # متوسط حجم التداول

    df["Volume_Avg"] = df["Volume"].rolling(
        20,
        min_periods=20
    ).mean()

    # نسبة حجم التداول

    df["Volume_Ratio"] = (
        df["Volume"]
        / df["Volume_Avg"].replace(
            0,
            float("nan")
        )
    )

    # VWAP لليوم الحالي

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
# 8. تحليل السهم وتصنيف الفرص
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

        df15, today15 = prepare_candles(
            ticker,
            "15m"
        )

        if len(df5) < 22 or len(df15) < 22:

            result["reason"] = (
                f"بيانات غير كافية: "
                f"5m={len(df5)} | "
                f"15m={len(df15)}"
            )

            return result

        c5 = df5.iloc[-1]

        previous_c5 = df5.iloc[-2]

        c15 = df15.iloc[-1]

        close5 = safe_float(c5["Close"])

        previous_close5 = safe_float(
            previous_c5["Close"]
        )

        ema5 = safe_float(
            c5["EMA20"]
        )

        rsi5 = safe_float(
            c5["RSI"]
        )

        volume_ratio = safe_float(
            c5["Volume_Ratio"]
        )

        close15 = safe_float(
            c15["Close"]
        )

        ema15 = safe_float(
            c15["EMA20"]
        )

        vwap = safe_float(
            today5.iloc[-1]["VWAP"]
        )

        values = [
            close5,
            previous_close5,
            ema5,
            rsi5,
            volume_ratio,
            close15,
            ema15,
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

        details = (

            f"السعر={close5:.2f} | "

            f"VWAP={vwap:.2f} | "

            f"EMA20={ema5:.2f} | "

            f"RSI={rsi5:.1f} | "

            f"Volume={volume_ratio:.2f}x | "

            f"15m={close15:.2f}/{ema15:.2f}"
        )

        result["details"] = details

        # =================================================
        # شروط الاتجاه الأساسية الأصلية
        # =================================================

        call_core = (

            close5 > vwap

            and close5 > ema5

            and close15 > ema15

            and volume_ratio >= 1.2
        )

        put_core = (

            close5 < vwap

            and close5 < ema5

            and close15 < ema15

            and volume_ratio >= 1.2
        )

        # =================================================
        # شروط CALL الأصلية
        # =================================================

        call_original = (

            call_core

            and 52 <= rsi5 <= 72
        )

        # =================================================
        # CALL استمرار الصعود
        # =================================================

        call_continuation = (

            call_core

            and rsi5 > 72

            and close5 > previous_close5
        )

        # =================================================
        # شروط PUT الأصلية
        # =================================================

        put_original = (

            put_core

            and 28 <= rsi5 <= 48
        )

        # =================================================
        # PUT استمرار الهبوط
        # =================================================

        put_continuation = (

            put_core

            and rsi5 < 28

            and close5 < previous_close5
        )

        # =================================================
        # الفرص القوية
        # =================================================

        if call_original:

            result["signal"] = "CALL"

            result["grade"] = "قوي"

            result["reason"] = (
                "🟢 CALL قوي: اكتملت الشروط الأصلية"
            )

            return result

        if call_continuation:

            result["signal"] = "CALL"

            result["grade"] = "قوي"

            result["reason"] = (
                "🟢 CALL قوي: استمرار الصعود "
                "مع تأكيد إغلاق الشمعة"
            )

            return result

        if put_original:

            result["signal"] = "PUT"

            result["grade"] = "قوي"

            result["reason"] = (
                "🟢 PUT قوي: اكتملت الشروط الأصلية"
            )

            return result

        if put_continuation:

            result["signal"] = "PUT"

            result["grade"] = "قوي"

            result["reason"] = (
                "🟢 PUT قوي: استمرار الهبوط "
                "مع تأكيد إغلاق الشمعة"
            )

            return result

        # =================================================
        # الفرص المتوسطة
        # =================================================

        if call_core:

            result["signal"] = "CALL"

            result["grade"] = "متوسط"

            result["reason"] = (
                "🟡 CALL متوسط: الاتجاه والحجم متوافقان "
                "لكن شرط RSI أو تأكيد الاستمرار غير مكتمل.\n"
                + details
            )

            return result

        if put_core:

            result["signal"] = "PUT"

            result["grade"] = "متوسط"

            result["reason"] = (
                "🟡 PUT متوسط: الاتجاه والحجم متوافقان "
                "لكن شرط RSI أو تأكيد الاستمرار غير مكتمل.\n"
                + details
            )

            return result

        # =================================================
        # لا دخول
        # =================================================

        failed_call = []

        failed_put = []

        if close5 <= vwap:
            failed_call.append("السعر ليس فوق VWAP")

        if close5 <= ema5:
            failed_call.append("السعر ليس فوق EMA20")

        if close15 <= ema15:
            failed_call.append("اتجاه 15 دقيقة ليس صاعدًا")

        if volume_ratio < 1.2:
            failed_call.append("حجم التداول أقل من 1.2x")

        if close5 >= vwap:
            failed_put.append("السعر ليس تحت VWAP")

        if close5 >= ema5:
            failed_put.append("السعر ليس تحت EMA20")

        if close15 >= ema15:
            failed_put.append("اتجاه 15 دقيقة ليس هابطًا")

        if volume_ratio < 1.2:
            failed_put.append("حجم التداول أقل من 1.2x")

        result["reason"] = (

            "🔴 لا دخول\n"

            "CALL: "
            + ", ".join(failed_call)

            + "\nPUT: "
            + ", ".join(failed_put)

            + "\n"

            + details
        )

        return result

    except Exception as e:

        result["reason"] = (
            f"خطأ في التحليل: {str(e)[:180]}"
        )

        return result


# =====================================================
# 9. جلب عقود 0DTE
# =====================================================

def get_0dte_contract(
    ticker,
    signal,
    underlying_price
):

    """
    جلب عقد ينتهي اليوم فقط.

    لا يتم اختيار تاريخ مستقبلي بديل.
    لا يتم اختراع سعر عند فشل البيانات.
    """

    stock = yf.Ticker(ticker)

    today_string = now_ny().date().isoformat()

    # -------------------------------------------------
    # جلب تواريخ الانتهاء
    # -------------------------------------------------

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
            f"لا يوجد عقد ينتهي اليوم {today_string}. "
            f"التواريخ المتاحة: "
            f"{', '.join(expirations[:5])}"
        )

    # -------------------------------------------------
    # جلب سلسلة الخيارات
    # -------------------------------------------------

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

    # -------------------------------------------------
    # تنظيف البيانات
    # -------------------------------------------------

    options["strike"] = pd.to_numeric(
        options["strike"],
        errors="coerce"
    )

    options["bid"] = pd.to_numeric(
        options["bid"],
        errors="coerce"
    )

    options["ask"] = pd.to_numeric(
        options["ask"],
        errors="coerce"
    )

    options["volume"] = pd.to_numeric(
        options["volume"],
        errors="coerce"
    )

    options["openInterest"] = pd.to_numeric(
        options["openInterest"],
        errors="coerce"
    )

    options = options.dropna(
        subset=required_columns
    )

    if options.empty:

        raise ValueError(
            "لا توجد عقود ببيانات مكتملة"
        )

    # -------------------------------------------------
    # التحقق من حداثة آخر تداول
    # -------------------------------------------------

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
            "لا توجد عقود بتاريخ تداول صالح"
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
            "آخر تداول للعقود قديم أو غير حديث "
            f"(أكثر من {MAX_OPTION_TRADE_AGE_MINUTES} دقيقة)"
        )

    # -------------------------------------------------
    # شروط السيولة والسعر
    # -------------------------------------------------

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
            "لا توجد عقود تحقق شروط السيولة والسعر "
            "والحجم المفتوح"
        )

    # -------------------------------------------------
    # حساب فرق السعر
    # -------------------------------------------------

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
            "العقود المتاحة فروق أسعارها أعلى من 20%"
        )

    # -------------------------------------------------
    # اختيار أقرب Strike للسعر الحالي
    # -------------------------------------------------

    underlying_price = safe_float(
        underlying_price
    )

    if underlying_price is None or underlying_price <= 0:

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

    # -------------------------------------------------
    # التحقق النهائي من بيانات العقد
    # -------------------------------------------------

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
            "سعر Bid أو Ask غير صالح"
        )

    if ask < MIN_OPTION_PRICE:

        raise ValueError(
            "سعر العقد أقل من الحد الأدنى"
        )

    # سعر الدخول = Ask
    # هذا تقدير محافظ للشراء، وليس ضمان تنفيذ.

    return {

        "symbol": ticker,

        "type": signal,

        "contract": str(
            contract["contractSymbol"]
        ),

        "strike": strike,

        "expiration": today_string,

        "bid": bid,

        "ask": ask,

        "entry": ask,

        "spread": spread,

        "volume": int(
            contract["volume"]
        ),

        "open_interest": int(
            contract["openInterest"]
        ),

        "underlying": underlying_price,

        "last_trade": str(
            contract["lastTradeDate"]
        )
    }


# =====================================================
# 10. متابعة أسعار العقود
# =====================================================

def get_contract_quote(
    ticker,
    expiration,
    contract_symbol
):

    """
    جلب Bid وAsk للعقد من سلسلة الخيارات نفسها.
    لا نعتمد على history للعقد؛ لأن بياناته قد لا تتوفر.
    """

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

        # استخدام منتصف السعر لتقييم الصفقة
        midpoint = (bid + ask) / 2

        if midpoint <= 0:
            return None

        return midpoint

    except Exception:

        logger.exception(
            "تعذر جلب سعر العقد %s",
            contract_symbol
        )

        return None


# =====================================================
# 11. رسائل الفرص
# =====================================================

def trade_message(trade):

    return (

        "🚨 فرصة أوبشن جديدة\n\n"

        f"السهم: {trade['symbol']}\n"

        f"النوع: {trade['type']}\n"

        f"قوة الفرصة: {trade.get('grade', 'قوي')} 🟢\n\n"

        f"العقد: {trade['contract']}\n"

        f"Strike: {trade['strike']:.2f}\n"

        f"الانتهاء: {trade['expiration']} (0DTE)\n\n"

        f"سعر الدخول التقديري: ${trade['entry']:.2f}\n"

        f"Bid: ${trade['bid']:.2f}\n"

        f"Ask: ${trade['ask']:.2f}\n"

        f"فرق السعر: {trade['spread']:.1f}%\n"

        f"حجم العقد: {trade['volume']}\n"

        f"Open Interest: {trade['open_interest']}\n\n"

        f"سعر السهم الأساسي: ${trade['underlying']:.2f}\n\n"

        "🎯 الهدف الأول: +30%\n"

        "🎯 الهدف الثاني: +60%\n"

        "🛑 وقف الخسارة: -35%\n\n"

        "⚠️ سعر الدخول تقديري وفق Ask، "
        "وسعر المتابعة وفق منتصف Bid وAsk.\n"

        "⚠️ التنبيه تحليلي فقط وليس تنفيذًا لصفقة."
    )


# =====================================================
# 12. إرسال الرسائل للمشتركين
# =====================================================

async def send_to_subscribers(
    application,
    message
):

    for chat_id in list(subscribers):

        try:

            for chunk in split_message(message):

                await application.bot.send_message(
                    chat_id=chat_id,
                    text=chunk
                )

        except Exception:

            logger.exception(
                "تعذر إرسال رسالة للمشترك %s",
                chat_id
            )


# =====================================================
# 13. فتح فرصة
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

        "opened_at": now_ny().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "tp1_sent": False,

        "tp2_sent": False
    }

    active_trades[ticker] = trade

    sent_today.add(ticker)

    strong_seen_today.add(ticker)

    daily_count += 1

    await send_to_subscribers(
        application,
        trade_message(trade)
    )

    logger.info(
        "تم إرسال فرصة %s %s",
        ticker,
        signal
    )

    return True


# =====================================================
# 14. متابعة الأهداف ووقف الخسارة
# =====================================================

async def monitor_trades(application):

    if not active_trades:
        return

    now = now_ny()

    for ticker in list(active_trades.keys()):

        trade = active_trades.get(ticker)

        if not trade:
            continue

        # إغلاق المتابعة عند 15:55

        if now.time() >= CLOSE_ALL_TIME:

            await send_to_subscribers(

                application,

                f"⏰ انتهت متابعة {ticker}\n"

                f"العقد: {trade['contract']}\n"

                "يرجى التحقق من العقد يدويًا."
            )

            active_trades.pop(ticker, None)

            continue

        # جلب سعر العقد

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

        change = (
            (price - entry) / entry
        )

        # -------------------------------------------------
        # وقف الخسارة
        # -------------------------------------------------

        if change <= STOP_LOSS:

            await send_to_subscribers(

                application,

                "🛑 تنبيه وقف الخسارة\n\n"

                f"{ticker} | {trade['type']}\n"

                f"العقد: {trade['contract']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

                f"السعر الحالي التقريبي: ${price:.2f}\n"

                f"التغير: {change * 100:.1f}%"
            )

            active_trades.pop(ticker, None)

            continue

        # -------------------------------------------------
        # الهدف الثاني
        # -------------------------------------------------

        if (

            change >= TAKE_PROFIT_2

            and not trade["tp2_sent"]

        ):

            trade["tp2_sent"] = True

            await send_to_subscribers(

                application,

                "🎯 تحقق الهدف الثاني\n\n"

                f"{ticker} | {trade['type']}\n"

                f"العقد: {trade['contract']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

                f"السعر الحالي التقريبي: ${price:.2f}\n"

                f"الربح التقريبي: {change * 100:.1f}%"
            )

        # -------------------------------------------------
        # الهدف الأول
        # -------------------------------------------------

        elif (

            change >= TAKE_PROFIT_1

            and not trade["tp1_sent"]

        ):

            trade["tp1_sent"] = True

            await send_to_subscribers(

                application,

                "✅ تحقق الهدف الأول\n\n"

                f"{ticker} | {trade['type']}\n"

                f"العقد: {trade['contract']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

                f"السعر الحالي التقريبي: ${price:.2f}\n"

                f"الربح التقريبي: {change * 100:.1f}%"
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

        if not market_is_open_for_scanning():

            message = (

                "السوق خارج وقت البحث المحدد.\n\n"

                f"وقت نيويورك الآن: "
                f"{now_ny().strftime('%H:%M:%S')}\n"

                "وقت البحث: 09:40 إلى 15:30 "
                "بتوقيت نيويورك.\n\n"

                "لم يتم فتح فرص جديدة."
            )

            last_scan_summary = {}

            last_scan_errors = [message]

            return message

        if daily_count >= MAX_PER_DAY:

            return (
                "وصل البوت إلى الحد الأقصى: "
                "10 فرص قوية اليوم."
            )

        for ticker in TICKERS:

            reset_daily_data_if_needed()

            if daily_count >= MAX_PER_DAY:
                break

            if ticker in sent_today:

                summary[ticker] = (
                    "تم إرسال فرصة لهذا السهم اليوم"
                )

                continue

            if ticker in active_trades:

                summary[ticker] = (
                    "لديه فرصة متابعة نشطة"
                )

                continue

            # -------------------------------------------------
            # تحليل السهم
            # -------------------------------------------------

            analysis = await asyncio.to_thread(

                analyze_ticker,

                ticker
            )

            grade = analysis["grade"]

            signal = analysis["signal"]

            # -------------------------------------------------
            # لا دخول
            # -------------------------------------------------

            if not signal:

                summary[ticker] = (

                    f"{grade} | "

                    f"{analysis['reason']}"
                )

                continue

            # -------------------------------------------------
            # الفرص المتوسطة
            # -------------------------------------------------

            if grade == "متوسط":

                medium_seen_today.add(ticker)

                summary[ticker] = (

                    f"🟡 متوسط {signal} | "

                    f"{analysis['reason']}"
                )

                continue

            # -------------------------------------------------
            # لا نرسل إلا القوي
            # -------------------------------------------------

            if grade != "قوي":

                summary[ticker] = (
                    f"لم يتم الإرسال: {grade}"
                )

                continue

            strong_seen_today.add(ticker)

            # -------------------------------------------------
            # جلب عقد حقيقي
            # -------------------------------------------------

            try:

                contract = await asyncio.to_thread(

                    get_0dte_contract,

                    ticker,

                    signal,

                    analysis["underlying"]
                )

                contract["grade"] = "قوي"

                sent = await open_trade(

                    application,

                    ticker,

                    signal,

                    contract
                )

                if sent:

                    summary[ticker] = (

                        f"🟢 تم إرسال {signal} قوي\n"

                        f"{analysis['details']}\n"

                        f"العقد: {contract['contract']}\n"

                        f"Ask: ${contract['ask']:.2f}"
                    )

                else:

                    summary[ticker] = (
                        "لم يتم إرسال الفرصة"
                    )

            except Exception as e:

                reason = (
                    f"إشارة قوية لكن تعذر تأكيد العقد: "
                    f"{str(e)[:200]}"
                )

                summary[ticker] = reason

                errors.append(
                    f"{ticker}: {reason}"
                )

                logger.warning(
                    "%s",
                    reason
                )

        last_scan_summary = summary

        last_scan_errors = errors

        report = (

            f"انتهى الفحص: "
            f"{now_ny().strftime('%H:%M:%S')} نيويورك\n\n"

            f"الفرص القوية المرسلة: "
            f"{daily_count}/{MAX_PER_DAY}\n"

            f"الفرص المتوسطة المرصودة: "
            f"{len(medium_seen_today)}\n"

            f"عدد الأسهم التي تم فحصها: "
            f"{len(summary)}\n"

            f"الصفقات النشطة: "
            f"{len(active_trades)}\n"

            f"أخطاء جلب العقود: "
            f"{len(errors)}"
        )

        logger.info(report)

        return report


# =====================================================
# 16. محرك الفحص التلقائي
# =====================================================

async def scanner_loop(application):

    logger.info(
        "بدأ محرك الفحص التلقائي"
    )

    while True:

        try:

            reset_daily_data_if_needed()

            if market_is_open_for_scanning():

                await run_scan(
                    application,
                    manual=False
                )

            # متابعة الصفقات حتى 15:55

            await monitor_trades(application)

        except asyncio.CancelledError:

            raise

        except Exception:

            logger.exception(
                "خطأ في محرك الفحص"
            )

        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =====================================================
# 17. أوامر تيليجرام
# =====================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    subscribers.add(chat_id)

    await update.message.reply_text(

        "✅ تم تفعيل اشتراكك في Aboghala Bot.\n\n"

        "البوت يبحث عن فرص CALL وPUT.\n"

        "يرسل الفرص القوية فقط عند توفر عقد مناسب.\n"

        "الفرص المتوسطة تظهر في التشخيص فقط.\n\n"

        "أوامر البوت:\n"

        "/scan - فحص السوق\n"

        "/status - حالة البوت\n"

        "/report - تقرير آخر فحص\n"

        "/debug - أسباب قبول أو رفض الأسهم\n"

        "/active - الصفقات النشطة\n"

        "/help - المساعدة\n\n"

        "⚠️ التنبيهات تحليلية وليست تنفيذًا للصفقات."
    )


async def scan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "🔎 بدأ الفحص اليدوي.\n"
        "قد يستغرق بعض الوقت..."
    )

    result = await run_scan(
        context.application,
        manual=True
    )

    await update.message.reply_text(
        result
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reset_daily_data_if_needed()

    last_scan = (

        last_scan_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if last_scan_time

        else "لم يتم الفحص بعد"
    )

    yahoo_text = (

        "يعمل"

        if yahoo_status["ok"]

        else "تعذر الاتصال"
    )

    await update.message.reply_text(

        "📊 حالة Aboghala Bot\n\n"

        "الحالة: يعمل\n"

        f"وقت نيويورك: "
        f"{now_ny().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        f"آخر محاولة فحص: {last_scan}\n"

        f"اختبار Yahoo: {yahoo_text}\n"

        f"تفاصيل Yahoo: "
        f"{yahoo_status['message'][:250]}\n\n"

        f"الفرص القوية المرسلة اليوم: "
        f"{daily_count}/{MAX_PER_DAY}\n"

        f"الفرص المتوسطة المرصودة اليوم: "
        f"{len(medium_seen_today)}\n"

        f"الصفقات النشطة: "
        f"{len(active_trades)}\n"

        f"المشتركون: {len(subscribers)}\n"

        f"عدد الأسهم: {len(TICKERS)}\n\n"

        f"السوق ضمن وقت البحث: "
        f"{'نعم' if market_is_open_for_scanning() else 'لا'}\n"

        "نافذة فتح الفرص: 09:40–15:30 نيويورك\n"

        "إيقاف المتابعة: 15:55 نيويورك"
    )


async def report_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not last_scan_time:

        await update.message.reply_text(
            "لم يتم تنفيذ أي فحص حتى الآن. استخدم /scan."
        )

        return

    lines = [

        "📋 تقرير آخر فحص",

        f"الوقت: "
        f"{last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}",

        f"الفرص القوية: {daily_count}/{MAX_PER_DAY}",

        f"الفرص المتوسطة: {len(medium_seen_today)}",

        ""
    ]

    for ticker, result in last_scan_summary.items():

        lines.append(
            f"{ticker}: {result}"
        )

    if last_scan_errors:

        lines.append("\nأخطاء جلب العقود:")

        lines.extend(last_scan_errors)

    await reply_long(
        update,
        "\n".join(lines)
    )


async def debug_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not last_scan_summary:

        await update.message.reply_text(

            "لا توجد تفاصيل فحص حتى الآن.\n"

            "استخدم /scan أثناء نافذة البحث."
        )

        return

    lines = [

        "🔬 تشخيص الأسهم\n",

        "🟢 قوي: يرسل تنبيهًا بعد تأكيد العقد.",

        "🟡 متوسط: لا يرسل تنبيهًا.",

        "🔴 لا دخول: الشروط غير مكتملة.",

        "⚠️ فشل العقد: إشارة السهم قوية لكن لم يتم تأكيد عقد مناسب."
    ]

    for ticker, reason in last_scan_summary.items():

        lines.append(
            f"\n{ticker}: {reason}"
        )

    await reply_long(
        update,
        "\n".join(lines)
    )


async def active_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not active_trades:

        await update.message.reply_text(
            "لا توجد صفقات نشطة حاليًا."
        )

        return

    lines = [
        "📌 الصفقات النشطة"
    ]

    for ticker, trade in active_trades.items():

        lines.append(

            f"\n{ticker} {trade['type']}\n"

            f"العقد: {trade['contract']}\n"

            f"الانتهاء: {trade['expiration']}\n"

            f"الدخول: ${trade['entry']:.2f}\n"

            f"الهدف الأول: "
            f"{'تم' if trade['tp1_sent'] else 'لم يتحقق'}\n"

            f"الهدف الثاني: "
            f"{'تم' if trade['tp2_sent'] else 'لم يتحقق'}"
        )

    await reply_long(
        update,
        "\n".join(lines)
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "/start - تفعيل التنبيهات\n"

        "/scan - فحص السوق الآن\n"

        "/status - حالة البوت\n"

        "/report - تقرير آخر فحص\n"

        "/debug - تشخيص الأسهم\n"

        "/active - الصفقات النشطة\n"

        "/help - المساعدة"
    )


# =====================================================
# 18. بدء المهام
# =====================================================

async def post_init(application):

    # اختبار Yahoo في الخلفية

    asyncio.create_task(
        asyncio.to_thread(
            test_yahoo_connection
        )
    )

    # بدء محرك الفحص

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
