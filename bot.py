import os
import asyncio
import logging
import threading
import math
import time as time_module

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import yfinance as yf
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =====================================================
# 1. الإعدادات
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TRADIER_API_TOKEN = os.getenv("TRADIER_API_TOKEN", "").strip()

TZ = ZoneInfo("America/New_York")

TICKERS = [
    "SPY", "QQQ", "IWM", "DIA",
    "NVDA", "TSLA", "META", "AAPL", "AMD",
    "MSFT", "GOOGL", "AMZN", "MU", "AVGO",
    "ORCL", "PLTR", "ARM", "CRM",
    "COIN", "NFLX", "HOOD", "SOFI",
    "UBER", "SMCI",
    "JPM", "XOM", "BA", "DIS", "GLD", "TLT",
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

HTTP_TIMEOUT = 12
YAHOO_TEST_TIMEOUT = 8

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
)


# =====================================================
# 2. السجلات وحالة البوت
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("AboghalaBot")

subscribers = set()

active_trades = {}

sent_today = set()

strong_today = set()
medium_today = set()

daily_count = 0
medium_count = 0

current_day = None

last_scan_time = None
last_scan_summary = {}
last_scan_errors = []

scanner_started = False

yahoo_test_status = "لم يبدأ"
yahoo_test_summary = "لم يبدأ اختبار Yahoo بعد"

scan_lock = asyncio.Lock()

http_session = requests.Session()

http_session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
})


# =====================================================
# 3. الوقت والسوق
# =====================================================

def now_ny():
    return datetime.now(TZ)


def reset_daily_data_if_needed():

    global current_day
    global daily_count
    global medium_count
    global sent_today
    global strong_today
    global medium_today

    today = now_ny().date()

    if current_day != today:

        current_day = today

        daily_count = 0
        medium_count = 0

        sent_today = set()

        strong_today = set()
        medium_today = set()

        logger.info("تم تصفير عدادات اليوم الجديد")


def market_is_open_for_scanning():

    now = now_ny()

    if now.weekday() >= 5:
        return False

    return START_SCAN <= now.time() < STOP_NEW_TRADES


def market_is_open():

    now = now_ny()

    if now.weekday() >= 5:
        return False

    return time(9, 30) <= now.time() < time(16, 0)


# =====================================================
# 4. أدوات مساعدة
# =====================================================

def safe_float(value, default=None):

    try:

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except (TypeError, ValueError):
        return default


def split_telegram_text(text, limit=3500):

    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    for line in text.splitlines(keepends=True):

        if len(current) + len(line) > limit:

            if current:
                parts.append(current)
                current = ""

            while len(line) > limit:

                parts.append(line[:limit])
                line = line[limit:]

        current += line

    if current:
        parts.append(current)

    return parts


async def reply_long(update, text):

    for part in split_telegram_text(text):

        await update.message.reply_text(part)


# =====================================================
# 5. اختبار Yahoo Finance
# =====================================================

def test_yahoo_options():

    global yahoo_test_status
    global yahoo_test_summary

    yahoo_test_status = "جارٍ الفحص"

    ticker = "SPY"

    try:

        expirations = yahoo_get_expirations(ticker)

        if expirations:

            yahoo_test_status = "يعمل"

            yahoo_test_summary = (
                f"SPY | عدد تواريخ الانتهاء: {len(expirations)}"
            )

        else:

            yahoo_test_status = "لم يرجع تواريخ"

            yahoo_test_summary = (
                "Yahoo لم يرجع تواريخ انتهاء لسهم SPY"
            )

    except Exception as exc:

        yahoo_test_status = "تعذر الاتصال"

        yahoo_test_summary = str(exc)[:300]

    logger.info(
        "Yahoo test: %s | %s",
        yahoo_test_status,
        yahoo_test_summary,
    )


# =====================================================
# 6. جلب بيانات Yahoo Options
# =====================================================

def yahoo_get_crumb():

    try:

        http_session.get(
            "https://fc.yahoo.com",
            timeout=HTTP_TIMEOUT,
        )

        response = http_session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        crumb = response.text.strip()

        if crumb and "<" not in crumb:
            return crumb

    except Exception as exc:

        logger.warning(
            "تعذر الحصول على Yahoo crumb: %s",
            str(exc)[:150],
        )

    return None


