import os
import logging
import asyncio
import aiohttp
import json
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)

# ─── Logging ───────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Config from environment ───────────────────────────────
BOT_TOKEN       = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID   = int(os.environ["ADMIN_CHAT_ID"])   # عدد
OPENROUTER_KEY  = os.environ["OPENROUTER_KEY"]
NOWPAYMENTS_KEY = os.environ["NOWPAYMENTS_KEY"]

OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
NOWPAYMENTS_URL = "https://api.nowpayments.io/v1"

# ─── States ────────────────────────────────────────────────
TICKET_MSG, PAY_AMOUNT, PAY_CURRENCY = range(3)

# ─── In-memory stores (برای Railway کافیه؛ با DB جایگزین کن) ──
tickets   = {}   # ticket_id -> {user_id, username, messages, status}
user_hist = {}   # user_id -> [{"role":..,"content":..}]
ticket_counter = 0

SYSTEM_PROMPT = """You are a helpful bilingual support assistant for a website.
- Detect the language of the user's message and always reply in the same language.
- If the user writes in Persian/Farsi, reply in Persian.
- If the user writes in English, reply in English.
- Be friendly, concise, and professional.
- If you cannot solve the issue, tell the user to open a support ticket.
- Do not make up information about the website."""

# ─── Keyboards ─────────────────────────────────────────────
def main_menu(lang="fa"):
    if lang == "fa":
        return ReplyKeyboardMarkup([
            ["🤖 پشتیبانی هوشمند", "🎫 ثبت تیکت"],
            ["💳 پرداخت کریپتو",   "📋 تیکت‌های من"],
            ["ℹ️ راهنما"]
        ], resize_keyboard=True)
    else:
        return ReplyKeyboardMarkup([
            ["🤖 AI Support",      "🎫 Open Ticket"],
            ["💳 Crypto Payment",  "📋 My Tickets"],
            ["ℹ️ Help"]
        ], resize_keyboard=True)

# ─── Language detect (simple) ──────────────────────────────
def is_persian(text: str) -> bool:
    return any("\u0600" <= c <= "\u06ff" for c in text)

# ─── /start ────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = "fa" if is_persian(user.first_name or "") else "en"
    
    if lang == "fa":
        text = (
            f"سلام {user.first_name} عزیز! 👋\n\n"
            "به ربات پشتیبانی خوش اومدی.\n"
            "از منو زیر یه گزینه انتخاب کن:"
        )
    else:
        text = (
            f"Hello {user.first_name}! 👋\n\n"
            "Welcome to our support bot.\n"
            "Choose an option from the menu below:"
        )
    
    await update.message.reply_text(text, reply_markup=main_menu(lang))

# ─── AI Chat ───────────────────────────────────────────────
async def ai_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # init history
    if user_id not in user_hist:
        user_hist[user_id] = []

    user_hist[user_id].append({"role": "user", "content": text})
    # keep last 10 turns
    if len(user_hist[user_id]) > 20:
        user_hist[user_id] = user_hist[user_id][-20:]

    thinking = await update.message.reply_text("⏳ در حال پردازش..." if is_persian(text) else "⏳ Thinking...")

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + user_hist[user_id],
                "max_tokens": 800,
                "temperature": 0.7
            }
            headers = {
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://support-bot.app",
                "X-Title": "Support Bot"
            }
            async with session.post(OPENROUTER_URL, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise Exception(f"OpenRouter error: {data}")
                reply = data["choices"][0]["message"]["content"]

        user_hist[user_id].append({"role": "assistant", "content": reply})

        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎫 ثبت تیکت", callback_data="open_ticket"),
            InlineKeyboardButton("🔄 سوال جدید", callback_data="clear_chat")
        ]])
        await thinking.edit_text(reply, reply_markup=kbd)

    except Exception as e:
        logger.error(f"AI error: {e}")
        err = "❌ خطا در ارتباط با هوش مصنوعی. لطفاً دوباره تلاش کن." if is_persian(text) \
              else "❌ AI error. Please try again."
        await thinking.edit_text(err)

# ─── Ticket System ─────────────────────────────────────────
async def start_ticket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = "fa"
    text = (
        "📝 پیام خود را بنویسید:\n"
        "(مشکل، سوال یا درخواست خود را کامل توضیح دهید)\n\n"
        "برای لغو: /cancel"
    )
    await update.message.reply_text(text)
    return TICKET_MSG

