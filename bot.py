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
# 1. الإعدادات
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

TZ = ZoneInfo("America/New_York")

# الأسهم المرشحة
TICKERS = [
    "NVDA", "TSLA", "META", "AAPL", "AMD",
    "MSFT", "GOOGL", "AMZN", "MU", "AVGO",
    "ORCL", "PLTR", "ARM", "CRM",
    "COIN", "NFLX", "HOOD", "SOFI",
    "UBER", "SMCI", "JPM", "XOM",
    "BA", "DIS", "GLD", "F", "PFE",
    "RIVN", "SNAP", "INTC", "PYPL",
    "SHOP", "C", "BAC", "T", "NIO",
    "CCL", "DAL", "WBD", "MARA",
    "HIMS", "RBLX", "DKNG", "CVNA"
]

MIN_STOCK_PRICE = 10.0
MAX_STOCK_PRICE = 100.0

MAX_PER_DAY = 10

SCAN_INTERVAL = 60

START_SCAN = time(9, 40)
STOP_NEW_TRADES = time(15, 45)
CLOSE_ALL_TIME = time(15, 55)

# الحد الأدنى للتقييم
MIN_SCORE = 8

# العائد للمخاطرة
MIN_RISK_REWARD = 2.0

# وقف الخسارة
ATR_STOP_MULTIPLIER = 1.2

# أقصى وقف خسارة كنسبة من سعر السهم
MAX_STOP_PERCENT = 4.0

# صلاحية سعر الدخول
MAX_ENTRY_DEVIATION = 0.003

# متابعة الأسعار
MONITOR_INTERVAL = 60

# إعدادات Yahoo
YAHOO_RETRIES = 3
YAHOO_RETRY_DELAY = 2

# منع الطلبات المتزامنة
YAHOO_LOCK = threading.RLock()

# تخزين البيانات
PRICE_CACHE = {}
PRICE_CACHE_TTL = 20

# عدد الفرص المرسلة
MAX_ACTIVE_TRADES = 15


# =====================================================
# 2. السجلات وحالة البوت
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("AboghalaBot")

logger.info("YFINANCE VERSION: %s", yf.__version__)

subscribers = set()

active_trades = {}

sent_today = set()

daily_count = 0

current_day = None

last_scan_time = None

last_scan_summary = {}

last_scan_errors = []

market_regime = "غير معروف"

market_regime_details = ""

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

    today = now_ny().date()

    if current_day != today:

        current_day = today

        daily_count = 0

        sent_today = set()

        logger.info("تم تصفير إحصائيات اليوم")


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


def yahoo_history(ticker, period, interval):

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

                raise ValueError("Yahoo أعاد بيانات فارغة")

            return df

        except Exception as e:

            last_error = e

            logger.warning(
                "%s %s: المحاولة %s/%s: %s",
                ticker,
                interval,
                attempt,
                YAHOO_RETRIES,
                str(e)[:200]
            )

            if attempt < YAHOO_RETRIES:

                time_module.sleep(
                    YAHOO_RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"تعذر جلب بيانات {ticker}: {last_error}"
    )


# =====================================================
# 5. تجهيز البيانات والمؤشرات
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

        df.index = df.index.tz_localize(TZ)

    else:

        df.index = df.index.tz_convert(TZ)

    df = df.sort_index()

    df = df[
        (df["Close"] > 0)
        & (df["Volume"] >= 0)
    ]

    if df.empty:

        raise ValueError("لا توجد بيانات صالحة")

    # استبعاد الشمعة غير المكتملة
    if interval == "5m":
        minutes = 5

    elif interval == "15m":
        minutes = 15

    else:
        minutes = None

    if minutes:

        candle_close = (
            df.index + timedelta(minutes=minutes)
        )

        df = df.loc[
            candle_close <= now_ny() - timedelta(seconds=30)
        ]

    if len(df) < 22:

        raise ValueError("عدد الشموع غير كافٍ")

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

    tr2 = (df["High"] - previous_close).abs()

    tr3 = (df["Low"] - previous_close).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["ATR"] = true_range.rolling(
        14,
        min_periods=10
    ).mean()

    # ضغط الشراء والبيع التقديري
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
# 6. حساب VWAP
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

    vwap = (
        (typical * volume).sum()
        / total_volume
    )

    return safe_float(vwap)


# =====================================================
# 7. تحديد حالة السوق
# =====================================================