def yahoo_get_expirations(ticker):

    errors = []

    hosts = [
        "query1.finance.yahoo.com",
        "query2.finance.yahoo.com",
    ]

    crumb = yahoo_get_crumb()

    for host in hosts:

        url = (
            f"https://{host}/v7/finance/options/{ticker}"
        )

        params = {}

        if crumb:
            params["crumb"] = crumb

        try:

            response = http_session.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            chain = data.get("optionChain") or {}

            results = chain.get("result") or []

            if not results:

                errors.append(
                    f"{host}: لا توجد نتائج"
                )

                continue

            dates = results[0].get("expirationDates") or []

            if dates:

                return dates

            errors.append(
                f"{host}: قائمة تواريخ الانتهاء فارغة"
            )

        except Exception as exc:

            errors.append(
                f"{host}: {str(exc)[:150]}"
            )

    raise ValueError(
        "فشل جلب تواريخ Yahoo: " + " | ".join(errors)
    )


def yahoo_get_option_chain(ticker, expiration_timestamp):

    hosts = [
        "query1.finance.yahoo.com",
        "query2.finance.yahoo.com",
    ]

    crumb = yahoo_get_crumb()

    errors = []

    for host in hosts:

        url = (
            f"https://{host}/v7/finance/options/{ticker}"
        )

        params = {
            "date": expiration_timestamp,
        }

        if crumb:
            params["crumb"] = crumb

        try:

            response = http_session.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            results = (
                data.get("optionChain", {}).get("result") or []
            )

            if not results:
                continue

            options = results[0].get("options") or []

            if not options:
                continue

            return options[0]

        except Exception as exc:

            errors.append(
                f"{host}: {str(exc)[:150]}"
            )

    raise ValueError(
        "تعذر جلب سلسلة عقود Yahoo: "
        + " | ".join(errors)
    )


# =====================================================
# 7. مصدر Tradier الاختياري
# =====================================================

def tradier_request(endpoint, params):

    if not TRADIER_API_TOKEN:

        raise ValueError(
            "TRADIER_API_TOKEN غير موجود"
        )

    url = (
        "https://api.tradier.com/v1/"
        + endpoint.lstrip("/")
    )

    headers = {
        "Authorization": f"Bearer {TRADIER_API_TOKEN}",
        "Accept": "application/json",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def tradier_get_expirations(ticker):

    data = tradier_request(
        "markets/options/expirations",
        {
            "symbol": ticker,
            "includeAllRoots": "true",
            "strikes": "false",
        },
    )

    result = data.get("expirations") or {}

    dates = result.get("date") or []

    if isinstance(dates, str):
        dates = [dates]

    return dates


def tradier_get_chain(ticker, expiration, signal):

    data = tradier_request(
        "markets/options/chains",
        {
            "symbol": ticker,
            "expiration": expiration,
            "greeks": "false",
        },
    )

    result = data.get("options") or {}

    options = result.get("option") or []

    if isinstance(options, dict):
        options = [options]

    option_type = (
        "call" if signal == "CALL" else "put"
    )

    rows = []

    for option in options:

        if option.get("option_type") != option_type:
            continue

        rows.append({

            "contractSymbol": option.get("symbol"),

            "strike": safe_float(option.get("strike")),

            "bid": safe_float(option.get("bid")),

            "ask": safe_float(option.get("ask")),

            "volume": safe_float(option.get("volume"), 0),

            "openInterest": safe_float(
                option.get("open_interest"),
                0,
            ),
        })

    return pd.DataFrame(rows)


# =====================================================
# 8. حساب المؤشرات الفنية
# =====================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        0.000001,
    )

    return 100 - (100 / (1 + rs))


# =====================================================
# 9. جلب الشموع وتجهيز المؤشرات
# =====================================================

def prepare_candles(ticker, interval):

    stock = yf.Ticker(ticker)

    df = stock.history(
        period="5d",
        interval=interval,
        auto_adjust=False,
        prepost=False,
    )

    if df is None or df.empty:

        raise ValueError(
            "لم تصل بيانات الشموع من Yahoo Finance"
        )

    df = df.copy()

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    for column in required:

        if column not in df.columns:

            raise ValueError(
                f"عمود البيانات مفقود: {column}"
            )

    if df.index.tz is None:

        df.index = df.index.tz_localize(TZ)

    else:

        df.index = df.index.tz_convert(TZ)

    df = df.dropna(
        subset=required
    ).copy()

    minutes = 5 if interval == "5m" else 15

    candle_close_times = (
        df.index + timedelta(minutes=minutes)
    )

    df = df.loc[
        candle_close_times <= now_ny() - timedelta(seconds=30)
    ].copy()

    if df.empty:

        raise ValueError(
            "لا توجد شموع مكتملة"
        )

    df["EMA20"] = df["Close"].ewm(
        span=20,
        adjust=False,
    ).mean()

    df["RSI"] = calculate_rsi(
        df["Close"],
        14,
    )

    df["Volume_Avg"] = (
        df["Volume"].rolling(20).mean()
    )

    df["Volume_Ratio"] = (
        df["Volume"]
        / df["Volume_Avg"].replace(0, float("nan"))
    )

    today = now_ny().date()

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
            "حجم التداول غير متوفر لحساب VWAP"
        )

    today_df["VWAP"] = (
        (typical_price * today_df["Volume"]).cumsum()
        / cumulative_volume
    )

    return df, today_df


