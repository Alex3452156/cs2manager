"""
CS2 Manager Bot
Подписка + рефералы + CryptoBot + Supabase

pip install python-telegram-bot==20.7 supabase python-dotenv aiohttp
"""
import os, logging, asyncio, aiohttp
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from supabase import create_client

load_dotenv()

BOT_TOKEN    = os.getenv("BOT_TOKEN")
CRYPTO_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID     = int(os.getenv("ADMIN_ID", "0"))
APP_URL      = os.getenv("APP_URL", "https://example.com")
REF_PERCENT  = int(os.getenv("REF_PERCENT", "20"))
SUPPORT_URL  = os.getenv("SUPPORT_URL", "https://t.me/your_support")

PLANS = {
    "30":  {"days": 30,  "price": 3.00,  "label": "30 дней — $3.00"},
    "90":  {"days": 90,  "price": 7.00,  "label": "90 дней — $7.00 (экономия 22%)"},
    "180": {"days": 180, "price": 12.00, "label": "180 дней — $12.00 (экономия 33%)"},
    "360": {"days": 360, "price": 20.00, "label": "365 дней — $20.00 (экономия 44%)"},
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── DB ──────────────────────────────────────────────────────
def db_get_user(tg_id):
    r = sb.table("users").select("*").eq("tg_id", tg_id).execute()
    return r.data[0] if r.data else None

def db_create_user(tg_id, username, ref_by=None):
    data = {
        "tg_id": tg_id,
        "username": username or str(tg_id),
        "ref_code": f"ref{tg_id}",
        "ref_by": ref_by,
        "balance": 0.0,
        "sub_until": None,
        "created_at": datetime.utcnow().isoformat()
    }
    r = sb.table("users").insert(data).execute()
    return r.data[0]

def db_get_by_refcode(code):
    r = sb.table("users").select("*").eq("ref_code", code).execute()
    return r.data[0] if r.data else None

def db_activate_sub(tg_id, days):
    user = db_get_user(tg_id)
    now = datetime.utcnow()
    if user and user.get("sub_until"):
        try:
            cur = datetime.fromisoformat(user["sub_until"])
            until = (cur if cur > now else now) + timedelta(days=days)
        except:
            until = now + timedelta(days=days)
    else:
        until = now + timedelta(days=days)
    sb.table("users").update({"sub_until": until.isoformat()}).eq("tg_id", tg_id).execute()
    return until

def db_add_balance(tg_id, amount):
    user = db_get_user(tg_id)
    if user:
        new_bal = round((user.get("balance") or 0) + amount, 4)
        sb.table("users").update({"balance": new_bal}).eq("tg_id", tg_id).execute()

def db_log_payment(tg_id, plan, amount, invoice_id, ref_bonus=0):
    sb.table("payments").insert({
        "tg_id": tg_id, "plan": plan, "amount": amount,
        "invoice_id": str(invoice_id), "ref_bonus": ref_bonus,
        "paid_at": datetime.utcnow().isoformat()
    }).execute()

def db_invoice_exists(invoice_id):
    r = sb.table("invoices").select("status").eq("invoice_id", invoice_id).eq("status", "paid").execute()
    return bool(r.data)

def is_active(user):
    if not user or not user.get("sub_until"):
        return False
    try:
        return datetime.fromisoformat(user["sub_until"]) > datetime.utcnow()
    except:
        return False

def time_left(user):
    if not user or not user.get("sub_until"):
        return "нет подписки"
    try:
        delta = datetime.fromisoformat(user["sub_until"]) - datetime.utcnow()
        if delta.total_seconds() <= 0:
            return "истекла"
        d, h = delta.days, delta.seconds // 3600
        return f"{d} дн. {h} ч." if d > 0 else f"{h} ч."
    except:
        return "—"

# ─── CRYPTOBOT ───────────────────────────────────────────────
async def create_invoice(amount, payload, desc):
    async with aiohttp.ClientSession() as s:
        h = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
        d = {
            "currency_type": "fiat", "fiat": "USD",
            "accepted_assets": "USDT,TON,BTC",
            "amount": str(amount), "payload": payload,
            "description": desc, "expires_in": 3600
        }
        async with s.post("https://pay.crypt.bot/api/createInvoice", json=d, headers=h) as r:
            res = await r.json()
            return res["result"] if res.get("ok") else None

async def check_invoice(invoice_id):
    async with aiohttp.ClientSession() as s:
        h = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
        async with s.get("https://pay.crypt.bot/api/getInvoices",
                         params={"invoice_ids": invoice_id}, headers=h) as r:
            res = await r.json()
            items = res.get("result", {}).get("items", [])
            return items[0] if items else None

# ─── KEYBOARDS ───────────────────────────────────────────────
def kb_main(active_sub):
    rows = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("💳 Купить подписку", callback_data="buy")],
    ]
    if active_sub:
        rows.insert(0, [InlineKeyboardButton(
            "🎮 Открыть CS2 Manager",
            web_app=WebAppInfo(url=APP_URL)
        )])
    rows.append([InlineKeyboardButton("🆘 Поддержка", url=SUPPORT_URL)])
    return InlineKeyboardMarkup(rows)

