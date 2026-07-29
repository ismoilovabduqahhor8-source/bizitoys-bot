"""
Tugmalar (klaviatura) — foydalanuvchi buyruq yozmasdan bosib ishlatishi uchun.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.db import repo
from app.services import workflow

def main_menu(role: str) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📦 FBS"), KeyboardButton(text="🔥 Shoshilinch")],
        [KeyboardButton(text="📊 Hisobot"), KeyboardButton(text="🔍 Tahlil")],
        [KeyboardButton(text="🔢 FBS / FBO"), KeyboardButton(text="📉 Uzum qoldiq")],
        [KeyboardButton(text="🚫 Bloklangan")],
        [KeyboardButton(text="📋 Davomat")],
    ]
    if role == repo.ROLE_ADMIN:
        rows.append([KeyboardButton(text="👥 Xodimlar"), KeyboardButton(text="⚙️ Sozlamalar")])
    else:
        rows.append([KeyboardButton(text="✅ Keldim")])

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def order_actions(
    order_id: str,
    stage: str,
    assigned: bool,
    is_admin: bool = False,
    can_act: bool = True,
) -> InlineKeyboardMarkup | None:
    """
    Ish oqimi tugmalari.

    Faqat shu bosqichda RUXSAT ETILGAN harakatlar ko'rsatiladi —
    yig'uvchi tekshirmasdan «qadoqlandi» deb belgilay olmaydi.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    if not assigned:
        row = [InlineKeyboardButton(text="🙋 Men olaman", callback_data=f"take:{order_id}")]
        if is_admin:
            row.append(
                InlineKeyboardButton(text="👤 Biriktirish", callback_data=f"assign:{order_id}")
            )
        buttons.append(row)
    elif is_admin:
        buttons.append(
            [InlineKeyboardButton(text="🔄 Boshqaga berish", callback_data=f"assign:{order_id}")]
        )

    if can_act:
        for code, text in workflow.next_actions(stage):
            buttons.append(
                [InlineKeyboardButton(text=text, callback_data=f"stage:{order_id}:{code}")]
            )

    # Yig'ish bosqichida QR/yorliq kerak bo'ladi
    if stage in ("picking", "checking", "sklad_ready") and can_act:
        buttons.append(
            [InlineKeyboardButton(text="🏷 QR / yorliq", callback_data=f"label:{order_id}")]
        )

    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None



def employee_picker(order_id: str, people: list[dict]) -> InlineKeyboardMarkup:
    """Admin xodim tanlaydi. Faqat bugun ishdagilar ko'rsatiladi."""
    rows = []
    for p in people[:20]:
        rows.append([
            InlineKeyboardButton(
                text=f"{p['full_name']}",
                callback_data=f"assignto:{order_id}:{p['telegram_id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀️ Bekor", callback_data=f"cancelassign:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def attendance_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Keldim"), KeyboardButton(text="📋 Davomat")]],
        resize_keyboard=True,
    )


def group_actions(
    gid: str, stage: str, is_admin: bool = False, can_act: bool = True,
    minimal: bool = False,
) -> InlineKeyboardMarkup:
    """
    Akt (guruh) uchun tugmalar.

    Bosqichni surish BUTUN guruhga qo'llanadi — 12 ta buyurtmani
    birma-bir belgilash shart emas.

    MUHIM: «Yorliqlar» va «Biriktirish» ERTA bosqichlarda (new, sklad,
    shortage) ko'rsatilmaydi — bu qayerdan chaqirilishidan qat'i
    nazar amal qiladi (/orders, e'lonlar, FBS bo'limi). Bu bosqichda
    yig'ish hali boshlanmagan, shuning uchun yorliq kerak emas, va
    rolga qarab hamma ko'rgani uchun biriktirish ham shart emas.
    """
    EARLY_STAGES = {"new", "sklad", "shortage"}
    minimal = minimal or stage in EARLY_STAGES

    rows = [[InlineKeyboardButton(text="📦 To'liq ko'rish", callback_data=f"grp:{gid}")]]

    if can_act:
        for code, text in workflow.next_actions(stage):
            rows.append(
                [InlineKeyboardButton(text=f"{text}  (hammasi)",
                                      callback_data=f"gstage:{gid}:{code}")]
            )
        if not minimal:
            rows.append(
                [InlineKeyboardButton(text="🏷 Yorliqlar", callback_data=f"glabel:{gid}")]
            )
    if is_admin and not minimal:
        rows.append(
            [InlineKeyboardButton(text="👤 Biriktirish", callback_data=f"gassign:{gid}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