# =====================================================
# 10. تحليل السهم وتصنيف الفرص
# =====================================================

def analyze_ticker(ticker):

    result = {
        "ticker": ticker,
        "signal": None,
        "grade": "لا دخول",
        "reason": "",
        "details": "",
    }

    try:

        df5, today5 = prepare_candles(
            ticker,
            "5m",
        )

        df15, _ = prepare_candles(
            ticker,
            "15m",
        )

        if len(df5) < 22 or len(df15) < 22:

            result["reason"] = (
                f"بيانات غير كافية: "
                f"5m={len(df5)} | 15m={len(df15)}"
            )

            return result

        c5 = df5.iloc[-1]

        previous_c5 = df5.iloc[-2]

        c15 = df15.iloc[-1]

        close5 = safe_float(c5["Close"])
        previous_close5 = safe_float(previous_c5["Close"])

        ema5 = safe_float(c5["EMA20"])
        rsi5 = safe_float(c5["RSI"])

        volume_ratio = safe_float(c5["Volume_Ratio"])

        close15 = safe_float(c15["Close"])
        ema15 = safe_float(c15["EMA20"])

        vwap = safe_float(today5.iloc[-1]["VWAP"])

        values = [
            close5,
            previous_close5,
            ema5,
            rsi5,
            volume_ratio,
            close15,
            ema15,
            vwap,
        ]

        if any(value is None for value in values):

            result["reason"] = (
                "المؤشرات غير مكتملة أو تحتوي على بيانات ناقصة"
            )

            return result

        details = (
            f"السعر={close5:.2f} | "
            f"VWAP={vwap:.2f} | "
            f"EMA20={ema5:.2f} | "
            f"RSI={rsi5:.1f} | "
            f"Volume={volume_ratio:.2f}x | "
            f"15m={close15:.2f}/{ema15:.2f}"
        )

        result["details"] = details

        # شروط الاتجاه الأساسية الأصلية

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

        # شروط CALL القوي

        call_original = (
            call_core
            and 52 <= rsi5 <= 72
        )

        call_continuation = (
            call_core
            and rsi5 > 72
            and close5 > previous_close5
        )

        # شروط PUT القوي

        put_original = (
            put_core
            and 28 <= rsi5 <= 48
        )

        put_continuation = (
            put_core
            and rsi5 < 28
            and close5 < previous_close5
        )

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

        failed_call = []
        failed_put = []

        if not close5 > vwap:
            failed_call.append("السعر ليس فوق VWAP")

        if not close5 > ema5:
            failed_call.append("السعر ليس فوق EMA20")

        if not close15 > ema15:
            failed_call.append("اتجاه 15 دقيقة ليس صاعدًا")

        if not volume_ratio >= 1.2:
            failed_call.append("حجم التداول أقل من 1.2x")

        if not close5 < vwap:
            failed_put.append("السعر ليس تحت VWAP")

        if not close5 < ema5:
            failed_put.append("السعر ليس تحت EMA20")

        if not close15 < ema15:
            failed_put.append("اتجاه 15 دقيقة ليس هابطًا")

        if not volume_ratio >= 1.2:
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

    except Exception as exc:

        result["reason"] = (
            f"خطأ في التحليل: {str(exc)[:200]}"
        )

        return result


# =====================================================
# 11. اختيار تاريخ انتهاء العقد
# =====================================================

