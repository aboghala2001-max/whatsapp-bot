import os
import time
import requests
import threading

# ===== الإعدادات على حقك انت بس =====
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "")
FINNHUB_API_KEY = os.getenv("MARKETDATA_TOKEN", "")
LAST_CHAT_ID = 0

print("BOT_TOKEN found:", bool(TELEGRAM_TOKEN))
print("MARKETDATA_TOKEN found:", bool(FINNHUB_API_KEY))

# ===== إرسال تليجرام =====
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print("send error:", e)

def send_to_me(text):
    global LAST_CHAT_ID
    if LAST_CHAT_ID:
        send_telegram(LAST_CHAT_ID, text)
    else:
        print("No chat yet, skip:", text)

# ===== أسعار Finnhub =====
def get_price(symbol):
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=10).json()
        return r.get("c", 0)
    except Exception as e:
        print("price error:", e)
        return 0

def get_status_text():
    btc = get_price("BINANCE:BTCUSDT")
    eth = get_price("BINANCE:ETHUSDT")
    gold = get_price("OANDA:XAU_USD")
    return f"📊 الأسعار الحالية:\nBTC: ${btc}\nETH: ${eth}\nGOLD: ${gold}"

# ===== استقبال رسائل تليجرام =====
def poll_telegram():
    global LAST_CHAT_ID
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=20"
            r = requests.get(url, timeout=30).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                chat = msg.get("chat", {}).get("id")
                text = msg.get("text", "").strip().lower()

                if chat:
                    # هنا نحفظ رقمك تلقائيا، بدون MY_CHAT_ID
                    if LAST_CHAT_ID != chat:
                        LAST_CHAT_ID = chat
                        print("New chat saved:", chat)
                    if text == "/start":
                        send_telegram(chat, "✅ البوت شغال تمام\nارسل /status عشان تشوف الأسعار")
                    elif text == "/status":
                        send_telegram(chat, get_status_text())
                    elif text == "/price":
                        send_telegram(chat, get_status_text())
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)

# ===== تنبيهات كل 10 دقايق =====
def alerts_loop():
    while True:
        time.sleep(600)
        if LAST_CHAT_ID:
            try:
                send_to_me("⏰ تذكير:\n" + get_status_text())
            except Exception as e:
                print(e)

if __name__ == "__main__":
    print("Bot started...")
    threading.Thread(target=poll_telegram, daemon=True).start()
    threading.Thread(target=alerts_loop, daemon=True).start()
    while True:
        time.sleep(60)
