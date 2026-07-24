# 🦅 بات تلگرام پشتیبانی

## قابلیت‌ها
- 🤖 چت با هوش مصنوعی (OpenRouter)
- 🎫 سیستم تیکت با اطلاع‌رسانی به ادمین
- 💳 پرداخت کریپتو (NowPayments)
- 🌐 دوزبانه فارسی/انگلیسی

---

## راه‌اندازی روی Railway

### ۱. متغیرهای محیطی (Environment Variables)

در Railway → Settings → Variables اینا رو اضافه کن:

| متغیر | مقدار |
|-------|-------|
| `BOT_TOKEN` | توکن بات از @BotFather |
| `ADMIN_CHAT_ID` | chat_id عددی ادمین |
| `OPENROUTER_KEY` | کلید API از openrouter.ai |
| `NOWPAYMENTS_KEY` | کلید API از nowpayments.io |

### ۲. پیدا کردن ADMIN_CHAT_ID
به @userinfobot در تلگرام پیام بده، ID عددی‌ات رو میده.

### ۳. Deploy روی Railway
```bash
# گیت‌هاب
git init
git add .
git commit -m "first commit"
git push

# بعد Railway رو به ریپو وصل کن
```

یا مستقیم با Railway CLI:
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

---

## ساختار فایل‌ها
```
bot.py           ← کد اصلی بات
requirements.txt ← پکیج‌های Python
Procfile         ← دستور اجرا برای Railway
railway.json     ← تنظیمات Railway
```

---

## دستورات ادمین
وقتی کاربر تیکت میفرسته، بات به ادمین پیام میده با دو دکمه:
- **↩️ پاسخ** — روش کلیک کن، بعد پیامت رو بنویس، به کاربر میرسه
- **✅ بستن** — تیکت بسته میشه و کاربر خبردار میشه
