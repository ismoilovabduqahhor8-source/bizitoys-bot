"""
Test uchun soxta (mock) ma'lumotlar.
MOCK_MODE=true bo'lganda bot API kalitlarsiz ham to'liq ishlaydi —
shuning uchun logikani bugunoq sinab ko'rish mumkin.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

PRODUCTS = [
    ("BT-1001", "Yumshoq ayiqcha 40 sm"),
    ("BT-1002", "Konstruktor 120 detal"),
    ("BT-1003", "Radioboshqaruvli mashina"),
    ("BT-1004", "Pazl 500 bo'lak"),
    ("BT-1005", "Vanna uchun o'rdakchalar to'plami"),
    ("BT-1006", "Raskraska albom A4"),
    ("BT-1007", "Musiqali pianino (bolalar)"),
    ("BT-1008", "Qurilish to'plami — kran"),
]

# Haqiqiy do'konlaringiz — test ham realga o'xshasin
SHOPS = [
    (77165, "ORIKZOR o'yinchoqlar"),
    (77419, "KONSTOVAR Arzon"),
    (79873, "SENSOR o'yinchoqlar"),
    (97583, "IGRUSHKIRAZVIVASHKI"),
    (123074, "NOVIRA"),
]


def _seeded(seed_extra: str = "") -> random.Random:
    """Bir kun ichida bir xil natija chiqishi uchun."""
    return random.Random(f"{date.today().isoformat()}{seed_extra}")


def mock_orders(day: str | None = None) -> list[dict]:
    """Uzumning haqiqiy javob formatini taqlid qiladi (millisekundli sanalar)."""
    day = day or date.today().isoformat()
    rnd = _seeded(day)
    now_ms = int(datetime.now().timestamp() * 1000)
    orders = []

    for i in range(rnd.randint(5, 12)):
        sku, name = rnd.choice(PRODUCTS)
        shop_id, shop_name = rnd.choice(SHOPS)
        created = now_ms - rnd.randint(1, 20) * 3600_000
        # Uzum odatda ~53 soat beradi; qismini kechikkan qilamiz — eslatma sinovi uchun
        deliver = created + rnd.choice([53, 53, 53, 8, 3]) * 3600_000

        stage = rnd.randint(0, 3)
        orders.append({
            "id": 118400000 + i,
            "publicId": f"115{rnd.randint(1000, 9999)}-00{i:02d}",
            "status": "CREATED",
            "dateCreated": created,
            "acceptUntil": created + 16 * 3600_000,
            "deliverUntil": deliver,
            "acceptedDate": created + 3600_000 if stage >= 1 else None,
            "deliveredToDeliveryPointDate": created + 7200_000 if stage >= 2 else None,
            "completedDate": created + 10800_000 if stage >= 3 else None,
            "dateCancelled": None,
            "returnDate": None,
            "price": rnd.randint(45_000, 480_000),
            "shopId": shop_id,
            "stock": {"title": rnd.choice(["Сергели", "Юнусабад", "Чиланзар"]),
                      "address": "Toshkent"},
            "orderItems": [{
                "skuTitle": sku,
                "title": name,
                "price": rnd.randint(30, 400) * 1000,
                "amount": rnd.randint(1, 3),
            }],
            "invoiceNumber": rnd.choice([120003750766, 120003750101, None, None]),
            "place": "Склад или пункт приёма",
            "scheme": "FBS",
            "deliveryInfo": None,
        })
    return orders


def mock_stock() -> list[dict]:
    rnd = _seeded("stock")
    return [
        {"sku": sku, "name": name, "qty": rnd.randint(0, 30), "price": rnd.randrange(30, 400) * 1000}
        for sku, name in PRODUCTS
    ]


def mock_top_sales(days: int = 7) -> list[dict]:
    rnd = _seeded(f"sales{days}")
    rows = [
        {
            "sku": sku,
            "name": name,
            "sold_qty": rnd.randint(1, 40),
            "revenue": rnd.randrange(100, 3000) * 1000,
        }
        for sku, name in PRODUCTS
    ]
    rows.sort(key=lambda r: r["sold_qty"], reverse=True)
    return rows


def mock_finance() -> list[dict]:
    """Test uchun moliyaviy yozuvlar — haqiqiy hisobotga o'xshash."""
    rnd = _seeded("finance")
    rows = []
    for i, (sku, name) in enumerate(PRODUCTS):
        qty = rnd.randint(1, 14)
        price = rnd.choice([29_990, 49_990, 69_990, 99_990, 116_390])
        revenue = price * qty
        payout = int(revenue * rnd.uniform(0.72, 0.82))
        cost = int(revenue * rnd.uniform(0.55, 0.80))
        rows.append({
            "sku": sku, "name": name, "qty": qty,
            "revenue": revenue, "payout": payout, "cost": cost,
            "has_cost": True,
            "commission": int(revenue * 0.12),
            "logistics": qty * 12_000, "returns": 0,
            "cancelled": False,
            # build_full() uchun kerak — status bo'yicha guruhlanadi
            "status": rnd.choice(["TO_WITHDRAW", "TO_WITHDRAW", "PROCESSING"]),
            "order_id": 118400000 + i,
            "return_cause": None,
        })
    return rows


def mock_expenses() -> list[dict]:
    return [
        {"name": "Logistika", "source": "LOGISTICS", "amount": 98_450,
         "outcome": True, "status": "CREATED"},
        {"name": "Sklad", "source": "STORAGE", "amount": 14_904,
         "outcome": True, "status": "CREATED"},
        {"name": "Marketing", "source": "MARKETING", "amount": 7_810,
         "outcome": True, "status": "CREATED"},
        {"name": "Qaytarilgan", "source": "REFUND", "amount": 23_250,
         "outcome": False, "status": "REFUNDED"},
    ]