def get_valid_expirations(ticker):

    today = now_ny().date()

    errors = []

    # المحاولة الأولى: Yahoo المباشر

    try:

        expirations = yahoo_get_expirations(ticker)

        valid = []

        for value in expirations:

            try:

                if isinstance(value, (int, float)):

                    expiry_date = datetime.fromtimestamp(
                        value,
                        tz=timezone.utc,
                    ).date()

                else:

                    expiry_date = datetime.strptime(
                        str(value)[:10],
                        "%Y-%m-%d",
                    ).date()

                if expiry_date >= today:

                    valid.append(expiry_date)

            except Exception:
                continue

        if valid:
            return sorted(set(valid))

    except Exception as exc:

        errors.append(
            f"Yahoo مباشر: {str(exc)[:180]}"
        )

    # المحاولة الثانية: yfinance

    try:

        stock = yf.Ticker(ticker)

        expirations = stock.options

        valid = []

        for value in expirations:

            try:

                expiry_date = datetime.strptime(
                    str(value)[:10],
                    "%Y-%m-%d",
                ).date()

                if expiry_date >= today:
                    valid.append(expiry_date)

            except Exception:
                continue

        if valid:
            return sorted(set(valid))

    except Exception as exc:

        errors.append(
            f"yfinance: {str(exc)[:180]}"
        )

    # المحاولة الثالثة: Tradier

    if TRADIER_API_TOKEN:

        try:

            expirations = tradier_get_expirations(ticker)

            valid = []

            for value in expirations:

                try:

                    expiry_date = datetime.strptime(
                        str(value)[:10],
                        "%Y-%m-%d",
                    ).date()

                    if expiry_date >= today:
                        valid.append(expiry_date)

                except Exception:
                    continue

            if valid:
                return sorted(set(valid))

        except Exception as exc:

            errors.append(
                f"Tradier: {str(exc)[:180]}"
            )

    raise ValueError(
        "لم يتم العثور على تاريخ انتهاء صالح. "
        + " | ".join(errors)
    )


# =====================================================
# 12. جلب سلسلة العقود
# =====================================================

def get_options_dataframe(ticker, expiration, signal):

    expiry_string = expiration.isoformat()

    errors = []

    # Yahoo المباشر

    try:

        expiration_timestamp = int(
            datetime(
                expiration.year,
                expiration.month,
                expiration.day,
                tzinfo=timezone.utc,
            ).timestamp()
        )

        chain = yahoo_get_option_chain(
            ticker,
            expiration_timestamp,
        )

        options = (
            chain.get("calls")
            if signal == "CALL"
            else chain.get("puts")
        )

        if options:

            df = pd.DataFrame(options)

            if not df.empty:
                return df, "Yahoo مباشر"

    except Exception as exc:

        errors.append(
            f"Yahoo مباشر: {str(exc)[:180]}"
        )

    # yfinance

    try:

        stock = yf.Ticker(ticker)

        chain = stock.option_chain(
            expiry_string
        )

        options = (
            chain.calls
            if signal == "CALL"
            else chain.puts
        )

        if options is not None and not options.empty:

            return options.copy(), "yfinance"

    except Exception as exc:

        errors.append(
            f"yfinance: {str(exc)[:180]}"
        )

    # Tradier

    if TRADIER_API_TOKEN:

        try:

            options = tradier_get_chain(
                ticker,
                expiry_string,
                signal,
            )

            if not options.empty:

                return options, "Tradier"

        except Exception as exc:

            errors.append(
                f"Tradier: {str(exc)[:180]}"
            )

    raise ValueError(
        "تعذر جلب سلسلة العقود: "
        + " | ".join(errors)
    )


# =====================================================
# 13. اختيار أفضل عقد
# =====================================================

