"""
Umumiy buyruqlar: /start, /help, /id
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.keyboards import main_menu
from app.config import settings
from app.db import repo
from app.integrations.ai import ai
from app.services import context
from app.services import orders as order_service

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, employee: dict | None) -> None:
    if employee is None:
        await message.answer(
            "👋 Assalomu alaykum!\n\n"
            "Bu — BiziToys ichki xizmat boti. Faqat ro'yxatdagi xodimlar foydalana oladi.\n\n"
            f"Sizning Telegram ID: <code>{message.from_user.id}</code>\n"
            "Shu ID'ni adminga yuboring."
        )
        return

    role_label = "Admin" if employee["role"] == repo.ROLE_ADMIN else "Xodim"
    mode = " · 🧪 TEST rejimi" if settings.mock_mode else ""
    await message.answer(
        f"👋 Xush kelibsiz, <b>{employee['full_name']}</b>!\n"
        f"Rolingiz: <b>{role_label}</b>{mode}\n\n"
        "Quyidagi tugmalardan foydalaning yoki /help ni yozing.",
        reply_markup=main_menu(employee["role"]),
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(
        f"👤 Sizning ID: <code>{message.from_user.id}</code>\n"
        f"💬 Chat ID: <code>{message.chat.id}</code>\n"
        f"📎 Chat turi: {message.chat.type}"
    )


@router.message(Command("versiya"))
async def cmd_version(message: Message) -> None:
    """Qaysi versiya ishlayotganini ko'rsatadi — yangilanish tushdimi yo'qmi."""
    from app import VERSION
    await message.answer(
        f"🤖 <b>Versiya:</b> <code>{VERSION}</code>\n"
        f"👤 Sizning ID: <code>{message.from_user.id}</code>\n"
        f"💬 Chat: <code>{message.chat.id}</code> ({message.chat.type})"
    )


@router.message(Command("help"))
async def cmd_help(message: Message, employee: dict | None) -> None:
    base = (
        "<b>📖 Buyruqlar</b>\n\n"
        "/fbs — <b>asosiy bo'lim</b>: yangilar · yig'ishda · postavkada 📦\n"
        "/orders — buyurtmalar ro'yxati\n"
        "/shosh — muddati kam qolganlar 🔥\n"
        "/tahlil — muammolarni topish va tahlil 🔍\n"
        "/bloklangan — bloklangan (yo'qolgan) tovarlar 🚫\n"
        "/fboyuk — FBO yuk xatlari 🚛\n"
        "/yorliqlar — hamma buyurtma uchun QR/yorliq 🏷\n"
        "/aktlar — postavka aktlari, mahsulot va PDF 📋\n"
        "/report — qisqa hisobot\n"
        "/holat — bo'limlar bo'yicha soni\n"
        "/qoldiq — Uzum FBS ombor qoldig'i\n"
        "/top — eng ko'p sotilgan mahsulotlar\n"
        "/keldim — bugun ishda ekanini belgilash ✅\n"
        "/davomat — kim ishda\n"
        "/id — Telegram ID'ni bilish\n\n"
        "💡 Oddiy savol ham yozishingiz mumkin:\n"
        "<i>«Bugungi buyurtmalar PVZga oborildimi?»</i>"
    )
    if employee and employee["role"] == repo.ROLE_ADMIN:
        base += (
            "\n\n<b>👑 Admin buyruqlari</b>\n"
            "\n<b>Xodim qo'shish — eng oson yo'l:</b>\n"
            "guruhda uning xabariga <b>javob berib</b> quyidagini yozing:\n"
            "/add_yiguvchi · /add_sklad · /add_haydovchi\n"
            "<i>ID kerak emas, o'zi olinadi.</i>\n\n"
            "/rol — mavjud xodimning rolini o'zgartirish\n"
            "/add_employee &lt;id&gt; &lt;Ism&gt; — umumiy xodim\n"
            "/add_sklad &lt;id&gt; &lt;Ism&gt; — 🏬 sklad xodimi\n"
            "/add_yiguvchi &lt;id&gt; &lt;Ism&gt; — 📦 yig'uvchi\n"
            "/add_haydovchi &lt;id&gt; &lt;Ism&gt; — 🚚 haydovchi\n"
            "/soramoq — guruhda davomat so'rash\n"
            "/bor &lt;id&gt; · /yoq &lt;id&gt; — davomat belgilash\n"
            "/add_admin &lt;id&gt; &lt;Ism&gt; — admin qo'shish\n"
            "/employees — xodimlar ro'yxati\n"
            "/remove_employee &lt;id&gt; — o'chirish\n"
            "/set_min &lt;SKU&gt; &lt;son&gt; — minimal qoldiq chegarasi\n"
            "/postavka — postavka ochish (PVZ va vaqt) 🚚\n"
            "/health — API'lar ishlayaptimi tekshirish"
        )
    await message.answer(base)


# ------------------------------------------------------------------
#  ERKIN SAVOL -> AI
#  Bu handler ENG OXIRIDA turadi: boshqa hech biri javob bermasa,
#  savol AI'ga yuboriladi.
# ------------------------------------------------------------------
@router.message(F.text & ~F.text.startswith("/"))
async def free_question(message: Message, employee: dict | None) -> None:
    if employee is None:
        return

    text = (message.text or "").strip()
    if len(text) < 4:
        return

    if not ai.enabled:
        await message.answer(
            "Bu savolni tushunmadim.\n\n"
            "Buyruqlar ro'yxati: /help"
        )
        return

    thinking = await message.answer("🤔 O'ylayapman…")

    # Agar yaqinda /tahlil ishlatilgan bo'lsa — savol o'sha muammolar
    # haqida bo'lishi ehtimoli katta. Shunda AI ularni eslab turadi va
    # suhbatni davom ettirish mumkin bo'ladi:
    #   /tahlil  ->  "eng katta muammo nima?"  ->  "uni qanday hal qilaman?"
    from app.services.analytics import recall_analysis

    recent = recall_analysis(message.from_user.id)
    if recent:
        answer = await ai.analyze(recent, question=text)
    else:
        try:
            items = await order_service.orders_for_user(employee)
        except Exception:
            items = []

        # AI HAMMA MA'LUMOTNI KO'RSIN: buyurtmalar + bugungi savdo.
        # Savdo keshda (1 soat) — soatlik vazifa yangilab turadi,
        # shuning uchun javob sekinlashmaydi.
        ctx = context.build(items, employee)
        try:
            from app.services import report

            ctx["bugungi_savdo"] = await report.today_sales_cached()
        except Exception:
            pass
        answer = await ai.ask(text, ctx)

    if answer:
        await thinking.edit_text(answer)
    else:
        await thinking.edit_text(
            "Javob bera olmadim.\n\nBuyruqlar ro'yxati: /help"
        )
