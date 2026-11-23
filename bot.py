import os
import asyncio
import logging
import threading
import math
import time as time_module

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd
import yfinance as yf

from telegram import Update
from telegram.ext import Application, CommandHandler


# =====================================================
# 1. الإعدادات العامة
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

TZ_NY = ZoneInfo("America/New_York")
TZ_SA = ZoneInfo("Asia/Riyadh")


# =====================================================
# قائمة الأسهم الموسعة
# =====================================================

TICKERS = list(dict.fromkeys([

    # التقنية والبرمجيات
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "ORCL", "CRM", "ADBE", "CSCO", "INTC",
    "AMD", "NVDA", "PLTR", "SNOW", "NET",
    "SHOP", "UBER", "LYFT", "PINS", "SNAP",
    "RBLX", "U", "PATH", "AI", "SOUN",
    "BBAI", "GTLB", "DBX", "ZI", "DOCN",
    "FSLY", "WIX", "OKTA", "TWLO", "ZM",
    "BOX", "BILL", "APPN", "FIVN", "DOCU",
    "ESTC", "CFLT", "MDB", "DDOG", "DT",
    "GDDY", "GEN", "HPE", "HPQ", "DELL",

    # أشباه الموصلات والذكاء الاصطناعي
    "ARM", "MRVL", "QCOM", "ON", "SWKS",
    "MCHP", "MPWR", "UMC", "WDC", "STX",
    "CRUS", "COHR", "LITE", "AAOI", "FN",
    "SMCI", "MU", "AVGO", "ASML", "TSM",
    "AMAT", "LRCX", "KLAC", "TER", "TXN",
    "ADI", "NXPI", "ENTG", "QRTEA",

    # البنوك والخدمات المالية
    "JPM", "BAC", "WFC", "C", "USB",
    "KEY", "RF", "FITB", "HBAN", "ALLY",
    "SOFI", "HOOD", "COIN", "PYPL", "AFRM",
    "UPST", "NU", "BBD", "LC", "OPEN",
    "CLOV", "FUTU", "TIGR", "MELI", "V",
    "MA", "AXP", "DFS", "SYF", "CFG",
    "MTB", "ZION", "CMA", "EWBC", "ONB",

    # العملات الرقمية والتعدين
    "MARA", "RIOT", "HUT", "CLSK", "IREN",
    "WULF", "APLD", "CORZ", "CIFR", "BTDR",

    # الطاقة والنفط والغاز
    "XOM", "OXY", "SLB", "HAL", "DVN",
    "APA", "FANG", "MRO", "RIG", "RUN",
    "ENPH", "FSLR", "SEDG", "PLUG", "BE",
    "FCEL", "QS", "STEM", "NOVA", "CHPT",
    "EQT", "ET", "MPLX", "KMI", "VLO",

    # السيارات والنقل والطيران
    "F", "GM", "RIVN", "LCID", "NIO",
    "XPEV", "LI", "DASH", "ABNB", "JOBY",
    "ACHR", "AAL", "UAL", "DAL", "JBLU",
    "CCL", "NCLH", "RCL", "UBER", "LYFT",
    "PCAR", "SAVE", "ALK", "LUV", "CHRW",

    # الصناعة والمواد
    "CAT", "DE", "CLF", "X", "NUE",
    "AA", "VALE", "FCX", "CDE", "AG",
    "MP", "CCJ", "UEC", "UUUU", "LUNR",
    "ASTS", "RKLB", "RDW", "ACHR", "PL",
    "GOLD", "HL", "WPM", "GFI", "BTG",

    # التجزئة والسلع الاستهلاكية
    "WMT", "TGT", "COST", "KR", "WBA",
    "CVS", "BBY", "DG", "DLTR", "FIVE",
    "BURL", "GPS", "ANF", "DKS", "NKE",
    "CROX", "ETSY", "CHWY", "CVNA", "KSS",
    "M", "GME", "AMZN", "HD", "LOW",
    "ULTA", "ELF", "SBUX", "MCD", "YUM",

    # الصحة والأدوية والتقنية الحيوية
    "PFE", "BMY", "MRNA", "BNTX", "GILD",
    "BIIB", "CRSP", "EDIT", "NTLA", "BEAM",
    "RXRX", "DNA", "VKTX", "HIMS", "TDOC",
    "IOVA", "GERN", "SMMT", "ACHV", "LLY",
    "ABBV", "MRK", "JNJ", "UNH", "CVS",
    "NVO", "AMGN", "REGN", "ALNY", "IONS",

    # الاتصالات والإعلام والترفيه
    "T", "VZ", "PARA", "FOXA", "LYV",
    "SPOT", "ROKU", "AMC", "CZR", "PENN",
    "DKNG", "MGM", "DIS", "NFLX", "CMCSA",
    "CHTR", "WBD", "FOX", "SIRI", "TMUS",

    # شركات عالمية متداولة في أمريكا
    "BABA", "PDD", "JD", "BIDU", "SE",
    "GRAB", "CPNG", "TME", "BILI", "YMM",
    "ZTO", "BEKE", "VIPS", "TAL", "EDU",

    # صناديق وقطاعات
    "QQQ", "IWM", "DIA", "XLF",
    "XLE", "XLK", "SMH", "SOXX", "ARKK",
    "GLD", "SLV", "TLT", "HYG", "XLI",
    "XLY", "XLP", "XLV", "XBI", "KRE"

]))

REMOVED_TICKERS = {
    "WBD",
    "QRVO"
}

TICKERS = [
    ticker for ticker in TICKERS
    if ticker not in REMOVED_TICKERS
]


# =====================================================
# 2. إعدادات التداول
# =====================================================

MIN_STOCK_PRICE = 10.0
MAX_STOCK_PRICE = 100.0

MAX_PER_DAY = 10
MAX_ACTIVE_TRADES = 15

SCAN_BATCH_SIZE = 12
SCAN_INTERVAL = 180
MONITOR_INTERVAL = 60

# منع تكرار السهم في دفعات متقاربة
SCAN_REPEAT_COOLDOWN = 15 * 60

# عدد شموع 5 دقائق المستخدمة في حساب الأولويات
PRIORITY_LOOKBACK_BARS = 20

# نافذة السعودية: 17:00 إلى 00:00
SAUDI_SCAN_START = time(17, 0)
SAUDI_SCAN_END = time(0, 0)

# توقيت نيويورك
START_SCAN_NY = time(9, 40)
STOP_NEW_TRADES_NY = time(15, 45)
CLOSE_ALL_TIME_NY = time(15, 55)

MIN_SCORE = 8
MIN_RISK_REWARD = 2.0
TARGET2_MIN_RISK_REWARD = 3.0

ATR_STOP_MULTIPLIER = 1.2
MAX_STOP_PERCENT = 4.0
MAX_ENTRY_DEVIATION = 0.003

# إعدادات اكتشاف المقاومات
RESISTANCE_LOOKBACK = 120
PIVOT_LEFT = 2
PIVOT_RIGHT = 2

YAHOO_RETRIES = 2
YAHOO_RETRY_DELAY = 2

YAHOO_LOCK = threading.RLock()

PRICE_CACHE = {}
PRICE_CACHE_TTL = 30

NO_DATA_CACHE = {}
NO_DATA_CACHE_TTL = 20 * 60

