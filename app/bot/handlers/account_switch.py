"""Do'kon egasini tanlash — «har safar kirganda» usuli.

Foydalanuvchi bir nechta egasiga (masalan Abduqahhor + Kamoliddin) kira
oladigan bo'lsa, Uzum bilan ishlaydigan har bir buyruqda avval egasini
tanlash tugmalari chiqadi. Tanlangach, buyruq o'sha egasi bilan bajariladi.

Bitta egasi bor foydalanuvchi uchun hech qanday tugma chiqmaydi — hamma
narsa avvalgidek ishlayveradi.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.services import accounts

log = logging.getLogger(__name__)
router = Router(name="account_switch")

# Qaysi buyruq qaysi handler'ga boradi: cmd -> (modul, funksiya, employee kerakmi?)
_CMDS: dict[str, tuple[str, str, bool]] = {}


def register(cmd: str, module: str, func: str, wants_employee: bool = True) -> None:
    """Buyruq kalitini handler funksiyasiga bog'laydi (tanlashdan keyin chaqirish uchun)."""
    _CMDS[cmd] = (module, func, wants_employee)


# Har bir xabar uchun asl buyruq xabari eslab qolinadi — tanlangach o'shanga
# javob beriladi (from_user/chat to'g'ri bo'lishi uchun).
_PENDING: dict[int, Message] = {}


async def ensure_account(message: Message, employee: dict, cmd: str) -> bool:
    """
    Uzum-buyruq boshida chaqiriladi.

    Qaytaradi:
      True  — tanlash tugmalari ko'rsatildi, buyruq shu yerda to'xtaydi;
      False — davom etish mumkin (joriy egasi allaqachon tanlangan).
    """
    allowed = accounts.for_employee(employee)
    if not allowed:
        return False
    if len(allowed) == 1:
        accounts.select(allowed[0].key)
        return False
    if not accounts.is_multi():
        accounts.select(allowed[0].key)
        return False

    # Bir nechta egasi — tanlash kerak
    _PENDING[message.from_user.id] = message
    buttons = [
        [InlineKeyboardButton(text=f"🏪 {a.name}", callback_data=f"acct:{a.key}:{cmd}")]
        for a in allowed
    ]
    buttons.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="acct:cancel")])
    await message.answer(
        "🏪 <b>Do'kon egasini tanlang:</b>\n"
        "<i>Hisobot va buyurtmalar tanlangan egasi bo'yicha ko'rsatiladi.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    return True


@router.callback_query(F.data.startswith("acct:"))
async def cb_pick_account(callback: CallbackQuery, employee: dict) -> None:
    parts = callback.data.split(":")
    if len(parts) < 2 or parts[1] == "cancel":
        _PENDING.pop(callback.from_user.id, None)
        accounts.forget(callback.from_user.id)
        await callback.answer("Bekor qilindi", show_alert=False)
        try:
            await callback.message.delete()
        except Exception:
            pass
        return

    key = parts[1]
    cmd = parts[2] if len(parts) > 2 else ""
    accounts.select(key)
    accounts.remember(callback.from_user.id, key)
    await callback.answer(f"🏪 {accounts.account(key).name} tanlandi")

    entry = _CMDS.get(cmd)
    if not entry:
        return
    module, func, wants_employee = entry
    try:
        mod = __import__(f"app.bot.handlers.{module}", fromlist=[func])
    except ImportError as e:  # pragma: no cover
        log.exception("Handler import qilinmadi: %s.%s", module, func)
        await callback.answer(f"Xato: {e}", show_alert=True)
        return
    fn = getattr(mod, func)

    # Asl buyruq xabari bilan chaqiramiz (tanlash tugmasi o'rniga)
    orig = _PENDING.pop(callback.from_user.id, None) or callback.message
    try:
        if wants_employee:
            await fn(orig, employee)
        else:
            await fn(orig)
    except Exception as e:  # pragma: no cover
        log.exception("Tanlangan buyruq bajarilmadi: %s", cmd)
        try:
            await callback.message.answer(
                f"⚠️ Buyruq bajarilmadi.\n<code>{type(e).__name__}: {str(e)[:200]}</code>"
            )
        except Exception:
            pass
