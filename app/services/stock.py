"""
Ombor qoldig'i va sotuv tahlili bo'yicha logika.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.db import repo
from app.integrations.billz import billz

log = logging.getLogger(__name__)


def _fmt_money(v: int | float) -> str:
    return f"{int(v):,}".replace(",", " ") + " so'm"


async def low_stock_items() -> list[dict[str, Any]]:
    """Chegaradan past tushgan mahsulotlar ro'yxati."""
    stock = await billz.get_stock()
    thresholds = await repo.all_thresholds()

    low = []
    for p in stock:
        limit = thresholds.get(p["sku"], settings.default_min_stock)
        if p["qty"] <= limit:
            low.append({**p, "min_qty": limit})
    low.sort(key=lambda p: p["qty"])
    return low


def format_low_stock(items: list[dict[str, Any]]) -> str:
    if not items:
        return "✅ Barcha mahsulotlar yetarli miqdorda. Ogohlantirish yo'q."
    lines = ["<b>⚠️ Qoldiq kam — qayta buyurtma qilish vaqti keldi</b>", ""]
    for p in items:
        icon = "🔴" if p["qty"] == 0 else "🟡"
        lines.append(
            f"{icon} <b>{p['name']}</b>\n"
            f"   └ SKU: <code>{p['sku']}</code> · qoldiq: <b>{p['qty']}</b> "
            f"(chegara: {p['min_qty']})"
        )
    return "\n".join(lines)


async def top_sales(days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
    return await billz.get_top_sales(days=days, limit=limit)


def format_top_sales(rows: list[dict[str, Any]], days: int) -> str:
    if not rows:
        return "Bu davr uchun sotuv ma'lumoti topilmadi."
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"<b>🔥 Oxirgi {days} kunda eng ko'p sotilgan</b>", ""]
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(
            f"{mark} <b>{r['name']}</b>\n"
            f"   └ {r['sold_qty']} dona · {_fmt_money(r['revenue'])}"
        )
    return "\n".join(lines)


def format_stock_table(stock: list[dict[str, Any]], limit: int = 20) -> str:
    lines = ["<b>📦 Ombor qoldig'i</b>", ""]
    for p in sorted(stock, key=lambda x: x["qty"])[:limit]:
        lines.append(f"• {p['name']} — <b>{p['qty']}</b> dona ({_fmt_money(p['price'])})")
    if len(stock) > limit:
        lines.append(f"\n… va yana {len(stock) - limit} ta mahsulot.")
    return "\n".join(lines)