# بيانات أولوية الأسهم
PRIORITY_METRICS = {}
LAST_TICKER_SCAN = {}
priority_updated_at = None


# =====================================================
# 3. السجلات وحالة البوت
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("AboghalaBot")

logging.getLogger("yfinance").setLevel(logging.ERROR)

logger.info("YFINANCE VERSION: %s", yf.__version__)
logger.info("عدد الأسهم في القائمة: %s", len(TICKERS))

subscribers = set()
active_trades = {}
sent_today = set()

daily_count = 0
current_day = None

last_scan_time = None
last_scan_summary = {}
last_scan_errors = []

scan_lock = asyncio.Lock()


# =====================================================
# 4. الوقت
# =====================================================

def now_ny():
    return datetime.now(TZ_NY)


def now_saudi():
    return datetime.now(TZ_SA)


def reset_daily_data_if_needed():

    global current_day
    global daily_count
    global sent_today

    today = now_ny().date()

    if current_day != today:

        current_day = today
        daily_count = 0
        sent_today = set()

        logger.info("تم تصفير إحصائيات اليوم")


def saudi_time_is_allowed():

    current_time = now_saudi().time()

    return (
        current_time >= SAUDI_SCAN_START
        or current_time < SAUDI_SCAN_END
    )


def market_is_open():

    now = now_ny()

    if now.weekday() >= 5:
        return False

    # لا يتضمن تقويم العطلات أو الإغلاق المبكر.
    return time(9, 30) <= now.time() < time(16, 0)


def market_is_open_for_scanning():

    if not saudi_time_is_allowed():
        return False

    now = now_ny()

    if now.weekday() >= 5:
        return False

    return START_SCAN_NY <= now.time() <= STOP_NEW_TRADES_NY


# =====================================================
# 4.1 تحديث بيانات أولوية جميع الأسهم
# =====================================================

def refresh_priority_metrics():

    """
    يجلب بيانات 5 دقائق لجميع الأسهم دفعة واحدة،
    ويحسب لكل سهم:

    - متوسط قيمة التداول بالدولار.
    - ارتفاع حجم التداول مقارنة بمتوسطه.
    - ATR كنسبة من السعر.

    هذه البيانات تستخدم لترتيب أولوية الفحص فقط.
    """

    global PRIORITY_METRICS
    global priority_updated_at

    try:

        with YAHOO_LOCK:

            raw = yf.download(
                tickers=TICKERS,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                prepost=False,
                threads=True,
                progress=False,
                timeout=30
            )

        if raw is None or raw.empty:

            logger.warning(
                "لم تصل بيانات تحديث أولويات الأسهم من Yahoo"
            )

            return False

        updated_metrics = {}
        current_time = now_ny()

        for ticker in TICKERS:

            try:

                # استخراج بيانات السهم من نتيجة Yahoo
                if isinstance(raw.columns, pd.MultiIndex):

                    level0 = raw.columns.get_level_values(0)
                    level1 = raw.columns.get_level_values(1)

                    if ticker in level0:

                        stock_df = raw[ticker].copy()

                    elif ticker in level1:

                        stock_df = raw.xs(
                            ticker,
                            axis=1,
                            level=1
                        ).copy()

                    else:

                        continue

                else:

                    if len(TICKERS) != 1:
                        continue

                    stock_df = raw.copy()

                required = [
                    "High",
                    "Low",
                    "Close",
                    "Volume"
                ]

                if not all(
                    col in stock_df.columns
                    for col in required
                ):
                    continue

                stock_df = stock_df[required].copy()
                stock_df = stock_df.dropna()

                if stock_df.empty:
                    continue

                if not isinstance(
                    stock_df.index,
                    pd.DatetimeIndex
                ):
                    continue

                if stock_df.index.tz is None:

                    stock_df.index = (
                        stock_df.index.tz_localize(TZ_NY)
                    )

                else:

                    stock_df.index = (
                        stock_df.index.tz_convert(TZ_NY)
                    )

                stock_df = stock_df.sort_index()

                # استبعاد شمعة 5 دقائق غير المكتملة
                candle_close = (
                    stock_df.index + timedelta(minutes=5)
                )

                stock_df = stock_df.loc[
                    candle_close <= (
                        current_time - timedelta(seconds=30)
                    )
                ]

                if len(stock_df) < 5:
                    continue

                stock_df = stock_df[
                    (stock_df["Close"] > 0)
                    & (stock_df["Volume"] >= 0)
                ]

                if len(stock_df) < 5:
                    continue

                close = stock_df["Close"].astype(float)
                high = stock_df["High"].astype(float)
                low = stock_df["Low"].astype(float)
                volume = stock_df["Volume"].astype(float)

                latest_close = safe_float(close.iloc[-1])
                latest_volume = safe_float(volume.iloc[-1])

                if (
                    latest_close is None
                    or latest_volume is None
                    or latest_close <= 0
                ):
                    continue

                # متوسط قيمة التداول بالدولار
                # السعر × الحجم
                dollar_volume = close * volume

                avg_dollar_volume = safe_float(
                    dollar_volume.tail(
                        PRIORITY_LOOKBACK_BARS
                    ).mean()
                )

                # حجم آخر شمعة مقارنة بمتوسط الشموع السابقة
                previous_volumes = volume.iloc[
                    -PRIORITY_LOOKBACK_BARS - 1:-1
                ]

                if previous_volumes.empty:

                    volume_ratio = 0.0

                else:

                    average_volume = safe_float(
                        previous_volumes.mean()
                    )

                    if (
                        average_volume is None
                        or average_volume <= 0
                    ):

                        volume_ratio = 0.0

                    else:

                        volume_ratio = (
                            latest_volume / average_volume
                        )

                # حساب ATR من بيانات 5 دقائق
                previous_close = close.shift(1)

                tr1 = high - low

                tr2 = (
                    high - previous_close
                ).abs()

                tr3 = (
                    low - previous_close
                ).abs()

                true_range = pd.concat(
                    [tr1, tr2, tr3],
                    axis=1
                ).max(axis=1)

                atr = safe_float(
                    true_range.tail(14).mean()
                )

                if atr is None or atr <= 0:
                    atr = 0.0

                atr_percent = (
                    atr / latest_close * 100
                )

                if avg_dollar_volume is None:
                    continue

                updated_metrics[ticker] = {
                    "avg_dollar_volume": avg_dollar_volume,
                    "volume_ratio": volume_ratio,
                    "atr": atr,
                    "atr_percent": atr_percent,
                    "price": latest_close,
                    "timestamp": time_module.time()
                }

            except Exception as e:

                logger.debug(
                    "تعذر حساب أولوية %s: %s",
                    ticker,
                    str(e)[:120]
                )

        # الاحتفاظ بالبيانات السابقة للأسهم التي لم تصل بياناتها
        # وعدم استبدالها بنتيجة فارغة.
        PRIORITY_METRICS.update(updated_metrics)

        priority_updated_at = time_module.time()

        logger.info(
            "تم تحديث أولويات %s من أصل %s سهمًا",
            len(updated_metrics),
            len(TICKERS)
        )

        return bool(updated_metrics)

    except Exception:

        logger.exception(
            "فشل تحديث بيانات أولوية الأسهم"
        )

        return False


