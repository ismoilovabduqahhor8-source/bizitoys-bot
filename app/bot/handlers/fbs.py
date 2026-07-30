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

    # «Postavkada» soni — ICHKI holatdan emas, HAQIQIY aktlardan.
    #
    # Sabab: ichki baza "in_postavka" belgisini saqlab qoladi, hatto
    # akt Uzumda allaqachon qabul qilingan (ACCEPTED) bo'lsa ham —
    # buyurtma bosqichi hech kim tomonidan qo'lda surilmaguncha.
    # Natijada eski, allaqachon yopilgan aktlarning buyurtmalari ham
    # "22" ga qo'shilib, haqiqiy akt sonidan (masalan 11) ko'p chiqadi.
    #
    # Aktlar ro'yxati (get_invoices) esa har doim aniq — chunki u
    # to'g'ridan-to'g'ri Uzumdan, ACTIVE_INVOICE_STATUSES bo'yicha.
    try:
        invoices = await uzum.get_invoices()
        counts["postavka"] = sum(inv.get("orders") or 0 for inv in invoices)
    except ApiError as e:
        log.warning("Aktlar soni olinmadi, ichki holatdan foydalanamiz: %s", e)

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
    """
    Birinchi qadam — FBS yoki FBO tanlanadi.

    Ilgari bu tugma darrov FBS bo'limlariga (Yangilar/Yig'ishda/
    Postavkada) olib borardi. Endi avval qaysi ish turi kerakligi
    so'raladi, chunki FBO'da butunlay boshqa ish (yuk xatlari,
    zelyoniy koridor) bor.
    """
    await message.answer(
        "<b>📦 Qaysi ish turi?</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏬 FBS", callback_data="top:fbs"),
            InlineKeyboardButton(text="🚛 FBO", callback_data="top:fbo"),
        ]]),
    )


@router.callback_query(F.data == "top:fbs")
async def cb_top_fbs(callback: CallbackQuery, employee: dict) -> None:
    await callback.answer()
    wait_msg = callback.message
    try:
        await wait_msg.edit_text("⏳ Yuklanmoqda…")
    except Exception:
        wait_msg = await callback.message.answer("⏳ Yuklanmoqda…")
    await _show_menu(wait_msg, employee, edit=True)


# ------------------------------------------------------------------
#  🚛 FBO — yuk xatlari va zelyoniy koridor
# ------------------------------------------------------------------

# "Qaysi sanaga rejalashtirilgan?" javobini kutayotgan foydalanuvchilar.
# {telegram_id: (shop_id, invoice_id)}
_AWAITING_DATE: dict[int, tuple[int, str]] = {}

# Bu statuslarga ega akt ENDI YANGI emas — Excel/bildirishnoma
# kerak emas (Uzum allaqachon qabul qilgan yoki qilyapti).
_FBO_DONE_MARKERS = ("прин", "accept")  # rus/ingliz: принят(а)/принимается, accepted


def _fbo_is_new(inv: dict) -> bool:
    label = (inv.get("status_label") or "").lower()
    value = (inv.get("status_value") or "").upper()
    if value == "ACCEPTED":
        return False
    return not any(m in label for m in _FBO_DONE_MARKERS)


@router.callback_query(F.data == "top:fbo")
async def cb_top_fbo(callback: CallbackQuery, employee: dict) -> None:
    """FBO yuk xatlari — faqat hali qabul qilinmagan (yangi) aktlar."""
    await callback.answer("Yuk xatlari so'ralmoqda…")
    try:
        await callback.message.edit_text("⏳ FBO yuk xatlari olinmoqda…")
    except Exception:
        pass

    try:
        invoices = await uzum.get_fbo_invoices()
    except ApiError as e:
        await callback.message.answer(f"⚠️ Olinmadi.\n<code>{e}</code>")
        return

    new_ones = [i for i in invoices if _fbo_is_new(i)]

    try:
        await callback.message.edit_text(
            f"<b>🚛 FBO yuk xatlari</b>\n\n"
            f"Yangi (hali qabul qilinmagan): <b>{len(new_ones)}</b> ta\n"
            f"<i>Qabul qilingan aktlar bu yerda ko'rsatilmaydi — "
            f"ular uchun endi hech qanday amal kerak emas.</i>"
        )
    except Exception:
        pass

    if not new_ones:
        return

    for inv in new_ones[:10]:
        rows = [[
            InlineKeyboardButton(
                text="📦 Mahsulotlar",
                callback_data=f"fboprod:{inv['shop_id']}:{inv['id']}",
            ),
            InlineKeyboardButton(
                text="📗 Excel (zelyoniy koridor)",
                callback_data=f"fboxls:{inv['shop_id']}:{inv['id']}",
            ),
        ]]
        await callback.message.answer(
            f"📦 <b>Yuk xati № {inv['number']}</b>\n"
            f"🏪 {inv['shop_name']}\n"
            f"📋 Holati: {inv['status_label']}\n"
            f"💰 Qiymati: {inv['total_price']:,} so'm\n".replace(",", " ")
            + f"📤 Jo'natilgan: <b>{inv['total_to_stock']}</b> dona",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("fboprod:"))
