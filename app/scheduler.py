"""
Avtomatik vazifalar (APScheduler):
  • ertalabki va kechqurungi hisobot;
  • kechikkan buyurtmalar bo'yicha shaxsiy eslatma;
  • qoldig'i kam mahsulotlar bo'yicha ogohlantirish.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db import repo
from app.integrations.uzum import set_account
from app.services import accounts as accounts_service
from app.services import orders as order_service
from app.bot.keyboards import order_actions
from app.services import notifications as notif
from app.services import stock as stock_service

log = logging.getLogger(__name__)


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = value.split(":")
        return int(h), int(m)
    except Exception:
        return default


def _per_account(func):
    """
    Vazifani HAR do'kon egasi uchun alohida ishga tushiradi.

    Bitta egasi bo'lsa — eski xatti-harakat (account_key=None).
    Ko'p egasi bo'lsa — har bir egasi o'z tokeni va do'konlari bilan
    alohida hisobot/tekshiruv oladi.
    """
    async def wrapper(*args):
        accts = accounts_service.all_accounts()
        if len(accts) <= 1:
            await func(*args, account_key=None)
            return
        for a in accts:
            try:
                await func(*args, account_key=a.key)
            except Exception as e:
                log.warning("Vazifa %s (%s) xatosi: %s",
                            getattr(func, "__name__", "?"), a.name, e)

    wrapper.__name__ = f"per_account_{getattr(func, '__name__', '?')}"
    return wrapper


async def send_daily_report(bot: Bot, title: str) -> None:
    """Guruhga umumiy hisobot, adminlarga shaxsiy nusxa."""
    try:
        orders = await order_service.sync_today()
    except Exception as e:
        log.error("Hisobot tayyorlanmadi: %s", e)
        return

    counts = order_service.summarize(orders)
    text = order_service.format_summary(counts, title)

    if counts["pending"]:
        text += "\n\n⚠️ Tayyor bo'lmagan buyurtmalar bor — tekshiring."

    targets: set[int] = set()
    if settings.group_chat_id:
        targets.add(settings.group_chat_id)
    for admin in await repo.list_employees():
        if admin["role"] == repo.ROLE_ADMIN:
            targets.add(admin["telegram_id"])

    for chat_id in targets:
        try:
            await bot.send_message(chat_id, text)
        except Exception as e:
            log.warning("Xabar yuborilmadi (chat %s): %s", chat_id, e)


async def remind_late_tasks(bot: Bot, account_key: str | None = None) -> None:
    """
    Uzumning HAQIQIY muddatiga qarab eslatma yuboradi.

    Ilgari "4 soatdan beri qotib turibdi" degan o'ylab topilgan qoida edi.
    Endi deliverUntil ishlatiladi — xodim aniq necha soat qolganini biladi.
    """
    if account_key:
        set_account(account_key)

    try:
        orders = await order_service.sync_today()
    except Exception as e:
        log.error("Muddatlar tekshirilmadi: %s", e)
        return

    urgent = [o for o in orders if order_service.is_urgent(o)]
    if not urgent:
        return

    log.info("Muddati yaqin buyurtmalar: %d ta", len(urgent))
    unassigned: list[dict] = []

    for order in urgent:
        local = await repo.get_order(order["order_id"])
        # Yaqinda eslatilgan bo'lsa, takror bezovta qilmaymiz
        if local and local.get("reminded_at"):
            continue

        text = (
            f"{notif._owner_label(account_key)}"
            "⏰ <b>Muddat yaqinlashdi</b>\n\n"
            + order_service.format_order_card(order)
            + "\n\nIltimos, holatini yangilang."
        )
        if order.get("employee_id"):
            try:
                await bot.send_message(order["employee_id"], text)
                await repo.mark_reminded(order["order_id"])
            except Exception as e:
                log.warning("Eslatma yuborilmadi (%s): %s", order["employee_id"], e)
        else:
            unassigned.append(order)
            await repo.mark_reminded(order["order_id"])

    if unassigned and settings.group_chat_id:
        lines = [
            f"• <code>{o.get('public_id', o['order_id'])}</code> — {o.get('shop_name', '')}"
            f" — {order_service.deadline_label(o.get('deliver_until'))}"
            for o in unassigned[:15]
        ]
        try:
            await bot.send_message(
                settings.group_chat_id,
                f"{notif._owner_label(account_key)}"
                "⚠️ <b>Mas'ul biriktirilmagan, muddati yaqin</b>\n\n"
                + "\n".join(lines)
                + "\n\nKim oladi? /orders",
            )
        except Exception as e:
            log.warning("Guruhga xabar yuborilmadi: %s", e)


async def check_low_stock(bot: Bot) -> None:
    """Qoldig'i chegaradan past mahsulotlar bo'yicha ogohlantirish."""
    try:
        low = await stock_service.low_stock_items()
    except Exception as e:
        log.error("Ombor tekshirilmadi: %s", e)
        return

    fresh = [p for p in low if await repo.should_alert(p["sku"])]
    if not fresh:
        return

    text = stock_service.format_low_stock(fresh)
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
            log.warning("Ogohlantirish yuborilmadi (%s): %s", chat_id, e)


async def prefetch_orders(account_key: str | None = None) -> None:
    """
    Fonda buyurtmalarni oldindan yuklab qo'yadi.

    Shunda xodim /orders bosganda 15 soniya kutmaydi — javob keshdan
    darrov chiqadi. Bu Uzumning sekundiga 2 so'rov chegarasini
    aylanib o'tishning eng oddiy yo'li.
    """
    if account_key:
        set_account(account_key)

    try:
        orders = await order_service.sync_today()
        log.debug("Kesh yangilandi (%s): %d ta buyurtma",
                  account_key or "main", len(orders))
    except Exception as e:
        log.warning("Kesh yangilanmadi: %s", e)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    # MUHIM: vaqt mintaqasi trigger'ga ham aniq berilishi kerak.
    # Aks holda APScheduler serverning o'z vaqtini (odatda UTC) oladi
    # va hisobotlar noto'g'ri soatda yuboriladi.
    tz = ZoneInfo(settings.timezone)
    sched = AsyncIOScheduler(timezone=tz)

    # --- Kunlik hisobotlar O'CHIRILDI (foydalanuvchi talabi) ---
    # 🌅 Ertalabki (09:30) va 🌇 Kechqurungi (18:30) avtomatik hisobot
    # endi UMUMAN yuborilmaydi — guruhga ham, adminlarga ham.
    # Agar kerak bo'lsa, /report buyrug'i orqali qo'lda so'raladi.

    # --- Yangi buyurtmalarni kuzatish (eng muhimi) ---
    sched.add_job(
        _per_account(notif.watch_new_orders),
        IntervalTrigger(minutes=settings.new_order_check_min),
        args=[bot],
        id="watch_new",
        next_run_time=datetime.now(tz) + timedelta(seconds=30),
    )

    # --- "Hali ish bor" eslatmasi ---
    sched.add_job(
        _per_account(notif.nudge_pending),
        IntervalTrigger(minutes=settings.nudge_every_min),
        args=[bot],
        id="nudge",
    )

    # --- Muddat ogohlantirishlari ---
    sched.add_job(
        _per_account(remind_late_tasks),
        IntervalTrigger(minutes=settings.late_check_every_min),
        args=[bot],
        id="late_tasks",
    )

    # --- Fonda ma'lumotni yangilab turish (tezlik uchun) ---
    sched.add_job(
        _per_account(prefetch_orders),
        IntervalTrigger(minutes=2),
        id="prefetch_orders",
        next_run_time=datetime.now(tz),
    )

    # --- Skladga kunlik ro'yxat (shaxsiy chatga) ---
    kh, km = _parse_hhmm(settings.sklad_list_at, (11, 0))
    sched.add_job(
        _per_account(notif.sklad_daily_list),
        CronTrigger(hour=kh, minute=km, timezone=tz),
        args=[bot],
        id="sklad_list",
    )

    # --- Kunlik moliyaviy hisobot (adminlarga, kechagi to'liq kun) ---
    rh, rm = _parse_hhmm(settings.money_report_at, (8, 0))
    sched.add_job(
        _per_account(notif.money_report),
        CronTrigger(hour=rh, minute=rm, timezone=tz),
        args=[bot],
        id="money_report",
    )

    # --- Soatlik hisobot (adminlarga, 08:00–23:00, har soat) ---
    sched.add_job(
        _per_account(notif.hourly_report),
        CronTrigger(hour="8-23", minute=0, timezone=tz),
        args=[bot],
        id="hourly_report",
    )

    # --- FBO yuk xatlari qabul qilinganini kuzatish (har 20 daqiqada) ---
    sched.add_job(
        _per_account(notif.check_fbo_invoices),
        IntervalTrigger(minutes=20, timezone=tz),
        args=[bot],
        id="check_fbo_invoices",
    )

    # DIQQAT: "Ombor tekshiruvi" (check_low_stock) OLIB TASHLANDI.
    # Sabab: bu Billz'dan ma'lumot olardi, Billz esa hali ulanmagan
    # (test/soxta rejimda). Natijada bot HAQIQIY bo'lmagan "qoldiq
    # kam" ogohlantirishlarini yuborardi. Billz ulanganda yoki
    # Uzum'ning haqiqiy ma'lumotiga (get_product_stats) o'tkazilgan
    # holda qaytarish mumkin.

    # --- Mahsulot holati (kam qoldiq, sotuvga chiqdi, pullik saqlash) ---
    # Endi Uzum'ning O'ZIDAN — Billz emas.
    sched.add_job(
        _per_account(notif.check_product_state),
        IntervalTrigger(minutes=30, timezone=tz),
        args=[bot],
        id="check_product_state",
    )

    sched.start()
    log.info("Scheduler ishga tushdi (%s):", settings.timezone)
    log.info("  • do'kon egasi:              %s",
             ", ".join(a.name for a in accounts_service.all_accounts()))
    log.info("  • yangi buyurtma tekshiruvi: har %d daqiqada", settings.new_order_check_min)
    log.info("  • «ish bor» eslatmasi:       har %d daqiqada (%d:00–%d:00)",
             settings.nudge_every_min, settings.work_from, settings.work_to)
    log.info("  • muddat tekshiruvi:         har %d daqiqada", settings.late_check_every_min)
    log.info("  • skladga ro'yxat:           %s (shaxsiy chatga)", settings.sklad_list_at)
    log.info("  • moliyaviy hisobot:         %s (adminlarga, kechagi kun)", settings.money_report_at)
    log.info("  • soatlik hisobot:           08:00-23:00, har soat (adminlarga)")
    log.info("  • FBO qabul kuzatuvi:        har 20 daqiqada")
    return sched