# =====================================================
# 4.2 اختيار أفضل 12 سهمًا حسب السيولة وحجم التداول
# =====================================================

def get_priority_batch():

    """
    يختار أفضل 12 سهمًا بترتيب موحّد:

    - 50% لترتيب السيولة (متوسط قيمة التداول بالدولار).
    - 50% لترتيب نشاط حجم التداول (Volume Ratio).

    لا توجد مجموعات منفصلة أو حصص ثابتة.

    يمنع تكرار السهم لمدة 15 دقيقة.
    وإذا لم يتوفر 12 سهمًا بعد التبريد،
    يستكمل العدد بالأسهم الأقدم فحصًا.
    """

    now_timestamp = time_module.time()

    if not TICKERS:
        return []

    # -------------------------------------------------
    # 1. استبعاد الأسهم التي لم تنتهِ مدة التبريد لها
    # -------------------------------------------------

    eligible = []

    for ticker in TICKERS:

        last_scan = LAST_TICKER_SCAN.get(ticker)

        if last_scan is None:

            eligible.append(ticker)
            continue

        elapsed = now_timestamp - last_scan

        if elapsed >= SCAN_REPEAT_COOLDOWN:
            eligible.append(ticker)

    # -------------------------------------------------
    # 2. استكمال العدد بالأقدم فحصًا عند الحاجة
    # -------------------------------------------------

    if len(eligible) < SCAN_BATCH_SIZE:

        remaining = [
            ticker
            for ticker in TICKERS
            if ticker not in eligible
        ]

        remaining.sort(
            key=lambda ticker: (
                LAST_TICKER_SCAN.get(ticker, 0),
                TICKERS.index(ticker)
            )
        )

        needed = SCAN_BATCH_SIZE - len(eligible)

        eligible.extend(
            remaining[:needed]
        )

    if not eligible:
        return []

    # -------------------------------------------------
    # 3. تجهيز بيانات السيولة وحجم التداول
    # -------------------------------------------------

    metrics_rows = []

    for ticker in eligible:

        metrics = PRIORITY_METRICS.get(ticker, {})

        avg_dollar_volume = safe_float(
            metrics.get("avg_dollar_volume", 0)
        )

        volume_ratio = safe_float(
            metrics.get("volume_ratio", 0)
        )

        if avg_dollar_volume is None:
            avg_dollar_volume = 0.0

        if volume_ratio is None:
            volume_ratio = 0.0

        metrics_rows.append({
            "ticker": ticker,
            "avg_dollar_volume": max(
                avg_dollar_volume,
                0.0
            ),
            "volume_ratio": max(
                volume_ratio,
                0.0
            )
        })

    metrics_df = pd.DataFrame(metrics_rows)

    # -------------------------------------------------
    # 4. ترتيب السيولة وحجم التداول بشكل نسبي
    # -------------------------------------------------

    metrics_df["liquidity_rank"] = (
        metrics_df["avg_dollar_volume"].rank(
            method="average",
            pct=True
        )
    )

    metrics_df["volume_rank"] = (
        metrics_df["volume_ratio"].rank(
            method="average",
            pct=True
        )
    )

    # -------------------------------------------------
    # 5. الدرجة الموحدة: 50% سيولة + 50% حجم تداول
    # -------------------------------------------------

    metrics_df["combined_score"] = (
        metrics_df["liquidity_rank"] * 0.50
        +
        metrics_df["volume_rank"] * 0.50
    )

    # -------------------------------------------------
    # 6. كسر التعادل بالسيولة ثم الحجم ثم الأقدم فحصًا
    # -------------------------------------------------

    metrics_df["last_scan"] = (
        metrics_df["ticker"].map(
            lambda ticker: LAST_TICKER_SCAN.get(
                ticker,
                0
            )
        )
    )

    metrics_df["original_order"] = (
        metrics_df["ticker"].map(
            lambda ticker: TICKERS.index(ticker)
        )
    )

    metrics_df = metrics_df.sort_values(
        by=[
            "combined_score",
            "liquidity_rank",
            "volume_rank",
            "avg_dollar_volume",
            "volume_ratio",
            "last_scan",
            "original_order"
        ],
        ascending=[
            False,
            False,
            False,
            False,
            False,
            True,
            True
        ],
        kind="stable"
    )

    selected = (
        metrics_df["ticker"]
        .head(SCAN_BATCH_SIZE)
        .tolist()
    )

    logger.info(
        "تم اختيار أفضل %s سهمًا حسب الترتيب الموحد للسيولة وحجم التداول: %s",
        len(selected),
        ", ".join(selected)
    )

    return selected


# =====================================================
# 5. أدوات مساعدة
# =====================================================

def safe_float(value):

    try:

        result = float(value)

        if not math.isfinite(result):
            return None

        return result

    except (TypeError, ValueError):
        return None


def ceil_cent(value):

    return math.ceil(
        (value - 1e-10) * 100
    ) / 100


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


# =====================================================
# 6. جلب بيانات Yahoo Finance
# =====================================================

class NoDataError(Exception):
    pass


def yahoo_history(ticker, period, interval):

    cache_key = (ticker, interval)

    cached_until = NO_DATA_CACHE.get(cache_key)

    if cached_until and time_module.time() < cached_until:

        raise NoDataError(
            f"لا توجد بيانات حديثة لهذا السهم على {interval}"
        )

    last_error = None

    for attempt in range(1, YAHOO_RETRIES + 1):

        try:

            with YAHOO_LOCK:

                stock = yf.Ticker(ticker)

                df = stock.history(
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    prepost=False
                )

            if df is None or df.empty:

                NO_DATA_CACHE[cache_key] = (
                    time_module.time() + NO_DATA_CACHE_TTL
                )

                raise NoDataError(
                    f"Yahoo لم يرجع بيانات {interval}"
                )

            NO_DATA_CACHE.pop(cache_key, None)

            return df

        except NoDataError:
            raise

        except Exception as e:

            last_error = e

            logger.warning(
                "%s %s: المحاولة %s/%s: %s",
                ticker,
                interval,
                attempt,
                YAHOO_RETRIES,
                str(e)[:150]
            )

            if attempt < YAHOO_RETRIES:

                time_module.sleep(
                    YAHOO_RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"تعذر جلب بيانات {ticker}: {last_error}"
    )


# =====================================================
# 7. المؤشرات الفنية
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


