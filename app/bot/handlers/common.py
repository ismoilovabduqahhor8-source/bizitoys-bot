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
            "/xodim_tahlil — xodimlar samaradorligi 👷\n"
            "/trend — haftalik/oylik savdo trendi 📈\n"
            "/health — API'lar ishlayaptimi tekshirish"
        )
    await message.answer(base)


# ------------------------------------------------------------------
#  ERKIN SAVOL -> AMAL yoki AI
#  Bu handler ENG OXIRIDA turadi: boshqa hech biri javob bermasa.
#  Avval AMAL (intent) tekshiriladi: «hodimlar ro'yxati» -> ro'yxat,
#  «QR kerak» -> yorliqlar, «rasmini tashla» -> hisobot rasmi va h.k.
#  Amal bo'lmasa — savol AI'ga yuboriladi (buyurtmalar + savdo bilan).
# ------------------------------------------------------------------
@router.message(F.text & ~F.text.startswith("/"))
async def free_question(message: Message, employee: dict | None) -> None:
    if employee is None:
        return

    text = (message.text or "").strip()
    if len(text) < 4:
        return

    await _handle_free_text(text, message, employee)


async def _handle_free_text(
    text: str, message: Message, employee: dict, thinking: Message | None = None
) -> None:
    """
    Erkin matnga (yozma yoki ovozdan aylantirilgan) javob beradi.

    Avval AMAL (intent) tekshiriladi, topilmasa AI'ga yuboriladi.
    `thinking` — allaqachon ko'rsatilgan "kutish" xabari bo'lsa (masalan
    ovozli xabar tayyorlanayotganda), o'sha tahrirlanadi.
    """
    from app.services import ai_intent

    intent = ai_intent.detect(text)
    if intent:
        if thinking:
            try:
                await thinking.delete()
            except Exception:
                pass
        if await _run_intent(intent, text, message, employee):
            return

    if not ai.enabled:
        msg = "Bu savolni tushunmadim.\n\nBuyruqlar ro'yxati: /help"
        if thinking:
            await thinking.edit_text(msg)
        else:
            await message.answer(msg)
        return

    if thinking is None:
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


# ------------------------------------------------------------------
#  OVOZLI XABAR -> matn -> yuqoridagi ERKIN SAVOL pipeline
# ------------------------------------------------------------------
@router.message(F.voice)
async def free_voice(message: Message, employee: dict | None) -> None:
    """Xodim tugma bosish o'rniga ovozli xabar yuborsa ham ishlaydi."""
    if employee is None:
        return
    if not ai.enabled:
        await message.answer(
            "🎙 Ovozli xabarlarni tushunish uchun AI ulanmagan.\n"
            "Buyruqlar ro'yxati: /help"
        )
        return

    thinking = await message.answer("🎙 Ovozli xabar eshitilmoqda…")
    try:
        file = await message.bot.get_file(message.voice.file_id)
        buf = await message.bot.download_file(file.file_path)
        audio_bytes = buf.read()
    except Exception as e:
        await thinking.edit_text(f"⚠️ Ovoz yuklab olinmadi.\n<code>{e}</code>")
        return

    text = await ai.transcribe_voice(audio_bytes, mime="audio/ogg")
    if not text:
        await thinking.edit_text(
            "⚠️ Ovozli xabarni tushunmadim. Matn bilan qayta yozib ko'ring."
        )
        return

    await thinking.edit_text(f"🎙 <i>«{text}»</i>")
    await _handle_free_text(text, message, employee)


