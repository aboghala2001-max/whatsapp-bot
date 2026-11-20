import os
import asyncio
import logging
import threading
from datetime import datetime, time, timedelta
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

# قائمة 30 سهمًا
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
daily_count = 0
current_day = None

last_scan_time = None
last_scan_summary = {}
last_scan_errors = []

scan_lock = asyncio.Lock()


def now_ny():
    return datetime.now(TZ)


def reset_daily_data_if_needed():

    global current_day, daily_count, sent_today

    today = now_ny().date()

    if current_day != today:

        current_day = today
        daily_count = 0
        sent_today = set()

        logger.info("تم تصفير عداد الفرص لليوم الجديد")


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
# 3. حساب المؤشرات الفنية
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
# 4. جلب الشموع وتجهيز المؤشرات
# =====================================================

def prepare_candles(ticker, interval):

    stock = yf.Ticker(ticker)

    df = stock.history(
        period="5d",
        interval=interval,
        auto_adjust=False,
        prepost=False
    )

    if df is None or df.empty:

        raise ValueError(
            "لم تصل بيانات الشموع من Yahoo Finance"
        )

    df = df.copy()

    if df.index.tz is None:
        df.index = df.index.tz_localize(TZ)
    else:
        df.index = df.index.tz_convert(TZ)

    now = now_ny()

    minutes = 5 if interval == "5m" else 15

    # الاحتفاظ بالشموع المكتملة فقط
    candle_close_times = (
        df.index + timedelta(minutes=minutes)
    )

    df = df.loc[
        candle_close_times <= now - timedelta(seconds=30)
    ]

    if df.empty:

        raise ValueError("لا توجد شموع مكتملة")

    # المتوسط المتحرك
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
    df["Volume_Avg"] = df["Volume"].rolling(20).mean()

    # معالجة القيم الصفرية
    df["Volume_Ratio"] = (
        df["Volume"] /
        df["Volume_Avg"].replace(0, float("nan"))
    )

    # بيانات اليوم الحالي لحساب VWAP
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

    volume_sum = today_df["Volume"].sum()

    if volume_sum <= 0:

        raise ValueError(
            "حجم التداول غير متوفر لحساب VWAP"
        )

    today_df["VWAP"] = (
        (typical_price * today_df["Volume"]).cumsum()
        / today_df["Volume"].cumsum()
    )

    return df, today_df


# =====================================================
# 5. تحليل السهم وتصنيف الفرص
# =====================================================

