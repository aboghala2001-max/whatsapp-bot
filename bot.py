import os
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
from telegram import Bot
import asyncio

# ========== الاعدادات العامة ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# وقت العمل بتوقيت السعودية: من 5 عصراً الى 12 منتصف الليل
SAUDI_TZ = pytz.timezone("Asia/Riyadh")

# ========== قائمة الشركات - 40 شركة الأكثر حركة يومية - تحت 300$ ==========
WATCHLIST = [
    # تكنولوجيا وذكاء اصطناعي - الأكثر حركة
    "PLTR", "AMD", "MU", "HOOD", "SOFI", "INTC", "SNAP", "U",
    "DKNG", "AFRM", "UPST", "OPEN", "RBLX", "SHOP", "SQ", "PYPL",
    # سيارات وكهرباء
    "TSLA", "RIVN", "LCID", "NIO", "QS", "CHPT",
    # كريبتو وتعدين
    "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF",
    # سفر وطيران
    "CCL", "AAL", "UAL", "DAL", "NCLH",
    # طاقة وشركات كبيرة سريعة
    "ET", "F", "BAC", "WBD", "PARA", "CPNG", "NU"
]

# حد التنبيهات باليوم
MAX_ALERTS = {"strong": 3, "medium": 3, "bounce": 3}
last_sent = {}  # اخر مرة ارسلنا فيها السهم - عشان نسمح بالتكرار كل ساعة
alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
last_reset_day = None

async def send_msg(text):
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML")

def is_work_time():
    now = datetime.now(SAUDI_TZ)
    # يشتغل من 17:00 الى 23:59 بتوقيت السعودية
    return 17 <= now.hour <= 23

def reset_daily():
    global alerts_count, last_reset_day
    today = datetime.now(SAUDI_TZ).date()
    if last_reset_day != today:
        alerts_count = {"strong": 0, "medium": 0, "bounce": 0}
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
    except:
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

    # فلتر السعر: 5$ - 300$
    if not (5 <= price <= 300):
        return None

    # المتوسطات
    ema20_1h = float(df_1h["Close"].ewm(span=20).mean().iloc[-1])
    ema50_1h = float(df_1h["Close"].ewm(span=50).mean().iloc[-1])
    ema50_1d = float(df_1d["Close"].ewm(span=50).mean().iloc[-1])
    ema200_1d = float(df_1d["Close"].ewm(span=200).mean().iloc[-1])

    # RSI ساعة
    rsi_1h = float(calc_rsi(df_1h["Close"]).iloc[-1])

    # حجم التداول والسيولة
    vol_today = float(df_1h["Volume"].iloc[-1])
    vol_avg = float(df_1h["Volume"].tail(20).mean())
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0
    liquidity = price * vol_today * 6

    # الشمعة الاخيرة
    last_close = float(df_1h["Close"].iloc[-1])
    last_open = float(df_1h["Open"].iloc[-1])
    last_low = float(df_1h["Low"].iloc[-1])
    prev_high_1d = float(df_1d["High"].iloc[-2])
    open_today = float(df_1d["Open"].iloc[-1])

    # لا يرسل اذا صاعد اكثر من 8% اليوم
    day_change = ((last_close - open_today) / open_today) * 100
    if day_change > 8:
        return None

    # ===== 1. فرصة قوية 🔥 - اختراق =====
    if (liquidity > 50_000_000 and vol_ratio > 2.0 and
        price > ema20_1h and price > ema50_1h and
        50 <= rsi_1h <= 65 and last_close > prev_high_1d):
        target = price * 1.10
        stop = price * 0.95
        return ("strong", f"🔥 <b>فرصة قوية - {ticker}</b>\n"
                f"السعر: {price:.2f}$ | السيولة: {liquidity/1e6:.0f}M | الحجم: {vol_ratio:.1f}x\n"
                f"RSI: {rsi_1h:.0f}\n"
                f"الهدف: {target:.2f}$ (+10%) | الوقف: {stop:.2f}$ (-5%)")

    # ===== 2. فرصة متوسطة ⚡ - اختراق هادي =====
    if (10_000_000 <= liquidity <= 50_000_000 and vol_ratio > 1.3 and
        price > ema20_1h and 45 <= rsi_1h <= 68 and
        last_close >= prev_high_1d * 0.99):
        target = price * 1.06
        stop = price * 0.963
        return ("medium", f"⚡ <b>فرصة متوسطة - {ticker}</b>\n"
                f"السعر: {price:.2f}$ | السيولة: {liquidity/1e6:.0f}M | الحجم: {vol_ratio:.1f}x\n"
                f"RSI: {rsi_1h:.0f}\n"
                f"الهدف: {target:.2f}$ (+6%) | الوقف: {stop:.2f}$ (-3.7%)")

    # ===== 3. فرصة ارتداد 🛡️ - من الدعم =====
    near_support = abs(price - ema50_1d) / ema50_1d < 0.01 or abs(price - ema200_1d) / ema200_1d < 0.01
    low_20 = float(df_1d["Low"].tail(20).min())
    near_low = abs(price - low_20) / low_20 < 0.015
    is_green_after_support = last_close > last_open
    has_long_tail = (last_close - last_low) > (last_close - last_open) * 1.5 if (last_close - last_open) != 0 else False

    if (liquidity > 20_000_000 and vol_ratio > 1.5 and
        (near_support or near_low) and 30 <= rsi_1h <= 45 and
        (is_green_after_support or has_long_tail)):
        support = ema50_1d if abs(price - ema50_1d) < abs(price - ema200_1d) else ema200_1d
        if near_low and not near_support:
            support = low_20
        target = price * 1.053
        stop = price * 0.98
        return ("bounce", f"🛡️ <b>فرصة ارتداد من دعم - {ticker}</b>\n"
                f"السعر: {price:.2f}$ | الدعم: {support:.2f}$ | RSI: {rsi_1h:.0f}\n"
                f"السيولة: {liquidity/1e6:.0f}M | الحجم: {vol_ratio:.1f}x\n"
                f"الهدف: {target:.2f}$ (+5.3%) | الوقف: {stop:.2f}$ (-2%)")

    return None

async def main_loop():
    while True:
        reset_daily()
        if is_work_time():
            for ticker in WATCHLIST:
                # التكرار مسموح: اذا ارسلنا السهم قبل اقل من ساعة، نتخطاه
                # اذا مرت ساعة ودخلت سيولة جديدة، يرسل مرة ثانية
                if ticker in last_sent:
                    minutes_passed = (datetime.now(SAUDI_TZ) - last_sent[ticker]).total_seconds() / 60
                    if minutes_passed < 60:
                        continue

                result = check_stock(ticker)
                if result:
                    kind, msg = result
                    if alerts_count[kind] < MAX_ALERTS[kind]:
                        await send_msg(msg)
                        alerts_count[kind] += 1
                        last_sent[ticker] = datetime.now(SAUDI_TZ)
                        print(f"Sent {kind} {ticker}")
                await asyncio.sleep(1)
            print("انتهت الجولة، انتظار 5 دقايق...")
            await asyncio.sleep(300)
        else:
            print("خارج وقت العمل 5م - 12ص، انتظار...")
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main_loop())