async def cb_fbo_products(callback: CallbackQuery) -> None:
    """FBO yuk xatidagi mahsulotlar ro'yxati — soni bilan."""
    _, shop_id, invoice_id = callback.data.split(":", 2)
    await callback.answer("Mahsulotlar so'ralmoqda…")

    try:
        products = await uzum.get_fbo_invoice_products(int(shop_id), invoice_id)
    except ApiError as e:
        await callback.message.answer(f"⚠️ Olinmadi.\n<code>{e}</code>")
        return

    if not products:
        await callback.message.answer("Mahsulot topilmadi.")
        return

    lines = [f"📦 <b>Yuk xati № {invoice_id} — mahsulotlar</b>", ""]
    for p in products:
        mark = "✅" if p["accepted"] >= p["to_stock"] else "⚠️"
        lines.append(
            f"{mark} <b>{p['name'][:40]}</b>\n"
            f"   SKU: <code>{p['sku']}</code>\n"
            f"   Jo'natilgan: {p['to_stock']} · Qabul qilingan: {p['accepted']}"
        )
    await callback.message.answer("\n\n".join(lines))


@router.callback_query(F.data.startswith("fboxls:"))
async def cb_fbo_excel_start(callback: CallbackQuery) -> None:
    """
    Excel yaratishni boshlaydi — avval rejalashtirilgan sanani so'raydi.

    Fayl NOMI bugungi sanadan olinadi (avtomatik), lekin jadval
    ICHIDAGI "Планируемая дата отгрузки" ustuni FOYDALANUVCHI
    bergan sana bilan to'ldiriladi — bular ikki xil narsa.
    """
    _, shop_id, invoice_id = callback.data.split(":", 2)
    await callback.answer()
    _AWAITING_DATE[callback.from_user.id] = (int(shop_id), invoice_id)
    await callback.message.answer(
        "📗 <b>Zelyoniy koridor — Excel</b>\n\n"
        "Qaysi sanaga rejalashtirilgan? Masalan: <code>25.07.2026</code>\n\n"
        "<i>Bu — jadvaldagi «Планируемая дата отгрузки» ustuni uchun. "
        "Nakladnoyning haqiqiy sanasi Uzumdan avtomatik olinadi.</i>"
    )


@router.message(F.text, F.func(lambda m: m.from_user.id in _AWAITING_DATE))
async def on_fbo_date(message: Message) -> None:
    """Foydalanuvchi rejalashtirilgan sanani yozganda — Excel quriladi."""
    shop_id, invoice_id = _AWAITING_DATE.pop(message.from_user.id)
    planned_date = (message.text or "").strip()

    wait = await message.answer("⏳ Excel tayyorlanmoqda…")
    try:
        invoices = await uzum.get_fbo_invoices(shop_ids=[shop_id])
        invoice = next((i for i in invoices if str(i["id"]) == str(invoice_id)), None)
        products = await uzum.get_fbo_invoice_products(shop_id, invoice_id)
    except ApiError as e:
        await wait.edit_text(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    if not invoice:
        await wait.edit_text("⚠️ Bu yuk xati topilmadi.")
        return

    from app.services import fbo_excel
    from aiogram.types import BufferedInputFile
    from datetime import datetime as _dt

    xlsx = fbo_excel.build(invoice, products, planned_date)
    fname = f"zelyoniy_koridor_{_dt.now():%d.%m.%Y}.xlsx"

    await wait.delete()
    await message.answer_document(
        BufferedInputFile(xlsx, filename=fname),
        caption=(
            f"📗 Yuk xati № {invoice['number']}\n"
            f"Rejalashtirilgan sana: {planned_date}\n\n"
            f"<i>«Ссылка на акты» ustuni bo'sh — havolani o'zingiz "
            f"qo'shing.</i>"
        ),
    )


@router.message(Command("fbo"))
async def cmd_fbo_shortcut(message: Message, employee: dict) -> None:
    """/fbo — to'g'ridan-to'g'ri FBO bo'limiga o'tish (menyusiz)."""
    await message.answer(
        "<b>🚛 FBO</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📦 Yuk xatlarini ko'rish", callback_data="top:fbo")
        ]]),
    )


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
            "<i>Bu — yo'lda turgan BARCHA buyurtmalar soni (bir nechta "
            "aktga tarqalgan bo'lishi mumkin). «Aktlarni ochish» esa "
            "faqat hali OCHIQ (Uzum tomonidan qabul qilinmagan) "
            "aktlarni ko'rsatadi — shuning uchun ikkala son boshqa-boshqa "
            "bo'lishi normal: Uzum allaqachon qabul qilgan akt bu "
            "ro'yxatda ko'rinmaydi, garchi uning buyurtmalari hali "
            "yo'lda bo'lsa ham.</i>",
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
    «Yangilar» guruhini yuboradi.

    Umumiy funksiyaga o'tkazildi (app/bot/cards.py) — u yerdan FBS
    menyusi ham, guruhga avtomatik yuboriladigan e'lon ham bir xil
    ko'rinishda foydalanadi.
    """
    await cards.send_new_order_card(bot, chat_id, group)


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
