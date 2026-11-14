import yfinance as yf
import datetime
import pytz

# ========== 1-2-3-4: الاعدادات الاساسية ==========
TICKERS = ["NVDA","TSLA","META","AAPL","AMD","PLTR","COIN","MSFT","AMZN","GOOGL","NFLX","AVGO","MU","SMCI","ARM","SHOP","SQ","PYPL","UBER","ABNB","CRWD","PANW","MRVL","INTC","QCOM","TXN","ADBE","ORCL","CRM","BA","DIS","NKE","XOM"]
MIN_PRICE = 0.50
MAX_PRICE = 2.50
STOP_LOSS_PCT = -0.30
TARGET1_PCT = 0.50
TARGET2_PCT = 1.00

# ========== 13: الوقت الجديد - من 4:30 العصر الى 12:00 الليل ==========
def check_time():
    tz = pytz.timezone("Asia/Riyadh")
    now = datetime.datetime.now(tz)
    mins = now.hour * 60 + now.minute
    open_time = 16*60+30  # 4:30 العصر
    close_time = 24*60    # 12:00 الليل
    if now.weekday() >= 5:
        return False, "ويكند - السوق مسكر"
    if open_time <= mins < close_time:
        return True, "مفتوح"
    return False, "خارج وقت التداول"

# ========== 5-6-7-8-9-10-11: فحص الفرصة ==========
def check_signal(ticker):
    ok, msg = check_time()
    if not ok:
        return f"⏳ انتظار - {msg}"

    stock = yf.Ticker(ticker)
    df15 = stock.history(period="1d", interval="15m")
    df1h = stock.history(period="2d", interval="60m")
    df3d = stock.history(period="5d", interval="1d")
    if len(df15) < 2 or len(df1h) < 2 or len(df3d) < 3:
        return "⏳ انتظار - ما فيه بيانات كافية"

    price = df15['Close'].iloc[-1]
    mom15 = (df15['Close'].iloc[-1] - df15['Close'].iloc[-2]) / df15['Close'].iloc[-2] * 100
    mom1h = (df1h['Close'].iloc[-1] - df1h['Close'].iloc[-2]) / df1h['Close'].iloc[-2] * 100
    trend3d = (df3d['Close'].iloc[-1] - df3d['Close'].iloc[-3]) / df3d['Close'].iloc[-3] * 100

    # VWAP
    typical = (df15['High'] + df15['Low'] + df15['Close']) / 3
    vwap = (typical * df15['Volume']).sum() / df15['Volume'].sum()

    # SPY
    spy = yf.Ticker("SPY").history(period="1d", interval="15m")
    spy_mom = (spy['Close'].iloc[-1] - spy['Close'].iloc[-2]) / spy['Close'].iloc[-2] * 100

    # حجم التداول
    avg_vol = df15['Volume'].mean()
    last_vol = df15['Volume'].iloc[-1]
    vol_ok = last_vol >= avg_vol * 1.2

    # RSI
    delta = df15['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi_val = rsi.iloc[-1] if not rsi.isna().all() else 50

    # تحديد الاتجاه
    direction = None
    if mom15 >= 0.50 and mom1h > 0 and trend3d > 0 and price > vwap and spy_mom > 0 and vol_ok:
        direction = "CALL"
    elif mom15 <= -0.50 and mom1h < 0 and trend3d < 0 and price < vwap and spy_mom < 0 and vol_ok:
        direction = "PUT"
    else:
        return f"⏳ انتظار ⚠️\n{ticker} | سعر {price:.2f}$ | زخم 15د {mom15:.2f}% | VWAP {vwap:.2f}$ | SPY {spy_mom:.2f}% | RSI {rsi_val:.0f}"

    # البحث عن عقد 0.50 - 2.50
    # نبحث لحد 8% من سعر السهم
    return f"✅ {direction} {ticker}\nسعر السهم {price:.2f}$ | RSI {rsi_val:.0f} | زخم {mom15:.2f}%"

# مثال تشغيل
# print(check_signal("META"))
