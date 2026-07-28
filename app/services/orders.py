"""
Buyurtmalar bo'yicha biznes-logika.

Uzumdan kelgan ma'lumot + bizning bazadagi ichki holat birlashtiriladi.
Endi Uzumning haqiqiy muddatlaridan (deliverUntil) foydalanamiz —
"4 soatdan keyin kechikdi" degan o'ylab topilgan qoida o'rniga.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import repo
from app.services import workflow
from app.integrations.uzum import uzum

log = logging.getLogger(__name__)
TZ = ZoneInfo(settings.timezone)


def _fmt_money(v: int | float) -> str:
    return f"{int(v):,}".replace(",", " ") + " so'm"


# ------------------------------------------------------------------
#  MUDDATLAR
# ------------------------------------------------------------------
def hours_left(deadline: datetime | None) -> float | None:
    if not deadline:
        return None
    return (deadline - datetime.now(TZ)).total_seconds() / 3600


def deadline_label(deadline: datetime | None) -> str:
    """Muddatni odam o'qiydigan ko'rinishda: '3 soat qoldi' / 'KECHIKDI'."""
    left = hours_left(deadline)
    if left is None:
        return ""
    if left < 0:
        return f"🔴 KECHIKDI ({abs(left):.0f} soat)"
    if left < 3:
        return f"🔴 {left:.1f} soat qoldi"
    if left < settings.warn_before_hours:
        return f"🟡 {left:.0f} soat qoldi"
    if left < 24:
        return f"🟢 {left:.0f} soat qoldi"
    return f"🟢 {deadline:%d-%b %H:%M} gacha"


def is_urgent(order: dict[str, Any]) -> bool:
    if not workflow.is_active(order["status"]):
        return False
    left = hours_left(order.get("deliver_until"))
    return left is not None and left < settings.warn_before_hours


# ------------------------------------------------------------------
#  SINXRONIZATSIYA
# ------------------------------------------------------------------
def _status_index(status: str) -> int:
    try:
        return repo.STATUS_FLOW.index(status)
    except ValueError:
        return -1


async def sync_today(day: str | None = None) -> list[dict[str, Any]]:
    """Uzumdan buyurtmalarni oladi, bazaga yozadi, ichki holat bilan birlashtiradi."""
    remote = await uzum.get_orders(day)
    merged: list[dict[str, Any]] = []

    for order in remote:
        created = order.get("created_at")
        order_date = created.date().isoformat() if created else (day or "")
        await repo.ensure_order(order["order_id"], order_date, order.get("shop_id"))
        local = await repo.get_order(order["order_id"]) or {}

        # Uzum holati bilan bizning batafsil bosqichni birlashtiramiz
        status = workflow.merge_with_uzum(
            local.get("local_status", "new"), order["status"]
        )

        merged.append({**order, "status": status, "employee_id": local.get("employee_id")})

    return merged


# Har bir rol qaysi bosqichlarni ko'radi.
#
# Ilgari xodim faqat O'ZIGA BIRIKTIRILGAN buyurtmani ko'rardi. Kichik
# jamoada bu noqulay: yangi buyurtma kelganda hech kimga biriktirilmagan
# bo'ladi va yig'uvchi bo'sh ro'yxat ko'radi.
#
# Endi har kim O'Z BOSQICHIDAGI hamma buyurtmani ko'radi — plus o'ziga
# biriktirilganlarini, ular qaysi bosqichda bo'lishidan qat'i nazar.
# Bosqichlar QISMAN KESISHADI — bu ataylab.
# Yig'uvchi kelayotgan ishni oldindan ko'rsin, haydovchi esa
# qachon yo'lga chiqishni bilsin. Kichik jamoada bu qulayroq.
ROLE_STAGES = {
    repo.ROLE_SKLAD: [
        "new", "sklad", "shortage", "sklad_ready",
    ],
    repo.ROLE_PICKER: [
        "new", "sklad", "shortage", "sklad_ready", "checking", "picking", "packed",
    ],
    repo.ROLE_DRIVER: [
        "packed", "in_postavka", "to_pvz",
    ],
}


async def orders_for_user(employee: dict[str, Any], day: str | None = None) -> list[dict[str, Any]]:
    """
    Kim nimani ko'radi:
      • admin       — hammasini
      • sklad       — tovar chiqarish bosqichlarini
      • yig'uvchi   — tekshirish va yig'ish bosqichlarini
      • haydovchi   — jo'natish bosqichlarini
      • umumiy xodim — hammasini (kichik jamoa uchun)

    Har kim o'ziga biriktirilgan buyurtmani ham ko'radi.
    """
    orders = await sync_today(day)
    role = employee["role"]

    if role in (repo.ROLE_ADMIN, repo.ROLE_EMPLOYEE):
        return orders

    stages = ROLE_STAGES.get(role)
    if not stages:
        return orders

    mine = employee["telegram_id"]
    return [
        o for o in orders
        if o["status"] in stages or o.get("employee_id") == mine
    ]


# ------------------------------------------------------------------
#  HISOBOTLAR
# ------------------------------------------------------------------
def summarize(orders: list[dict[str, Any]]) -> dict[str, int]:
    """Bosqichlar bo'yicha sanoq."""
    counts: dict[str, int] = {code: 0 for code in workflow.WORKFLOW}
    for o in orders:
        counts[o["status"]] = counts.get(o["status"], 0) + 1
    counts["total"] = len(orders)
    counts["pending"] = sum(1 for o in orders if workflow.is_active(o["status"]))
    counts["urgent"] = sum(1 for o in orders if is_urgent(o))
    counts["late"] = sum(
        1 for o in orders
        if workflow.is_active(o["status"]) and (hours_left(o.get("deliver_until")) or 99) < 0
    )
    return counts


