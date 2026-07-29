"""
AKTLAR (nakladnoy / поставка).

Har bir akt alohida xabar bo'lib chiqadi. Ustidagi tugmalar orqali:
  • ichidagi mahsulotlarni rasmi bilan ko'rish
  • aktning PDF variantini olish
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.integrations.base import ApiError
from app.integrations.uzum import uzum
from app.services import orders as order_service
from app.services import labels
from app.services import pdfmerge

log = logging.getLogger(__name__)
router = Router(name="invoices")


def _money(v) -> str:
    try:
        return f"{int(v):,}".replace(",", " ") + " so'm"
    except (TypeError, ValueError):
        return "—"


def _diff(it: dict) -> str:
    """Yuborilgan va qabul qilingan farqi — skrinshotdagidek 64/96 ko'rinishida."""
    acc = it.get("accepted") or 0
    if acc and acc != it["qty"]:
        return f"  ⚠️ <b>{acc}/{it['qty']}</b>"
    if acc:
        return "  ✅"
    return ""


def _item_caption(it: dict) -> str:
    caption = f"<b>{it['sku']}</b>\nSoni: {it['qty']}"
    acc, rej = it.get("accepted") or 0, it.get("not_accepted") or 0
    if rej:
        caption += f"\n⚠️ Qabul qilingan: <b>{acc}/{it['qty']}</b>"
    elif acc:
        caption += "\n✅ To'liq qabul qilindi"
    return caption


def akt_buttons(invoice_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Mahsulotlar", callback_data=f"akt:{invoice_id}"
                ),
                InlineKeyboardButton(
                    text="📄 Akt PDF", callback_data=f"aktpdf:{invoice_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏷 Yorliqlar va QR", callback_data=f"aktqr:{invoice_id}"
                ),
            ],
        ]
    )


def _akt_card(inv: dict) -> str:
    lines = [f"📋 <b>Yuk xati № {inv['number']}</b>"]
    if inv.get("shop_name"):
        lines.append(f"🏪 {inv['shop_name']}")
    lines.append(inv.get("status_label") or "")
    if inv.get("drop_off") and inv["drop_off"] != "—":
        lines.append(f"📍 {inv['drop_off']}")
    if inv.get("time_slot"):
        lines.append(f"🕐 {inv['time_slot']}")
    if inv.get("created"):
        lines.append(f"📅 {inv['created']:%d-%b %H:%M}")

    lines.append("")
    sent = inv.get("orders") or 0
    acc = inv.get("orders_accepted") or 0
    lines.append(f"📤 Yuborilgan: <b>{sent}</b> ta")
    if inv["status"] == "ACCEPTED" or acc:
        icon = "✅" if acc == sent else "⚠️"
        lines.append(f"{icon} Qabul qilingan: <b>{acc}</b> ta")
        if acc < sent:
            lines.append(f"❗️ <b>Tafovut: {sent - acc} ta</b>")
    if inv.get("total"):
        lines.append(f"💰 Umumiy qiymati: {_money(inv['total'])}")
    if inv.get("total_accepted") and inv["total_accepted"] != inv.get("total"):
        lines.append(f"💵 Qabul qilingani: {_money(inv['total_accepted'])}")
    return "\n".join(x for x in lines if x is not None)


@router.callback_query(F.data == "fbs:akt")
async def cb_akt_from_fbs(callback: CallbackQuery) -> None:
    """FBS bo'limidagi «Aktlarni ochish» tugmasi."""
    await callback.answer()
    await cmd_invoices(callback.message)


@router.message(Command("aktlar"))
@router.message(F.text == "📋 Aktlar")
async def cmd_invoices(message: Message) -> None:
    await _list_invoices(message, all_statuses=False)


@router.callback_query(F.data == "akthammasi")
async def cb_all_invoices(callback: CallbackQuery) -> None:
    await callback.answer("Hammasi so'ralmoqda…")
    await _list_invoices(callback.message, all_statuses=True)