def get_0dte_contract(ticker, signal):

    today = now_ny().date()

    expirations = get_valid_expirations(ticker)

    if not expirations:

        raise ValueError(
            "لا توجد تواريخ انتهاء متاحة"
        )

    selected_expiry = min(expirations)

    expiry_string = selected_expiry.isoformat()

    stock = yf.Ticker(ticker)

    hist = stock.history(
        period="1d",
        interval="5m",
    )

    if hist is None or hist.empty:

        raise ValueError(
            "تعذر الحصول على سعر السهم الأساسي"
        )

    underlying_prices = (
        hist["Close"].dropna()
    )

    if underlying_prices.empty:

        raise ValueError(
            "سعر السهم الأساسي غير متوفر"
        )

    underlying_price = safe_float(
        underlying_prices.iloc[-1]
    )

    if underlying_price is None:

        raise ValueError(
            "سعر السهم الأساسي غير صالح"
        )

    options, source = get_options_dataframe(
        ticker,
        selected_expiry,
        signal,
    )

    required_columns = [
        "contractSymbol",
        "strike",
        "bid",
        "ask",
        "volume",
        "openInterest",
    ]

    for column in required_columns:

        if column not in options.columns:

            raise ValueError(
                f"بيانات العقود ناقصة: {column}"
            )

    total_contracts = len(options)

    options = options.copy()

    for column in [
        "strike",
        "bid",
        "ask",
        "volume",
        "openInterest",
    ]:

        options[column] = pd.to_numeric(
            options[column],
            errors="coerce",
        )

    options = options.dropna(
        subset=required_columns
    ).copy()

    valid_data_count = len(options)

    if options.empty:

        raise ValueError(
            f"عدد العقود={total_contracts}، "
            "لكن لا توجد بيانات مكتملة"
        )

    options = options[
        (options["bid"] > 0)
        & (options["ask"] > 0)
        & (options["ask"] >= MIN_OPTION_PRICE)
        & (options["openInterest"] >= MIN_OPEN_INTEREST)
        & (options["volume"] >= MIN_OPTION_VOLUME)
    ].copy()

    liquidity_count = len(options)

    if options.empty:

        raise ValueError(
            f"وجد {total_contracts} عقدًا، "
            f"والمكتمل {valid_data_count}، "
            "لكن لا يوجد عقد يحقق شروط السيولة."
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

    spread_count = len(options)

    if options.empty:

        raise ValueError(
            f"وجد {liquidity_count} عقدًا يحقق السيولة، "
            f"لكن جميعها تجاوزت السبريد الأقصى "
            f"{MAX_SPREAD_PERCENT}%."
        )

    options["distance"] = (
        options["strike"] - underlying_price
    ).abs()

    options = options.sort_values(
        by=["distance", "spread_pct"],
        ascending=[True, True],
    )

    contract = options.iloc[0]

    bid = safe_float(contract["bid"])
    ask = safe_float(contract["ask"])

    if bid is None or ask is None or ask <= 0:

        raise ValueError(
            "سعر العقد غير صالح"
        )

    logger.info(
        "%s %s | expiry=%s | source=%s | "
        "contracts=%s | complete=%s | liquidity=%s | spread=%s",
        ticker,
        signal,
        expiry_string,
        source,
        total_contracts,
        valid_data_count,
        liquidity_count,
        spread_count,
    )

    return {

        "symbol": ticker,

        "type": signal,

        "contract": str(
            contract["contractSymbol"]
        ),

        "strike": float(
            contract["strike"]
        ),

        "expiration": expiry_string,

        "bid": bid,

        "ask": ask,

        "entry": ask,

        "spread": float(
            contract["spread_pct"]
        ),

        "volume": int(
            contract["volume"]
        ),

        "open_interest": int(
            contract["openInterest"]
        ),

        "underlying": underlying_price,

        "source": source,
    }


# =====================================================
# 14. متابعة أسعار العقود
# =====================================================

def get_contract_quote(contract_symbol):

    try:

        ticker = yf.Ticker(contract_symbol)

        hist = ticker.history(
            period="1d",
            interval="1m",
        )

        if hist is None or hist.empty:
            return None

        prices = hist["Close"].dropna()

        if prices.empty:
            return None

        price = safe_float(
            prices.iloc[-1]
        )

        return price if price and price > 0 else None

    except Exception:

        logger.exception(
            "خطأ في جلب سعر العقد %s",
            contract_symbol,
        )

        return None


# =====================================================
# 15. إرسال التنبيهات
# =====================================================

def trade_message(trade):

    return (

        "🚨 فرصة أوبشن جديدة\n\n"

        f"السهم: {trade['symbol']}\n"

        f"النوع: {trade['type']}\n"

        f"قوة الفرصة: {trade.get('grade', 'قوي')} 🟢\n"

        f"العقد: {trade['contract']}\n"

        f"Strike: {trade['strike']:.2f}\n"

        f"تاريخ الانتهاء: {trade['expiration']}\n\n"

        f"سعر الدخول التقريبي: ${trade['entry']:.2f}\n"

        f"Bid: ${trade['bid']:.2f}\n"

        f"Ask: ${trade['ask']:.2f}\n"

        f"فرق السعر: {trade['spread']:.1f}%\n"

        f"حجم العقد: {trade['volume']}\n"

        f"Open Interest: {trade['open_interest']}\n"

        f"مصدر العقد: {trade.get('source', 'غير محدد')}\n\n"

        "🎯 الهدف الأول: +30%\n"

        "🎯 الهدف الثاني: +60%\n"

        "🛑 وقف الخسارة: -35%\n\n"

        "⚠️ تنبيه تحليلي فقط، وليس تنفيذًا لصفقة."
    )


async def send_to_subscribers(application, message):

    for chat_id in list(subscribers):

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=message,
            )

        except Exception:

            logger.exception(
                "تعذر إرسال رسالة للمشترك %s",
                chat_id,
            )