def format_summary(counts: dict[str, int], title: str = "📊 Bugungi holat") -> str:
    if counts["total"] == 0:
        return f"<b>{title}</b>\n\nHozircha buyurtma yo'q. 👌"

    lines = [f"<b>{title}</b>", "", f"Jami: <b>{counts['total']}</b> ta", ""]
    for code in workflow.ACTIVE_STAGES + ["delivered", "done"]:
        n = counts.get(code, 0)
        if n:
            lines.append(f"{workflow.label(code)}: {n}")
    lines += ["", f"⏳ Ish qolgan: <b>{counts['pending']}</b>"]
    if counts.get("late"):
        lines.append(f"🔴 <b>MUDDATI O'TGAN: {counts['late']}</b>")
    elif counts.get("urgent"):
        lines.append(f"🟡 Muddati yaqin: <b>{counts['urgent']}</b>")
    return "\n".join(lines)


def format_by_shop(orders: list[dict[str, Any]]) -> str:
    """Do'konlar bo'yicha taqsimot — 5 ta do'kon bo'lgani uchun kerak."""
    if not orders:
        return ""
    by_shop: dict[str, list[dict[str, Any]]] = {}
    for o in orders:
        by_shop.setdefault(o.get("shop_name", "—"), []).append(o)

    lines = ["<b>🏪 Do'konlar bo'yicha</b>", ""]
    for shop, items in sorted(by_shop.items(), key=lambda x: -len(x[1])):
        pending = sum(1 for o in items if workflow.is_active(o["status"]))
        mark = f" · ⏳ {pending}" if pending else " · ✅"
        lines.append(f"• {shop}: {len(items)} ta{mark}")
    return "\n".join(lines)


def format_order_card(order: dict[str, Any], employee_name: str | None = None) -> str:
    """Bitta buyurtma kartochkasi."""
    items_txt = "\n".join(
        f"   • {i['name'][:55]} ×{i['qty']}" for i in (order.get("items") or [])
    ) or "   • —"

    stage = order["status"]
    parts = [
        f"<code>{order.get('public_id', order['order_id'])}</code>  ·  {order.get('shop_name', '')}",
        f"{workflow.label(stage)}   {workflow.progress_bar(stage)}",
        "",
        items_txt,
        "",
        f"💰 {_fmt_money(order.get('total', 0))}",
        f"📍 {order.get('pickup_point', '—')}",
    ]
    dl = deadline_label(order.get("deliver_until"))
    if dl:
        parts.append(f"⏰ {dl}")
    if employee_name:
        parts.append(f"👤 {employee_name}")
    return "\n".join(parts)


async def all_shipped_today() -> tuple[bool, dict[str, int]]:
    """«Bugungi buyurtmalar PVZga oborildimi?» savoliga javob."""
    orders = await sync_today()
    counts = summarize(orders)
    return counts["pending"] == 0 and counts["total"] > 0, counts