async def _list_invoices(message: Message, all_statuses: bool) -> None:
    wait = await message.answer("⏳ Aktlar so'ralmoqda…")
    try:
        invoices = await uzum.get_invoices(all_statuses=all_statuses)
    except ApiError as e:
        await wait.edit_text(f"⚠️ Aktlar olinmadi.\n<code>{e}</code>")
        return

    if not invoices:
        await wait.edit_text(
            "Ish qolgan akt yo'q. 👌\n\n"
            "<i>Barcha aktlar Uzum tomonidan qabul qilingan.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📋 Hammasini ko'rish",
                                     callback_data="akthammasi")
            ]]) if not all_statuses else None,
        )
        return

    title = "Barcha aktlar" if all_statuses else "Ish qolgan aktlar"
    await wait.edit_text(f"📋 <b>{title}: {len(invoices)} ta</b>")
    for inv in invoices:
        await message.answer(_akt_card(inv), reply_markup=akt_buttons(inv["id"]))

    if not all_statuses:
        await message.answer(
            "<i>Qabul qilinganlarini ham ko'rish uchun:</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📋 Hammasini ko'rish",
                                     callback_data="akthammasi")
            ]]),
        )


@router.callback_query(F.data.startswith("akt:"))
async def cb_akt_items(callback: CallbackQuery, bot: Bot) -> None:
    """
    Akt ichidagi mahsulotlar — rasmi bilan.

    Faqat SHAXSIY chatga yuboriladi — guruhda bosilsa ham, guruh
    o'nlab rasm bilan to'lib ketmasin.
    """
    invoice_id = callback.data.split(":", 1)[1]
    uid = callback.from_user.id

    await callback.answer("Mahsulotlar so'ralmoqda…")
    try:
        await bot.send_chat_action(uid, "typing")
    except Exception:
        await callback.message.answer(
            "⚠️ Avval botga shaxsiy <code>/start</code> yozing — "
            "mahsulotlar shaxsiy chatga yuboriladi."
        )
        return

    try:
        items = await uzum.get_invoice_items(invoice_id)
    except ApiError as e:
        await bot.send_message(uid, f"⚠️ Olinmadi.\n<code>{e}</code>")
        return

    if not items:
        await bot.send_message(uid, "Bu aktda mahsulot topilmadi.")
        return

    total = sum(i["qty"] for i in items)
    accepted = sum(i.get("accepted") or 0 for i in items)
    rejected = sum(i.get("not_accepted") or 0 for i in items)

    head = [
        f"📦 <b>Yuk xati № {invoice_id}</b>",
        f"{len(items)} xil mahsulot · jami <b>{total}</b> dona",
    ]
    if accepted:
        head.append(f"✅ Qabul qilingan: <b>{accepted}</b>")
    if rejected:
        head.append(f"❌ Qabul qilinmagan: <b>{rejected}</b>")
    await bot.send_message(uid, "\n".join(head))

    # Har bir mahsulot alohida rasm — yozuvi ko'rinib tursin
    for it in items[:40]:
        caption = _item_caption(it)
        if it.get("photo"):
            try:
                await bot.send_photo(uid, it["photo"], caption=caption)
                continue
            except Exception as e:
                log.warning("Rasm yuborilmadi (%s): %s", it["sku"], e)
        await bot.send_message(uid, f"{caption}\n<i>{it['name'][:60]}</i>")

    if callback.message and callback.message.chat.type != "private":
        try:
            await callback.message.answer(
                f"📦 <b>{callback.from_user.full_name}</b> mahsulotlarni "
                f"shaxsiy chatiga oldi"
            )
        except Exception:
            pass

    if len(items) > 40:
        await callback.message.answer(f"… va yana {len(items) - 40} xil mahsulot.")


