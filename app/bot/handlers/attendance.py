"""
DAVOMAT — kim bugun ishda.

Nega alohida bot emas, shu botning ichida?
Telegram botlari bir-birining xabarlarini o'qiy olmaydi. Agar davomat
boshqa botda bo'lsa, bizning bot «haydovchi keldimi?» degan savolga
javob topa olmaydi. Bir joyda bo'lgani — ishonchli.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.db import repo

log = logging.getLogger(__name__)
router = Router(name="attendance")


def mark_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ishdaman", callback_data="att:present"),
                InlineKeyboardButton(text="🕐 Kechikaman", callback_data="att:late"),
            ],
            [InlineKeyboardButton(text="❌ Bugun yo'qman", callback_data="att:absent")],
        ]
    )


async def _render(work_date: str | None = None) -> str:
    """Davomat jadvalini matn ko'rinishida chizadi."""
    people = await repo.list_employees()
    att = await repo.get_attendance(work_date)
    date_label = work_date or repo.today_str()

    lines = [f"<b>📋 Davomat — {date_label}</b>", ""]
    order = [repo.ROLE_ADMIN, repo.ROLE_SKLAD, repo.ROLE_PICKER,
             repo.ROLE_DRIVER, repo.ROLE_EMPLOYEE]

    present = absent = unknown = 0
    for role in order:
        group = [p for p in people if p["role"] == role]
        if not group:
            continue
        lines.append(f"<b>{repo.ROLE_LABELS.get(role, role)}</b>")
        for p in group:
            row = att.get(p["telegram_id"])
            if row:
                mark = repo.ATT_LABELS.get(row["status"], row["status"])
                if row["status"] == repo.ATT_ABSENT:
                    absent += 1
                else:
                    present += 1
            else:
                mark = "⬜ belgilanmagan"
                unknown += 1
            lines.append(f"   {mark} — {p['full_name']}")
        lines.append("")

    lines.append(f"Ishda: <b>{present}</b> · Yo'q: <b>{absent}</b> · Noma'lum: {unknown}")
    return "\n".join(lines)


@router.message(Command("keldim"))
@router.message(F.text == "✅ Keldim")
async def cmd_i_am_here(message: Message, employee: dict) -> None:
    await repo.mark_attendance(
        employee["telegram_id"], repo.ATT_PRESENT, marked_by=employee["telegram_id"]
    )
    await message.answer(
        f"✅ Belgilandi: <b>{employee['full_name']}</b> bugun ishda.\n"
        f"<i>{repo.ROLE_LABELS.get(employee['role'], '')}</i>"
    )


@router.message(Command("davomat"))
@router.message(F.text == "📋 Davomat")
async def cmd_attendance(message: Message, employee: dict) -> None:
    text = await _render()
    if employee["role"] == repo.ROLE_ADMIN:
        await message.answer(text)
    else:
        # Xodim faqat o'z holatini belgilaydi
        await message.answer(text, reply_markup=mark_keyboard())


@router.message(Command("soramoq"))
async def cmd_ask_all(message: Message, employee: dict) -> None:
    """Admin: hammadan davomat so'rash (guruhga tugmali xabar)."""
    if employee["role"] != repo.ROLE_ADMIN:
        return
    await message.answer(
        "🌅 <b>Xayrli tong!</b>\n\nBugun ishdamisiz? Tugmani bosing:",
        reply_markup=mark_keyboard(),
    )


@router.callback_query(F.data.startswith("att:"))
async def cb_mark(callback: CallbackQuery, employee: dict) -> None:
    status = callback.data.split(":", 1)[1]
    if status not in repo.ATT_LABELS:
        await callback.answer("Noma'lum holat")
        return

    await repo.mark_attendance(
        employee["telegram_id"], status, marked_by=employee["telegram_id"]
    )
    label = repo.ATT_LABELS[status]
    await callback.answer(f"Belgilandi: {label}")

    # Guruhda hamma ko'rsin
    if callback.message and callback.message.chat.type != "private":
        try:
            await callback.message.answer(f"{label} — {employee['full_name']}")
        except Exception:
            pass


# ------------------------------------------------------------------
#  ADMIN: boshqa xodim uchun belgilash
# ------------------------------------------------------------------
@router.message(Command("bor"))
async def cmd_mark_present(message: Message, employee: dict) -> None:
    await _admin_mark(message, employee, repo.ATT_PRESENT)


@router.message(Command("yoq"))
async def cmd_mark_absent(message: Message, employee: dict) -> None:
    await _admin_mark(message, employee, repo.ATT_ABSENT)


async def _admin_mark(message: Message, employee: dict, status: str) -> None:
    if employee["role"] != repo.ROLE_ADMIN:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer(
            "Format: <code>/bor 123456789</code> yoki <code>/yoq 123456789</code>\n"
            "ID'ni <code>/employees</code> dan olasiz."
        )
        return
    target = int(parts[1])
    person = await repo.get_employee(target)
    if not person:
        await message.answer("❌ Bunday xodim topilmadi.")
        return
    await repo.mark_attendance(target, status, marked_by=employee["telegram_id"])
    await message.answer(
        f"{repo.ATT_LABELS[status]} — <b>{person['full_name']}</b> belgilandi."
    )