def analyze_ticker(ticker):

    result = {
        "ticker": ticker,
        "signal": None,
        "grade": "لا دخول",
        "reason": "",
        "details": ""
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

        vwap = today5.iloc[-1]["VWAP"]

        close5 = float(c5["Close"])
        previous_close5 = float(previous_c5["Close"])

        ema5 = float(c5["EMA20"])
        rsi5 = float(c5["RSI"])
        volume_ratio = float(c5["Volume_Ratio"])

        close15 = float(c15["Close"])
        ema15 = float(c15["EMA20"])

        vwap = float(vwap)

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

        if any(pd.isna(x) for x in values):

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

        # -------------------------------------------------
        # شروط الاتجاه الأساسية
        # -------------------------------------------------

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

        # -------------------------------------------------
        # شروط CALL الأصلية
        # -------------------------------------------------

        call_original = (
            call_core
            and 52 <= rsi5 <= 72
        )

        # -------------------------------------------------
        # CALL استمرار الصعود
        # RSI أعلى من 72 مع تأكيد شمعة صاعدة
        # -------------------------------------------------

        call_continuation = (
            call_core
            and rsi5 > 72
            and close5 > previous_close5
        )

        # -------------------------------------------------
        # شروط PUT الأصلية
        # -------------------------------------------------

        put_original = (
            put_core
            and 28 <= rsi5 <= 48
        )

        # -------------------------------------------------
        # PUT استمرار الهبوط
        # RSI أقل من 28 مع تأكيد شمعة هابطة
        # -------------------------------------------------

        put_continuation = (
            put_core
            and rsi5 < 28
            and close5 < previous_close5
        )

        # =================================================
        # تصنيف الفرص القوية
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
        # تصنيف الفرص المتوسطة
        # توافق الاتجاه والحجم دون اكتمال RSI
        # =================================================

        if call_core:

            result["grade"] = "متوسط"

            result["reason"] = (
                "🟡 CALL متوسط: الاتجاه والحجم متوافقان "
                "لكن شرط RSI أو تأكيد الاستمرار غير مكتمل.\n"
                + details
            )

            return result

        if put_core:

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

        result["grade"] = "لا دخول"

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

    except Exception as e:

        result["reason"] = (
            f"خطأ في التحليل: {str(e)[:150]}"
        )

        return result


# =====================================================
# 6. البحث عن عقود 0DTE
# =====================================================

def get_0dte_contract(ticker, signal):

    stock = yf.Ticker(ticker)

    today_string = now_ny().date().isoformat()

    expirations = stock.options

    if (
        not expirations
        or today_string not in expirations
    ):

        raise ValueError(
            "لا يوجد تاريخ انتهاء اليوم لهذا السهم"
        )

    chain = stock.option_chain(today_string)

    options = (
        chain.calls
        if signal == "CALL"
        else chain.puts
    )

    if options is None or options.empty:

        raise ValueError(
            "سلسلة العقود فارغة"
        )

    hist = stock.history(
        period="1d",
        interval="5m"
    )

    if hist is None or hist.empty:

        raise ValueError(
            "تعذر الحصول على سعر السهم"
        )

    underlying_price = float(
        hist["Close"].dropna().iloc[-1]
    )

    options = options.copy()

    required_columns = [
        "strike",
        "bid",
        "ask",
        "volume",
        "openInterest"
    ]

    for col in required_columns:

        if col not in options.columns:

            raise ValueError(
                f"بيانات العقد ناقصة: {col}"
            )

    options = options.dropna(
        subset=required_columns
    )

    # شروط السيولة والسعر
    options = options[
        (options["bid"] > 0)
        & (options["ask"] > 0)
        & (options["ask"] >= MIN_OPTION_PRICE)
        & (options["openInterest"] >= MIN_OPEN_INTEREST)
        & (options["volume"] >= MIN_OPTION_VOLUME)
    ].copy()

    if options.empty:

        raise ValueError(
            "لا توجد عقود تحقق شروط السيولة والسعر"
        )

    # حساب فرق السعر نسبة إلى منتصف Bid و Ask
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
            "العقود المتاحة فروق أسعارها مرتفعة"
        )

    # اختيار أقرب Strike للسعر الحالي
    options["distance"] = (
        options["strike"] - underlying_price
    ).abs()

    options = options.sort_values(
        by=["distance", "spread_pct"],
        ascending=[True, True]
    )

    contract = options.iloc[0]

    return {

        "symbol": ticker,

        "type": signal,

        "contract": str(
            contract["contractSymbol"]
        ),

        "strike": float(
            contract["strike"]
        ),

        "expiration": today_string,

        "bid": float(
            contract["bid"]
        ),

        "ask": float(
            contract["ask"]
        ),

        "entry": float(
            contract["ask"]
        ),

        "spread": float(
            contract["spread_pct"]
        ),

        "volume": int(
            contract["volume"]
        ),

        "open_interest": int(
            contract["openInterest"]
        ),

        "underlying": underlying_price
    }


# =====================================================
# 7. متابعة أسعار العقود
# =====================================================

def get_contract_quote(contract_symbol):

    try:

        ticker = yf.Ticker(contract_symbol)

        hist = ticker.history(
            period="1d",
            interval="1m"
        )

        if hist is None or hist.empty:
            return None

        price = float(
            hist["Close"].dropna().iloc[-1]
        )

        if price <= 0:
            return None

        return price

    except Exception:

        logger.exception(
            "خطأ في جلب سعر العقد"
        )

        return None


# =====================================================
# 8. إرسال التنبيهات
# =====================================================