async def open_trade(
    application,
    ticker,
    signal,
    contract,
):

    global daily_count

    reset_daily_data_if_needed()

    if not market_is_open_for_scanning():

        logger.info(
            "%s: تم منع فتح الصفقة لأن نافذة الدخول انتهت",
            ticker,
        )

        return False

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

        "tp2_sent": False,

    }

    active_trades[ticker] = trade

    sent_today.add(ticker)

    strong_today.add(ticker)

    daily_count = len(strong_today)

    await send_to_subscribers(
        application,
        trade_message(trade),
    )

    logger.info(
        "تم إرسال فرصة %s %s",
        ticker,
        signal,
    )

    return True


# =====================================================
# 16. مراقبة الأهداف ووقف الخسارة
# =====================================================

async def monitor_trades(application):

    if not active_trades:
        return

    now = now_ny()

    for ticker in list(active_trades.keys()):

        trade = active_trades.get(ticker)

        if not trade:
            continue

        opened_date = datetime.strptime(
            trade["opened_at"],
            "%Y-%m-%d %H:%M:%S",
        ).date()

        if (
            now.date() > opened_date
            or now.time() >= CLOSE_ALL_TIME
        ):

            await send_to_subscribers(
                application,

                f"⏰ انتهت متابعة {ticker}\n"
                "يرجى التحقق من العقد يدويًا.",
            )

            active_trades.pop(ticker, None)

            continue

        price = await asyncio.to_thread(
            get_contract_quote,
            trade["contract"],
        )

        if price is None:
            continue

        entry = trade["entry"]

        if entry <= 0:
            continue

        change = (price - entry) / entry

        if change <= STOP_LOSS:

            await send_to_subscribers(
                application,

                "🛑 تنبيه وقف الخسارة\n"

                f"{ticker} | {trade['type']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

                f"السعر الحالي: ${price:.2f}\n"

                f"التغير: {change * 100:.1f}%",
            )

            active_trades.pop(ticker, None)

            continue

        if (
            change >= TAKE_PROFIT_2
            and not trade["tp2_sent"]
        ):

            trade["tp2_sent"] = True

            await send_to_subscribers(
                application,

                "🎯 تحقق الهدف الثاني\n"

                f"{ticker} | {trade['type']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

                f"السعر الحالي: ${price:.2f}\n"

                f"الربح التقريبي: {change * 100:.1f}%",
            )

        elif (
            change >= TAKE_PROFIT_1
            and not trade["tp1_sent"]
        ):

            trade["tp1_sent"] = True

            await send_to_subscribers(
                application,

                "✅ تحقق الهدف الأول\n"

                f"{ticker} | {trade['type']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

                f"السعر الحالي: ${price:.2f}\n"

                f"الربح التقريبي: {change * 100:.1f}%",
            )


# =====================================================
# 17. محرك الفحص
# =====================================================