async def receive_ticket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global ticket_counter
    ticket_counter += 1
    tid = f"T{ticket_counter:04d}"
    user = update.effective_user
    msg = update.message.text

    tickets[tid] = {
        "user_id": user.id,
        "username": user.username or user.first_name,
        "messages": [{"from": "user", "text": msg, "time": datetime.now().isoformat()}],
        "status": "open",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    # notify admin
    admin_text = (
        f"🎫 تیکت جدید: #{tid}\n"
        f"👤 کاربر: @{user.username or 'N/A'} (ID: {user.id})\n"
        f"⏰ زمان: {tickets[tid]['created']}\n\n"
        f"📩 پیام:\n{msg}"
    )
    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"↩️ پاسخ به #{tid}", callback_data=f"reply_{tid}_{user.id}"),
        InlineKeyboardButton("✅ بستن تیکت",        callback_data=f"close_{tid}_{user.id}")
    ]])
    try:
        await ctx.bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kbd)
    except Exception as e:
        logger.error(f"Cannot notify admin: {e}")

    await update.message.reply_text(
        f"✅ تیکت شما با شماره *#{tid}* ثبت شد.\n"
        "پشتیبانی به زودی پاسخ می‌دهد.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def list_tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    my = [(tid, t) for tid, t in tickets.items() if t["user_id"] == user_id]

    if not my:
        await update.message.reply_text("📋 شما هیچ تیکتی ندارید.")
        return

    text = "📋 *تیکت‌های شما:*\n\n"
    for tid, t in my[-5:]:
        icon = "🟢" if t["status"] == "open" else "🔴"
        text += f"{icon} #{tid} — {t['created']}\n"
        if len(t["messages"]) > 1:
            text += f"   💬 {len(t['messages'])} پیام\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── NowPayments ───────────────────────────────────────────
async def start_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 *پرداخت کریپتو*\n\n"
        "مبلغ را به دلار وارد کنید:\n"
        "مثال: `25`\n\n/cancel برای لغو",
        parse_mode="Markdown"
    )
    return PAY_AMOUNT

async def receive_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
        ctx.user_data["pay_amount"] = amount
    except ValueError:
        await update.message.reply_text("❌ مبلغ نامعتبر. عدد وارد کنید.")
        return PAY_AMOUNT

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ Bitcoin (BTC)", callback_data="pay_btc"),
         InlineKeyboardButton("⟠ Ethereum (ETH)", callback_data="pay_eth")],
        [InlineKeyboardButton("🔷 Tether (USDT)", callback_data="pay_usdttrc20"),
         InlineKeyboardButton("🟡 BNB", callback_data="pay_bnbbsc")],
        [InlineKeyboardButton("❌ لغو", callback_data="pay_cancel")]
    ])
    await update.message.reply_text(
        f"💰 مبلغ: *${amount:.2f}*\n\nارز پرداختی را انتخاب کنید:",
        parse_mode="Markdown", reply_markup=kbd
    )
    return PAY_CURRENCY

async def create_invoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "pay_cancel":
        await query.edit_message_text("❌ پرداخت لغو شد.")
        return ConversationHandler.END

    currency = query.data.replace("pay_", "")
    amount   = ctx.user_data.get("pay_amount", 10)
    user     = query.from_user

    await query.edit_message_text("⏳ در حال ساخت فاکتور...")

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "price_amount":   amount,
                "price_currency": "usd",
                "pay_currency":   currency,
                "order_id":       f"order_{user.id}_{int(datetime.now().timestamp())}",
                "order_description": f"Support payment - User {user.id}",
                "ipn_callback_url": os.environ.get("WEBHOOK_URL", "")
            }
            headers = {
                "x-api-key":    NOWPAYMENTS_KEY,
                "Content-Type": "application/json"
            }
            async with session.post(
                f"{NOWPAYMENTS_URL}/payment", json=payload, headers=headers
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise Exception(f"NowPayments: {data}")

        pay_addr   = data.get("pay_address", "N/A")
        pay_amount = data.get("pay_amount", "N/A")
        pay_cur    = data.get("pay_currency", currency).upper()
        pay_id     = data.get("payment_id", "N/A")
        expires    = data.get("expiration_estimate_date", "")[:16] if data.get("expiration_estimate_date") else "N/A"

        text = (
            f"✅ *فاکتور ساخته شد*\n\n"
            f"🆔 شناسه: `{pay_id}`\n"
            f"💵 مبلغ دلاری: ${amount}\n"
            f"💰 مبلغ پرداختی: `{pay_amount}` {pay_cur}\n"
            f"📬 آدرس:\n`{pay_addr}`\n"
            f"⏰ انقضا: {expires}\n\n"
            f"⚠️ دقیقاً همین مبلغ را به آدرس بالا ارسال کنید."
        )
        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔍 چک وضعیت", callback_data=f"check_pay_{pay_id}"),
        ]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kbd)

        # notify admin
        await ctx.bot.send_message(
            ADMIN_CHAT_ID,
            f"💳 فاکتور جدید\n"
            f"👤 @{user.username or 'N/A'} (ID: {user.id})\n"
            f"💵 ${amount} → {pay_amount} {pay_cur}\n"
            f"🆔 {pay_id}"
        )

    except Exception as e:
        logger.error(f"NowPayments error: {e}")
        await query.edit_message_text(
            "❌ خطا در ساخت فاکتور. لطفاً دوباره تلاش کن.\n"
            f"جزئیات: `{str(e)[:100]}`",
            parse_mode="Markdown"
        )

    return ConversationHandler.END