def trade_message(trade):

    return (

        "🚨 فرصة أوبشن جديدة\n\n"

        f"السهم: {trade['symbol']}\n"

        f"النوع: {trade['type']}\n"

        f"قوة الفرصة: {trade.get('grade', 'قوي')} 🟢\n"

        f"العقد: {trade['contract']}\n"

        f"Strike: {trade['strike']:.2f}\n"

        f"الانتهاء: {trade['expiration']} (0DTE)\n\n"

        f"سعر الدخول التقريبي: ${trade['entry']:.2f}\n"

        f"Bid: ${trade['bid']:.2f}\n"

        f"Ask: ${trade['ask']:.2f}\n"

        f"فرق السعر: {trade['spread']:.1f}%\n"

        f"حجم العقد: {trade['volume']}\n"

        f"Open Interest: {trade['open_interest']}\n\n"

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
                text=message
            )

        except Exception:

            logger.exception(
                "تعذر إرسال رسالة للمشترك %s",
                chat_id
            )


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

        "opened_at": now_ny().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "tp1_sent": False,

        "tp2_sent": False
    }

    active_trades[ticker] = trade

    sent_today.add(ticker)

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
# 9. مراقبة الأهداف ووقف الخسارة
# =====================================================

async def monitor_trades(application):

    if not active_trades:
        return

    now = now_ny()

    for ticker in list(active_trades.keys()):

        trade = active_trades.get(ticker)

        if not trade:
            continue

        if now.time() >= CLOSE_ALL_TIME:

            await send_to_subscribers(
                application,

                f"⏰ انتهت متابعة {ticker}\n"
                "يرجى التحقق من العقد يدويًا."
            )

            active_trades.pop(ticker, None)

            continue

        price = await asyncio.to_thread(
            get_contract_quote,
            trade["contract"]
        )

        if price is None:
            continue

        entry = trade["entry"]

        if entry <= 0:
            continue

        change = (price - entry) / entry

        # وقف الخسارة
        if change <= STOP_LOSS:

            await send_to_subscribers(
                application,

                "🛑 تنبيه وقف الخسارة\n"

                f"{ticker} | {trade['type']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

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

                "🎯 تحقق الهدف الثاني\n"

                f"{ticker} | {trade['type']}\n"

                f"سعر الدخول: ${entry:.2f}\n"

                f"السعر الحالي: ${price:.2f}\n"

                f"الربح التقريبي: {change * 100:.1f}%"
            )

        # الهدف الأول
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

                f"الربح التقريبي: {change * 100:.1f}%"
            )


# =====================================================
# 10. محرك الفحص
# =====================================================

async def run_scan(application, manual=False):

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

                "السوق خارج وقت البحث المحدد.\n"

                f"وقت نيويورك الآن: "
                f"{now_ny().strftime('%H:%M:%S')}\n"

                "وقت البحث: 09:40 إلى 15:30 "
                "بتوقيت نيويورك."
            )

            last_scan_summary = {}

            last_scan_errors = [message]

            return message

        if daily_count >= MAX_PER_DAY:

            return (
                "وصل البوت إلى الحد الأقصى: "
                "10 فرص اليوم."
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
                    "لديه صفقة متابعة نشطة"
                )

                continue

            # تحليل السهم
            analysis = await asyncio.to_thread(
                analyze_ticker,
                ticker
            )

            # -------------------------------------------------
            # الفرص المتوسطة أو التي لا تحقق شروط الدخول
            # لا يتم إرسال تنبيه لها
            # -------------------------------------------------

            if not analysis["signal"]:

                summary[ticker] = (
                    f"{analysis['grade']} | "
                    f"{analysis['reason']}"
                )

                continue

            # -------------------------------------------------
            # لا نرسل إلا الفرص القوية
            # -------------------------------------------------

            if analysis["grade"] != "قوي":

                summary[ticker] = (
                    f"لم يتم الإرسال: {analysis['grade']} | "
                    f"{analysis['reason']}"
                )

                continue

            # البحث عن العقد
            try:

                contract = await asyncio.to_thread(
                    get_0dte_contract,
                    ticker,
                    analysis["signal"]
                )

                contract["grade"] = analysis["grade"]

                sent = await open_trade(
                    application,
                    ticker,
                    analysis["signal"],
                    contract
                )

                if sent:

                    summary[ticker] = (

                        f"🟢 تم إرسال {analysis['signal']} قوي | "

                        f"{analysis['details']}"
                    )

                else:

                    summary[ticker] = (
                        "لم يتم إرسال الفرصة"
                    )

            except Exception as e:

                reason = (
                    f"فشل عقد 0DTE: {str(e)[:150]}"
                )

                summary[ticker] = reason

                errors.append(
                    f"{ticker}: {reason}"
                )

        last_scan_summary = summary

        last_scan_errors = errors

        report = (

            f"انتهى الفحص: "
            f"{now_ny().strftime('%H:%M:%S')} نيويورك\n"

            f"عدد الفرص المرسلة اليوم: "
            f"{daily_count}/{MAX_PER_DAY}\n"

            f"عدد الأسهم التي تم فحصها: "
            f"{len(summary)}\n"

            f"الصفقات النشطة: "
            f"{len(active_trades)}"
        )

        logger.info(report)

        return report


