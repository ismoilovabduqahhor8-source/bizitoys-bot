"""
POSTAVKA OCHISH — bosqichma-bosqich.

    /postavka
      1. Tayyor buyurtmalar ko'rsatiladi
      2. Qabul punkti (PVZ) tanlanadi
      3. Vaqt oynasi tanlanadi
      4. TASDIQLASH -> postavka ochiladi

Nega tasdiqlash bosqichi bor? Postavka ochilganda vaqt oynasi band
qilinadi va uni bekor qilish alohida ish. Tasodifiy bosishdan himoya.

Faqat admin ocha oladi.
"""
from __future__ import annotations

import logging
import uuid as uuid_lib
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import settings
from app.db import repo
from app.integrations.base import ApiError
from app.integrations.uzum import uzum
from app.services import grouping
from app.services import orders as order_service
from app.services import workflow

log = logging.getLogger(__name__)
router = Router(name="postavka")
TZ = ZoneInfo(settings.timezone)

# Tanlov jarayoni: gid -> {orders, points, dop, slot}
_DRAFTS: dict[str, dict[str, Any]] = {}

# Qidiruv kutilayotgan foydalanuvchilar: user_id -> gid
_AWAITING: dict[int, str] = {}

# Postavka faqat «В сборке» (Uzum: PACKING) dagi buyurtmalardan ochiladi.
# «В поставке» (PENDING_DELIVERY) dagilar uchun postavka ALLAQACHON ochilgan.
READY_STAGES = tuple(workflow.PACKING_STAGES)


def _to_dt(v: Any) -> datetime | None:
    """Uzum vaqtni ham millisekundda, ham matn ko'rinishida yuboradi."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v) / 1000, TZ)
        except (ValueError, OSError):
            return None
    txt = str(v)
    if txt.isdigit():
        try:
            return datetime.fromtimestamp(int(txt) / 1000, TZ)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def _fmt_slot(sl: dict) -> str:
    a, b = _to_dt(sl.get("from")), _to_dt(sl.get("to"))
    if a and b:
        same_day = a.date() == b.date()
        return f"{a:%d-%b %H:%M}–{b:%H:%M}" if same_day else \
               f"{a:%d-%b %H:%M} – {b:%d-%b %H:%M}"
    return "vaqt noma'lum"


@router.callback_query(F.data == "fbs:postavka_ochish")
async def cb_from_fbs(callback: CallbackQuery, employee: dict) -> None:
    """FBS bo'limidagi «Postavka ochish» tugmasi."""
    await callback.answer()
    await cmd_postavka(callback.message, employee)


@router.message(Command("postavka"))
@router.message(F.text == "🚚 Postavka ochish")
async def cmd_postavka(message: Message, employee: dict) -> None:
    # Admin va yig'uvchi ochishi mumkin — yig'uvchi tovarni yig'ib
    # bo'lgach, o'zi darrov postavka ocha oladi, admin kutish shart emas.
    if employee["role"] not in (repo.ROLE_ADMIN, repo.ROLE_PICKER):
        await message.answer("Postavkani faqat admin yoki yig'uvchi ocha oladi.")
        return

    wait = await message.answer("⏳ Tayyor buyurtmalar qidirilmoqda…")
    try:
        items = await order_service.orders_for_user(employee)
    except ApiError as e:
        await wait.edit_text(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    ready = [o for o in items if o["status"] in READY_STAGES]
    if not ready:
        stages = {}
        for o in items:
            stages[o["status"]] = stages.get(o["status"], 0) + 1
        detail = "\n".join(
            f"   {workflow.label(k)}: {v}" for k, v in stages.items() if v
        )
        await wait.edit_text(
            "Postavkaga tayyor buyurtma yo'q.\n\n"
            f"<b>Hozirgi holat:</b>\n{detail or '   —'}\n\n"
            "<i>Postavka «В сборке» bosqichidagi buyurtmalardan ochiladi.</i>"
        )
        return

    # Do'kon bo'yicha ajratamiz — bir postavka bitta do'kondan bo'ladi
    by_shop: dict[Any, list[dict]] = {}
    for o in ready:
        by_shop.setdefault(o.get("shop_id"), []).append(o)

    # Qaysi bosqichdan nechtasi kirayotganini ko'rsatamiz —
    # kabinetdagi «В сборке» soni bilan solishtirish uchun
    by_stage: dict[str, int] = {}
    for o in ready:
        by_stage[o["status"]] = by_stage.get(o["status"], 0) + 1
    detail = "\n".join(f"   {workflow.label(k)}: {v}" for k, v in by_stage.items())

    await wait.edit_text(
        f"🚚 <b>{len(ready)} ta buyurtma postavkaga tayyor</b>\n\n{detail}\n\n"
        "<i>Bu son kabinetdagi «В сборке» bilan mos kelishi kerak.</i>"
    )

    for shop_id, orders in by_shop.items():
        gid = grouping.remember(f"pv:{shop_id}", [o["order_id"] for o in orders])
        _DRAFTS[gid] = {"shop_id": shop_id, "orders": orders}

        items_n = sum(sum(i.get("qty", 1) for i in (o.get("items") or [])) for o in orders)
        await message.answer(
            f"<b>🏪 {orders[0].get('shop_name', shop_id)}</b>\n"
            f"📦 {len(orders)} ta buyurtma · {items_n} dona mahsulot\n"
            f"💰 {order_service._fmt_money(sum(o.get('total') or 0 for o in orders))}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚚 Postavka ochish", callback_data=f"pv:{gid}")
            ]]),
        )