def detect_market_regime():

    global market_regime
    global market_regime_details

    try:

        df = yahoo_history(
            "SPY",
            "6mo",
            "1d"
        )

        df = prepare_dataframe(
            df,
            "1d"
        )

        last = df.iloc[-1]

        close = safe_float(last["Close"])

        ema20 = safe_float(last["EMA20"])

        ema50 = safe_float(last["EMA50"])

        if any(
            value is None
            for value in [close, ema20, ema50]
        ):

            raise ValueError("مؤشرات SPY غير مكتملة")

        if close > ema20 > ema50:

            market_regime = "صاعد"

        elif close < ema20 < ema50:

            market_regime = "هابط"

        else:

            market_regime = "متذبذب"

        market_regime_details = (
            f"SPY={close:.2f} | "
            f"EMA20={ema20:.2f} | "
            f"EMA50={ema50:.2f}"
        )

        logger.info(
            "حالة السوق: %s | %s",
            market_regime,
            market_regime_details
        )

        return market_regime

    except Exception as e:

        market_regime = "غير معروف"

        market_regime_details = str(e)[:200]

        logger.exception(
            "تعذر تحديد حالة السوق"
        )

        return market_regime


# =====================================================
# 8. حساب ضغط الشراء والبيع
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

    buy_percent = buy / total * 100

    sell_percent = sell / total * 100

    return buy_percent, sell_percent


# =====================================================
# 9. تحليل استراتيجية واحدة
# =====================================================

def evaluate_strategy(
    ticker,
    df,
    style,
    regime,
    vwap=None
):

    if df is None or len(df) < 22:

        return None

    last = df.iloc[-1]

    previous = df.iloc[-2]

    entry = safe_float(last["Close"])

    ema20 = safe_float(last["EMA20"])

    ema50 = safe_float(last["EMA50"])

    rsi = safe_float(last["RSI"])

    volume_ratio = safe_float(
        last["Volume_Ratio"]
    )

    atr = safe_float(last["ATR"])

    if any(
        value is None
        for value in [
            entry,
            ema20,
            ema50,
            rsi,
            volume_ratio,
            atr
        ]
    ):

        return None

    if not MIN_STOCK_PRICE <= entry <= MAX_STOCK_PRICE:

        return None

    if atr <= 0:

        return None

    buy_pressure, sell_pressure = calculate_pressure(df)

    if buy_pressure is None:

        return None

    score = 0

    reasons = []

    # ---------------------------------
    # 1. الاتجاه الفني: نقطتان
    # ---------------------------------

    trend_score = 0

    if entry > ema20:

        trend_score += 1

        reasons.append("السعر فوق EMA20")

    if ema20 > ema50:

        trend_score += 1

        reasons.append("EMA20 فوق EMA50")

    score += trend_score

    # ---------------------------------
    # 2. VWAP: نقطة
    # ---------------------------------

    if style in ("سكالبينج", "تداول يومي"):

        if vwap is not None and entry > vwap:

            score += 1

            reasons.append("السعر فوق VWAP")

    else:

        score += 1 if entry > ema20 else 0

    # ---------------------------------
    # 3. حجم التداول: نقطتان
    # ---------------------------------

    if volume_ratio >= 1.5:

        score += 2

        reasons.append("حجم تداول مرتفع")

    elif volume_ratio >= 1.0:

        score += 1

        reasons.append("حجم تداول مقبول")

    # ---------------------------------
    # 4. RSI: نقطة
    # ---------------------------------

    if 48 <= rsi <= 68:

        score += 1

        reasons.append("RSI مناسب")

    # ---------------------------------
    # 5. ضغط الشراء: نقطتان
    # ---------------------------------

    if buy_pressure >= 65:

        score += 2

        reasons.append("ضغط شراء قوي")

    elif buy_pressure >= 55:

        score += 1

        reasons.append("ضغط شراء إيجابي")

    # ---------------------------------
    # 6. الدعم والمقاومة: نقطتان
    # ---------------------------------

    previous_bars = df.iloc[-21:-1]

    support = safe_float(
        previous_bars["Low"].min()
    )

    resistance = safe_float(
        previous_bars["High"].max()
    )

    structure_score = 0

    if support and resistance and resistance > support:

        distance_support = (
            entry - support
        ) / entry

        distance_resistance = (
            resistance - entry
        ) / entry

        # قرب السعر من الدعم مع ارتداد
        if (
            distance_support <= 0.025
            and entry > safe_float(previous["Close"])
        ):

            structure_score = 2

            reasons.append("ارتداد من منطقة دعم")

        # اختراق المقاومة
        elif (
            entry > resistance
            and entry > safe_float(previous["Close"])
        ):

            structure_score = 2

            reasons.append("اختراق مقاومة")

        # حركة صاعدة قرب المقاومة
        elif (
            entry > ema20
            and distance_resistance > 0.02
        ):

            structure_score = 1

            reasons.append("مساحة صعود متاحة")

    score += structure_score

    # ---------------------------------
    # تحديد حالة السوق
    # ---------------------------------

    if regime == "هابط":

        # لا نطارد الأسهم الهابطة
        if style != "سوينغ":

            return None

        # السوينغ في السوق الهابط يحتاج تأكيدًا أقوى
        if not (
            entry > ema20
            and buy_pressure >= 65
            and score >= 8
        ):

            return None

        reasons.append("ارتداد انتقائي في سوق هابط")

    elif regime == "متذبذب":

        if structure_score < 2:

            return None

        reasons.append("استراتيجية سوق متذبذب")

    elif regime == "غير معروف":

        return None

    # ---------------------------------
    # تحديد وقف الخسارة
    # ---------------------------------

    stop_distance = max(
        atr * ATR_STOP_MULTIPLIER,
        entry * 0.01
    )

    stop = entry - stop_distance

    stop_percent = (
        stop_distance / entry
    ) * 100

    if stop_percent > MAX_STOP_PERCENT:

        return None

    if stop <= 0:

        return None

    risk = entry - stop

    # ---------------------------------
    # الهدف الأول والثاني
    # ---------------------------------

    target1 = entry + (risk * 2)

    target2 = entry + (risk * 3)

    rr = (target1 - entry) / risk

    if rr < MIN_RISK_REWARD:

        return None

    # ---------------------------------
    # التقييم النهائي
    # ---------------------------------

    if score < MIN_SCORE:

        return None

    return {
        "ticker": ticker,
        "style": style,
        "score": score,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_reward": round(rr, 2),
        "rsi": round(rsi, 1),
        "volume_ratio": round(volume_ratio, 2),
        "buy_pressure": round(buy_pressure, 1),
        "sell_pressure": round(sell_pressure, 1),
        "atr": round(atr, 3),
        "support": round(support, 2) if support else None,
        "resistance": round(resistance, 2) if resistance else None,
        "regime": regime,
        "reasons": reasons,
        "timestamp": now_ny()
    }