def kb_plans():
    rows = [[InlineKeyboardButton(p["label"], callback_data=f"buy_{k}")] for k, p in PLANS.items()]
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(rows)

def kb_back(cb="main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=cb)]])

# ─── HANDLERS ────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    name = update.effective_user.first_name or str(tg_id)
    uname = update.effective_user.username or str(tg_id)

    # Реферал из аргумента
    ref_by = None
    if ctx.args:
        ref = db_get_by_refcode(ctx.args[0])
        if ref and ref["tg_id"] != tg_id:
            ref_by = ref["tg_id"]

    user = db_get_user(tg_id)
    if not user:
        user = db_create_user(tg_id, uname, ref_by)

    active = is_active(user)

    if active:
        text = (
            f"👋 Привет, *{name}*!\n\n"
            f"✅ Подписка активна — *{time_left(user)}*\n\n"
            f"Нажми кнопку ниже чтобы открыть приложение 🎮"
        )
    else:
        text = (
            f"👋 Привет, *{name}*!\n\n"
            f"🎮 *CS2 Manager* — учёт CS2 фермы\n\n"
            f"❌ Подписка неактивна\n\n"
            f"Купи подписку чтобы получить доступ к приложению."
        )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_main(active))


async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = q.from_user.id
    name = q.from_user.first_name or str(tg_id)
    data = q.data

    # Главное меню
    if data == "main":
        user = db_get_user(tg_id)
        active = is_active(user) if user else False
        text = (
            f"👋 *{name}*\n\n"
            f"{'✅ Подписка активна — *'+time_left(user)+'*' if active else '❌ Подписка неактивна'}\n\n"
            f"{'Нажми Открыть чтобы войти в приложение 🎮' if active else 'Купи подписку для доступа.'}"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_main(active))

    # Профиль
    elif data == "profile":
        user = db_get_user(tg_id)
        if not user:
            user = db_create_user(tg_id, str(tg_id))

        active = is_active(user)
        ref_code = user.get("ref_code", f"ref{tg_id}")
        bot_username = (await ctx.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={ref_code}"

        refs = sb.table("users").select("tg_id", count="exact").eq("ref_by", tg_id).execute()
        ref_count = refs.count or 0
        balance = user.get("balance") or 0.0

        text = (
            f"👤 *Профиль*\n\n"
            f"🆔 ID: `{tg_id}`\n"
            f"📅 Подписка: {'✅ '+time_left(user) if active else '❌ Неактивна'}\n\n"
            f"💰 *Баланс*: ${balance:.2f}\n"
            f"_(начисляется с покупок рефералов)_\n\n"
            f"👥 *Рефералы*: {ref_count} чел.\n"
            f"💸 Бонус: *{REF_PERCENT}%* с каждой покупки\n\n"
            f"🔗 *Твоя ссылка:*\n"
            f"`{ref_link}`"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Поделиться ссылкой",
             url=f"https://t.me/share/url?url={ref_link}&text=CS2%20Manager%20-%20учёт%20CS2%20фермы")],
            [InlineKeyboardButton("◀️ Назад", callback_data="main")]
        ])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    # Меню покупки
    elif data == "buy":
        text = (
            f"💳 *Купить подписку*\n\n"
            f"Выбери тариф:\n\n"
            f"• 30 дней — *$3.00*\n"
            f"• 90 дней — *$7.00* _(−22%)_\n"
            f"• 180 дней — *$12.00* _(−33%)_\n"
            f"• 365 дней — *$20.00* _(−44%)_\n\n"
            f"_Оплата: USDT, TON, BTC через @CryptoBot_"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_plans())

    # Создать счёт
    elif data.startswith("buy_"):
        plan_key = data[4:]
        plan = PLANS.get(plan_key)
        if not plan:
            return

        await q.edit_message_text("⏳ Создаю счёт...", parse_mode="Markdown")

        payload = f"{tg_id}:{plan_key}:{int(datetime.utcnow().timestamp())}"
        invoice = await create_invoice(
            plan["price"], payload,
            f"CS2 Manager — {plan['days']} дней"
        )

        if not invoice:
            await q.edit_message_text(
                "❌ Ошибка создания счёта.\nПопробуй позже или обратись в поддержку.",
                reply_markup=kb_back("buy")
            )
            return

        # Сохраняем счёт
        sb.table("invoices").insert({
            "invoice_id": str(invoice["invoice_id"]),
            "tg_id": tg_id, "plan": plan_key,
            "amount": plan["price"], "payload": payload,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Перейти к оплате", url=invoice["bot_invoice_url"])],
            [InlineKeyboardButton("✅ Я оплатил — проверить", callback_data=f"check_{invoice['invoice_id']}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="buy")]
        ])
        text = (
            f"💳 *Счёт создан*\n\n"
            f"Тариф: *{plan['label']}*\n\n"
            f"*Как оплатить:*\n"
            f"1. Нажми *Перейти к оплате*\n"
            f"2. Оплати в @CryptoBot\n"
            f"3. Вернись и нажми *Я оплатил*\n\n"
            f"_Счёт действителен 1 час_"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    # Проверить оплату
    elif data.startswith("check_"):
        invoice_id = data[6:]

        # Уже активировали?
        if db_invoice_exists(invoice_id):
            await q.answer("✅ Подписка уже активирована!", show_alert=True)
            user = db_get_user(tg_id)
            await q.edit_message_text(
                f"✅ *Подписка активна!*\n\nОсталось: *{time_left(user)}*",
                parse_mode="Markdown",
                reply_markup=kb_main(True)
            )
            return

        invoice = await check_invoice(invoice_id)

        if not invoice or invoice["status"] != "paid":
            await q.answer("⏳ Оплата не найдена. Оплати счёт и попробуй снова.", show_alert=True)
            return

        # Парсим payload
        try:
            parts = invoice["payload"].split(":")
            pay_tg_id = int(parts[0])
            plan_key = parts[1]
            plan = PLANS[plan_key]
        except:
            await q.answer("❌ Ошибка обработки платежа", show_alert=True)
            return

        # Активируем подписку
        until = db_activate_sub(pay_tg_id, plan["days"])
        sb.table("invoices").update({"status": "paid"}).eq("invoice_id", invoice_id).execute()

        # Реферальный бонус
        user = db_get_user(pay_tg_id)
        ref_bonus = 0.0
        if user and user.get("ref_by"):
            ref_bonus = round(plan["price"] * REF_PERCENT / 100, 4)
            db_add_balance(user["ref_by"], ref_bonus)
            try:
                await ctx.bot.send_message(
                    user["ref_by"],
                    f"🎉 *Твой реферал купил подписку!*\n\n"
                    f"💰 Начислено: *${ref_bonus:.2f}*\n"
                    f"💼 Твой баланс обновлён.",
                    parse_mode="Markdown"
                )
            except:
                pass

        db_log_payment(pay_tg_id, plan_key, plan["price"], invoice_id, ref_bonus)

        text = (
            f"🎉 *Подписка активирована!*\n\n"
            f"Тариф: *{plan['label']}*\n"
            f"Активна до: *{until.strftime('%d.%m.%Y')} UTC*\n\n"
            f"Добро пожаловать в CS2 Manager! 🎮"
        )
        await q.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎮 Открыть CS2 Manager", web_app=WebAppInfo(url=APP_URL))],
                [InlineKeyboardButton("👤 Профиль", callback_data="profile")]
            ])
        )

