import os, asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("شغال ✅ ارسل /s")

async def s(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("وصلني امرك")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("s", s))
app.run_polling()