def prepare_dataframe(df, interval):

    df = df.copy()

    required = [
        "Open", "High", "Low", "Close", "Volume"
    ]

    if not all(col in df.columns for col in required):
        raise ValueError("أعمدة البيانات ناقصة")

    df = df[required].copy()
    df = df.dropna(subset=required)

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("وقت الشموع غير صالح")

    if df.index.tz is None:
        df.index = df.index.tz_localize(TZ_NY)
    else:
        df.index = df.index.tz_convert(TZ_NY)

    df = df.sort_index()

    df = df[
        (df["Close"] > 0)
        & (df["Volume"] >= 0)
    ]

    if df.empty:
        raise ValueError("لا توجد بيانات صالحة")

    # استبعاد الشمعة غير المكتملة للفريمات اللحظية
    minutes = {
        "5m": 5,
        "15m": 15
    }.get(interval)

    if minutes:

        candle_close = (
            df.index + timedelta(minutes=minutes)
        )

        df = df.loc[
            candle_close <= now_ny() - timedelta(seconds=30)
        ]

    # استبعاد شمعة اليوم غير المكتملة من الفريم اليومي
    if interval == "1d":

        today = now_ny().date()
        df = df[df.index.date < today]

    if len(df) < 60:
        raise ValueError("عدد الشموع غير كافٍ لحساب EMA50")

    df["EMA20"] = df["Close"].ewm(
        span=20,
        adjust=False
    ).mean()

    df["EMA50"] = df["Close"].ewm(
        span=50,
        adjust=False
    ).mean()

    df["RSI"] = calculate_rsi(df["Close"])

    df["Volume_Avg"] = df["Volume"].rolling(
        20,
        min_periods=10
    ).mean()

    df["Volume_Ratio"] = (
        df["Volume"]
        / df["Volume_Avg"].replace(0, float("nan"))
    )

    previous_close = df["Close"].shift(1)

    tr1 = df["High"] - df["Low"]

    tr2 = (
        df["High"] - previous_close
    ).abs()

    tr3 = (
        df["Low"] - previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["ATR"] = true_range.rolling(
        14,
        min_periods=10
    ).mean()

    # ضغط شراء وبيع تقديري من موقع الإغلاق داخل الشمعة والحجم.
    # لا يمثل بيانات دفتر الأوامر أو تدفق الأوامر الحقيقي.
    candle_range = (
        df["High"] - df["Low"]
    ).replace(0, float("nan"))

    df["Buy_Pressure"] = (
        (
            df["Close"] - df["Low"]
        ) / candle_range
    ).clip(0, 1) * df["Volume"]

    df["Sell_Pressure"] = (
        (
            df["High"] - df["Close"]
        ) / candle_range
    ).clip(0, 1) * df["Volume"]

    return df.dropna(
        subset=[
            "EMA20",
            "EMA50",
            "RSI",
            "Volume_Ratio",
            "ATR"
        ]
    )


# =====================================================
# 8. حساب VWAP
# =====================================================

def calculate_vwap(df):

    today = now_ny().date()

    today_df = df[
        df.index.date == today
    ].copy()

    if today_df.empty:
        return None

    typical = (
        today_df["High"]
        + today_df["Low"]
        + today_df["Close"]
    ) / 3

    volume = today_df["Volume"]

    total_volume = volume.sum()

    if total_volume <= 0:
        return None

    return safe_float(
        (typical * volume).sum() / total_volume
    )


# =====================================================
# 9. ضغط الشراء والبيع
# =====================================================

def calculate_pressure(df):

    recent = df.tail(10)

    buy = safe_float(
        recent["Buy_Pressure"].sum()
    )

    sell = safe_float(
        recent["Sell_Pressure"].sum()
    )

    if buy is None or sell is None:
        return None, None

    total = buy + sell

    if total <= 0:
        return None, None

    return (
        buy / total * 100,
        sell / total * 100
    )


# =====================================================
# 10. اكتشاف المقاومات الفنية
# =====================================================

def find_resistance_levels(df, entry, atr):

    """
    يحدد مستويات المقاومة من القمم المحلية السابقة.
    لا يستخدم سعرًا ثابتًا بالدولار لتحديد الأهداف.
    """

    if df is None or len(df) < 10:
        return []

    recent = df.tail(RESISTANCE_LOOKBACK).copy()

    highs = recent["High"].astype(float)

    candidates = []

    # اكتشاف القمم المحلية
    for i in range(
        PIVOT_LEFT,
        len(highs) - PIVOT_RIGHT
    ):

        current_high = float(highs.iloc[i])

        left = highs.iloc[
            i - PIVOT_LEFT:i
        ]

        right = highs.iloc[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        if (
            current_high >= left.max()
            and current_high >= right.max()
        ):

            if current_high > entry:
                candidates.append(current_high)

    # إضافة أعلى قمة سابقة قريبة
    previous_bars = recent.iloc[:-1]

    if not previous_bars.empty:

        recent_high = safe_float(
            previous_bars["High"].tail(20).max()
        )

        if recent_high is not None and recent_high > entry:
            candidates.append(recent_high)

    if not candidates:
        return []

    candidates = sorted(candidates)

    # دمج المقاومات المتقاربة
    tolerance = max(
        atr * 0.35,
        entry * 0.002
    )

    merged = []

    for level in candidates:

        if level <= entry:
            continue

        if not merged:

            merged.append(level)
            continue

        if abs(level - merged[-1]) <= tolerance:

            merged[-1] = (
                merged[-1] + level
            ) / 2

        else:

            merged.append(level)

    return [
        round(level, 2)
        for level in merged
        if level > entry
    ]


# =====================================================
# 11. حساب وقف الخسارة والأهداف الفنية
# =====================================================

def calculate_trade_levels(entry, atr, df):

    if entry <= 0 or atr <= 0:
        return None

    entry = round(entry, 2)

    # وقف الخسارة بحسب تذبذب السهم
    stop_distance = max(
        atr * ATR_STOP_MULTIPLIER,
        entry * 0.01
    )

    stop = round(
        entry - stop_distance,
        2
    )

    if stop <= 0 or stop >= entry:
        return None

    risk = entry - stop

    if risk <= 0:
        return None

    stop_percent = (
        risk / entry
    ) * 100

    if stop_percent > MAX_STOP_PERCENT:
        return None

    # المقاومات الفنية الخاصة بالسهم
    resistance_levels = find_resistance_levels(
        df,
        entry,
        atr
    )

    # أقل مسافة مطلوبة لتحقيق 1:2
    minimum_target1 = ceil_cent(
        entry + risk * MIN_RISK_REWARD
    )

    # أقل مسافة مطلوبة لتحقيق 1:3
    minimum_target2 = ceil_cent(
        entry + risk * TARGET2_MIN_RISK_REWARD
    )

    # الهدف الأول:
    # أقرب مقاومة تحقق 1:2 على الأقل
    target1_candidates = [
        level
        for level in resistance_levels
        if level >= minimum_target1
    ]

    if target1_candidates:

        target1 = min(target1_candidates)

        target1_source = "مقاومة فنية"

    else:

        target1 = ceil_cent(
            max(
                minimum_target1,
                entry + atr * 2
            )
        )

        target1_source = "إسقاط ATR"

    rr1 = (
        target1 - entry
    ) / risk

    if rr1 < MIN_RISK_REWARD:
        return None

    # الهدف الثاني:
    # المقاومة التالية بعد الهدف الأول،
    # بشرط أن تحقق 1:3 على الأقل.
    target2_candidates = [
        level
        for level in resistance_levels
        if (
            level > target1
            and level >= minimum_target2
        )
    ]

    if target2_candidates:

        target2 = min(target2_candidates)

        target2_source = "المقاومة الفنية التالية"

    else:

        target2 = ceil_cent(
            max(
                minimum_target2,
                target1 + atr,
                entry + atr * 3
            )
        )

        target2_source = "إسقاط ATR"

    if target2 <= target1:
        return None

    rr2 = (
        target2 - entry
    ) / risk

    if rr2 < TARGET2_MIN_RISK_REWARD:
        return None

    return {
        "entry": entry,
        "stop": stop,

        "target1": round(target1, 2),
        "target2": round(target2, 2),

        "risk": round(risk, 2),

        "risk_reward": round(rr1, 2),
        "target2_risk_reward": round(rr2, 2),

        "target1_source": target1_source,
        "target2_source": target2_source,

        "resistance_levels": resistance_levels
    }


# =====================================================
# 12. تحليل استراتيجية واحدة لكل سهم
# =====================================================

def evaluate_strategy(ticker, df, style, vwap=None):

    if df is None or len(df) < 60:
        return None

    last = df.iloc[-1]
    previous = df.iloc[-2]

    entry = safe_float(last["Close"])
    ema20 = safe_float(last["EMA20"])
    ema50 = safe_float(last["EMA50"])
    rsi = safe_float(last["RSI"])
    volume_ratio = safe_float(last["Volume_Ratio"])
    atr = safe_float(last["ATR"])

    previous_ema50 = safe_float(previous["EMA50"])
    previous_close = safe_float(previous["Close"])

    if any(value is None for value in [
        entry,
        ema20,
        ema50,
        rsi,
        volume_ratio,
        atr,
        previous_ema50,
        previous_close
    ]):
        return None

    if not MIN_STOCK_PRICE <= entry <= MAX_STOCK_PRICE:
        return None

    if atr <= 0:
        return None

    buy_pressure, sell_pressure = calculate_pressure(df)

    if buy_pressure is None or sell_pressure is None:
        return None

    # اتجاه السهم نفسه فقط
    bullish_setup = (
        (entry > ema20 and ema20 >= ema50)
        or (
            entry > ema20
            and buy_pressure >= 65
            and entry > previous_close
        )
    )

    if not bullish_setup:
        return None

    score = 0
    reasons = []

    # الاتجاه الفني: نقطتان
    if entry > ema20:
        score += 1
        reasons.append("السعر فوق EMA20")

    if ema20 > ema50:
        score += 1
        reasons.append("EMA20 فوق EMA50")

    # تأكيد الاتجاه: نقطة واحدة
    if style in ("سكالبينج", "تداول يومي"):

        if vwap is not None and entry > vwap:
            score += 1
            reasons.append("السعر فوق VWAP")

    else:

        if ema50 > previous_ema50:
            score += 1
            reasons.append("ميل EMA50 صاعد")

    # حجم التداول: نقطتان
    if volume_ratio >= 1.5:

        score += 2
        reasons.append("حجم تداول مرتفع")

    elif volume_ratio >= 1.0:

        score += 1
        reasons.append("حجم تداول مقبول")

    # RSI: نقطة
    if 48 <= rsi <= 68:

        score += 1
        reasons.append("RSI ضمن النطاق الإيجابي")

    # ضغط الشراء: نقطتان
    if buy_pressure >= 65:

        score += 2
        reasons.append("ضغط شراء تقديري قوي")

    elif buy_pressure >= 55:

        score += 1
        reasons.append("ضغط شراء تقديري إيجابي")

    # الدعم والمقاومة
    previous_bars = df.iloc[-21:-1]

    support = safe_float(
        previous_bars["Low"].min()
    )

    resistance = safe_float(
        previous_bars["High"].max()
    )

    structure_score = 0

    if (
        support is not None
        and resistance is not None
        and resistance > support
    ):

        distance_support = (
            entry - support
        ) / entry

        distance_resistance = (
            resistance - entry
        ) / entry

        if (
            0 <= distance_support <= 0.025
            and entry > previous_close
        ):

            structure_score = 2
            reasons.append("ارتداد صاعد من منطقة دعم")

        elif (
            entry > resistance
            and entry > previous_close
        ):

            structure_score = 2
            reasons.append("اختراق مقاومة سابقة")

        elif (
            entry > ema20
            and distance_resistance > 0.02
        ):

            structure_score = 1
            reasons.append("توجد مساحة فنية فوق السعر")

    score += structure_score

    score = min(score, 10)

    if score < MIN_SCORE:
        return None

    # حساب الأهداف الفنية من بيانات السهم نفسه
    levels = calculate_trade_levels(
        entry,
        atr,
        df
    )

    if levels is None:
        return None

    return {
        "ticker": ticker,
        "style": style,
        "score": score,

        "entry": levels["entry"],
        "stop": levels["stop"],

        "target1": levels["target1"],
        "target2": levels["target2"],

        "risk_reward": levels["risk_reward"],
        "target2_risk_reward": levels["target2_risk_reward"],

        "risk": levels["risk"],

        "target1_source": levels["target1_source"],
        "target2_source": levels["target2_source"],

        "resistance_levels": levels["resistance_levels"],

        "rsi": round(rsi, 1),
        "volume_ratio": round(volume_ratio, 2),

        "buy_pressure": round(buy_pressure, 1),
        "sell_pressure": round(sell_pressure, 1),

        "atr": round(atr, 3),

        "support": round(support, 2) if support is not None else None,
        "resistance": round(resistance, 2) if resistance is not None else None,

        "reasons": reasons,
        "timestamp": now_saudi()
    }


# =====================================================
# 13. تحليل السهم بالاستراتيجيات الثلاث
# =====================================================

def analyze_ticker(ticker):

    results = []
    errors = []

    configurations = [
        {
            "style": "سكالبينج",
            "interval": "5m",
            "period": "5d"
        },
        {
            "style": "تداول يومي",
            "interval": "15m",
            "period": "5d"
        },
        {
            "style": "سوينغ",
            "interval": "1d",
            "period": "6mo"
        }
    ]

    for config in configurations:

        try:

            raw = yahoo_history(
                ticker,
                config["period"],
                config["interval"]
            )

            df = prepare_dataframe(
                raw,
                config["interval"]
            )

            vwap = None

            if config["interval"] in ("5m", "15m"):
                vwap = calculate_vwap(df)

            result = evaluate_strategy(
                ticker=ticker,
                df=df,
                style=config["style"],
                vwap=vwap
            )

            if result:
                results.append(result)

        except NoDataError as e:

            errors.append(
                f"{config['style']}: {str(e)[:100]}"
            )

        except Exception as e:

            errors.append(
                f"{config['style']}: {str(e)[:120]}"
            )

    if not results:

        return {
            "ticker": ticker,
            "signal": None,
            "reason": "لا توجد فرصة مستوفية للشروط",
            "errors": errors
        }

    results.sort(
        key=lambda x: (
            x["score"],
            x["risk_reward"],
            x["target2_risk_reward"],
            x["buy_pressure"]
        ),
        reverse=True
    )

    best = results[0]

    best["signal"] = "BUY"

    best["other_styles"] = [
        item["style"]
        for item in results[1:]
    ]

    return best


# =====================================================
# 14. رسالة الفرصة
# =====================================================

def trade_message(trade):

    profit1 = trade["target1"] - trade["entry"]
    profit2 = trade["target2"] - trade["entry"]

    resistance_text = (
        ", ".join(
            f"${level:.2f}"
            for level in trade["resistance_levels"][:5]
        )
        if trade["resistance_levels"]
        else "لم تُرصد مقاومات واضحة"
    )

    return (
        "🚀 فرصة شراء أسهم أمريكية\n\n"

        f"📌 السهم: {trade['ticker']}\n"
        f"💵 سعر السهم: ${trade['entry']:.2f}\n"
        f"📊 نوع الصفقة: {trade['style']}\n"
        f"⭐ قوة الإشارة: {trade['score']}/10\n\n"

        "📍 خطة الصفقة\n"

        f"🟢 سعر الدخول: ${trade['entry']:.2f}\n"
        f"🛑 وقف الخسارة: ${trade['stop']:.2f}\n\n"

        f"🎯 الهدف الأول: ${trade['target1']:.2f}\n"
        f"مصدر الهدف الأول: {trade['target1_source']}\n"
        f"الربح النظري: ${profit1:.2f} للسهم\n"
        f"العائد للمخاطرة: 1:{trade['risk_reward']:.2f}\n\n"

        f"🎯 الهدف الثاني: ${trade['target2']:.2f}\n"
        f"مصدر الهدف الثاني: {trade['target2_source']}\n"
        f"الربح النظري: ${profit2:.2f} للسهم\n"
        f"العائد للمخاطرة: 1:{trade['target2_risk_reward']:.2f}\n\n"

        "📈 المؤشرات\n"

        f"ضغط الشراء التقديري: {trade['buy_pressure']:.1f}%\n"
        f"ضغط البيع التقديري: {trade['sell_pressure']:.1f}%\n"
        f"RSI: {trade['rsi']}\n"
        f"حجم التداول: {trade['volume_ratio']}x\n"
        f"ATR: {trade['atr']}\n\n"

        f"📌 المقاومات المرصودة:\n{resistance_text}\n\n"

        "🔍 أسباب الترشيح:\n"

        + "\n".join(
            f"• {reason}"
            for reason in trade["reasons"]
        )

        + "\n\n"

        f"🕒 وقت التحليل: "
        f"{trade['timestamp'].strftime('%H:%M:%S')} السعودية\n\n"

        "⚠️ الأهداف تقديرات فنية مبنية على المقاومات "
        "وتذبذب السهم، وليست ضمانًا للوصول إليها. "
        "قد تتغير مستويات الدعم والمقاومة مع حركة السعر. "
        "ضغط الشراء تقديري من بيانات الشموع والحجم، "
        "وليس قراءة مباشرة لتدفق الأوامر.\n\n"

        "تحقق من السعر قبل الشراء؛ إذا ابتعد السعر "
        "عن الدخول بأكثر من 0.3% فلا تطارد السهم."
    )


# =====================================================
# 15. إرسال الرسائل
# =====================================================

async def send_to_subscribers(application, message):

    if not subscribers:

        logger.warning("لا يوجد مشتركون")

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
                "فشل الإرسال إلى %s",
                chat_id
            )

    return delivered


# =====================================================
# 16. فتح صفقة
# =====================================================

async def open_trade(application, opportunity):

    global daily_count

    reset_daily_data_if_needed()

    ticker = opportunity["ticker"]

    if daily_count >= MAX_PER_DAY:
        return False

    if ticker in sent_today:
        return False

    if ticker in active_trades:
        return False

    if len(active_trades) >= MAX_ACTIVE_TRADES:
        return False

    delivered = await send_to_subscribers(
        application,
        trade_message(opportunity)
    )

    if not delivered:
        return False

    trade = {
        **opportunity,
        "opened_at": now_ny(),
        "tp1_sent": False,
        "tp2_sent": False,
        "stop_sent": False
    }

    active_trades[ticker] = trade

    sent_today.add(ticker)

    daily_count += 1

    logger.info(
        "تم إرسال فرصة %s | %s | %s/10",
        ticker,
        opportunity["style"],
        opportunity["score"]
    )

    return True


# =====================================================
# 17. السعر الحالي
# =====================================================

def get_current_price(ticker):

    now_timestamp = time_module.time()

    cached = PRICE_CACHE.get(ticker)

    if cached:

        if (
            now_timestamp - cached["timestamp"]
            < PRICE_CACHE_TTL
        ):

            return cached["price"]

    try:

        with YAHOO_LOCK:

            stock = yf.Ticker(ticker)

            df = stock.history(
                period="1d",
                interval="1m",
                prepost=False
            )

        if df is None or df.empty:
            return None

        df = df.dropna(subset=["Close"])

        if df.empty:
            return None

        last_timestamp = df.index[-1]

        if last_timestamp.tzinfo is None:

            last_timestamp = last_timestamp.tz_localize(TZ_NY)

        else:

            last_timestamp = last_timestamp.tz_convert(TZ_NY)

        if last_timestamp.date() != now_ny().date():
            return None

        age = now_ny() - last_timestamp

        if age > timedelta(minutes=10):
            return None

        price = safe_float(
            df["Close"].iloc[-1]
        )

        if price is None or price <= 0:
            return None

        PRICE_CACHE[ticker] = {
            "price": price,
            "timestamp": now_timestamp
        }

        return price

    except Exception:

        logger.warning(
            "تعذر جلب السعر الحالي للسهم %s",
            ticker
        )

        return None


# =====================================================
# 18. متابعة الصفقات
# =====================================================

async def monitor_trades(application):

    if not active_trades:
        return

    now = now_ny()

    if not market_is_open():

        if now.time() >= CLOSE_ALL_TIME_NY:

            for ticker in list(active_trades.keys()):

                trade = active_trades.get(ticker)

                if not trade:
                    continue

                if trade["style"] != "سوينغ":

                    await send_to_subscribers(
                        application,
                        "⏰ انتهت جلسة التداول\n"
                        f"السهم: {ticker}\n"
                        "انتهت متابعة الصفقة اليومية."
                    )

                    active_trades.pop(ticker, None)

        return

    for ticker in list(active_trades.keys()):

        trade = active_trades.get(ticker)

        if not trade:
            continue

        if (
            trade["style"] != "سوينغ"
            and now.time() >= CLOSE_ALL_TIME_NY
        ):

            await send_to_subscribers(
                application,
                "⏰ انتهت جلسة التداول\n"
                f"السهم: {ticker}\n"
                "انتهت متابعة الصفقة اليومية."
            )

            active_trades.pop(ticker, None)

            continue

        price = await asyncio.to_thread(
            get_current_price,
            ticker
        )

        if price is None:
            continue

        entry = trade["entry"]
        stop = trade["stop"]
        target1 = trade["target1"]
        target2 = trade["target2"]

        # وقف الخسارة
        if price <= stop and not trade["stop_sent"]:

            trade["stop_sent"] = True

            await send_to_subscribers(
                application,
                "🛑 وصل السهم إلى وقف الخسارة\n\n"
                f"السهم: {ticker}\n"
                f"سعر الدخول: ${entry:.2f}\n"
                f"السعر الحالي: ${price:.2f}\n"
                f"وقف الخسارة: ${stop:.2f}\n"
            )

            active_trades.pop(ticker, None)

            continue

        # الهدف الثاني
        if price >= target2 and not trade["tp2_sent"]:

            trade["tp2_sent"] = True

            await send_to_subscribers(
                application,
                "🏆 تحقق الهدف الثاني\n\n"
                f"السهم: {ticker}\n"
                f"الدخول: ${entry:.2f}\n"
                f"السعر الحالي: ${price:.2f}\n"
                f"الهدف الثاني: ${target2:.2f}\n"
            )

            active_trades.pop(ticker, None)

            continue

        # الهدف الأول
        if price >= target1 and not trade["tp1_sent"]:

            trade["tp1_sent"] = True

            await send_to_subscribers(
                application,
                "✅ تحقق الهدف الأول\n\n"
                f"السهم: {ticker}\n"
                f"الدخول: ${entry:.2f}\n"
                f"السعر الحالي: ${price:.2f}\n"
                f"الهدف الأول: ${target1:.2f}\n\n"
                "يمكن دراسة تأمين جزء من الربح."
            )


async def monitor_loop(application):

    logger.info(
        "بدأت متابعة الصفقات كل %s ثانية",
        MONITOR_INTERVAL
    )

    while True:

        try:

            reset_daily_data_if_needed()

            await monitor_trades(application)

        except asyncio.CancelledError:
            raise

        except Exception:

            logger.exception(
                "خطأ في متابعة الصفقات"
            )

        await asyncio.sleep(MONITOR_INTERVAL)


# =====================================================
# 19. محرك الفحص
# =====================================================

async def run_scan(application, manual=False):

    global last_scan_time
    global last_scan_summary
    global last_scan_errors

    reset_daily_data_if_needed()

    if scan_lock.locked():
        return "يوجد فحص آخر قيد التنفيذ."

    async with scan_lock:

        # التحقق من الوقت أولًا
        if not market_is_open_for_scanning():

            return (
                "⏰ الفحص غير متاح حاليًا.\n\n"
                f"الوقت السعودي: {now_saudi().strftime('%H:%M:%S')}\n"
                "نافذة السعودية: 17:00 إلى 00:00.\n"
                f"وقت نيويورك: {now_ny().strftime('%H:%M:%S')}\n"
                "يشترط أن يكون السوق الأمريكي ضمن ساعات الفحص."
            )

        if daily_count >= MAX_PER_DAY:

            return (
                f"وصل البوت إلى الحد اليومي "
                f"{MAX_PER_DAY} فرص."
            )

        last_scan_time = now_saudi()

        summary = {}
        errors = []

        # تحديث بيانات الأولويات قبل اختيار الدفعة
        await asyncio.to_thread(
            refresh_priority_metrics
        )

        # اختيار أفضل 12 سهمًا حسب الترتيب الموحد للسيولة وحجم التداول
        batch = get_priority_batch()

        logger.info(
            "بدء فحص دفعة أفضل الأسهم | عدد الأسهم: %s",
            len(batch)
        )

        for ticker in batch:

            reset_daily_data_if_needed()

            if daily_count >= MAX_PER_DAY:
                break

            # تسجيل وقت فحص السهم لمنع تكراره في دفعات متقاربة
            LAST_TICKER_SCAN[ticker] = time_module.time()

            if ticker in sent_today:

                summary[ticker] = "تم إرسال فرصة اليوم"
                continue

            if ticker in active_trades:

                summary[ticker] = "صفقة نشطة"
                continue

            try:

                analysis = await asyncio.to_thread(
                    analyze_ticker,
                    ticker
                )

                if not analysis.get("signal"):

                    summary[ticker] = analysis.get(
                        "reason",
                        "لا توجد فرصة"
                    )

                    if analysis.get("errors"):

                        errors.extend(
                            [
                                f"{ticker}: {e}"
                                for e in analysis["errors"]
                            ]
                        )

                    continue

                current_price = await asyncio.to_thread(
                    get_current_price,
                    ticker
                )

                if current_price is None:

                    summary[ticker] = (
                        "استبعاد: تعذر تأكيد السعر الحالي"
                    )

                    continue

                entry = analysis["entry"]

                deviation = abs(
                    current_price - entry
                ) / entry

                if deviation > MAX_ENTRY_DEVIATION:

                    summary[ticker] = (
                        "استبعاد: السعر ابتعد عن الدخول "
                        f"(السعر الحالي ${current_price:.2f})"
                    )

                    continue

                sent = await open_trade(
                    application,
                    analysis
                )

                if sent:

                    summary[ticker] = (
                        f"تم إرسال فرصة {analysis['style']} "
                        f"| التقييم {analysis['score']}/10"
                    )

                else:

                    summary[ticker] = (
                        "لم يتم الإرسال: لا يوجد مشترك "
                        "أو بلغ البوت الحد اليومي"
                    )

            except NoDataError as e:

                summary[ticker] = (
                    f"لا توجد بيانات: {str(e)[:100]}"
                )

            except Exception as e:

                reason = str(e)[:200]

                errors.append(
                    f"{ticker}: {reason}"
                )

                summary[ticker] = (
                    f"خطأ: {reason}"
                )

                logger.exception(
                    "خطأ تحليل %s",
                    ticker
                )

        last_scan_summary = summary
        last_scan_errors = errors

        report = (
            "📊 انتهى فحص الدفعة\n\n"
            "طريقة التحليل: كل سهم مستقل.\n"
            "ترتيب الأسهم: أفضل 12 حسب السيولة وحجم التداول بترتيب موحّد.\n"
            "التحديث: كل 3 دقائق.\n"
            "منع التكرار: 15 دقيقة، مع استكمال العدد بالأقدم فحصًا عند الحاجة.\n"
            "الأهداف: مقاومات فنية وإسقاط ATR عند الحاجة.\n"
            f"الفرص المرسلة: {daily_count}/{MAX_PER_DAY}\n"
            f"الأسهم في هذه الدفعة: {len(summary)}\n"
            f"إجمالي الأسهم في القائمة: {len(TICKERS)}\n"
            f"الصفقات النشطة: {len(active_trades)}\n"
            f"الأخطاء: {len(errors)}\n"
            f"الوقت السعودي: {now_saudi().strftime('%H:%M:%S')}"
        )

        logger.info(report)

        return report


# =====================================================
# 20. الفحص التلقائي
# =====================================================

async def scanner_loop(application):

    logger.info("بدأ محرك فحص الأسهم")

    while True:

        try:

            reset_daily_data_if_needed()

            if market_is_open_for_scanning():

                await run_scan(
                    application,
                    manual=False
                )

        except asyncio.CancelledError:
            raise

        except Exception:

            logger.exception(
                "خطأ في محرك الفحص"
            )

        await asyncio.sleep(SCAN_INTERVAL)


# =====================================================
# 21. أوامر تيليجرام
# =====================================================

async def start_command(update, context):

    chat_id = update.effective_chat.id

    subscribers.add(chat_id)

    await update.message.reply_text(
        "✅ تم تفعيل اشتراكك في Aboghala Bot.\n\n"

        "البوت يبحث عن الأسهم الأمريكية بين 10 و100 دولار.\n\n"

        "أنواع الصفقات:\n"
        "⚡ سكالبينج\n"
        "📊 تداول يومي\n"
        "📈 سوينغ\n\n"

        "يحدد البوت:\n"
        "سعر الدخول\n"
        "وقف الخسارة\n"
        "الهدف الأول والثاني من المقاومات الفنية\n"
        "قوة الإشارة\n"
        "العائد للمخاطرة\n\n"

        "الأهداف تعتمد على المقاومات وتذبذب السهم، "
        "ولا توجد مبالغ ربح ثابتة.\n\n"

        "كل سهم يُحلل بصورة مستقلة.\n\n"

        "🕒 وقت الفحص بتوقيت السعودية:\n"
        "من 5:00 مساءً إلى 12:00 منتصف الليل.\n\n"

        "الأوامر:\n"
        "/scan - فحص دفعة من الأسهم\n"
        "/status - حالة البوت\n"
        "/report - تقرير الفحص\n"
        "/debug - تشخيص الأسهم\n"
        "/active - الصفقات النشطة\n"
        "/help - المساعدة\n\n"

        "⚠️ التنبيهات تحليلية وليست تنفيذًا للصفقات."
    )


async def scan_command(update, context):

    await update.message.reply_text(
        "🔎 بدأ فحص الدفعة التالية من الأسهم..."
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
        else "لم يتم تنفيذ فحص فعلي"
    )

    scan_allowed = market_is_open_for_scanning()

    await update.message.reply_text(
        "📊 حالة Aboghala Bot\n\n"

        "الحالة: يعمل\n"
        "تحليل الأسهم: مستقل لكل سهم\n\n"

        f"وقت السعودية: {now_saudi().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"وقت نيويورك: {now_ny().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        f"آخر فحص فعلي: {last_scan}\n\n"

        f"سعر الأسهم: {MIN_STOCK_PRICE}$ - {MAX_STOCK_PRICE}$\n"
        f"الحد الأدنى للتقييم: {MIN_SCORE}/10\n"
        f"العائد للمخاطرة للهدف الأول: 1:{MIN_RISK_REWARD:.1f} على الأقل\n"
        f"العائد للمخاطرة للهدف الثاني: 1:{TARGET2_MIN_RISK_REWARD:.1f} على الأقل\n\n"

        "🎯 الأهداف: مقاومات السهم الفنية، "
        "وإسقاط ATR عند عدم وجود مقاومة مناسبة.\n"
        "لا توجد أهداف ربح ثابتة بالدولار.\n\n"

        f"الفرص المرسلة اليوم: {daily_count}/{MAX_PER_DAY}\n"
        f"الصفقات النشطة: {len(active_trades)}\n"
        f"المشتركون: {len(subscribers)}\n\n"

        f"عدد الأسهم: {len(TICKERS)}\n"
        f"حجم الدفعة: {SCAN_BATCH_SIZE}\n"
        f"الفحص كل: {SCAN_INTERVAL // 60} دقائق تقريبًا\n"
        f"متابعة الصفقات كل: {MONITOR_INTERVAL} ثانية\n\n"

        "أولوية الفحص:\n"
        "أفضل 12 سهمًا بترتيب موحّد يجمع السيولة وحجم التداول، "
        "بنسبة 50% لكل منهما.\n"
        f"منع تكرار السهم لمدة: {SCAN_REPEAT_COOLDOWN // 60} دقيقة.\n"
        "عند عدم توفر 12 سهمًا بعد التبريد، يُستكمل العدد بالأقدم فحصًا.\n\n"

        f"الفحص التلقائي الآن: {'نعم' if scan_allowed else 'لا'}\n"
        "الفحص التلقائي مجدول: نعم\n\n"

        "نافذة السعودية: 17:00–00:00\n"
        "يشترط توافقها مع ساعات فحص السوق الأمريكي."
    )


async def report_command(update, context):

    if not last_scan_time:

        await update.message.reply_text(
            "لم يتم تنفيذ فحص فعلي حتى الآن."
        )

        return

    lines = [
        "📋 تقرير آخر فحص",
        f"الوقت السعودي: {last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}",
        "طريقة التحليل: كل سهم مستقل.",
        "ترتيب الأسهم: أفضل 12 حسب السيولة وحجم التداول بترتيب موحّد.",
        "وزن السيولة: 50%، ووزن حجم التداول: 50%.",
        "التحديث: كل 3 دقائق.",
        "منع التكرار: 15 دقيقة، والاستكمال بالأقدم فحصًا عند الحاجة.",
        "الأهداف: مقاومات فنية وإسقاط ATR عند الحاجة.",
        f"الفرص المرسلة اليوم: {daily_count}/{MAX_PER_DAY}",
        f"عدد الأسهم في القائمة: {len(TICKERS)}",
        ""
    ]

    for ticker, result in last_scan_summary.items():

        lines.append(
            f"{ticker}: {result}"
        )

    if last_scan_errors:

        lines.append("\nالأخطاء:")
        lines.extend(last_scan_errors)

    await reply_long(
        update,
        "\n".join(lines)
    )


async def debug_command(update, context):

    if not last_scan_summary:

        await update.message.reply_text(
            "لا توجد نتائج فحص حتى الآن."
        )

        return

    lines = [
        "🔬 تشخيص الأسهم\n",

        "تحليل مستقل لكل سهم.",

        f"نطاق السعر: {MIN_STOCK_PRICE}$–{MAX_STOCK_PRICE}$",
        f"التقييم الأدنى: {MIN_SCORE}/10",

        f"العائد الأدنى للهدف الأول: 1:{MIN_RISK_REWARD}",
        f"العائد الأدنى للهدف الثاني: 1:{TARGET2_MIN_RISK_REWARD}",

        "الأهداف: مقاومات فنية وإسقاط ATR عند الحاجة.",

        "الاستراتيجيات: سكالبينج، يومي، سوينغ",

        "ضغط الشراء والبيع: تقديري من بيانات الشموع",

        f"عدد الأسهم: {len(TICKERS)}",

        "وقت الفحص: 17:00–00:00 السعودية",

        ""
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
            "لا توجد صفقات نشطة."
        )

        return

    lines = ["📌 الصفقات النشطة"]

    for ticker, trade in active_trades.items():

        lines.append(
            f"\n📈 {ticker}\n"
            f"النوع: {trade['style']}\n"
            f"الدخول: ${trade['entry']:.2f}\n"
            f"الهدف الأول: ${trade['target1']:.2f}\n"
            f"مصدر الهدف الأول: {trade['target1_source']}\n"
            f"الهدف الثاني: ${trade['target2']:.2f}\n"
            f"مصدر الهدف الثاني: {trade['target2_source']}\n"
            f"وقف الخسارة: ${trade['stop']:.2f}\n"
            f"التقييم: {trade['score']}/10\n"
            f"العائد للمخاطرة للهدف الأول: 1:{trade['risk_reward']:.2f}\n"
            f"العائد للمخاطرة للهدف الثاني: 1:{trade['target2_risk_reward']:.2f}"
        )

    await reply_long(
        update,
        "\n".join(lines)
    )


async def help_command(update, context):

    await update.message.reply_text(
        "/start - تفعيل التنبيهات\n"
        "/scan - فحص الدفعة التالية\n"
        "/status - حالة البوت\n"
        "/report - تقرير الفحص\n"
        "/debug - تشخيص الأسهم\n"
        "/active - الصفقات النشطة\n"
        "/help - المساعدة"
    )


# =====================================================
# 22. بدء المهام
# =====================================================

async def post_init(application):

    application.create_task(
        scanner_loop(application)
    )

    application.create_task(
        monitor_loop(application)
    )

    logger.info(
        "تم تشغيل محرك الأسهم ومتابعة الصفقات"
    )


# =====================================================
# 23. خادم Render
# =====================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path in ("/", "/health"):

            body = b"Aboghala Stock Scanner is running"

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
# 24. تشغيل البوت
# =====================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "لم يتم العثور على BOT_TOKEN"
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
        "Aboghala Stock Bot is starting..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
