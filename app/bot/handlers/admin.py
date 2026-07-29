"""
Admin buyruqlari: xodimlarni boshqarish, chegaralarni sozlash, tizim holati.
Bu router'ga faqat admin rolidagi foydalanuvchi kira oladi (filtr pastda).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from app import VERSION
from app.config import settings
from app.db import repo
from app.integrations.billz import billz
from app.integrations.uzum import uzum

router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message, employee: dict | None = None) -> bool:
        return bool(employee and employee["role"] == repo.ROLE_ADMIN)


router.message.filter(IsAdmin())


@router.message(Command("employees"))
@router.message(F.text == "👥 Xodimlar")
async def cmd_employees(message: Message) -> None:
    people = await repo.list_employees()
    if not people:
        await message.answer("Ro'yxat bo'sh.")
        return
    lines = ["<b>👥 Xodimlar</b>", ""]
    att = await repo.get_attendance()
    for p in people:
        role_label = repo.ROLE_LABELS.get(p["role"], p["role"])
        uname = f" (@{p['username']})" if p["username"] else ""
        row = att.get(p["telegram_id"])
        mark = repo.ATT_LABELS.get(row["status"], "") if row else "⬜"
        lines.append(
            f"{role_label} {p['full_name']}{uname}\n"
            f"     <code>{p['telegram_id']}</code> · {mark}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("add_employee"))
async def cmd_add_employee(message: Message) -> None:
    await _add_person(message, repo.ROLE_EMPLOYEE)


@router.message(Command("add_sklad"))
async def cmd_add_sklad(message: Message) -> None:
    await _add_person(message, repo.ROLE_SKLAD)


@router.message(Command("add_yiguvchi"))
async def cmd_add_picker(message: Message) -> None:
    await _add_person(message, repo.ROLE_PICKER)


@router.message(Command("add_haydovchi"))
async def cmd_add_driver(message: Message) -> None:
    await _add_person(message, repo.ROLE_DRIVER)


@router.message(Command("add_admin"))
async def cmd_add_admin(message: Message) -> None:
    await _add_person(message, repo.ROLE_ADMIN)


async def _add_person(message: Message, role: str) -> None:
    """
    Xodim qo'shishning UCH usuli:

    1. Guruhda xodim yozgan xabarga JAVOB berib buyruq yozish
       -> ID avtomatik olinadi, hech narsa terish shart emas
    2. Xodimning xabarini botga forward qilib, keyin buyruq
    3. ID ni qo'lda: /add_yiguvchi 123456789 Aziz Karimov

    Birinchi usul eng qulay — shuning uchun u birinchi tekshiriladi.
    """
    parts = (message.text or "").split(maxsplit=1)
    name_arg = parts[1].strip() if len(parts) > 1 else ""

    target_id = None
    username = None
    full_name = None

    # 1) Javob berilgan xabardan
    if message.reply_to_message:
        src = message.reply_to_message
        user = src.forward_from or src.from_user
        if user and not user.is_bot:
            target_id = user.id
            username = user.username
            full_name = " ".join(
                x for x in (user.first_name, user.last_name) if x
            ) or f"Xodim {user.id}"

    # 2) Qo'lda ID berilgan bo'lsa
    if target_id is None and name_arg:
        bits = name_arg.split(maxsplit=1)
        if bits[0].lstrip("-").isdigit():
            target_id = int(bits[0])
            full_name = bits[1].strip() if len(bits) > 1 else f"Xodim {target_id}"

    if target_id is None:
        cmd = (message.text or "/add_employee").split()[0]
        await message.answer(
            f"<b>{repo.ROLE_LABELS.get(role, role)} qo'shish</b>\n\n"
            "<b>Eng oson yo'l:</b>\n"
            f"Guruhda xodim yozgan xabarga <b>javob bering</b> va "
            f"<code>{cmd}</code> deb yozing.\n"
            "ID kerak emas, o'zi olinadi.\n\n"
            "<b>Yoki:</b>\n"
            f"<code>{cmd} 123456789 Aziz Karimov</code>\n\n"
            "<i>ID ni bilish: xodim botga /id yozadi.</i>"
        )
        return

    # Javobdan olingan bo'lsa ham, ism yozilgan bo'lsa o'shani ishlatamiz
    if message.reply_to_message and name_arg and not name_arg.split()[0].isdigit():
        full_name = name_arg

    await repo.upsert_employee(target_id, username, full_name, role)
    label = repo.ROLE_LABELS.get(role, role)
    uname = f" (@{username})" if username else ""

    await message.answer(
        f"✅ <b>{label} qo'shildi</b>\n\n"
        f"👤 {full_name}{uname}\n"
        f"🆔 <code>{target_id}</code>"
    )

    # Xodimga xabar berib qo'yamiz
    try:
        await message.bot.send_message(
            target_id,
            f"👋 Sizni BiziToys botiga qo'shishdi.\n\n"
            f"Rolingiz: <b>{label}</b>\n\n"
            f"Boshlash uchun /start bosing.",
        )
    except Exception:
        await message.answer(
            "ℹ️ Xodimga xabar yuborilmadi — u hali botga "
            "<code>/start</code> yozmagan.\n"
            "<i>Unga botni ochib /start bosishni ayting.</i>"
        )


@router.message(Command("remove_employee"))
async def cmd_remove_employee(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Format: <code>/remove_employee 123456789</code>")
        return
    ok = await repo.deactivate_employee(int(parts[1]))
    await message.answer("✅ O'chirildi." if ok else "❌ Bunday ID topilmadi.")


@router.message(Command("set_min"))
async def cmd_set_min(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 3 or not parts[2].isdigit():
        await message.answer(
            "Format: <code>/set_min BT-1001 10</code>\n"
            f"Standart chegara hozir: <b>{settings.default_min_stock}</b>"
        )
        return
    sku, qty = parts[1].upper(), int(parts[2])
    await repo.set_threshold(sku, qty)
    await message.answer(f"✅ <code>{sku}</code> uchun minimal qoldiq: <b>{qty}</b>")


@router.message(Command("health"))
@router.message(F.text == "⚙️ Sozlamalar")
async def cmd_health(message: Message) -> None:
    mode = settings.mode_report()
    lines = [
        "<b>⚙️ Tizim holati</b>",
        f"<code>v{VERSION}</code>",
        "",
        f"Rejim: <b>{mode}</b>",
        f"Vaqt mintaqasi: {settings.timezone}",
        f"Ertalabki hisobot: {settings.morning_report_at}",
        f"Kechqurungi hisobot: {settings.evening_report_at}",
        f"Kechikish chegarasi: {settings.late_after_hours} soat",
        f"Standart min. qoldiq: {settings.default_min_stock}",
        f"Guruh chat ID: <code>{settings.group_chat_id or 'sozlanmagan'}</code>",
        "",
    ]

    if settings.uzum_mock:
        lines.append("Uzum: 🧪 soxta ma'lumot (kalit kiritilmagan)")
    else:
        ok = await uzum.check_token()
        lines.append(f"Uzum API: {'✅ ishlayapti' if ok else '❌ xato'}")

    from app.integrations.ai import ai
    if not ai.enabled:
        lines.append("AI: ⬜ o'chiq")
    elif settings.ai_provider == "make":
        lines.append("AI: ✅ yoqilgan (Make.com webhook)")
    else:
        lines.append(f"AI: ✅ yoqilgan ({settings.ai_model})")

    if settings.billz_mock:
        lines.append("Billz: 🧪 soxta ma'lumot (kalit kiritilmagan)")
    else:
        try:
            await billz.get_stock()
            lines.append("Billz API: ✅ ishlayapti")
        except Exception as e:
            lines.append(f"Billz API: ❌ {str(e)[:80]}")

    await message.answer("\n".join(lines))


@router.message(Command("rol"))
async def cmd_change_role(message: Message) -> None:
    """Mavjud xodimning rolini o'zgartirish: /rol 123456789 yiguvchi"""
    parts = (message.text or "").split()
    roles = {
        "admin": repo.ROLE_ADMIN,
        "sklad": repo.ROLE_SKLAD,
        "yiguvchi": repo.ROLE_PICKER,
        "haydovchi": repo.ROLE_DRIVER,
        "xodim": repo.ROLE_EMPLOYEE,
    }

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        role_word = parts[1].lower() if len(parts) > 1 else ""
    elif len(parts) >= 3 and parts[1].lstrip("-").isdigit():
        target_id = int(parts[1])
        role_word = parts[2].lower()
    else:
        role_word = ""

    if target_id is None or role_word not in roles:
        await message.answer(
            "<b>Rolni o'zgartirish</b>\n\n"
            "<code>/rol 123456789 yiguvchi</code>\n"
            "yoki xabarga javob berib: <code>/rol yiguvchi</code>\n\n"
            "Rollar: <code>admin</code> · <code>sklad</code> · "
            "<code>yiguvchi</code> · <code>haydovchi</code> · <code>xodim</code>"
        )
        return

    person = await repo.get_employee(target_id)
    if not person:
        await message.answer("❌ Bunday xodim ro'yxatda yo'q. Avval qo'shing.")
        return

    await repo.upsert_employee(
        target_id, person.get("username"), person["full_name"], roles[role_word]
    )
    await message.answer(
        f"✅ <b>{person['full_name']}</b> endi "
        f"{repo.ROLE_LABELS[roles[role_word]]}"
    )