async def check_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pay_id = query.data.replace("check_pay_", "")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"x-api-key": NOWPAYMENTS_KEY}
            async with session.get(
                f"{NOWPAYMENTS_URL}/payment/{pay_id}", headers=headers
            ) as resp:
                data = await resp.json()

        status = data.get("payment_status", "unknown")
        icons  = {"waiting":"⏳","confirming":"🔄","confirmed":"✅",
                  "sending":"📤","finished":"🎉","failed":"❌","expired":"⌛"}
        icon   = icons.get(status, "❓")

        text = (
            f"{icon} *وضعیت پرداخت*\n\n"
            f"🆔 ID: `{pay_id}`\n"
            f"📊 وضعیت: *{status}*\n"
        )
        if data.get("actually_paid"):
            text += f"✅ پرداخت شده: {data['actually_paid']} {data.get('pay_currency','').upper()}\n"

        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"check_pay_{pay_id}")
        ]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kbd)

    except Exception as e:
        logger.error(f"Check payment error: {e}")
        await query.answer("❌ خطا در دریافت وضعیت", show_alert=True)

# ─── Callback handler ──────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data

    if data == "open_ticket":
        await query.answer()
        await query.message.reply_text(
            "📝 پیام خود را بنویسید:\n/cancel برای لغو"
        )
        ctx.user_data["in_ticket"] = True

    elif data == "clear_chat":
        user_id = query.from_user.id
        user_hist.pop(user_id, None)
        await query.answer("🔄 تاریخچه پاک شد")
        await query.edit_message_text("✅ مکالمه جدید شروع شد. سوال خود را بپرسید:")

    elif data.startswith("reply_"):
        # admin replies to a ticket
        _, tid, uid = data.split("_", 2)
        ctx.user_data["reply_to"] = (tid, int(uid))
        await query.answer()
        await query.message.reply_text(
            f"✏️ پاسخ به تیکت #{tid}:\n(متن پاسخ را بنویسید)"
        )

    elif data.startswith("close_"):
        _, tid, uid = data.split("_", 2)
        if tid in tickets:
            tickets[tid]["status"] = "closed"
        await query.answer("✅ تیکت بسته شد")
        await query.edit_message_text(
            query.message.text + f"\n\n🔴 *بسته شد توسط ادمین*", parse_mode="Markdown"
        )
        try:
            await ctx.bot.send_message(int(uid), f"🔴 تیکت #{tid} بسته شد.")
        except Exception:
            pass

    elif data.startswith("check_pay_"):
        await check_payment(update, ctx)

# ─── Admin reply handler ────────────────────────────────────
async def admin_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    reply_info = ctx.user_data.get("reply_to")
    if not reply_info:
        return
    tid, uid = reply_info
    msg = update.message.text
    if tid in tickets:
        tickets[tid]["messages"].append({
            "from": "admin", "text": msg, "time": datetime.now().isoformat()
        })
    try:
        await ctx.bot.send_message(
            uid,
            f"📩 *پاسخ پشتیبانی — تیکت #{tid}:*\n\n{msg}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ پاسخ به تیکت #{tid} ارسال شد.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ارسال: {e}")
    ctx.user_data.pop("reply_to", None)

# ─── Help ──────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *راهنما:*\n\n"
        "🤖 *پشتیبانی هوشمند* — چت با هوش مصنوعی\n"
        "🎫 *ثبت تیکت* — ارسال مشکل به تیم پشتیبانی\n"
        "💳 *پرداخت کریپتو* — پرداخت با ارز دیجیتال\n"
        "📋 *تیکت‌های من* — مشاهده تیکت‌های ثبت شده\n\n"
        "دستورات:\n"
        "/start — شروع مجدد\n"
        "/help — راهنما\n"
        "/cancel — لغو عملیات جاری"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END

# ─── Text router ───────────────────────────────────────────
async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid  = update.effective_user.id

    # admin reply mode
    if uid == ADMIN_CHAT_ID and ctx.user_data.get("reply_to"):
        await admin_reply(update, ctx)
        return

    # menu buttons
    if text in ["🤖 پشتیبانی هوشمند", "🤖 AI Support"]:
        lang = "fa" if "پشتیبانی" in text else "en"
        msg = "سوال خود را بنویسید:" if lang=="fa" else "Ask your question:"
        await update.message.reply_text(msg)
        ctx.user_data["mode"] = "ai"
        return

    if text in ["🎫 ثبت تیکت", "🎫 Open Ticket"]:
        await start_ticket(update, ctx)
        return

    if text in ["💳 پرداخت کریپتو", "💳 Crypto Payment"]:
        await start_payment(update, ctx)
        return

    if text in ["📋 تیکت‌های من", "📋 My Tickets"]:
        await list_tickets(update, ctx)
        return

    if text in ["ℹ️ راهنما", "ℹ️ Help"]:
        await cmd_help(update, ctx)
        return

    # AI chat
    await ai_chat(update, ctx)

# ─── Main ──────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Ticket conversation
    ticket_conv = ConversationHandler(
        entry_points=[],
        states={TICKET_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ticket)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False
    )

    # Payment conversation
    payment_conv = ConversationHandler(
        entry_points=[],
        states={
            PAY_AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_amount)],
            PAY_CURRENCY: [CallbackQueryHandler(create_invoice, pattern="^pay_")]
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("✅ Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