# ------------------------------------------------------------------
#  RASM -> mahsulot tavsifi -> nom bo'yicha mos buyurtma/tovar qidirish
# ------------------------------------------------------------------
@router.message(F.photo)
async def free_photo(message: Message, employee: dict | None) -> None:
    """Xodim mahsulot rasmini yuborsa, AI tavsiflab nomini topishga harakat qiladi."""
    if employee is None:
        return
    if not ai.enabled:
        return  # oddiy rasm bo'lishi mumkin — jim o'tkazamiz

    thinking = await message.answer("🖼 Rasm tekshirilmoqda…")
    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        buf = await message.bot.download_file(file.file_path)
        image_bytes = buf.read()
    except Exception as e:
        await thinking.edit_text(f"⚠️ Rasm yuklab olinmadi.\n<code>{e}</code>")
        return

    desc = await ai.describe_image(image_bytes, mime="image/jpeg")
    if not desc:
        await thinking.edit_text(
            "⚠️ Rasmni tushunmadim. Mahsulot nomini yozib so'rang."
        )
        return

    try:
        items = await order_service.orders_for_user(employee)
    except Exception:
        items = []

    hint = desc.lower()
    matches = []
    for o in items:
        for it in (o.get("items") or []):
            name = (it.get("name") or "").lower()
            if any(word in name for word in hint.split() if len(word) > 3):
                matches.append({
                    "buyurtma": o.get("public_id"),
                    "tovar": it.get("name"),
                    "soni": it.get("qty"),
                })

    lines = [f"🖼 <b>Rasmda:</b> {desc}", ""]
    if matches:
        lines.append("<b>Mos kelgan buyurtmalar:</b>")
        seen = set()
        for m in matches[:8]:
            key = (m["buyurtma"], m["tovar"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"• {m['tovar']} ×{m['soni']} — <code>{m['buyurtma']}</code>")
    else:
        lines.append("<i>Bugungi buyurtmalar orasida mos tovar topilmadi.</i>")

    await thinking.edit_text("\n".join(lines))


async def _run_intent(
    intent: str, text: str, message: Message, employee: dict
) -> bool:
    """Aniqlangan amalni bajaradi. Qaytaradi: True — bajarildi."""
    if intent == "employees":
        await _show_employees(message)
        return True
    if intent == "xodim_tahlil":
        from app.bot.handlers.stock import cmd_employee_performance
        await cmd_employee_performance(message, employee)
        return True
    if intent == "yorliqlar":
        from app.bot.handlers.orders import cmd_labels_bulk
        await cmd_labels_bulk(message, employee)
        return True
    if intent == "aktlar":
        from app.bot.handlers.invoices import cmd_invoices
        await cmd_invoices(message, employee)
        return True
    if intent == "hisobot":
        from app.bot.handlers.stock import cmd_report
        await cmd_report(message, employee)
        return True
    if intent == "qoldiq":
        from app.bot.handlers.stock import cmd_uzum_stock
        await cmd_uzum_stock(message, employee)
        return True
    if intent == "orders":
        from app.bot.handlers.orders import cmd_orders
        await cmd_orders(message, employee)
        return True
    if intent == "shosh":
        from app.bot.handlers.orders import cmd_urgent
        await cmd_urgent(message, employee)
        return True
    if intent == "postavka":
        from app.bot.handlers.postavka import cmd_postavka
        await cmd_postavka(message, employee)
        return True
    if intent == "trend":
        from app.bot.handlers.stock import cmd_trend
        await cmd_trend(message, employee)
        return True
    if intent == "rol":
        return await _change_role(text, message, employee)
    return False


async def _show_employees(message: Message) -> None:
    """Xodimlar ro'yxati — ism va rol bilan."""
    people = await repo.list_employees()
    if not people:
        await message.answer("Xodimlar ro'yxati bo'sh.")
        return

    lines = ["<b>👥 Xodimlar</b>", ""]
    for p in people:
        role_label = repo.ROLE_LABELS.get(p["role"], p["role"])
        uname = f" (@{p['username']})" if p.get("username") else ""
        lines.append(f"• {p['full_name']}{uname} — {role_label}")
    await message.answer("\n".join(lines))


async def _change_role(text: str, message: Message, employee: dict) -> bool:
    """«Xodim rolini o'zgartir» — faqat admin bajarishi mumkin."""
    from app.services import ai_intent

    if employee["role"] != repo.ROLE_ADMIN:
        await message.answer("🔒 Rol o'zgartirish faqat <b>admin</b> uchun.")
        return True

    parsed = ai_intent.parse_role_change(text)
    if not parsed:
        await message.answer(
            "Rol o'zgartirish uchun shunday yozing:\n"
            "<i>«Aziz rolini yig'uvchi qil»</i>\n"
            "Rollar: admin · sklad · yig'uvchi · haydovchi · xodim"
        )
        return True

    name_hint, role_code = parsed
    people = await repo.list_employees()
    match = ai_intent.find_employee_by_name(people, name_hint)
    if not match:
        await message.answer(
            f"❌ <b>{name_hint}</b> ismli xodim topilmadi.\n"
            "Xodimlar ro'yxati: /employees"
        )
        return True

    await repo.upsert_employee(
        match["telegram_id"], match.get("username"), match["full_name"], role_code
    )
    await message.answer(
        f"✅ <b>{match['full_name']}</b> endi — "
        f"{repo.ROLE_LABELS.get(role_code, role_code)}"
    )
    return True