def _filter_points(points: list[dict], query: str | None = None) -> list[dict]:
    """
    Punktlarni saralaydi.

    query berilsa — matn bo'yicha qidiradi.
    Berilmasa — .env dagi afzal tumanlar bo'yicha.
    """
    if query:
        q = query.lower().strip()
        return [p for p in points if q in p["address"].lower()]

    prefer = [x.lower() for x in settings.pvz_preferred]
    if not prefer:
        return points
    return [
        p for p in points
        if any(x in p["address"].lower() for x in prefer)
    ]


def _points_keyboard(gid: str, points: list[dict], shown: list[dict],
                     total: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"📍 {p['address'][:55]}",
            callback_data=f"pvdop:{gid}:{points.index(p)}",
        )]
        for p in shown[:12]
    ]
    rows.append([
        InlineKeyboardButton(text="🔎 Qidirish", callback_data=f"pvfind:{gid}"),
        InlineKeyboardButton(text=f"📋 Hammasi ({total})", callback_data=f"pvall:{gid}:0"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_points(message: Message, gid: str, shown: list[dict],
                       title: str) -> None:
    draft = _DRAFTS[gid]
    points = draft["points"]
    head = [f"<b>📍 {title}</b>"]
    if not shown:
        head.append("<i>Bu so'rov bo'yicha punkt topilmadi.</i>")
    else:
        head.append(f"<i>{len(shown)} ta ko'rsatilmoqda · jami {len(points)} ta</i>")
    head.append("")
    head.append(f"📦 Bu postavkada: <b>{len(draft['orders'])}</b> ta buyurtma")
    if draft.get("note"):
        head.append(draft["note"])

    await message.answer(
        "\n".join(head),
        reply_markup=_points_keyboard(gid, points, shown, len(points)),
    )


@router.callback_query(F.data.startswith("pv:"))
async def cb_choose_point(callback: CallbackQuery, employee: dict) -> None:
    """1-qadam: qabul punktini tanlash."""
    if employee["role"] not in (repo.ROLE_ADMIN, repo.ROLE_PICKER):
        await callback.answer("Faqat admin yoki yig'uvchi", show_alert=True)
        return

    gid = callback.data.split(":", 1)[1]
    draft = _DRAFTS.get(gid)
    if not draft:
        await callback.answer("Eskirgan. /postavka bosing.", show_alert=True)
        return

    await callback.answer("Qabul punktlari so'ralmoqda…")
    try:
        points, fitting, note = await uzum.get_drop_off_points(draft["orders"])
    except ApiError as e:
        await callback.message.answer(f"⚠️ Punktlar olinmadi.\n<code>{e}</code>")
        return

    if not points:
        sample = draft["orders"][0]
        await callback.message.answer(
            "❌ <b>Mos qabul punkti topilmadi</b>\n\n"
            f"<b>Tashxis:</b>\n<code>{note or 'javob bo\'sh'}</code>\n\n"
            f"Namuna: <code>{sample.get('public_id')}</code> · "
            f"{workflow.label(sample['status'])}\n\n"
            "<i>Batafsil tekshirish: /pvtekshir</i>"
        )
        return

    draft["orders"] = fitting
    draft["points"] = points
    draft["note"] = note

    preferred = _filter_points(points)
    if preferred:
        title = "Yaqin punktlar"
        shown = preferred
    else:
        title = "Qabul punktini tanlang"
        shown = points

    await _show_points(callback.message, gid, shown, title)


@router.callback_query(F.data.startswith("pvall:"))
async def cb_all_points(callback: CallbackQuery) -> None:
    """Hamma punktlar — sahifalab."""
    _, gid, page_s = callback.data.split(":", 2)
    draft = _DRAFTS.get(gid)
    if not draft:
        await callback.answer("Eskirgan. /postavka bosing.", show_alert=True)
        return

    page = int(page_s)
    points = draft["points"]
    per = 10
    chunk = points[page * per:(page + 1) * per]
    if not chunk:
        await callback.answer("Boshqa punkt yo'q")
        return

    rows = [
        [InlineKeyboardButton(text=f"📍 {p['address'][:55]}",
                              callback_data=f"pvdop:{gid}:{points.index(p)}")]
        for p in chunk
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pvall:{gid}:{page-1}"))
    nav.append(InlineKeyboardButton(text="🔎 Qidirish", callback_data=f"pvfind:{gid}"))
    if (page + 1) * per < len(points):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pvall:{gid}:{page+1}"))
    rows.append(nav)

    await callback.answer()
    try:
        await callback.message.edit_text(
            f"<b>📍 Barcha punktlar</b>\n"
            f"<i>{page * per + 1}–{min((page+1) * per, len(points))} / {len(points)}</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("pvfind:"))
async def cb_find_start(callback: CallbackQuery) -> None:
    """Qidiruvni boshlash — keyingi xabar qidiruv so'zi bo'ladi."""
    gid = callback.data.split(":", 1)[1]
    if gid not in _DRAFTS:
        await callback.answer("Eskirgan. /postavka bosing.", show_alert=True)
        return

    _AWAITING[callback.from_user.id] = gid
    await callback.answer()
    await callback.message.answer(
        "🔎 <b>Qidiruv</b>\n\n"
        "Tuman yoki ko'cha nomini yozing.\n"
        "<i>Masalan: Chilonzor, Sergeli, Amir Temur</i>"
    )


@router.message(F.text, F.func(lambda m: m.from_user.id in _AWAITING))
async def on_search_text(message: Message, employee: dict) -> None:
    """Qidiruv so'zi kelganda."""
    gid = _AWAITING.pop(message.from_user.id, None)
    draft = _DRAFTS.get(gid) if gid else None
    if not draft:
        return

    found = _filter_points(draft["points"], message.text)
    await _show_points(message, gid, found, f"«{message.text[:30]}» bo'yicha")


@router.callback_query(F.data.startswith("pvdop:"))
async def cb_choose_slot(callback: CallbackQuery, employee: dict) -> None:
    """2-qadam: vaqt oynasini tanlash."""
    _, gid, idx = callback.data.split(":", 2)
    draft = _DRAFTS.get(gid)
    if not draft or "points" not in draft:
        await callback.answer("Eskirgan. /postavka bosing.", show_alert=True)
        return

    point = draft["points"][int(idx)]
    draft["dop"] = point
    await callback.answer("Vaqt oynalari so'ralmoqda…")

    try:
        slots = await uzum.get_time_slots(point["uuid"], draft["orders"])
    except ApiError as e:
        await callback.message.answer(f"⚠️ Vaqt oynalari olinmadi.\n<code>{e}</code>")
        return

    if not slots:
        await callback.message.answer("Bu punktda bo'sh vaqt oynasi yo'q.")
        return

    usable = [s for s in slots if s.get("uuid")]
    if not usable:
        # Hujjatda uuid yo'q edi — haqiqiy javobda ham topilmasa, aniq aytamiz
        keys = list((slots[0].get("raw") or {}).keys())
        await callback.message.answer(
            "⚠️ Vaqt oynalari keldi, lekin ularning <b>uuid</b> maydoni topilmadi.\n\n"
            f"Javobdagi maydonlar:\n<code>{keys}</code>\n\n"
            "<i>Shu ro'yxatni Claude'ga yuboring — bir qatorlik tuzatish kerak.</i>"
        )
        return

    draft["slots"] = usable
    need = len(draft["orders"])

    rows = []
    for i, sl in enumerate(usable[:12]):
        rem = sl.get("remaining")
        if rem is None:
            cap_txt = ""
        elif rem < need:
            cap_txt = f" · ⚠️ {rem} joy"      # sig'maydi
        else:
            cap_txt = f" · {rem} joy"
        rows.append([InlineKeyboardButton(
            text=f"🕐 {_fmt_slot(sl)}{cap_txt}",
            callback_data=f"pvslot:{gid}:{i}",
        )])

    tight = [s for s in usable if (s.get("remaining") or 999) < need]
    head = [
        "<b>🕐 Vaqt oynasini tanlang</b>",
        f"📍 {point['address'][:60]}",
        f"📦 Kerak: <b>{need}</b> ta joy",
    ]
    if tight:
        head.append("")
        head.append(f"⚠️ <i>{len(tight)} ta oynada joy yetmaydi — ular belgilangan.</i>")

    await callback.message.answer(
        "\n".join(head),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("pvslot:"))
async def cb_confirm(callback: CallbackQuery, employee: dict) -> None:
    """3-qadam: tasdiqlash."""
    _, gid, idx = callback.data.split(":", 2)
    draft = _DRAFTS.get(gid)
    if not draft or "slots" not in draft:
        await callback.answer("Eskirgan. /postavka bosing.", show_alert=True)
        return

    slot = draft["slots"][int(idx)]
    draft["slot"] = slot
    need = len(draft["orders"])
    rem = slot.get("remaining")

    if rem is not None and rem < need:
        await callback.answer(
            f"Bu oynada faqat {rem} ta joy bor, sizga {need} ta kerak.",
            show_alert=True,
        )
        return

    await callback.answer()

    cap_line = f"🎫 Bo'sh joy: {rem}\n" if rem is not None else ""
    await callback.message.answer(
        "<b>⚠️ Tasdiqlang</b>\n\n"
        f"🏪 {draft['orders'][0].get('shop_name', '')}\n"
        f"📦 {need} ta buyurtma\n"
        f"📍 {draft['dop']['address'][:60]}\n"
        f"🕐 {_fmt_slot(slot)}\n"
        f"{cap_line}\n"
        "<i>Tasdiqlansa, vaqt oynasi band qilinadi.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Ha, postavka ochilsin",
                                  callback_data=f"pvgo:{gid}")],
            [InlineKeyboardButton(text="◀️ Bekor", callback_data=f"pvno:{gid}")],
        ]),
    )


@router.callback_query(F.data.startswith("pvno:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    gid = callback.data.split(":", 1)[1]
    _DRAFTS.pop(gid, None)
    await callback.answer("Bekor qilindi")
    try:
        await callback.message.edit_text("◀️ Bekor qilindi.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("pvgo:"))
async def cb_create(callback: CallbackQuery, employee: dict) -> None:
    """4-qadam: haqiqiy yaratish."""
    if employee["role"] not in (repo.ROLE_ADMIN, repo.ROLE_PICKER):
        await callback.answer("Faqat admin yoki yig'uvchi", show_alert=True)
        return

    gid = callback.data.split(":", 1)[1]
    draft = _DRAFTS.get(gid)
    if not draft or "slot" not in draft:
        await callback.answer("Eskirgan. /postavka bosing.", show_alert=True)
        return

    await callback.answer()
    # Ko'rinadigan javob — foydalanuvchi kutayotganini bilsin
    wait = await callback.message.answer(
        "⏳ <b>Holat tekshirilmoqda…</b>\n"
        "<i>Uzumdan yangi ma'lumot olinmoqda, ~10 soniya</i>"
    )

    # MUHIM: buyurtmalarni QAYTA olamiz. Uzum eski ma'lumotda
    # "holat o'zgardi" xatosini beradi.
    # Faqat SHU do'kon so'raladi — hammasi emas, tezroq bo'lsin.
    uzum.invalidate_cache()
    try:
        fresh = await uzum.get_orders(
            shop_ids=[draft["shop_id"]], use_cache=False
        )
    except ApiError as e:
        await wait.edit_text(f"⚠️ Yangilanmadi.\n<code>{e}</code>")
        return

    # Uzum holatini ichki bosqichga o'giramiz
    from app.services import workflow as wf
    for o in fresh:
        local = await repo.get_order(o["order_id"])
        o["status"] = wf.merge_with_uzum(
            (local or {}).get("local_status", "new"), o["status"]
        )

    wanted = {o["order_id"] for o in draft["orders"]}
    still_ok = [
        o for o in fresh
        if o["order_id"] in wanted and o["status"] in READY_STAGES
    ]
    dropped = len(wanted) - len(still_ok)

    if not still_ok:
        await wait.delete()
        await callback.message.answer(
            "⚠️ <b>Buyurtmalar holati o'zgargan</b>\n\n"
            "Hech biri endi postavkaga tayyor emas.\n"
            "Qaytadan boshlang: /postavka"
        )
        _DRAFTS.pop(gid, None)
        return

    if dropped:
        await callback.message.answer(
            f"ℹ️ {dropped} ta buyurtma holati o'zgargan — ular chiqarib tashlandi.\n"
            f"Postavkada <b>{len(still_ok)}</b> ta buyurtma qoladi."
        )

    order_ids = [o["order_id"] for o in still_ok]

    # sellerId — do'kon raqami EMAS, yuridik shaxs ID'si.
    # Uni /v1/finance/expenses javobidan olamiz.
    seller_id = await uzum.get_seller_id()
    if not seller_id:
        await callback.message.answer(
            "⚠️ <b>sellerId aniqlanmadi</b>\n\n"
            "Postavka yaratish uchun yuridik shaxs ID'si kerak. "
            "U <code>/v1/finance/expenses</code> javobidan olinadi, "
            "lekin u yerda ma'lumot topilmadi.\n\n"
            f"Do'kon raqami bilan urinib ko'raman: <code>{draft['shop_id']}</code>"
        )
        seller_id = draft["shop_id"]

    try:
        await wait.edit_text(
            f"⏳ <b>Postavka ochilmoqda…</b>\n"
            f"📦 {len(order_ids)} ta buyurtma · sellerId {seller_id}"
        )
    except Exception:
        pass

    try:
        res = await uzum.create_invoice(
            orders=still_ok,
            dop_uuid=draft["dop"]["uuid"],
            slot_uuid=draft["slot"]["uuid"],
            seller_id=seller_id,
            idempotency_key=str(uuid_lib.uuid4()),
        )
    except ApiError as e:
        msg = str(e)
        if "wrong-order-status" in msg:
            hint = (
                "Buyurtmalar holati yana o'zgardi.\n\n"
                "Bu odatda shundan bo'ladi:\n"
                "• kimdir kabinetda ular bilan ishlayapti\n"
                "• buyurtma allaqachon boshqa postavkaga qo'shilgan\n\n"
                "Qaytadan urinib ko'ring: /postavka"
            )
        elif "seller" in msg.lower() and "id" in msg.lower():
            hint = (
                f"sellerId noto'g'ri bo'lishi mumkin (hozir: <code>{seller_id}</code>).\n"
                "<i>Shu matnni Claude'ga yuboring.</i>"
            )
        else:
            hint = "<i>Shu matnni Claude'ga yuboring.</i>"

        # Buyurtmalarning HAQIQIY Uzum holatini ko'rsatamiz —
        # "holat o'zgardi" xatosi rostmi yoki ID noto'g'rimi, shundan bilinadi
        raw_states: dict[str, int] = {}
        for o in still_ok:
            rs = o.get("raw_status") or "?"
            raw_states[rs] = raw_states.get(rs, 0) + 1
        states = ", ".join(f"{k}: {v}" for k, v in raw_states.items())

        sample = still_ok[0]
        await callback.message.answer(
            f"⚠️ <b>Postavka ochilmadi</b>\n\n"
            f"<code>{msg[:400]}</code>\n\n"
            f"<b>Uzumdagi haqiqiy holat:</b> {states}\n"
            f"<b>Namuna:</b> Номер <code>{sample['order_id']}</code> · "
            f"ID <code>{sample.get('public_id')}</code>\n"
            f"<b>sellerId:</b> <code>{seller_id}</code>\n\n{hint}"
        )
        return

    _DRAFTS.pop(gid, None)
    num = res.get("number") or res.get("id") or "—"

    draft["orders"] = still_ok

    # Buyurtmalarni «postavkada» bosqichiga o'tkazamiz
    for oid in order_ids:
        await repo.set_local_status(oid, "in_postavka", employee["telegram_id"])
    uzum.invalidate_cache()

    if res.get("used"):
        log.info("Postavka ochildi, ishlagan ID turi: %s", res["used"])

    text = (
        f"✅ <b>Postavka ochildi</b>\n\n"
        f"📋 № {num}\n"
        f"📦 {len(order_ids)} ta buyurtma\n"
        f"📍 {draft['dop']['address'][:60]}\n"
        f"🕐 {_fmt_slot(draft['slot'])}\n\n"
        f"👤 {employee['full_name']}"
    )
    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)

    # Haydovchiga xabar
    for drv in await repo.employees_by_role(repo.ROLE_DRIVER):
        try:
            await callback.bot.send_message(
                drv["telegram_id"],
                f"🚚 <b>Yangi postavka</b>\n\n"
                f"📋 № {num}\n"
                f"📦 {len(order_ids)} ta buyurtma\n"
                f"📍 {draft['dop']['address'][:60]}\n"
                f"🕐 {_fmt_slot(draft['slot'])}\n\n"
                f"Shu vaqt ichida yetkazish kerak.",
            )
        except Exception:
            pass

    if settings.group_chat_id:
        try:
            await callback.bot.send_message(settings.group_chat_id, text)
        except Exception:
            pass


@router.message(Command("pvtekshir"))
async def cmd_probe(message: Message, employee: dict) -> None:
    """
    TASHXIS: qabul punktlari so'rovining haqiqiy javobini ko'rsatadi.

    Taxmin qilish o'rniga Uzum aynan nima yuborayotganini ko'ramiz.
    """
    if employee["role"] != repo.ROLE_ADMIN:
        return

    wait = await message.answer("⏳ 7 xil variant sinalmoqda… (~10 soniya)")
    try:
        items = await order_service.orders_for_user(employee)
    except ApiError as e:
        await wait.edit_text(f"⚠️ {e}")
        return

    ready = [o for o in items if o["status"] in READY_STAGES]
    if not ready:
        await wait.edit_text("Postavkaga tayyor buyurtma yo'q.")
        return

    # Bitta do'kondan, ko'pi bilan 7 ta
    shop = ready[0].get("shop_id")
    batch = [o for o in ready if o.get("shop_id") == shop][:7]

    head = [
        f"<b>🔬 Tashxis</b> · {len(batch)} ta buyurtma",
        f"do'kon: {batch[0].get('shop_name')}",
        "",
        "<b>Namunalar:</b>",
    ]
    for o in batch[:3]:
        head.append(
            f"  Номер: <code>{o['order_id']}</code>  "
            f"ID: <code>{o.get('public_id')}</code>"
        )
    await wait.edit_text("\n".join(head))

    results = await uzum.probe_drop_off_points(batch)
    for label, body in results:
        await message.answer(f"<b>{label}</b>\n<code>{body}</code>")

    await message.answer(
        "<i>Shu xabarlarni Claude'ga yuboring — qaysi variant "
        "ma'lumot qaytarganini ko'rib, aniq tuzatamiz.</i>"
    )


@router.message(Command("slottekshir"))
async def cmd_probe_slots(message: Message, employee: dict) -> None:
    """
    TASHXIS: vaqt oynasi javobining XOM ko'rinishi.

    Postavka «wrong-order-status» xatosini bersa, aybdor ko'pincha
    noto'g'ri timeSlotUuid bo'ladi. Bu buyruq javobda qanday maydonlar
    borligini aniq ko'rsatadi.
    """
    if employee["role"] != repo.ROLE_ADMIN:
        return

    wait = await message.answer("⏳ Tekshirilmoqda…")
    try:
        items = await order_service.orders_for_user(employee)
    except ApiError as e:
        await wait.edit_text(f"⚠️ {e}")
        return

    ready = [o for o in items if o["status"] in READY_STAGES]
    if not ready:
        await wait.edit_text("Postavkaga tayyor buyurtma yo'q.")
        return

    shop = ready[0].get("shop_id")
    batch = [o for o in ready if o.get("shop_id") == shop][:7]

    points, fitting, _ = await uzum.get_drop_off_points(batch)
    if not points:
        await wait.edit_text("Qabul punkti topilmadi — avval /pvtekshir qiling.")
        return

    await wait.edit_text(
        f"📍 Punkt: {points[0]['address'][:60]}\n"
        f"uuid: <code>{points[0]['uuid']}</code>\n"
        f"📦 {len(fitting)} ta buyurtma"
    )

    raw = await uzum.probe_time_slots(points[0]["uuid"], fitting)
    await message.answer(f"<b>Vaqt oynalari — xom javob</b>\n<code>{raw}</code>")
    await message.answer(
        "<i>Shu javobni Claude'ga yuboring — qaysi maydon "
        "timeSlotUuid ekanini aniqlaymiz.</i>"
    )
