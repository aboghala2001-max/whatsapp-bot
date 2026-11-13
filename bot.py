async def auto_alert(context: ContextTypes.DEFAULT_TYPE):
    if not is_market_open():
        return
    for chat_id, symbols in list(WATCHLIST.items()):
        for sym in symbols:
            sig=get_signal(sym)
            if not sig: continue
            if sig['strength'] not in ["قوية", "متوسطة"]:
                continue
            key=f"{chat_id}_{sym}_{sig['option']}"
            last_price=ALERTED.get(key, 0)
            if abs(sig['price']-last_price) < sig['price']*0.002:
                continue
            ALERTED[key]=sig['price']
            msg=f"""🚨 تنبيه تلقائي - {sig['symbol']}

🎯 {sig['option']} - {sig['strength']}
💰 السعر: {sig['price']:.2f}$ ({sig['change_pct']:+.2f}%)
📊 RSI: {sig['rsi']:.0f} | VWAP: {sig['vwap']:.2f}$
⚡ زخم 15د: {sig['mom15']:+.2f}%
🟢 دعم: {sig['support']:.2f}$ | 🔴 مقاومة: {sig['resistance']:.2f}$
⏳ مدة الفرصة: {sig['duration']}

اكتب /s {sig['symbol']} للتفاصيل"""
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg)
            except: pass