async def run_scan(application, manual=False):

    global last_scan_time
    global last_scan_summary
    global last_scan_errors
    global medium_count

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

        entry_window_open = market_is_open_for_scanning()

        if not entry_window_open and not manual:

            message = (

                "السوق خارج وقت البحث المحدد.\n"

                f"وقت نيويورك الآن: "
                f"{now_ny().strftime('%H:%M:%S')}\n"

                "وقت فتح فرص جديدة: "
                "09:40 إلى 15:30 بتوقيت نيويورك."
            )

            last_scan_summary = {}

            last_scan_errors = [message]

            return message

        if daily_count >= MAX_PER_DAY and entry_window_open:

            return (
                f"وصل البوت إلى الحد الأقصى: "
                f"{MAX_PER_DAY} فرص اليوم."
            )

        if not entry_window_open:

            logger.info(
                "فحص يدوي تشخيصي خارج نافذة الدخول؛ "
                "لن يتم فتح أو إرسال صفقات."
            )

        for ticker in TICKERS:

            reset_daily_data_if_needed()

            if (
                entry_window_open
                and daily_count >= MAX_PER_DAY
            ):
                break

            if ticker in sent_today:

                summary[ticker] = (
                    "تم إرسال فرصة لهذا السهم اليوم"
                )

                continue

            if ticker in active_trades:

                summary[ticker] = (
                    "لديه صفقة متابعة نشطة"
                )

                continue

            analysis = await asyncio.to_thread(
                analyze_ticker,
                ticker,
            )

            if not analysis["signal"]:

                summary[ticker] = (
                    f"{analysis['grade']} | "
                    f"{analysis['reason']}"
                )

                continue

            # تسجيل الفرص المتوسطة مرة واحدة يوميًا

            if analysis["grade"] == "متوسط":

                medium_today.add(ticker)

                medium_count = len(medium_today)

                summary[ticker] = (
                    f"🟡 فرصة متوسطة - لا يتم الإرسال | "
                    f"{analysis['reason']}"
                )

                continue

            if analysis["grade"] != "قوي":

                summary[ticker] = (
                    f"لم يتم الإرسال: {analysis['grade']} | "
                    f"{analysis['reason']}"
                )

                continue

            if not market_is_open_for_scanning():

                summary[ticker] = (
                    f"إشارة {analysis['signal']} قوية، "
                    "لكن لم يُفتح عقد لأن نافذة الدخول انتهت. | "
                    + analysis["details"]
                )

                continue

            try:

                contract = await asyncio.to_thread(
                    get_0dte_contract,
                    ticker,
                    analysis["signal"],
                )

                contract["grade"] = analysis["grade"]

                sent = await open_trade(
                    application,
                    ticker,
                    analysis["signal"],
                    contract,
                )

                if sent:

                    summary[ticker] = (

                        f"🟢 تم إرسال {analysis['signal']} قوي | "

                        f"{analysis['details']} | "

                        f"تاريخ الانتهاء: {contract['expiration']}"
                    )

                else:

                    summary[ticker] = (
                        "لم يتم إرسال الفرصة؛ تحقق شرط منع الدخول "
                        "أو الحد اليومي."
                    )

            except Exception as exc:

                reason = (
                    f"فشل جلب العقد\n"
                    f"الإشارة: {analysis['signal']}\n"
                    f"المؤشرات: {analysis['details']}\n"
                    f"سبب الفشل: {str(exc)[:450]}"
                )

                summary[ticker] = reason

                errors.append(
                    f"{ticker} | {analysis['signal']} | {reason}"
                )

                logger.warning(
                    "%s: فشل جلب العقد | الإشارة=%s | السبب=%s",
                    ticker,
                    analysis["signal"],
                    str(exc),
                )

        last_scan_summary = summary

        last_scan_errors = errors

        report = (

            f"انتهى الفحص: "
            f"{now_ny().strftime('%H:%M:%S')} نيويورك\n"

            f"الفرص القوية المرسلة اليوم: "
            f"{daily_count}/{MAX_PER_DAY}\n"

            f"الفرص المتوسطة المرصودة اليوم: "
            f"{medium_count}\n"

            f"عدد الأسهم التي تم فحصها: "
            f"{len(summary)}\n"

            f"الصفقات النشطة: "
            f"{len(active_trades)}"
        )

        if not entry_window_open:

            report += (
                "\nملاحظة: هذا فحص تشخيصي فقط، "
                "ولم تُفتح صفقات خارج نافذة الدخول."
            )

        logger.info(report)

        return report


# =====================================================
# 18. الفحص التلقائي
# =====================================================

async def scanner_loop(application):

    global scanner_started

    scanner_started = True

    logger.info(
        "بدأ محرك الفحص التلقائي"
    )

    while True:

        try:

            reset_daily_data_if_needed()

            if market_is_open_for_scanning():

                await run_scan(
                    application,
                    manual=False,
                )

            # مراقبة الصفقات حتى بعد انتهاء الدخول
            # وحتى وقت الإغلاق المحدد.

            if market_is_open():

                await monitor_trades(application)

            elif active_trades:

                await monitor_trades(application)

        except asyncio.CancelledError:

            logger.info(
                "تم إيقاف محرك الفحص"
            )

            raise

        except Exception:

            logger.exception(
                "خطأ في محرك الفحص"
            )

        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =====================================================
# 19. أوامر تيليجرام
# =====================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    subscribers.add(chat_id)

    await update.message.reply_text(

        "✅ تم تفعيل اشتراكك في Aboghala Bot.\n\n"

        "البوت يبحث عن أقرب تاريخ انتهاء متاح للعقود.\n"

        "يرسل الفرص القوية فقط.\n"

        "الفرص المتوسطة تظهر في التشخيص فقط.\n\n"

        "التنبيهات فرص تحليلية وليست أوامر تداول.\n\n"

        "الأوامر:\n"

        "/scan - فحص السوق الآن\n"

        "/status - حالة البوت\n"

        "/report - تقرير آخر فحص\n"

        "/debug - أسباب قبول أو رفض الأسهم\n"

        "/active - الصفقات النشطة\n"

        "/help - المساعدة"
    )