# =====================================================
# 11. الفحص التلقائي
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
# 12. أوامر تيليجرام
# =====================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    subscribers.add(chat_id)

    await update.message.reply_text(

        "✅ تم تفعيل اشتراكك في Aboghala Bot.\n\n"

        "البوت يبحث عن عقود 0DTE فقط عند توفرها.\n"

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
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔎 بدأ الفحص اليدوي الآن. قد يستغرق بعض الوقت..."
    )

    result = await run_scan(
        context.application,
        manual=True
    )

    await update.message.reply_text(result)


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

    await update.message.reply_text(

        "📊 حالة Aboghala Bot\n\n"

        "الحالة: يعمل\n"

        f"وقت نيويورك: "
        f"{now_ny().strftime('%Y-%m-%d %H:%M:%S')}\n"

        f"آخر فحص: {last_scan}\n"

        f"الفرص المرسلة اليوم: "
        f"{daily_count}/{MAX_PER_DAY}\n"

        f"الصفقات النشطة: "
        f"{len(active_trades)}\n"

        f"المشتركون: {len(subscribers)}\n"

        f"عدد الأسهم: {len(TICKERS)}\n"

        f"السوق ضمن وقت البحث: "
        f"{'نعم' if market_is_open_for_scanning() else 'لا'}"
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

        f"الوقت: {last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}",

        f"الفرص اليوم: {daily_count}/{MAX_PER_DAY}",

        ""
    ]

    for ticker, result in last_scan_summary.items():

        lines.append(
            f"{ticker}: {result}"
        )

    text = "\n".join(lines)

    for i in range(0, len(text), 3500):

        await update.message.reply_text(
            text[i:i + 3500]
        )


async def debug_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not last_scan_summary:

        await update.message.reply_text(
            "لا توجد تفاصيل فحص حتى الآن. استخدم /scan أولًا."
        )

        return

    lines = [
        "🔬 تشخيص الأسهم\n",
        "🟢 قوي: يرسل تنبيهًا عند توفر عقد مناسب.",
        "🟡 متوسط: يظهر هنا فقط ولا يرسل تنبيهًا.",
        "🔴 لا دخول: الشروط الأساسية غير مكتملة."
    ]

    for ticker, reason in last_scan_summary.items():

        lines.append(
            f"\n{ticker}: {reason}"
        )

    text = "\n".join(lines)

    for i in range(0, len(text), 3500):

        await update.message.reply_text(
            text[i:i + 3500]
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

            f"الدخول: ${trade['entry']:.2f}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "/start - تفعيل التنبيهات\n"

        "/scan - فحص فوري للسوق\n"

        "/status - حالة البوت\n"

        "/report - تقرير الفحص\n"

        "/debug - تشخيص أسباب رفض الأسهم وتصنيفها\n"

        "/active - الصفقات النشطة\n"

        "/help - المساعدة"
    )


# =====================================================
# 13. بدء المهام
# =====================================================

async def post_init(application):

    application.create_task(
        scanner_loop(application)
    )


# =====================================================
# 14. خادم Render
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
# 15. تشغيل البوت
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
