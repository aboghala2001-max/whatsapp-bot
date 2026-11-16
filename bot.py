async def auto_scan(app_bot):
    global DAILY_COUNT, SUMMARY_SENT_DATE
    last_light_check = 0
    while True:
        await asyncio.sleep(120)  # كل دقيقتين للقوية والمتوسطة
        if not AUTO_CHAT_ID: continue
        check_daily_reset()
        if is_end_of_day() and SUMMARY_SENT_DATE != date.today():
            SUMMARY_SENT_DATE = date.today()
            for t in list(ACTIVE.keys()):
                tr = ACTIVE[t]
                p = get_option_price(tr)
                if p is not None:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    res = "win" if pct > 0 else "lose"
                    results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": res})
                del ACTIVE[t]
            wins = sum(1 for r in results if r['result'] == "win")
            lines = []
            lines.append("🏁 ملخص نهاية الجلسة")
            lines.append(f"📊 فرص اليوم: {DAILY_COUNT}/{MAX_DAILY}")
            lines.append(f"✅ نجاح: {wins} | ❌ فشل: {len(results)-wins}")
            if results:
                lines.append("")
                for r in results:
                    icon = "✅" if r['result'] == "win" else "❌"
                    lines.append(f"{icon} {r['direction']} {r['ticker']} {int(r['strike'])} | {r['entry']:.2f}$ -> {r['exit']:.2f}$ ({r['pct']:+.0f}%)")
            else:
                lines.append("ما كان فيه فرص انتهت اليوم")
            txt = NL.join(lines)
            try: await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt)
            except: pass
            continue
        if not is_trading_hours(): continue
        if DAILY_COUNT >= MAX_DAILY: continue
        # الخفيفة كل 15 دقيقة فقط
        is_light_time = (time.time() - last_light_check) >= 900
        if is_light_time:
            last_light_check = time.time()
        try:
            load_market()
            for t in list(ACTIVE.keys()):
                tr = ACTIVE[t]
                p = get_option_price(tr)
                if p is None: continue
                if p >= tr['t2']:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "win"})
                    txt2 = NL.join([f"✅ نجاح كامل {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ (+{pct:.0f}%)", "🎯 وصل الهدف الثاني +100%، الفرصة انتهت"])
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    del ACTIVE[t]
                elif p >= tr['t1'] and not tr.get('t1_hit'):
                    tr['t1_hit'] = True
                    tr['stop'] = tr['entry']
                    txt2 = NL.join([f"🎯 هدف اول {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> الحين {p:.2f}$ (+50%)", f"⚠️ خذ جزء من ربحك، الوقف صار على الدخول، نكمل للهدف الثاني {tr['t2']:.2f}$ (+100%)"])
                    await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                elif p <= tr['stop']:
                    pct = (p - tr['entry']) / tr['entry'] * 100
                    if tr.get('t1_hit'):
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "win"})
                        txt2 = NL.join([f"✅ انتهت بربح {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ ({pct:+.0f}%)", "وصل الهدف الاول قبل، ورجع للوقف على الدخول، الفرصة انتهت"])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    else:
                        results.append({"ticker": t, "direction": tr['direction'], "strike": tr['strike'], "entry": tr['entry'], "exit": p, "pct": pct, "result": "lose"})
                        txt2 = NL.join([f"❌ فشل {tr['direction']} {t} {int(tr['strike'])}", f"💰 دخول {tr['entry']:.2f}$ -> خروج {p:.2f}$ ({pct:.0f}%)", "🛑 ضرب الوقف، الفرصة انتهت"])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                    del ACTIVE[t]
            for t in TICKERS:
                if DAILY_COUNT >= MAX_DAILY: break
                if t in ACTIVE:
                    # اذا السهم مسجل اصلا، ما نرسله مرة ثانية - بس نشيك انعكاس
                    trade, msg = check_signal(t)
                    if trade and ACTIVE[t]['direction'] != trade['direction']:
                        old = ACTIVE[t]
                        txt2 = NL.join([f"🔄 تنبيه انعكاس {t}", f"كان: {old['direction']} {int(old['strike'])} دخول {old['entry']:.2f}$", f"الحين: {trade['direction']} {int(trade['strike'])} دخول {trade['entry']:.2f}$", "⚠️ الفرصة الاولى انتهت"])
                        await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=txt2)
                        ACTIVE[t] = trade
                        DAILY_COUNT += 1
                    continue
                trade, msg = check_signal(t)
                if not trade: continue
                # منع التكرار: الخفيفة ما ترسل الا في وقتها
                if trade['strength'] == "خفيفة" and not is_light_time:
                    continue
                ACTIVE[t] = trade
                DAILY_COUNT += 1
                await app_bot.bot.send_message(chat_id=AUTO_CHAT_ID, text=msg + NL + f"📊 فرصة {DAILY_COUNT}/{MAX_DAILY} اليوم")
        except: pass
