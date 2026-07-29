"""
FBS bo'limi — kabinetdagi bo'limlarning aynan o'zi.

    📦 FBS
     ├─ 🆕 Yangilar     -> buyurtmalar + «Qabul qilish»
     ├─ 📦 Yig'ishda    -> buyurtmalar + «Postavka ochish»
     └─ 🚚 Postavkada   -> aktlar + «QR» va «Akt PDF»

Nega shunday? Yig'uvchi bitta joydan boshqaradi va har bo'limda
aynan o'sha bosqichda kerak bo'ladigan amal turadi. Buyruqlarni
eslab qolish shart emas.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from app.bot import cards
from app.bot.keyboards import group_actions
from app.db import repo
from app.integrations.base import ApiError
from app.integrations.uzum import uzum
from app.services import grouping
from app.services import orders as order_service
from app.services import workflow

log = logging.getLogger(__name__)
router = Router(name="fbs")

# Bo'lim -> qaysi bosqichlar kiradi
SECTIONS = {
    "new": ("🆕 Yangilar", ["new"]),
    "packing": ("📦 Yig'ishda", ["sklad", "shortage", "sklad_ready",
                                 "checking", "picking", "packed"]),
    "postavka": ("🚚 Postavkada", ["in_postavka", "to_pvz"]),
}


def _menu(counts: dict[str, int]) -> InlineKeyboardMarkup:
    rows = []
    for code, (label, _) in SECTIONS.items():
        n = counts.get(code, 0)
        rows.append([InlineKeyboardButton(
            text=f"{label}  ·  {n}", callback_data=f"fbs:{code}"
        )])
    rows.append([InlineKeyboardButton(text="🔄 Yangilash", callback_data="fbs:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _counts(employee: dict) -> tuple[dict[str, int], list[dict]]:
    orders = await order_service.orders_for_user(employee)
    counts = {}
    for code, (_, stages) in SECTIONS.items():
        counts[code] = sum(1 for o in orders if o["status"] in stages)
    return counts, orders


async def _show_menu(target, employee: dict, edit: bool = False) -> None:
    counts, _ = await _counts(employee)
    total = sum(counts.values())
    text = (
        "<b>📦 FBS buyurtmalar</b>\n\n"
        f"Jami ish: <b>{total}</b> ta\n\n"
        "<i>Bo'limni tanlang</i>"
    )
    kb = _menu(counts)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await target.answer(text, reply_markup=kb)


@router.message(Command("fbs"))
@router.message(F.text == "📦 FBS")
async def cmd_fbs(message: Message, employee: dict) -> None:
    wait = await message.answer("⏳ Yuklanmoqda…")
    try:
        await _show_menu(wait, employee, edit=True)
    except ApiError as e:
        await wait.edit_text(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")


@router.callback_query(F.data == "fbs:menu")
async def cb_menu(callback: CallbackQuery, employee: dict) -> None:
    await callback.answer("Yangilanmoqda…")
    uzum.invalidate_cache()
    await _show_menu(callback.message, employee, edit=True)


@router.callback_query(F.data.startswith("fbs:"))
async def cb_section(callback: CallbackQuery, employee: dict) -> None:
    code = callback.data.split(":", 1)[1]

    # MUHIM: Telegram tugma bosilganda javob kutadi. Javob kelmasa,
    # tugma "qotib" qoladi va foydalanuvchi hech nima bo'lmadi deb o'ylaydi.
    # Shuning uchun ENG BOSHIDA javob beramiz.
    await callback.answer()

    # DIQQAT: bu handler "fbs:" bilan boshlanadigan HAMMA tugmani ushlaydi.
    # Shuning uchun maxsus tugmalar shu yerda hal qilinadi — alohida
    # handler yozilsa, u umuman ishga tushmaydi (bu handler oldin turadi).
    if code == "akt":
        await _open_invoices(callback)
        return
    if code == "postavka_ochish":
        await _open_postavka(callback, employee)
        return

    if code not in SECTIONS:
        return

    label, stages = SECTIONS[code]

    try:
        orders = await order_service.orders_for_user(employee)
    except ApiError as e:
        await callback.message.answer(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    mine = [o for o in orders if o["status"] in stages]
    if not mine:
        await callback.message.answer(
            f"<b>{label}</b>\n\nBu bo'limda buyurtma yo'q. 👌"
        )
        return

    # --- 🚚 Postavkada: aktlar ko'rsatiladi ---
    if code == "postavka":
        await callback.message.answer(
            f"<b>{label}</b> — {len(mine)} ta buyurtma\n\n"
            "<i>Aktlar ro'yxati va QR kodlar:</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📋 Aktlarni ochish", callback_data="fbs:akt")
            ]]),
        )
        return

    groups = grouping.build(mine)

    # --- 🆕 Yangilar: hamma mahsulot rasmi + bitta "Qabul qilish" ---
    if code == "new":
        await callback.message.answer(
            f"<b>{label}</b> — {len(mine)} ta buyurtma, {len(groups)} ta guruh"
        )
        for g in groups[:8]:
            await _send_new_group(callback.bot, callback.message.chat.id, g)
        return

    # --- 📦 Yig'ishda va boshqalar: avvalgidek ---
    is_admin = employee["role"] == repo.ROLE_ADMIN

    await callback.message.answer(
        f"<b>{label}</b> — {len(mine)} ta buyurtma, {len(groups)} ta guruh"
    )
    for g in groups[:8]:
        await cards.send_group_card(
            callback.bot,
            callback.message.chat.id,
            g,
            group_actions(g["gid"], g["stage"], is_admin,
                          can_act=workflow.can_act(g["stage"], employee)),
        )

    # --- Bo'limga mos asosiy amal ---
    await callback.message.answer(
        "<b>🚚 Postavka ochish</b>\n\n"
        "PVZ va vaqtni tanlab, postavka ochasiz.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🚚 Postavka ochish", callback_data="fbs:postavka_ochish"
            )
        ]]),
    )


async def _send_new_group(bot: Bot, chat_id: int, group: dict) -> None:
    """
    «Yangilar» guruhini yuboradi: HAR mahsulotning rasmi + bitta
    «Qabul qilish» tugmasi, shu guruh uchun.

    Ilgari: bitta rasm (eng ko'p sonli mahsulotniki) + «To'liq ko'rish»
    va «Skladga berish» tugmalari, keyin ALOHIDA xabarda umumiy
    «Qabul qilish». Endi — bir joyda, bir bosqichda.
    """
    with_photo, without = grouping.items_with_photos(group["orders"])

    if with_photo:
        media = [
            InputMediaPhoto(media=r["photo"],
                            caption=f"<b>{r['sku']}</b>\nSoni: {r['qty']}",
                            parse_mode="HTML")
            for r in with_photo[:10]
        ]
        try:
            await bot.send_media_group(chat_id, media)
        except Exception as e:
            log.warning("Rasm albomi yuborilmadi: %s", e)
            for r in with_photo[:10]:
                await bot.send_message(chat_id, f"<b>{r['sku']}</b>\nSoni: {r['qty']}")

    text = grouping.format_group(group)
    gid = group["gid"]
    await bot.send_message(
        chat_id, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"✅ Qabul qilish ({len(group['orders'])} ta)",
                callback_data=f"fbsok:{gid}",
            )
        ]]),
    )


@router.callback_query(F.data.startswith("fbsok:"))
async def cb_accept(callback: CallbackQuery, employee: dict, bot: Bot) -> None:
    """Yangi buyurtmalarni qabul qilish — Uzumda tasdiqlash."""
    gid = callback.data.split(":", 1)[1]
    order_ids = grouping.recall(gid)
    if not order_ids:
        await callback.answer("Eskirgan. /fbs bosing.", show_alert=True)
        return

    await callback.answer("Qabul qilinmoqda…")
    wait = await callback.message.answer(
        f"⏳ {len(order_ids)} ta buyurtma qabul qilinmoqda…\n"
        f"<i>~{len(order_ids)} soniya</i>"
    )

    ok, errors = 0, []
    for oid in order_ids[:60]:
        done, err = await uzum.confirm_order(oid)
        if done:
            ok += 1
            await repo.set_local_status(oid, "sklad", employee["telegram_id"])
        else:
            errors.append(f"{oid}: {err}")

    uzum.invalidate_cache()

    msg = f"✅ <b>{ok} ta buyurtma qabul qilindi</b>"
    if ok:
        msg += "\n\n<i>Endi «📦 Yig'ishda» bo'limida.</i>"
    if errors:
        msg += f"\n\n⚠️ {len(errors)} tasi qabul qilinmadi:\n"
        msg += "\n".join(f"• {e}" for e in errors[:5])
    try:
        await wait.edit_text(msg)
    except Exception:
        pass

    # Skladga xabar
    for person in await repo.employees_by_role(repo.ROLE_SKLAD):
        try:
            await bot.send_message(
                person["telegram_id"],
                f"🏬 <b>{ok} ta yangi buyurtma qabul qilindi</b>\n\n"
                f"Tovar chiqarish kerak. /fbs",
            )
        except Exception:
            pass


# ------------------------------------------------------------------
#  Bo'limlardan chiqadigan amallar
#  (alohida handler emas — cb_section ichidan chaqiriladi)
# ------------------------------------------------------------------
async def _open_invoices(callback: CallbackQuery) -> None:
    """«📋 Aktlarni ochish» — postavka aktlari ro'yxati."""
    """«📋 Aktlarni ochish» — postavka aktlari ro'yxati."""
    from app.bot.handlers.invoices import _akt_card, akt_buttons

    wait = await callback.message.answer("⏳ Aktlar olinmoqda…")
    try:
        invoices = await uzum.get_invoices()
    except ApiError as e:
        await wait.edit_text(f"⚠️ Aktlar olinmadi.\n<code>{e}</code>")
        return

    if not invoices:
        await wait.edit_text(
            "Akt topilmadi.\n\n"
            "<i>Postavka ochilgach, akt shu yerda ko'rinadi.</i>"
        )
        return

    await wait.edit_text(f"📋 <b>{len(invoices)} ta akt</b>")
    for inv in invoices[:10]:
        await callback.message.answer(
            _akt_card(inv), reply_markup=akt_buttons(inv["id"])
        )


async def _open_postavka(callback: CallbackQuery, employee: dict) -> None:
    """«🚚 Postavka ochish» — PVZ va vaqt tanlash oqimini boshlaydi."""
    if employee["role"] not in (repo.ROLE_ADMIN, repo.ROLE_PICKER):
        await callback.message.answer(
            "🚚 <b>Postavka ochish</b>\n\n"
            "Buni faqat admin yoki yig'uvchi qila oladi."
        )
        return

    try:
        from app.bot.handlers.postavka import cmd_postavka
        await cmd_postavka(callback.message, employee)
    except Exception as e:
        log.exception("Postavka oqimi ochilmadi")
        await callback.message.answer(
            "⚠️ <b>Postavka oqimi ochilmadi</b>\n\n"
            f"<code>{type(e).__name__}: {str(e)[:200]}</code>\n\n"
            "<i>Shu matnni Claude'ga yuboring.</i>"
        )
