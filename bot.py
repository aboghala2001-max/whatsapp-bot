import os, asyncio, threading
asyncio.set_event_loop(asyncio.new_event_loop())
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

app_web = Flask(__name__)
@app_web.route('/')
def home(): return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("شغال ✅ ارسل /s")

async def s(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("وصلني امرك")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("s", s))
app.run_polling()
