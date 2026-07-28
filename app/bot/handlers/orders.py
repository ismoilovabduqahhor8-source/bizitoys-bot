"""
Buyurtmalar bilan ishlash: ro'yxat, hisobot, holatni o'zgartirish.
Guruh chatida ham, shaxsiy chatda ham bir xil ishlaydi.
"""
from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (BufferedInputFile, CallbackQuery, InputMediaPhoto,
                           Message)

from app.bot import cards
from app.bot.keyboards import employee_picker, group_actions, order_actions
from app.db import repo
from app.integrations.base import ApiError
from app.integrations.uzum import uzum
from app.config import settings
from app.services import orders as order_service
from app.services import grouping
from app.services import labels
from app.services import pdfmerge
from app.services import workflow

log = logging.getLogger(__name__)
router = Router(name="orders")

# Tabiiy tilda so'ralgan savollarni tanish uchun kalit so'zlar
PVZ_QUESTION = re.compile(
    r"(pvz|pvzga|jo'?nat|jonat|obor|yubor)", re.IGNORECASE
)
ORDER_QUESTION = re.compile(r"(buyurtma|zakaz|order)", re.IGNORECASE)


@router.message(Command("orders"))
@router.message(F.text == "📋 Bugungi buyurtmalar")
async def cmd_orders(message: Message, employee: dict) -> None:
    wait = await message.answer("⏳ Uzumdan olinmoqda…")
    try:
        items = await order_service.orders_for_user(employee)
    except ApiError as e:
        await wait.edit_text(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    active = [o for o in items if workflow.is_active(o["status"])]
    if not active:
        await wait.edit_text("Ish qolgan buyurtma yo'q. 👌")
        return

    counts = order_service.summarize(items)
    await wait.edit_text(order_service.format_summary(counts))

    # Buyurtmalarni akt bo'yicha guruhlaymiz — har biri alohida xabar bo'lmasin
    groups = grouping.build(active)
    is_admin = employee["role"] == repo.ROLE_ADMIN

    for g in groups[:12]:
        await cards.send_group_card(
            message.bot,
            message.chat.id,
            g,
            group_actions(
                g["gid"], g["stage"], is_admin,
                can_act=workflow.can_act(g["stage"], employee),
            ),
        )

    if len(groups) > 12:
        await message.answer(f"… va yana {len(groups) - 12} ta guruh.")


@router.message(Command("report"))
async def cmd_report(message: Message, employee: dict) -> None:
    """
    Buyurtma holati bo'yicha qisqa hisobot (nechta yangi, yig'ilmoqda va h.k).

    DIQQAT: «📊 Hisobot» tugmasi endi MOLIYAVIY hisobot menyusiga
    tegishli (app/bot/handlers/stock.py) — shuning uchun bu yerda
    faqat /report BUYRUG'I orqali chaqiriladi, tugma matniga
    bog'lanmaydi.
    """
    try:
        items = await order_service.orders_for_user(employee)
    except ApiError as e:
        await message.answer(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return
    counts = order_service.summarize(items)
    await message.answer(order_service.format_summary(counts))


@router.message(F.text.func(lambda t: bool(t and PVZ_QUESTION.search(t) and ORDER_QUESTION.search(t))))
async def natural_question(message: Message, employee: dict) -> None:
    """«Bugungi buyurtmalar PVZga oborildimi?» kabi savollarga javob."""
    try:
        done, counts = await order_service.all_shipped_today()
    except ApiError as e:
        await message.answer(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    if counts["total"] == 0:
        await message.answer("Bugun hali buyurtma tushmagan.")
        return

    if done:
        await message.answer(
            f"✅ Ha, bugungi <b>{counts['total']}</b> ta buyurtmaning hammasi jo'natilgan."
        )
    else:
        await message.answer(
            f"❌ Yo'q, hali <b>{counts['pending']}</b> ta buyurtma jo'natilmagan.\n\n"
            + order_service.format_summary(counts, "📊 Batafsil")
        )


# ------------------------------------------------------------------
#  Inline tugmalar
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("take:"))
async def cb_take(callback: CallbackQuery, employee: dict) -> None:
    order_id = callback.data.split(":", 1)[1]
    await repo.assign_order(order_id, employee["telegram_id"])
    await callback.answer("Buyurtma sizga biriktirildi ✅")
    order = await repo.get_order(order_id)
    status = order["local_status"] if order else "new"
    try:
        await callback.message.edit_reply_markup(
            reply_markup=order_actions(order_id, status, assigned=True)
        )
    except Exception:  # xabar o'zgarmagan bo'lsa Telegram xato beradi — muhim emas
        pass


@router.callback_query(F.data.startswith("stage:"))
async def cb_stage(callback: CallbackQuery, employee: dict, bot: Bot) -> None:
    """Ish oqimida keyingi bosqichga o'tish."""
    _, order_id, new_stage = callback.data.split(":", 2)

    order = await repo.get_order(order_id)
    current = order["local_status"] if order else "new"

    # 1) Bu bosqichda harakat qilishga ruxsat bormi?
    if not workflow.can_act(current, employee):
        need = workflow.responsible_role(current)
        await callback.answer(
            f"Bu bosqich {repo.ROLE_LABELS.get(need, 'boshqa xodim')} uchun.",
            show_alert=True,
        )
        return

    # 2) Bu o'tish ruxsat etilganmi?
    allowed = [code for code, _ in workflow.next_actions(current)]
    if new_stage not in allowed:
        await callback.answer("Bu bosqichga o'tib bo'lmaydi.", show_alert=True)
        return

    # 3) Mas'ul biriktirilmagan bo'lsa — o'ziga biriktiramiz
    if order and not order.get("employee_id"):
        await repo.assign_order(order_id, employee["telegram_id"])

    await repo.set_local_status(order_id, new_stage, employee["telegram_id"])
    uzum.invalidate_cache()

    stage = workflow.get(new_stage)
    await callback.answer(f"Belgilandi: {stage.label}")

    try:
        await callback.message.edit_text(
            f"<code>{order_id}</code>\n"
            f"{stage.label}\n"
            f"{workflow.progress_bar(new_stage)}\n"
            f"👤 {employee['full_name']}",
            reply_markup=order_actions(
                order_id, new_stage, True,
                is_admin=employee["role"] == repo.ROLE_ADMIN,
                can_act=workflow.can_act(new_stage, employee),
            ),
        )
    except Exception:
        pass

    await _notify_group(bot, order_id, new_stage, employee)
    await _notify_next_role(bot, order_id, new_stage)


async def _notify_group(bot: Bot, order_id: str, stage_code: str, actor: dict) -> None:
    """Guruhga bosqich o'zgarganini bildiradi."""
    if not settings.group_chat_id:
        return
    stage = workflow.get(stage_code)
    icon = "⚠️" if stage_code == "shortage" else "▶️"
    try:
        await bot.send_message(
            settings.group_chat_id,
            f"{icon} <code>{order_id}</code> — {stage.label}\n"
            f"{workflow.progress_bar(stage_code)}\n"
            f"👤 {actor['full_name']}",
        )
    except Exception as e:
        log.warning("Guruhga xabar yuborilmadi: %s", e)


async def _notify_next_role(bot: Bot, order_id: str, stage_code: str) -> None:
    """Keyingi bosqich mas'uliga xabar beradi."""
    role = workflow.responsible_role(stage_code)
    if not role:
        return
    stage = workflow.get(stage_code)
    people = await repo.employees_by_role(role, only_present=True)
    if not people:
        people = await repo.employees_by_role(role)

    for person in people:
        try:
            await bot.send_message(
                person["telegram_id"],
                f"📌 <b>Sizning navbatingiz</b>\n\n"
                f"Buyurtma: <code>{order_id}</code>\n"
                f"Bosqich: {stage.label}\n\n"
                f"/orders",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("label:"))
async def cb_label(callback: CallbackQuery, employee: dict) -> None:
    """QR / yorliqni chiqarish."""
    order_id = callback.data.split(":", 1)[1]
    await callback.answer("Yorliq so'ralmoqda…")

    try:
        content, url = await uzum.get_label(order_id)
    except ApiError as e:
        await callback.message.answer(f"⚠️ Yorliq olinmadi.\n<code>{e}</code>")
        return

    if content:
        await callback.message.answer_document(
            BufferedInputFile(content, filename=f"yorliq_{order_id}.pdf"),
            caption=f"🏷 <code>{order_id}</code> uchun yorliq",
        )
    elif url:
        await callback.message.answer(f"🏷 Yorliq: {url}")
    else:
        await callback.message.answer(
            "Yorliq olinmadi.\n\n"
            "<i>Test rejimida yorliq mavjud emas, yoki Uzum bu buyurtma uchun "
            "hali yorliq tayyorlamagan.</i>"
        )


# ------------------------------------------------------------------
#  ADMIN QO'LDA BIRIKTIRADI
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("assign:"))
async def cb_assign_start(callback: CallbackQuery, employee: dict) -> None:
    """Admin xodimlar ro'yxatini ochadi — faqat bugun ishdagilar."""
    if employee["role"] != repo.ROLE_ADMIN:
        await callback.answer("Faqat admin biriktira oladi", show_alert=True)
        return

    order_id = callback.data.split(":", 1)[1]
    people = []
    for role in (repo.ROLE_PICKER, repo.ROLE_SKLAD, repo.ROLE_DRIVER, repo.ROLE_EMPLOYEE):
        people += await repo.employees_by_role(role, only_present=True)

    if not people:
        await callback.answer(
            "Bugun ishda belgilangan xodim yo'q.\n"
            "Avval davomat belgilansin (/davomat).",
            show_alert=True,
        )
        return

    await callback.answer()
    try:
        await callback.message.edit_reply_markup(
            reply_markup=employee_picker(order_id, people)
        )
    except Exception:
        await callback.message.answer(
            "Kimga biriktiramiz?", reply_markup=employee_picker(order_id, people)
        )


@router.callback_query(F.data.startswith("assignto:"))
async def cb_assign_to(callback: CallbackQuery, employee: dict, bot: Bot) -> None:
    if employee["role"] != repo.ROLE_ADMIN:
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    _, order_id, target_id = callback.data.split(":", 2)
    target = await repo.get_employee(int(target_id))
    if not target:
        await callback.answer("Xodim topilmadi", show_alert=True)
        return

    # "g" bilan boshlansa — butun guruh
    if order_id.startswith("g"):
        ids = grouping.recall(order_id[1:])
        for oid in ids:
            await repo.assign_order(oid, target["telegram_id"])
        await callback.answer(f"{len(ids)} ta buyurtma: {target['full_name']}")
        try:
            await callback.message.edit_text(
                f"👤 <b>{len(ids)} ta buyurtma</b> biriktirildi:\n"
                f"{target['full_name']} ({repo.ROLE_LABELS.get(target['role'], '')})"
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                target["telegram_id"],
                f"📌 <b>Sizga {len(ids)} ta buyurtma biriktirildi</b>\n\n/orders",
            )
        except Exception:
            pass
        return

    await repo.assign_order(order_id, target["telegram_id"])
    await callback.answer(f"Biriktirildi: {target['full_name']}")

    order = await repo.get_order(order_id)
    status = order["local_status"] if order else "new"

    try:
        await callback.message.edit_text(
            f"<code>{order_id}</code>\n"
            f"{repo.STATUS_LABELS.get(status, status)}\n"
            f"👤 Mas'ul: <b>{target['full_name']}</b> "
            f"({repo.ROLE_LABELS.get(target['role'], '')})",
            reply_markup=order_actions(order_id, status, True, is_admin=True),
        )
    except Exception:
        pass

    # Xodimga shaxsiy xabar
    try:
        await bot.send_message(
            target["telegram_id"],
            f"📌 <b>Sizga yangi vazifa</b>\n\n"
            f"Buyurtma: <code>{order_id}</code>\n"
            f"Holat: {repo.STATUS_LABELS.get(status, status)}\n\n"
            f"Ko'rish uchun: /orders",
        )
    except Exception as e:
        log.warning("Xodimga xabar yuborilmadi (%s): %s", target["telegram_id"], e)


@router.callback_query(F.data.startswith("cancelassign:"))
async def cb_cancel_assign(callback: CallbackQuery, employee: dict) -> None:
    order_id = callback.data.split(":", 1)[1]
    order = await repo.get_order(order_id)
    status = order["local_status"] if order else "new"
    assigned = bool(order and order.get("employee_id"))
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(
            reply_markup=order_actions(order_id, status, assigned, is_admin=True)
        )
    except Exception:
        pass


@router.message(Command("yorliqlar"))
@router.message(F.text == "🏷 Yorliqlar")
async def cmd_labels_bulk(message: Message, employee: dict) -> None:
    """Barcha faol buyurtmalar uchun yorliq va QR."""
    try:
        items = await order_service.orders_for_user(employee)
    except ApiError as e:
        await message.answer(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    active = [o for o in items if workflow.is_active(o["status"])]
    if not active:
        await message.answer("Yig'ilishi kerak bo'lgan buyurtma yo'q. 👌")
        return

    wait = await message.answer(
        f"🏷 <b>{len(active)} ta buyurtma</b> uchun yorliq so'ralmoqda…\n"
        f"<i>~{len(active)} soniya</i>"
    )
    await _send_labels(message.bot, message.chat.id, active, wait)


# ==================================================================
#  GURUH (POSTAVKA) BILAN ISHLASH
# ==================================================================
async def _private(callback: CallbackQuery, bot: Bot) -> int | None:
    """
    Batafsil ma'lumot SHAXSIY chatga yuboriladi.

    Guruhda o'nlab rasm va PDF hammani bosib ketadi. Yig'uvchiga esa
    ular shaxsiy chatda qulayroq.
    """
    uid = callback.from_user.id
    try:
        await bot.send_chat_action(uid, "typing")
        return uid
    except Exception:
        await callback.answer(
            "Avval botga shaxsiy /start yozing — "
            "ma'lumot shaxsiy chatga yuboriladi.",
            show_alert=True,
        )
        return None


async def _group_orders(gid: str, employee: dict) -> list[dict]:
    ids = set(grouping.recall(gid))
    if not ids:
        return []
    items = await order_service.orders_for_user(employee)
    return [o for o in items if o["order_id"] in ids]


async def _send_labels(bot: Bot, chat_id: int, orders: list[dict], wait) -> None:
    """
    Yorliqlar — navbatlashgan tartibda: yorliq -> QR -> yorliq -> QR.

    Birlashtirib bo'lmasa (pypdf yo'q bo'lsa), betlar birma-bir
    yuboriladi. Ish hech qachon jim yo'qolmasligi kerak.
    """
    shop = (orders[0].get("shop_name") or "").replace(" ", "_")[:20] or "yorliq"
    pdf, parts, ok, fail, note = await labels.combined(orders)

    if pdf:
        pages = pdfmerge.page_count(pdf)
        await bot.send_document(
            chat_id,
            BufferedInputFile(pdf, filename=f"yorliqlar_{shop}_{ok}ta.pdf"),
            caption=(
                f"🏷 <b>Yorliqlar va QR kodlar</b>\n"
                f"{ok} ta buyurtma · {pages} bet\n\n"
                f"<i>Tartib: yorliq → QR → yorliq → QR …\n"
                f"Yorliq qutiga, QR tovarning o'ziga.</i>"
            ),
        )
        msg = f"✅ {ok} ta buyurtma · {pages} bet"

    elif parts:
        # Birlashmadi — betlarni alohida yuboramiz
        await bot.send_message(
            chat_id,
            f"🏷 <b>{len(parts)} ta fayl</b>\n"
            f"<i>Birlashtirilmadi, alohida yuborilmoqda</i>",
        )
        for n, part in enumerate(parts, 1):
            kind = "yorliq" if n % 2 else "qr"
            try:
                await bot.send_document(
                    chat_id,
                    BufferedInputFile(part, filename=f"{n:02d}_{kind}.pdf"),
                )
            except Exception as e:
                log.warning("Fayl yuborilmadi: %s", e)
        msg = f"✅ {len(parts)} ta fayl yuborildi"

    else:
        msg = "⚠️ Yorliq olinmadi"

    if fail:
        msg += f"\n⚠️ {fail} tasi olinmadi"
    if note:
        msg += f"\n<code>{note[:300]}</code>"
    try:
        await wait.edit_text(msg)
    except Exception:
        pass


@router.callback_query(F.data.startswith("grp:"))
async def cb_group_open(callback: CallbackQuery, employee: dict, bot: Bot) -> None:
    """Buyurtmalarni ochish — rasmlar va raqamlar shaxsiy chatga."""
    gid = callback.data.split(":", 1)[1]
    uid = await _private(callback, bot)
    if uid is None:
        return

    await callback.answer("Shaxsiy chatingizga yuborilmoqda…")
    orders = await _group_orders(gid, employee)
    if not orders:
        await bot.send_message(uid, "Guruh eskirgan. /orders bosing.")
        return

    with_photo, without = grouping.items_with_photos(orders)
    total = sum(r["qty"] for r in with_photo + without)

    await bot.send_message(
        uid,
        f"📦 <b>{len(orders)} ta buyurtma · "
        f"{len(with_photo) + len(without)} xil mahsulot</b>\n"
        f"Jami: <b>{total}</b> dona",
    )

    for start in range(0, len(with_photo), 10):
        chunk = with_photo[start:start + 10]
        media = [
            InputMediaPhoto(media=r["photo"],
                            caption=f"<b>{r['sku']}</b>\nSoni: {r['qty']}",
                            parse_mode="HTML")
            for r in chunk
        ]
        try:
            await bot.send_media_group(uid, media)
        except Exception as e:
            log.warning("Albom yuborilmadi: %s", e)
            for r in chunk:
                await bot.send_message(uid, f"<b>{r['sku']}</b>\nSoni: {r['qty']}")

    if without:
        lines = ["<b>Rasmsiz mahsulotlar</b>", ""]
        lines += [f"<b>{r['sku']}</b> — Soni: {r['qty']}" for r in without[:30]]
        await bot.send_message(uid, "\n".join(lines))

    nums = ", ".join(f"<code>{o.get('public_id', o['order_id'])}</code>" for o in orders[:40])
    await bot.send_message(uid, f"🧾 <b>Buyurtma raqamlari</b>\n\n{nums}")

    if callback.message and callback.message.chat.type != "private":
        try:
            await callback.message.answer(
                f"📤 <b>{employee['full_name']}</b> ro'yxatni oldi "
                f"({len(orders)} ta buyurtma)"
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("glabel:"))
async def cb_group_labels(callback: CallbackQuery, employee: dict, bot: Bot) -> None:
    """Yorliq va QR — shaxsiy chatga, ikkita alohida PDF."""
    gid = callback.data.split(":", 1)[1]
    uid = await _private(callback, bot)
    if uid is None:
        return

    orders = await _group_orders(gid, employee)
    if not orders:
        await callback.answer("Guruh eskirgan.", show_alert=True)
        return

    await callback.answer("Tayyorlanmoqda…")
    wait = await bot.send_message(
        uid,
        f"🏷 <b>{len(orders)} ta buyurtma</b> uchun yorliq so'ralmoqda…\n"
        f"<i>~{len(orders)} soniya</i>",
    )
    await _send_labels(bot, uid, orders, wait)

    if callback.message and callback.message.chat.type != "private":
        try:
            await callback.message.answer(
                f"🏷 <b>{employee['full_name']}</b> yorliqlarni oldi"
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("gstage:"))
async def cb_group_stage(callback: CallbackQuery, employee: dict, bot: Bot) -> None:
    """Butun guruhni keyingi bosqichga surish."""
    _, gid, new_stage = callback.data.split(":", 2)
    orders = await _group_orders(gid, employee)
    if not orders:
        await callback.answer("Guruh eskirgan. /orders bosing.", show_alert=True)
        return

    current = orders[0]["status"]
    if not workflow.can_act(current, employee):
        need = workflow.responsible_role(current)
        await callback.answer(
            f"Bu bosqich {repo.ROLE_LABELS.get(need, 'boshqa xodim')} uchun.",
            show_alert=True,
        )
        return

    if new_stage not in [c for c, _ in workflow.next_actions(current)]:
        await callback.answer("Bu bosqichga o'tib bo'lmaydi.", show_alert=True)
        return

    for o in orders:
        oid = o["order_id"]
        local = await repo.get_order(oid)
        if local and not local.get("employee_id"):
            await repo.assign_order(oid, employee["telegram_id"])
        await repo.set_local_status(oid, new_stage, employee["telegram_id"])

    uzum.invalidate_cache()
    stage = workflow.get(new_stage)
    await callback.answer(f"{len(orders)} ta buyurtma: {stage.label}")

    text = (
        f"{stage.label}   {workflow.progress_bar(new_stage)}\n\n"
        f"📦 {len(orders)} ta buyurtma\n"
        f"👤 {employee['full_name']}"
    )
    kb = group_actions(gid, new_stage, employee["role"] == repo.ROLE_ADMIN,
                       can_act=workflow.can_act(new_stage, employee))
    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass

    if settings.group_chat_id:
        try:
            await bot.send_message(
                settings.group_chat_id,
                f"▶️ <b>{len(orders)} ta buyurtma</b> — {stage.label}\n"
                f"{workflow.progress_bar(new_stage)}\n"
                f"👤 {employee['full_name']}",
            )
        except Exception:
            pass

    await _notify_next_role(bot, f"{len(orders)} ta buyurtma", new_stage)


@router.callback_query(F.data.startswith("gassign:"))
async def cb_group_assign(callback: CallbackQuery, employee: dict) -> None:
    """Butun guruhni bitta xodimga biriktirish."""
    if employee["role"] != repo.ROLE_ADMIN:
        await callback.answer("Faqat admin", show_alert=True)
        return
    gid = callback.data.split(":", 1)[1]

    people = []
    for role in (repo.ROLE_PICKER, repo.ROLE_SKLAD, repo.ROLE_DRIVER, repo.ROLE_EMPLOYEE):
        people += await repo.employees_by_role(role, only_present=True)
    if not people:
        people = await repo.list_employees()
    if not people:
        await callback.answer("Xodim yo'q.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        "Kimga biriktiramiz?", reply_markup=employee_picker(f"g{gid}", people)
    )


@router.message(Command("shosh"))
@router.message(F.text == "🔥 Shoshilinch")
async def cmd_urgent(message: Message, employee: dict) -> None:
    """
    Shoshilinch — bir joyda uch narsa:
      1. Muddati kam qolgan buyurtmalar (deadline yaqin)
      2. FBO'da top mahsulotlar (eng ko'p sotilgan)
      3. 20 tadan kam qolgan tovarlar

    Nega birga? Xodim ertalab bitta buyruq bilan "bugun nimaga
    e'tibor berish kerak"ni ko'rishi uchun.
    """

    wait = await message.answer("⏳ Tekshirilmoqda…")

    # --- 1. Muddati yaqin buyurtmalar ---
    try:
        items = await order_service.orders_for_user(employee)
    except ApiError as e:
        await wait.edit_text(f"⚠️ Buyurtmalar olinmadi.\n<code>{e}</code>")
        return

    # Faqat ICHKI bosqichlar (sklad/yig'uvchi ishlaydigan) hisobga
    # olinadi. "Yo'lda" va "Postavkada" — haydovchi ishi, bu yerda
    # kerak emas (haydovchi o'z /orders ro'yxatida ko'radi).
    DRIVER_STAGES = {"to_pvz", "in_postavka"}
    urgent = [
        o for o in items
        if workflow.is_active(o["status"])
        and o["status"] not in DRIVER_STAGES
        and order_service.is_urgent(o)
    ]
    urgent.sort(key=lambda o: o.get("deliver_until_ts") or 0)

    await wait.delete()

    if urgent:
        await message.answer(f"🔥 <b>Muddati kam qolgan: {len(urgent)} ta</b>")
        for o in urgent[:8]:
            emp = await repo.get_employee(o["employee_id"]) if o.get("employee_id") else None
            await message.answer(
                order_service.format_order_card(o, emp["full_name"] if emp else None),
                reply_markup=order_actions(
                    o["order_id"], o["status"], o.get("employee_id") is not None,
                    is_admin=employee["role"] == repo.ROLE_ADMIN,
                    can_act=workflow.can_act(o["status"], employee),
                ),
            )
        if len(urgent) > 8:
            await message.answer(f"… va yana {len(urgent) - 8} ta.")
    else:
        await message.answer("✅ Muddati yaqin buyurtma yo'q.")

    # --- 2 va 3: endi Billz emas, UZUM'ning o'z ma'lumotidan.
    #
    # FBO qoldig'i uchun Uzumda alohida maydon yo'q, lekin ikkita
    # haqiqiy son bor: quantityActive (umumiy) va quantityFbs
    # (faqat FBS omboringizda). Farqi — Uzum omborida (FBO) turgan
    # qism. "Top sotilgan" uchun esa avgdsales (kunlik o'rtacha
    # sotuv) × 7 — taxminiy haftalik son.
    try:
        stats = await uzum.get_product_stats()
    except ApiError as e:
        stats = []
        log.warning("Mahsulot statistikasi olinmadi: %s", e)

    if not stats:
        await message.answer(
            "\nℹ️ <i>Uzumdan mahsulot statistikasi olinmadi.</i>"
        )
        return

    top = sorted(stats, key=lambda r: -r["sold_7d"])[:8]
    if top and top[0]["sold_7d"]:
        lines = ["", "🚚 <b>FBO — eng ko'p sotilgan (taxminiy, 7 kun)</b>", ""]
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(top):
            if not r["sold_7d"]:
                break
            mark = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{mark} {r['name'][:38]} — ~{r['sold_7d']} dona")
        await message.answer("\n".join(lines))

    low20 = sorted(
        [r for r in stats if r["fbo_qty"] < 20], key=lambda r: r["fbo_qty"]
    )
    if low20:
        lines = ["", f"⚠️ <b>FBO'da 20 tadan kam qolgan: {len(low20)} ta</b>", ""]
        for r in low20[:12]:
            icon = "🔴" if r["fbo_qty"] == 0 else "🟡"
            lines.append(f"{icon} {r['name'][:38]} — <b>{r['fbo_qty']}</b> dona")
        if len(low20) > 12:
            lines.append(f"\n… va yana {len(low20) - 12} ta.")
        await message.answer("\n".join(lines))