# =====================================================
# 10. تحليل السهم بجميع الاستراتيجيات
# =====================================================

def analyze_ticker(ticker, regime):

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

            df = yahoo_history(
                ticker,
                config["period"],
                config["interval"]
            )

            df = prepare_dataframe(
                df,
                config["interval"]
            )

            vwap = None

            if config["interval"] in ("5m", "15m"):

                vwap = calculate_vwap(df)

            result = evaluate_strategy(
                ticker,
                df,
                config["style"],
                regime,
                vwap
            )

            if result:

                results.append(result)

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

    # اختيار أفضل استراتيجية
    results.sort(
        key=lambda x: (
            x["score"],
            x["risk_reward"],
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
# 11. رسالة الفرصة
# =====================================================

def trade_message(trade):

    return (
        "🚀 فرصة شراء أسهم أمريكية\n\n"

        f"📌 السهم: {trade['ticker']}\n"

        f"💵 سعر السهم: ${trade['entry']:.2f}\n"

        f"📊 نوع الصفقة: {trade['style']}\n"

        f"⭐ قوة الإشارة: {trade['score']}/10\n\n"

        "📍 خطة الصفقة\n"

        f"🟢 سعر الدخول: ${trade['entry']:.2f}\n"

        f"🛑 وقف الخسارة: ${trade['stop']:.2f}\n"

        f"🎯 الهدف الأول: ${trade['target1']:.2f}\n"

        f"🎯 الهدف الثاني: ${trade['target2']:.2f}\n\n"

        "📈 المؤشرات\n"

        f"ضغط الشراء التقديري: {trade['buy_pressure']:.1f}%\n"

        f"ضغط البيع التقديري: {trade['sell_pressure']:.1f}%\n"

        f"RSI: {trade['rsi']}\n"

        f"حجم التداول: {trade['volume_ratio']}x\n"

        f"حالة السوق: {trade['regime']}\n"

        f"العائد للمخاطرة: 1:{trade['risk_reward']:.1f}\n\n"

        "🔍 أسباب الترشيح:\n"

        + "\n".join(
            f"• {reason}"
            for reason in trade["reasons"]
        )

        + "\n\n"

        f"🕒 وقت التحليل: "
        f"{trade['timestamp'].strftime('%H:%M:%S')} نيويورك\n\n"

        "⚠️ هذه إشارة تحليلية وليست أمر تنفيذ. "
        "تحقق من السعر قبل الشراء؛ إذا ابتعد السعر "
        "عن الدخول بأكثر من 0.3% فلا تطارد السهم."
    )


# =====================================================
# 12. إرسال الرسائل
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
# 13. فتح صفقة سهم
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
# 14. جلب السعر الحالي
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

        price = safe_float(
            df["Close"].dropna().iloc[-1]
        )

        if price is None or price <= 0:

            return None

        PRICE_CACHE[ticker] = {
            "price": price,
            "timestamp": now_timestamp
        }

        return price

    except Exception:

        logger.exception(
            "تعذر جلب سعر %s",
            ticker
        )

        return None


# =====================================================
# 15. متابعة الصفقات
# =====================================================

async def monitor_trades(application):

    if not active_trades:

        return

    now = now_ny()

    for ticker in list(active_trades.keys()):

        trade = active_trades.get(ticker)

        if not trade:

            continue

        # إغلاق متابعة صفقات اليوم عند نهاية الجلسة
        if (
            trade["style"] != "سوينغ"
            and now.time() >= CLOSE_ALL_TIME
        ):

            await send_to_subscribers(
                application,
                f"⏰ انتهت جلسة التداول\n"
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


# =====================================================
# 16. محرك الفحص
# =====================================================

async def run_scan(application, manual=False):

    global last_scan_time
    global last_scan_summary
    global last_scan_errors
    global market_regime

    reset_daily_data_if_needed()

    if scan_lock.locked():

        return "يوجد فحص آخر قيد التنفيذ."

    async with scan_lock:

        last_scan_time = now_ny()

        summary = {}

        errors = []

        if not market_is_open_for_scanning():

            if not (manual and market_is_open()):

                return (
                    "السوق خارج وقت الفحص.\n"
                    f"وقت نيويورك: {now_ny().strftime('%H:%M:%S')}\n"
                    "وقت الفحص: 09:40–15:45 نيويورك"
                )

        if daily_count >= MAX_PER_DAY:

            return (
                f"وصل البوت إلى الحد اليومي "
                f"{MAX_PER_DAY} فرص."
            )

        # تحديد حالة السوق أولاً
        regime = await asyncio.to_thread(
            detect_market_regime
        )

        if regime == "غير معروف":

            return (
                "تعذر تحديد حالة السوق من SPY.\n"
                "تم إيقاف إرسال الفرص احترازيًا.\n"
                f"{market_regime_details}"
            )

        logger.info(
            "بدء الفحص | حالة السوق: %s",
            regime
        )

        for ticker in TICKERS:

            reset_daily_data_if_needed()

            if daily_count >= MAX_PER_DAY:

                break

            if ticker in sent_today:

                summary[ticker] = "تم إرسال فرصة اليوم"

                continue

            if ticker in active_trades:

                summary[ticker] = "صفقة نشطة"

                continue

            try:

                analysis = await asyncio.to_thread(
                    analyze_ticker,
                    ticker,
                    regime
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

                opportunity = analysis

                # التأكد من سعر السهم قبل الإرسال
                current_price = await asyncio.to_thread(
                    get_current_price,
                    ticker
                )

                if current_price is None:

                    summary[ticker] = (
                        "تم استبعاد الفرصة: تعذر تأكيد السعر الحالي"
                    )

                    continue

                entry = opportunity["entry"]

                deviation = abs(
                    current_price - entry
                ) / entry

                if deviation > MAX_ENTRY_DEVIATION:

                    summary[ticker] = (
                        "تم استبعاد الفرصة: السعر ابتعد عن الدخول "
                        f"(السعر الحالي ${current_price:.2f})"
                    )

                    continue

                sent = await open_trade(
                    application,
                    opportunity
                )

                if sent:

                    summary[ticker] = (
                        f"تم إرسال فرصة {opportunity['style']} "
                        f"| التقييم {opportunity['score']}/10"
                    )

                else:

                    summary[ticker] = (
                        "لم يتم الإرسال: لا يوجد مشترك "
                        "أو بلغ البوت الحد اليومي"
                    )

            except Exception as e:

                reason = str(e)[:250]

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
            "📊 انتهى فحص السوق\n\n"
            f"حالة السوق: {regime}\n"
            f"الفرص المرسلة: {daily_count}/{MAX_PER_DAY}\n"
            f"الأسهم المفحوصة: {len(summary)}\n"
            f"الصفقات النشطة: {len(active_trades)}\n"
            f"الأخطاء: {len(errors)}\n"
            f"الوقت: {now_ny().strftime('%H:%M:%S')} نيويورك"
        )

        logger.info(report)

        return report


# =====================================================
# 17. الفحص التلقائي
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

            await monitor_trades(application)

        except asyncio.CancelledError:

            raise

        except Exception:

            logger.exception(
                "خطأ في محرك الفحص"
            )

        await asyncio.sleep(SCAN_INTERVAL)


# =====================================================
# 18. أوامر تيليجرام
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
        "الهدف الأول والثاني\n"
        "قوة الإشارة\n"
        "العائد للمخاطرة\n\n"

        "الأوامر:\n"
        "/scan - فحص السوق\n"
        "/status - حالة البوت\n"
        "/report - تقرير الفحص\n"
        "/debug - تشخيص الأسهم\n"
        "/active - الصفقات النشطة\n"
        "/help - المساعدة\n\n"

        "⚠️ التنبيهات تحليلية وليست تنفيذًا للصفقات."
    )


async def scan_command(update, context):

    await update.message.reply_text(
        "🔎 بدأ فحص الأسهم الأمريكية..."
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
        else "لم يتم الفحص"
    )

    await update.message.reply_text(
        "📊 حالة Aboghala Bot\n\n"

        "الحالة: يعمل\n"

        f"وقت نيويورك: {now_ny().strftime('%Y-%m-%d %H:%M:%S')}\n"

        f"آخر فحص: {last_scan}\n\n"

        f"حالة السوق: {market_regime}\n"

        f"{market_regime_details}\n\n"

        f"سعر الأسهم: {MIN_STOCK_PRICE}$ - {MAX_STOCK_PRICE}$\n"

        f"الحد الأدنى للتقييم: {MIN_SCORE}/10\n"

        f"العائد للمخاطرة: 1:{MIN_RISK_REWARD:.1f}\n\n"

        f"الفرص المرسلة اليوم: {daily_count}/{MAX_PER_DAY}\n"

        f"الصفقات النشطة: {len(active_trades)}\n"

        f"المشتركون: {len(subscribers)}\n"

        f"عدد الأسهم: {len(TICKERS)}\n\n"

        f"الفحص التلقائي: "
        f"{'نعم' if market_is_open_for_scanning() else 'لا'}\n"

        "الفحص: 09:40–15:45 نيويورك"
    )


async def report_command(update, context):

    if not last_scan_time:

        await update.message.reply_text(
            "لم يتم تنفيذ فحص حتى الآن."
        )

        return

    lines = [
        "📋 تقرير آخر فحص",
        f"الوقت: {last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"حالة السوق: {market_regime}",
        f"الفرص المرسلة اليوم: {daily_count}/{MAX_PER_DAY}",
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

        f"حالة السوق: {market_regime}",

        f"نطاق السعر: {MIN_STOCK_PRICE}$–{MAX_STOCK_PRICE}$",

        f"التقييم الأدنى: {MIN_SCORE}/10",

        f"العائد للمخاطرة: 1:{MIN_RISK_REWARD}",

        "الاستراتيجيات: سكالبينج، يومي، سوينغ",

        "ضغط الشراء والبيع: تقديري من بيانات الشموع",

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
            f"السعر الحالي المستهدف: ${trade['target1']:.2f}\n"
            f"الهدف الثاني: ${trade['target2']:.2f}\n"
            f"وقف الخسارة: ${trade['stop']:.2f}\n"
            f"التقييم: {trade['score']}/10\n"
            f"العائد للمخاطرة: 1:{trade['risk_reward']:.1f}"
        )

    await reply_long(
        update,
        "\n".join(lines)
    )


async def help_command(update, context):

    await update.message.reply_text(
        "/start - تفعيل التنبيهات\n"
        "/scan - فحص السوق\n"
        "/status - حالة البوت\n"
        "/report - تقرير الفحص\n"
        "/debug - تشخيص الأسهم\n"
        "/active - الصفقات النشطة\n"
        "/help - المساعدة"
    )


# =====================================================
# 19. بدء المهام
# =====================================================

async def post_init(application):

    application.create_task(
        scanner_loop(application)
    )

    logger.info(
        "تم تشغيل محرك الأسهم"
    )


# =====================================================
# 20. خادم Render
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
# 21. تشغيل البوت
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
