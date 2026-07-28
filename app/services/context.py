"""
AI uchun kontekst — botning hozirgi holatini ixcham ko'rinishga keltirish.

Nega alohida modul? AI'ga butun ma'lumotni yuborish qimmat va sekin.
Bu yerda faqat KERAKLI qismi yig'iladi: sanoqlar, muddatlar, do'konlar.
Mijoz ismi, telefoni kabi maxfiy narsalar YUBORILMAYDI.
"""
from __future__ import annotations

from typing import Any

from app.services import orders as order_service
from app.services import workflow


def build(orders: list[dict[str, Any]], employee: dict[str, Any]) -> dict[str, Any]:
    """Botning holatini AI o'qiy oladigan ko'rinishga keltiradi."""
    counts = order_service.summarize(orders)
    active = [o for o in orders if workflow.is_active(o["status"])]

    # Bosqichlar bo'yicha
    stages = {}
    for o in active:
        label = workflow.label(o["status"])
        stages[label] = stages.get(label, 0) + 1

    # Do'konlar bo'yicha
    shops = {}
    for o in active:
        name = o.get("shop_name") or "—"
        shops[name] = shops.get(name, 0) + 1

    # Muddati yaqinlar (mijoz ma'lumotisiz!)
    urgent = []
    for o in sorted(active, key=lambda x: x.get("deliver_until_ts") or 0)[:8]:
        urgent.append({
            "raqam": o.get("public_id"),
            "dokon": o.get("shop_name"),
            "bosqich": workflow.label(o["status"]),
            "muddat": order_service.deadline_label(o.get("deliver_until")),
            "mahsulot": [
                f"{i.get('sku')} x{i.get('qty')}" for i in (o.get("items") or [])
            ][:3],
            "pvz": o.get("pickup_point"),
        })

    return {
        "kim_soradi": {
            "ism": employee.get("full_name"),
            "rol": employee.get("role"),
        },
        "jami_buyurtma": counts["total"],
        "ish_qolgan": counts["pending"],
        "muddati_otgan": counts.get("late", 0),
        "muddati_yaqin": counts.get("urgent", 0),
        "bosqichlar": stages,
        "dokonlar": shops,
        "eng_shoshilinch": urgent,
    }