# ─── ADMIN ───────────────────────────────────────────────────
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "🔧 *Панель администратора*\n\n"
        "/stats — статистика\n"
        "/give `<id>` `<дней>` — выдать подписку\n"
        "/check `<id>` — проверить пользователя",
        parse_mode="Markdown"
    )

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = sb.table("users").select("*").execute().data or []
    payments = sb.table("payments").select("*").execute().data or []
    active = [u for u in users if is_active(u)]
    revenue = sum(p.get("amount", 0) for p in payments)
    ref_paid = sum(p.get("ref_bonus", 0) for p in payments)
    await update.message.reply_text(
        f"📊 *Статистика CS2 Manager*\n\n"
        f"👥 Всего пользователей: *{len(users)}*\n"
        f"✅ Активных подписок: *{len(active)}*\n"
        f"💳 Платежей: *{len(payments)}*\n"
        f"💰 Выручка: *${revenue:.2f}*\n"
        f"🤝 Выплачено рефералам: *${ref_paid:.2f}*",
        parse_mode="Markdown"
    )

async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Использование: /give <tg_id> <дней>")
        return
    try:
        tg_id, days = int(ctx.args[0]), int(ctx.args[1])
        until = db_activate_sub(tg_id, days)
        await update.message.reply_text(
            f"✅ Выдано *{days}* дней пользователю `{tg_id}`\n"
            f"Активна до: *{until.strftime('%d.%m.%Y')}*",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Использование: /check <tg_id>")
        return
    user = db_get_user(int(ctx.args[0]))
    if not user:
        await update.message.reply_text("Пользователь не найден")
        return
    await update.message.reply_text(
        f"👤 `{user['tg_id']}` @{user.get('username','—')}\n"
        f"📅 Подписка: {time_left(user)}\n"
        f"💰 Баланс: ${user.get('balance',0):.2f}\n"
        f"👥 Реф. код: {user.get('ref_code','—')}",
        parse_mode="Markdown"
    )

# ─── MAIN ────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("give", cmd_give))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CallbackQueryHandler(cb))
    print("CS2 Manager Bot запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