@router.callback_query(F.data.startswith("aktpdf:"))
async def cb_akt_pdf(callback: CallbackQuery) -> None:
    """Aktning PDF varianti."""
    invoice_id = callback.data.split(":", 1)[1]
    await callback.answer("PDF tayyorlanmoqda…")

    try:
        content, url = await uzum.get_invoice_pdf(invoice_id)
    except ApiError as e:
        await callback.message.answer(f"⚠️ PDF olinmadi.\n<code>{e}</code>")
        return

    if content:
        await callback.message.answer_document(
            BufferedInputFile(content, filename=f"akt_{invoice_id}.pdf"),
            caption=f"📄 Akt № {invoice_id}",
        )
    elif url:
        await callback.message.answer(f"📄 Akt № {invoice_id}\n{url}")
    else:
        await callback.message.answer(
            "PDF olinmadi.\n\n"
            "<i>Akt hali yopilmagan bo'lishi mumkin — Uzum uni qabul qilgandan "
            "keyin hujjat tayyor bo'ladi.</i>"
        )


@router.callback_query(F.data.startswith("aktqr:"))
async def cb_akt_labels(callback: CallbackQuery, employee: dict, bot: Bot) -> None:
    """Aktdagi barcha buyurtmalar uchun yorliq va mahsulot QR."""
    invoice_id = callback.data.split(":", 1)[1]
    uid = callback.from_user.id

    await callback.answer("Tayyorlanmoqda…")
    try:
        await bot.send_chat_action(uid, "upload_document")
    except Exception:
        await callback.message.answer(
            "⚠️ Avval botga shaxsiy <code>/start</code> yozing."
        )
        return

    try:
        order_ids = await uzum.get_invoice_order_ids(invoice_id)
    except ApiError as e:
        await bot.send_message(uid, f"⚠️ Aktdagi buyurtmalar olinmadi.\n<code>{e}</code>")
        return

    if not order_ids:
        await bot.send_message(uid, "Bu aktda buyurtma topilmadi.")
        return

    wait = await bot.send_message(
        uid,
        f"🏷 <b>Akt № {invoice_id}</b>\n"
        f"{len(order_ids)} ta buyurtma · ~{len(order_ids)} soniya",
    )

    # Aktdagi buyurtmalarning to'liq ma'lumoti (QR uchun SKU kerak)
    try:
        all_orders = await order_service.orders_for_user(employee)
    except Exception:
        all_orders = []
    mine = [o for o in all_orders if o["order_id"] in set(order_ids)]

    if not mine:
        # Tafsilot topilmasa — hech bo'lmasa yorliqlarni beramiz
        merged, pdfs, fail = await labels.order_labels(order_ids)
        if merged:
            await bot.send_document(
                uid,
                BufferedInputFile(merged, filename=f"akt_{invoice_id}_yorliqlar.pdf"),
                caption=f"🏷 Akt № {invoice_id} · {len(pdfs)} ta yorliq",
            )
            await wait.edit_text(f"✅ {len(pdfs)} ta yorliq (QR'siz)")
        else:
            await wait.edit_text("⚠️ Yorliq olinmadi.")
        return

    pdf, parts, ok, fail, note = await labels.combined(mine)
    if pdf:
        pages = pdfmerge.page_count(pdf)
        await bot.send_document(
            uid,
            BufferedInputFile(pdf, filename=f"akt_{invoice_id}_yorliqlar.pdf"),
            caption=(
                f"🏷 <b>Akt № {invoice_id}</b>\n"
                f"{ok} ta buyurtma · {pages} bet\n\n"
                f"<i>Tartib: yorliq → QR → yorliq → QR …</i>"
            ),
        )
        msg = f"✅ {ok} ta buyurtma · {pages} bet"
    elif parts:
        for n, part in enumerate(parts, 1):
            kind = "yorliq" if n % 2 else "qr"
            try:
                await bot.send_document(
                    uid, BufferedInputFile(part, filename=f"{n:02d}_{kind}.pdf")
                )
            except Exception:
                pass
        msg = f"✅ {len(parts)} ta fayl (birlashtirilmadi)"
    else:
        msg = "⚠️ Yorliq olinmadi"
    if fail:
        msg += f"\n⚠️ {fail} tasi olinmadi"
    if note:
        msg += f"\n<code>{note[:250]}</code>"
    try:
        await wait.edit_text(msg)
    except Exception:
        pass

    if callback.message and callback.message.chat.type != "private":
        try:
            await callback.message.answer(
                f"🏷 <b>{employee['full_name']}</b> akt yorliqlarini oldi"
            )
        except Exception:
            pass
