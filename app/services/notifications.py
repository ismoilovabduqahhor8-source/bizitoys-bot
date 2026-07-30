"""
KUZATUVCHI — bot o'zi yozadi, so'ralishini kutmaydi.

Ilgarigi kamchilik: faqat CREATED (Новые) buyurtmalar kuzatilardi.
BiziToys'da esa u doim 0 — ish PACKING va DELIVERING da bo'ladi.
Shuning uchun bot jim turardi.

Endi BARCHA faol bosqichlar kuzatiladi:
    CREATED · PACKING · PENDING_DELIVERY · DELIVERING

Uch xil xabar bor:
  1. Yangi buyurtma paydo bo'ldi        -> darrov guruhga
  2. Ish bor, lekin hech kim tegmayapti -> vaqti-vaqti bilan eslatma
  3. Muddat yaqinlashdi                 -> mas'ulga shaxsiy

Bot bir necha soat o'chib qolsa ham, yoqilgach o'tkazib yuborilganini
aytadi — chunki nima e'lon qilinganini bazada saqlaydi.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InputMediaPhoto

from app.bot import cards
from app.bot.keyboards import group_actions
from app.config import settings
from app.db import repo
from app.services import grouping
from app.services import orders as order_service
from app.services import workflow

log = logging.getLogger(__name__)
TZ = ZoneInfo(settings.timezone)

KV_SEEN = "seen_orders"
KV_LAST_NUDGE = "last_nudge_at"


def in_work_hours() -> bool:
    """Tunda bezovta qilmaslik uchun."""
    now = datetime.now(TZ)
    return settings.work_from <= now.hour < settings.work_to


async def _seen_ids() -> set[str]:
    raw = await repo.kv_get(KV_SEEN, "") or ""
    return {x for x in raw.split(",") if x}


async def _remember(ids: set[str]) -> None:
    # Oxirgi 2000 tasini saqlaymiz — baza shishib ketmasin
    await repo.kv_set(KV_SEEN, ",".join(list(ids)[-2000:]))


# ------------------------------------------------------------------
#  1. YANGI BUYURTMALAR
# ------------------------------------------------------------------
async def watch_new_orders(bot: Bot) -> None:
    """Yangi ish paydo bo'lsa — darrov guruhga, akt bo'yicha guruhlab."""
    if not settings.group_chat_id:
        return

    try:
        orders = await order_service.sync_today()
    except Exception as e:
        log.warning("Kuzatuv: ma'lumot olinmadi — %s", e)
        return

    active = [o for o in orders if workflow.is_active(o["status"])]
    if not active:
        return

    seen = await _seen_ids()
    fresh = [o for o in active if o["order_id"] not in seen]
    if not fresh:
        return

    log.info("Yangi buyurtmalar: %d ta", len(fresh))
    groups = grouping.build(fresh)
    total_items = sum(
        sum(i.get("qty", 1) for i in (o.get("items") or [])) for o in fresh
    )

    sklad = await repo.employees_by_role(repo.ROLE_SKLAD)
    mention = " ".join(
        f'<a href="tg://user?id={p["telegram_id"]}">{p["full_name"]}</a>' for p in sklad
    )

    header = (
        f"🔔 <b>{len(fresh)} ta yangi buyurtma</b> · {total_items} dona mahsulot\n"
    )
    if mention:
        header += f"\n{mention} — tovar kerak:"

    try:
        await bot.send_message(settings.group_chat_id, header)
        for g in groups[:8]:
            # Yangi buyurtma — hamma mahsulot rasmi + bitta "Qabul qilish"
            # tugmasi. "To'liq ko'rish"/"Skladga berish" endi kerak emas.
            await cards.send_new_order_card(bot, settings.group_chat_id, g)
        if len(groups) > 8:
            await bot.send_message(
                settings.group_chat_id, f"… va yana {len(groups) - 8} ta guruh. /orders"
            )
    except Exception as e:
        log.warning("Guruhga e'lon yuborilmadi: %s", e)
        return

    await _remember(seen | {o["order_id"] for o in fresh})


# ------------------------------------------------------------------
#  2. TURIB QOLGAN ISH
# ------------------------------------------------------------------
async def nudge_pending(bot: Bot) -> None:
    """
    Ish bor, lekin hech kim tegmayapti — eslatib turadi.

    Bu bot «tirik» ekanini ko'rsatadi va unutilgan buyurtmalarni
    yodga soladi. Faqat ish vaqtida va faqat ish qolgan bo'lsa.
    """
    if not settings.group_chat_id or not in_work_hours():
        return

    try:
        orders = await order_service.sync_today()
    except Exception as e:
        log.warning("Eslatma: ma'lumot olinmadi — %s", e)
        return

    active = [o for o in orders if workflow.is_active(o["status"])]
    if not active:
        return

    counts = order_service.summarize(orders)
    by_stage: dict[str, int] = {}
    for o in active:
        by_stage[o["status"]] = by_stage.get(o["status"], 0) + 1

    lines = [f"⏳ <b>Hali {len(active)} ta buyurtma ustida ish bor</b>", ""]
    for code in workflow.ACTIVE_STAGES:
        n = by_stage.get(code)
        if not n:
            continue
        role = workflow.responsible_role(code)
        who = repo.ROLE_LABELS.get(role, "") if role else ""
        lines.append(f"{workflow.label(code)}: <b>{n}</b>  {who}")

    if counts.get("late"):
        lines += ["", f"🔴 <b>Muddati o'tgan: {counts['late']} ta</b>"]
    elif counts.get("urgent"):
        lines += ["", f"🟡 Muddati yaqin: <b>{counts['urgent']}</b> ta"]

    lines += ["", "Ko'rish uchun: /orders"]

    try:
        await bot.send_message(settings.group_chat_id, "\n".join(lines))
        await repo.kv_set(KV_LAST_NUDGE, datetime.now(TZ).isoformat())
        log.info("Eslatma yuborildi: %d ta ish qolgan", len(active))
    except Exception as e:
        log.warning("Eslatma yuborilmadi: %s", e)


# ------------------------------------------------------------------
#  3. KUNLIK YAKUN
# ------------------------------------------------------------------
async def daily_digest(bot: Bot, title: str) -> None:
    """Ertalabki va kechqurungi hisobot — guruhga va adminlarga."""
    try:
        orders = await order_service.sync_today()
    except Exception as e:
        log.error("Hisobot tayyorlanmadi: %s", e)
        return

    counts = order_service.summarize(orders)
    text = order_service.format_summary(counts, title)

    active = [o for o in orders if workflow.is_active(o["status"])]
    if active:
        shops = order_service.format_by_shop(active)
        if shops:
            text += "\n\n" + shops

    targets: set[int] = set()
    if settings.group_chat_id:
        targets.add(settings.group_chat_id)
    for emp in await repo.list_employees():
        if emp["role"] == repo.ROLE_ADMIN:
            targets.add(emp["telegram_id"])

    for chat_id in targets:
        try:
            await bot.send_message(chat_id, text)
        except Exception as e:
            log.warning("Hisobot yuborilmadi (%s): %s", chat_id, e)


# ------------------------------------------------------------------
#  4. SKLAD UCHUN KUNLIK RO'YXAT (soat 11:00)
# ------------------------------------------------------------------
async def sklad_daily_list(bot: Bot) -> None:
    """
    Sklad xodimlariga yig'ilishi kerak bo'lgan mahsulotlar ro'yxati.

    SHAXSIY chatga yuboriladi — guruhda rasm va ro'yxat bardak qiladi.
    Mahsulotlar SKU bo'yicha jamlanadi: bir xil tovar 5 ta buyurtmada
    bo'lsa, sklad uni bir marta 5 dona qilib chiqaradi. Bu ancha tez.
    """
    sklad = await repo.employees_by_role(repo.ROLE_SKLAD)
    if not sklad:
        log.info("Sklad ro'yxati: xodim yo'q")
        return

    try:
        orders = await order_service.sync_today()
    except Exception as e:
        log.warning("Sklad ro'yxati tayyorlanmadi: %s", e)
        return

    # Sklad ishlashi kerak bo'lgan bosqichlar
    need = [
        o for o in orders
        if o["status"] in ("new", "sklad", "shortage", "sklad_ready")
    ]
    if not need:
        log.info("Sklad ro'yxati: ish yo'q")
        return

    with_photo, without = grouping.items_with_photos(need)
    all_items = with_photo + without
    total = sum(r["qty"] for r in all_items)

    header = (
        f"🏬 <b>Bugungi yig'ish ro'yxati</b>\n"
        f"{datetime.now(TZ):%d-%b, %H:%M}\n\n"
        f"📦 {len(need)} ta buyurtma\n"
        f"🧸 {len(all_items)} xil mahsulot · <b>{total}</b> dona"
    )

    for person in sklad:
        uid = person["telegram_id"]
        try:
            await bot.send_message(uid, header)
        except Exception as e:
            log.warning("Sklad ro'yxati yuborilmadi (%s): %s", uid, e)
            continue

        # Rasmlar albom bo'lib ketadi (10 tadan)
        for start in range(0, len(with_photo), 10):
            chunk = with_photo[start:start + 10]
            media = [
                InputMediaPhoto(
                    media=r["photo"],
                    caption=f"<b>{r['sku']}</b>\nSoni: {r['qty']}",
                    parse_mode="HTML",
                )
                for r in chunk
            ]
            try:
                await bot.send_media_group(uid, media)
            except Exception:
                for r in chunk:
                    try:
                        await bot.send_message(uid, f"<b>{r['sku']}</b>\nSoni: {r['qty']}")
                    except Exception:
                        pass

        if without:
            lines = ["<b>Rasmsiz mahsulotlar</b>", ""]
            lines += [f"<b>{r['sku']}</b> — Soni: {r['qty']}" for r in without[:40]]
            try:
                await bot.send_message(uid, "\n".join(lines))
            except Exception:
                pass

        try:
            await bot.send_message(
                uid,
                "Tayyor bo'lgach guruhda «✅ Tovar tayyor» tugmasini bosing.\n/orders"
            )
        except Exception:
            pass

    log.info("Sklad ro'yxati %d xodimga yuborildi (%d xil mahsulot)",
             len(sklad), len(all_items))


# ------------------------------------------------------------------
#  5. KUNLIK MOLIYAVIY HISOBOT (adminlarga, PDF bilan)
# ------------------------------------------------------------------
async def money_report(bot: Bot) -> None:
    """
    Kunlik moliyaviy hisobot — soat 08:00 da, faqat adminlarga.

    MUHIM: har doim KECHAGI kun uchun hisoblanadi — aniq va qat'iy,
    00:00 dan 23:59 gacha (to'liq, yopilgan kun).

    Ilgari bu yerda build_with_fallback() chaqirilardi: u avval
    "bugun"gi ma'lumotni so'raydi, faqat bo'sh bo'lsa kechagiga
    o'tadi. Bu tasodifga tayangan edi — agar soat 8:00 da bugungi
    kun uchun bironta yozuv allaqachon tushib ulgursa, hisobot
    TO'LIQ BO'LMAGAN bugungi ma'lumotni ko'rsatib yuborardi.

    Tartib: avval rasm (tovarlar jadvali), keyin Uzum Market
    uslubidagi batafsil matn.
    """
    from datetime import timedelta
    from aiogram.types import BufferedInputFile
    from app.services import report, report_image

    admins = [
        e for e in await repo.list_employees() if e["role"] == repo.ROLE_ADMIN
    ]
    if not admins:
        return

    yesterday = datetime.now(TZ) - timedelta(days=1)

    try:
        rep = await report.build(yesterday)
        full = await report.build_full(yesterday)
    except Exception as e:
        log.warning("Moliyaviy hisobot tayyorlanmadi: %s", e)
        return

    if not rep["items"]:
        log.info("Moliyaviy hisobot: ma'lumot yo'q")
        return

    img = report_image.render(rep)
    text = report.as_full_text(full)

    for a in admins:
        uid = a["telegram_id"]
        try:
            if img:
                await bot.send_photo(
                    uid,
                    BufferedInputFile(
                        img, filename=f"hisobot_{rep['date']:%Y-%m-%d}.png"
                    ),
                )
            await bot.send_message(uid, text)
        except Exception as e:
            log.warning("Hisobot yuborilmadi (%s): %s", uid, e)

    log.info("Moliyaviy hisobot %d adminga yuborildi", len(admins))


async def hourly_report(bot: Bot) -> None:
    """
    Soatlik hisobot — kun bo'yi (08:00–23:00), faqat adminlarga.

    MUHIM: bu KUMULYATIV emas — aynan SHU SOAT ichidagi sotuv.
    Masalan soat 15:00 da yuborilgan hisobot 14:00 dan 15:00
    gachagi sotuvni ko'rsatadi, kun boshidan hozirgacha emas.

    Ma'lumot bo'lmasa (shu soatda hech narsa sotilmagan bo'lsa),
    jim o'tkazib yuboriladi — bo'sh hisobot yuborish foydasiz.
    """
    from datetime import timedelta
    from aiogram.types import BufferedInputFile
    from app.services import report, report_image

    admins = [
        e for e in await repo.list_employees() if e["role"] == repo.ROLE_ADMIN
    ]
    if not admins:
        return

    hour_end = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    hour_start = hour_end - timedelta(hours=1)
    title = f"{hour_start:%H:%M} – {hour_end:%H:%M}"

    try:
        rep = await report.build_between(hour_start, hour_end, title)
        full = await report.build_full_between(hour_start, hour_end, title)
    except Exception as e:
        log.warning("Soatlik hisobot tayyorlanmadi: %s", e)
        return

    if not rep["items"]:
        log.info("Soatlik hisobot: %s oralig'ida sotuv yo'q", title)
        return

    img = report_image.render(rep)
    text = f"🕐 <b>Soatlik sotuv</b>\n" + report.as_full_text(full)

    for a in admins:
        uid = a["telegram_id"]
        try:
            if img:
                await bot.send_photo(
                    uid,
                    BufferedInputFile(
                        img, filename=f"soatlik_{hour_end:%Y-%m-%d_%H}.png"
                    ),
                )
            await bot.send_message(uid, text)
        except Exception as e:
            log.warning("Soatlik hisobot yuborilmadi (%s): %s", uid, e)

    log.info("Soatlik hisobot %d adminga yuborildi (%s)", len(admins), title)


async def check_fbo_invoices(bot: Bot) -> None:
    """
    FBO yuk xatlarining holatini tekshiradi. Yangi holat "qabul
    qilingan"ga o'zgargan bo'lsa, adminlarga BIR MARTA xabar beradi.

    Nega bazada saqlaymiz? Har tekshiruvda qayta xabar bermaslik
    uchun — faqat holat O'ZGARGANDA (yoki birinchi marta ACCEPTED
    holatida topilganda) xabar ketadi.
    """
    admins = [
        e for e in await repo.list_employees() if e["role"] == repo.ROLE_ADMIN
    ]
    if not admins:
        return

    from app.integrations.uzum import uzum

    try:
        invoices, _diag = await uzum.get_fbo_invoices()
    except Exception as e:
        log.warning("FBO holatini tekshirishda xato: %s", e)
        return

    for inv in invoices:
        inv_id = str(inv["id"])
        is_accepted = (inv.get("status_value") or "").upper() == "ACCEPTED"

        prev_status = await repo.get_fbo_invoice_status(inv_id)
        already_notified = await repo.was_fbo_notified(inv_id)

        if is_accepted and not already_notified:
            diff = inv["total_to_stock"] - inv["total_accepted"]
            text = (
                f"✅ <b>Yuk xati № {inv['number']} qabul qilindi!</b>\n\n"
                f"🏪 {inv['shop_name']}\n"
                f"📤 Jo'natilgan: {inv['total_to_stock']} dona\n"
                f"📥 Qabul qilingan: {inv['total_accepted']} dona"
            )
            if diff > 0:
                text += f"\n⚠️ Farq: <b>{diff} dona</b> yo'qolgan yoki rad etilgan"

            for a in admins:
                try:
                    await bot.send_message(a["telegram_id"], text)
                except Exception:
                    pass

            await repo.set_fbo_invoice_state(
                inv_id, inv["shop_id"], inv["status_value"], notified=True
            )
        elif prev_status != inv.get("status_value"):
            # Status o'zgardi, lekin hali qabul qilingan emas — kuzatib
            # boramiz, xabar bermaymiz
            await repo.set_fbo_invoice_state(
                inv_id, inv["shop_id"], inv["status_value"], notified=False
            )