async def scan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🔎 بدأ الفحص اليدوي. قد يستغرق بعض الوقت..."
    )

    result = await run_scan(
        context.application,
        manual=True,
    )

    await update.message.reply_text(result)


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    reset_daily_data_if_needed()

    last_scan = (

        last_scan_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if last_scan_time

        else "لم يتم الفحص بعد"
    )

    await update.message.reply_text(

        "📊 حالة Aboghala Bot\n\n"

        "الحالة: يعمل\n"

        f"وقت نيويورك: "
        f"{now_ny().strftime('%Y-%m-%d %H:%M:%S')}\n"

        f"آخر محاولة فحص: {last_scan}\n"

        f"محرك الفحص: "
        f"{'بدأ' if scanner_started else 'لم يبدأ'}\n"

        f"اختبار Yahoo: {yahoo_test_status}\n"

        f"ملخص Yahoo: {yahoo_test_summary}\n"

        f"الفرص القوية المرسلة اليوم: "
        f"{daily_count}/{MAX_PER_DAY}\n"

        f"الفرص المتوسطة المرصودة اليوم: "
        f"{medium_count}\n"

        f"الصفقات النشطة: {len(active_trades)}\n"

        f"المشتركون: {len(subscribers)}\n"

        f"عدد الأسهم: {len(TICKERS)}\n"

        f"Tradier: "
        f"{'مفعّل بمفتاح API' if TRADIER_API_TOKEN else 'غير مفعّل'}\n"

        f"السوق ضمن وقت البحث: "
        f"{'نعم' if market_is_open_for_scanning() else 'لا'}\n"

        "نافذة فتح الفرص: 09:40–15:30 نيويورك"
    )


async def report_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not last_scan_time:

        await update.message.reply_text(
            "لم يتم تنفيذ أي فحص حتى الآن. استخدم /scan."
        )

        return

    lines = [

        "📋 تقرير آخر فحص",

        f"الوقت: {last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}",

        f"الفرص القوية اليوم: {daily_count}/{MAX_PER_DAY}",

        f"الفرص المتوسطة اليوم: {medium_count}",

        ""
    ]

    for ticker, result in last_scan_summary.items():

        lines.append(
            f"{ticker}: {result}"
        )

    if last_scan_errors:

        lines.append(
            "\n⚠️ تفاصيل أخطاء جلب العقود:"
        )

        lines.extend(last_scan_errors)

    await reply_long(
        update,
        "\n".join(lines),
    )


async def debug_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not last_scan_summary:

        await update.message.reply_text(
            "لا توجد تفاصيل فحص حتى الآن. استخدم /scan أولًا."
        )

        return

    lines = [

        "🔬 تشخيص الأسهم\n",

        "🟢 قوي: يرسل تنبيهًا عند توفر عقد مناسب وضمن وقت الدخول.",

        "🟡 متوسط: يظهر هنا فقط ولا يرسل تنبيهًا.",

        "🔴 لا دخول: الشروط الأساسية غير مكتملة."
    ]

    for ticker, reason in last_scan_summary.items():

        lines.append(
            f"\n{ticker}: {reason}"
        )

    if last_scan_errors:

        lines.append(
            "\n\n⚠️ أخطاء جلب العقود:"
        )

        lines.extend(last_scan_errors)

    await reply_long(
        update,
        "\n".join(lines),
    )


async def active_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
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

            f"تاريخ الانتهاء: {trade['expiration']}\n"

            f"الدخول: ${trade['entry']:.2f}"
        )

    await reply_long(
        update,
        "\n".join(lines),
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        "/start - تفعيل التنبيهات\n"

        "/scan - فحص تشخيصي/فوري\n"

        "/status - حالة البوت\n"

        "/report - تقرير الفحص\n"

        "/debug - تشخيص أسباب رفض الأسهم وتصنيفها\n"

        "/active - الصفقات النشطة\n"

        "/help - المساعدة"
    )


# =====================================================
# 20. بدء المهام
# =====================================================

async def post_init(application):

    logger.info(
        "تم تهيئة Telegram؛ سيتم تشغيل محرك الفحص."
    )

    application.create_task(
        scanner_loop(application),
        name="aboghala-scanner-loop",
    )

    # اختبار Yahoo لسهم واحد فقط حتى لا نرسل
    # عشرات الطلبات عند تشغيل Render.

    threading.Thread(
        target=test_yahoo_options,
        name="yahoo-options-diagnostic",
        daemon=True,
    ).start()


# =====================================================
# 21. خادم Render
# =====================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path in ("/", "/health"):

            body = b"Aboghala Bot is running"

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
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

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    logger.info(
        "Health server listening on port %s",
        port
    )

    server.serve_forever()


# =====================================================
# 22. تشغيل البوت
# =====================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "لم يتم العثور على BOT_TOKEN "
            "في Environment Variables"
        )

    threading.Thread(
        target=start_health_server,
        name="render-health-server",
        daemon=True,
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


# =====================================================
# 23. نقطة البداية
# =====================================================

if __name__ == "__main__":

    main()
