"""
GURUHLASH — buyurtmalarni akt (yuk xati) bo'yicha to'plash.

Muammo: 30 ta buyurtma = 30 ta xabar. Guruh chatida o'qib bo'lmaydi.
Yechim: bir aktdagi buyurtmalar bitta xabarga yig'iladi, tugma bosilganda
to'liq ro'yxat rasmlari bilan ochiladi.

Aktga tushmagan buyurtmalar do'kon va bosqich bo'yicha guruhlanadi.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from app.services import orders as order_service
from app.services import workflow

# Guruh ID -> buyurtma raqamlari. Telegram callback_data 64 belgidan oshmasligi
# kerak, shuning uchun ro'yxatni bevosita uzatmasdan qisqa kalit ishlatamiz.
_GROUPS: dict[str, tuple[float, list[str]]] = {}
_TTL = 3600


def _gid(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()[:10]


def _cleanup() -> None:
    now = time.monotonic()
    for k in [k for k, (t, _) in _GROUPS.items() if now - t > _TTL]:
        _GROUPS.pop(k, None)


def remember(key: str, order_ids: list[str]) -> str:
    _cleanup()
    gid = _gid(key)
    _GROUPS[gid] = (time.monotonic(), order_ids)
    return gid


def recall(gid: str) -> list[str]:
    row = _GROUPS.get(gid)
    return row[1] if row else []


def build(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Buyurtmalarni guruhlarga bo'ladi.

    Aktga kirganlar — akt raqami bo'yicha.
    Aktsizlar — do'kon + bosqich bo'yicha.
    """
    buckets: dict[str, dict[str, Any]] = {}

    for o in orders:
        inv = o.get("invoice_number")
        # Postavka HAQIQATAN ochilgan bo'lsagina "Yuk xati" deymiz.
        # invoiceNumber postavka ochilishidan oldin ham to'ldirilishi mumkin,
        # shuning uchun unga ishonmaymiz — bosqichga qaraymiz.
        opened = o["status"] in ("in_postavka", "to_pvz", "delivered", "done")

        if inv and opened:
            key = f"inv:{inv}"
            title = f"📋 Postavka № {inv}"
        else:
            key = f"stage:{o.get('shop_id')}:{o['status']}"
            # Do'kon nomi sarlavhada bo'lsin — aks holda guruhlar farqlanmaydi
            title = f"{workflow.label(o['status'])} · {o.get('shop_name', '')}".strip(" ·")
            inv = None

        b = buckets.setdefault(key, {
            "key": key,
            "title": title,
            "invoice": inv,
            "shop_name": o.get("shop_name", ""),
            "stage": o["status"],
            "orders": [],
            "items_count": 0,
            "total": 0,
            "deadline": o.get("deliver_until"),
        })
        b["orders"].append(o)
        b["items_count"] += sum(i.get("qty", 1) for i in (o.get("items") or []))
        b["total"] += o.get("total") or 0

        # Guruhning bosqichi — eng orqada qolgani (ish shundan boshlanadi)
        if workflow.rank(o["status"]) < workflow.rank(b["stage"]):
            b["stage"] = o["status"]
        # Muddat — eng yaqini
        dl = o.get("deliver_until")
        if dl and (not b["deadline"] or dl < b["deadline"]):
            b["deadline"] = dl

    out = list(buckets.values())
    for b in out:
        b["gid"] = remember(b["key"], [o["order_id"] for o in b["orders"]])
    # Muddati yaqinlari yuqorida
    out.sort(key=lambda b: (b["deadline"] is None, b["deadline"] or 0))
    return out


def format_group(b: dict[str, Any]) -> str:
    """Guruh kartochkasi — bitta xabar, hamma buyurtma o'rniga."""
    stage = b["stage"]
    shop_line = ""
    if b["shop_name"] and b["shop_name"] not in b["title"]:
        shop_line = f"🏪 {b['shop_name']}"

    lines = [
        f"<b>{b['title']}</b>",
        shop_line,
        (f"{workflow.label(stage)}   {workflow.progress_bar(stage)}"
         if b.get("invoice") else workflow.progress_bar(stage)),
        "",
        f"📦 <b>{len(b['orders'])}</b> ta buyurtma · <b>{b['items_count']}</b> dona mahsulot",
        f"💰 {order_service._fmt_money(b['total'])}",
    ]
    dl = order_service.deadline_label(b.get("deadline"))
    if dl:
        lines.append(f"⏰ {dl}")

    # Ichidagi mahsulotlarning qisqa ro'yxati
    top = _top_items(b["orders"], limit=4)
    if top:
        lines.append("")
        lines += [f"   • {name[:48]} ×{qty}" for name, qty in top]
        extra = _distinct_count(b["orders"]) - len(top)
        if extra > 0:
            lines.append(f"   … va yana {extra} xil mahsulot")

    return "\n".join(x for x in lines if x != "")


def _agg_items(orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for o in orders:
        for it in o.get("items") or []:
            raw_key = it.get("sku") or it.get("name") or "—"
            key = str(raw_key).strip().upper()
            row = agg.setdefault(key, {
                "name": it.get("name") or "—",
                "sku": it.get("sku") or "—",
                "qty": 0,
                "photo": it.get("photo"),
            })
            row["qty"] += it.get("qty", 1)
            if not row["photo"]:
                row["photo"] = it.get("photo")
    return agg


def _top_items(orders: list[dict[str, Any]], limit: int) -> list[tuple[str, int]]:
    agg = _agg_items(orders)
    rows = sorted(agg.values(), key=lambda r: -r["qty"])
    return [(r["name"], r["qty"]) for r in rows[:limit]]


def _distinct_count(orders: list[dict[str, Any]]) -> int:
    return len(_agg_items(orders))


def items_with_photos(orders: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Rasmli va rasmsiz mahsulotlarni ajratadi."""
    rows = sorted(_agg_items(orders).values(), key=lambda r: -r["qty"])
    return [r for r in rows if r.get("photo")], [r for r in rows if not r.get("photo")]
