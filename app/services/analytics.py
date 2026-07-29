"""
TAHLIL — biznes muammolarini topish.

MUHIM TAMOYIL: muammolar shu yerda ODDIY HISOB bilan topiladi, AI bilan
emas. Sabab:
  • arifmetika har safar bir xil natija beradi, AI esa har safar
    biroz boshqacha javob berishi mumkin;
  • bu bepul va bir zumda ishlaydi;
  • moliyaviy qarorlar taxminga emas, aniq songa tayanishi kerak.

AI keyinroq, alohida bosqichda, shu topilgan faktlarni IZOHLAYDI va
maslahat beradi — bu uning kuchli tomoni.

Har bir muammo uchta narsani qaytaradi:
    daraja   — qanchalik jiddiy (1 = eng jiddiy)
    fakt     — aniq raqamlar
    tavsiya  — nima qilish kerak
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.integrations.uzum import uzum
from app.services import report
from app.services import workflow

log = logging.getLogger(__name__)
TZ = ZoneInfo(settings.timezone)

# Chegaralar — .env orqali emas, shu yerda, chunki ular biznes qoidasi
DAYS_LEFT_CRITICAL = 5      # shuncha kunga qolgan tovar — shoshilinch
DAYS_LEFT_WARNING = 10
DEAD_STOCK_MIN_QTY = 10     # sotilmayotgan, lekin shuncha turgan tovar
CANCEL_RATE_HIGH = 15.0     # % — bundan yuqori bo'lsa muammo


def _fmt(v: int | float) -> str:
    try:
        return f"{int(round(v)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


# ------------------------------------------------------------------
#  1. ZARARDAGI TOVARLAR
# ------------------------------------------------------------------
def find_losing_products(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Har sotuvda pul yo'qotayotgan tovarlar.

    Chiqarishga (Uzum to'laydigan) < tannarx bo'lsa, har bir sotuv
    zarar keltiradi. Bu eng jiddiy muammo turi.
    """
    losing = [
        i for i in items
        if i.get("has_cost") and i["payout"] < i["cost"] and i["qty"] > 0
    ]
    if not losing:
        return None

    losing.sort(key=lambda i: i["payout"] - i["cost"])
    total_loss = sum(i["cost"] - i["payout"] for i in losing)

    return {
        "kod": "zarar",
        "daraja": 1,
        "sarlavha": f"Zarardagi tovarlar: {len(losing)} ta",
        "jami_zarar": total_loss,
        "royxat": [
            {
                "tovar": i["name"][:40],
                "sku": i["sku"],
                "sotilgan": i["qty"],
                "tannarx": i["cost"],
                "chiqarishga": i["payout"],
                "zarar": i["cost"] - i["payout"],
            }
            for i in losing[:8]
        ],
        "tavsiya": (
            "Narxni ko'tarish yoki tannarxni pasaytirish kerak. "
            "Aks holda har sotuv pul yo'qotadi."
        ),
    }


# ------------------------------------------------------------------
#  2. TUGAB QOLAYOTGAN TOVARLAR
# ------------------------------------------------------------------
def find_running_out(stats: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Necha kunga yetishini hisoblaydi: qoldiq ÷ kunlik sotuv.

    Bu oddiy bo'lish amali, lekin eng foydali ko'rsatkichlardan biri —
    "20 dona qoldi" degan son o'zi hech nima anglatmaydi, "3 kunga
    yetadi" degani esa darhol harakatga undaydi.
    """
    rows = []
    for s in stats:
        daily = s["sold_7d"] / 7
        if daily <= 0:
            continue
        days_left = s["fbo_qty"] / daily
        if days_left <= DAYS_LEFT_WARNING:
            rows.append({
                "tovar": s["name"][:40],
                "sku": s["sku"],
                "qoldiq": s["fbo_qty"],
                "kunlik_sotuv": round(daily, 1),
                "necha_kunga_yetadi": round(days_left, 1),
            })

    if not rows:
        return None

    rows.sort(key=lambda r: r["necha_kunga_yetadi"])
    critical = [r for r in rows if r["necha_kunga_yetadi"] <= DAYS_LEFT_CRITICAL]

    return {
        "kod": "tugash",
        "daraja": 1 if critical else 2,
        "sarlavha": f"Tugab qolayotgan tovarlar: {len(rows)} ta",
        "shoshilinch_soni": len(critical),
        "royxat": rows[:10],
        "tavsiya": (
            f"{len(critical)} ta tovar {DAYS_LEFT_CRITICAL} kundan kam qoldi — "
            "hozir buyurtma bermasa, sotuv to'xtaydi."
            if critical else
            "Yaqin kunlarda qayta buyurtma rejalashtiring."
        ),
    }


# ------------------------------------------------------------------
#  3. O'LIK QOLDIQ
# ------------------------------------------------------------------
def find_dead_stock(stats: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Ombordagi joyni egallab turgan, lekin sotilmayotgan tovarlar.

    Bu "yashirin" muammo: hech qanday ogohlantirish bermaydi, lekin
    pul tovarda qotib qoladi.
    """
    dead = [
        s for s in stats
        if s["sold_7d"] == 0 and s["fbo_qty"] >= DEAD_STOCK_MIN_QTY
    ]
    if not dead:
        return None

    dead.sort(key=lambda s: -s["fbo_qty"])
    return {
        "kod": "olik_qoldiq",
        "daraja": 3,
        "sarlavha": f"Sotilmayotgan tovarlar: {len(dead)} ta",
        "jami_dona": sum(s["fbo_qty"] for s in dead),
        "royxat": [
            {"tovar": s["name"][:40], "sku": s["sku"], "qoldiq": s["fbo_qty"]}
            for s in dead[:8]
        ],
        "tavsiya": (
            "7 kun ichida bitta ham sotilmagan. Narxni tushirish, reklama "
            "yoki chegirma bilan harakatga keltirish kerak."
        ),
    }


# ------------------------------------------------------------------
#  4. KECHIKKAN BUYURTMALAR
# ------------------------------------------------------------------
def find_late_orders(orders: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Muddati o'tgan yoki o'tib ketayotgan buyurtmalar."""
    now = datetime.now(TZ)
    late, soon = [], []

    for o in orders:
        if not workflow.is_active(o["status"]):
            continue
        dl = o.get("deliver_until")
        if not dl:
            continue
        hours = (dl - now).total_seconds() / 3600
        row = {
            "raqam": o.get("public_id"),
            "dokon": o.get("shop_name"),
            "bosqich": workflow.label(o["status"]),
            "soat": round(hours, 1),
        }
        if hours < 0:
            late.append(row)
        elif hours < settings.warn_before_hours:
            soon.append(row)

    if not (late or soon):
        return None

    return {
        "kod": "kechikish",
        "daraja": 1 if late else 2,
        "sarlavha": f"Muddati o'tgan: {len(late)} ta · Yaqin: {len(soon)} ta",
        "kechikkan": late[:8],
        "yaqin": soon[:8],
        "tavsiya": (
            "Kechikkan buyurtmalar reyting va jarimaga ta'sir qiladi — "
            "darhol yig'ish kerak."
            if late else
            "Muddati yaqin buyurtmalarni birinchi navbatda yig'ing."
        ),
    }


# ------------------------------------------------------------------
#  5. TANNARXI KIRITILMAGAN TOVARLAR
# ------------------------------------------------------------------
def find_missing_cost(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Tannarxi yo'q tovarlar — ularning foydasini o'lchab bo'lmaydi.

    Bu "bilim muammosi": tovar zarar keltirayotgan bo'lishi mumkin,
    lekin buni bilib bo'lmaydi.
    """
    missing = [i for i in items if not i.get("has_cost") and i["qty"] > 0]
    if not missing:
        return None

    missing.sort(key=lambda i: -i["revenue"])
    return {
        "kod": "tannarxsiz",
        "daraja": 2,
        "sarlavha": f"Tannarxi kiritilmagan: {len(missing)} ta tovar",
        "jami_daromad": sum(i["revenue"] for i in missing),
        "royxat": [
            {"tovar": i["name"][:40], "sku": i["sku"], "daromad": i["revenue"]}
            for i in missing[:8]
        ],
        "tavsiya": (
            "Uzum kabinetida tannarx kiritilsa, bu tovarlarning foydasi "
            "avtomatik hisoblanadi. Hozircha ular ko'r nuqta."
        ),
    }


# ------------------------------------------------------------------
#  6. BLOKLANGAN (YO'QOLGAN) MAHSULOTLAR
# ------------------------------------------------------------------
def find_blocked(stats: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Uzum tomonidan bloklangan mahsulotlar — sotuvchiga "yo'qolgan"
    bo'lib ko'rinadi, chunki ular ro'yxatda bor, lekin sotilmaydi.

    Sabab ko'pincha: rasm sifati, hujjat yetishmasligi, taqiqlangan
    tovar toifasi. `/v1/product/shop/{shopId}` javobidagi
    `blocked` va `skuBlockReason` maydonlaridan olinadi.
    """
    blocked = [s for s in stats if s.get("blocked")]
    if not blocked:
        return None

    blocked.sort(key=lambda s: -s["fbo_qty"])
    return {
        "kod": "bloklangan",
        "daraja": 1,
        "sarlavha": f"Bloklangan mahsulotlar: {len(blocked)} ta",
        "jami_dona": sum(s["fbo_qty"] for s in blocked),
        "royxat": [
            {
                "tovar": s["name"][:40],
                "sku": s["sku"],
                "qoldiq": s["fbo_qty"],
                "sabab": s.get("block_reason") or "sabab ko'rsatilmagan",
                "xabar": s.get("block_message") or "",
            }
            for s in blocked[:8]
        ],
        "tavsiya": (
            "Bu tovarlar omborda turibdi, lekin Uzum ularni sotuvga "
            "chiqarmayapti. Sababini tuzatib, kabinetda qayta yuboring — "
            "aks holda pul tovarda qotib qoladi va sotuv yo'qoladi."
        ),
    }


# ------------------------------------------------------------------
#  UMUMIY YIG'UVCHI
# ------------------------------------------------------------------
async def find_problems(days: int = 7) -> dict[str, Any]:
    """
    Barcha muammolarni topadi va jiddiyligi bo'yicha saralaydi.

    days — moliyaviy tahlil uchun necha kunlik davr olinadi.
    """
    now = datetime.now(TZ)
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0)

    problems: list[dict[str, Any]] = []
    errors: list[str] = []

    # Moliyaviy ma'lumot
    items: list[dict[str, Any]] = []
    try:
        rep = await report.build_between(start, now, f"{days} kun")
        items = rep["items"]
    except Exception as e:
        errors.append(f"moliya: {str(e)[:70]}")
        log.warning("Tahlil — moliya olinmadi: %s", e)

    # Ombor va sotuv tezligi
    stats: list[dict[str, Any]] = []
    try:
        stats = await uzum.get_product_stats()
    except Exception as e:
        errors.append(f"ombor: {str(e)[:70]}")
        log.warning("Tahlil — ombor olinmadi: %s", e)

    # Buyurtmalar
    orders: list[dict[str, Any]] = []
    try:
        orders = await uzum.get_orders(use_cache=False)
    except Exception as e:
        errors.append(f"buyurtmalar: {str(e)[:70]}")
        log.warning("Tahlil — buyurtmalar olinmadi: %s", e)

    for finder, arg in (
        (find_losing_products, items),
        (find_blocked, stats),
        (find_running_out, stats),
        (find_dead_stock, stats),
        (find_late_orders, orders),
        (find_missing_cost, items),
    ):
        if not arg:
            continue
        try:
            found = finder(arg)
            if found:
                problems.append(found)
        except Exception as e:
            log.warning("Tahlil bo'limi xato berdi (%s): %s", finder.__name__, e)

    problems.sort(key=lambda p: p["daraja"])

    return {
        "sana": now,
        "davr_kun": days,
        "muammolar": problems,
        "xatolar": errors,
        "tovar_soni": len(items),
        "sku_soni": len(stats),
    }


# ------------------------------------------------------------------
#  QISQA MUDDATLI XOTIRA
#
#  /tahlil natijasi shu yerda saqlanadi, shunda foydalanuvchi keyin
#  savol bera oladi:
#      /tahlil  ->  "eng katta muammo nima?"  ->  "uni qanday hal qilaman?"
#
#  Muddati o'tgach o'chadi — eski ma'lumot asosida javob berish
#  chalg'ituvchi bo'lardi.
# ------------------------------------------------------------------
_MEMORY: dict[int, tuple[float, dict[str, Any]]] = {}
_MEMORY_TTL = 1800  # 30 daqiqa


def remember_analysis(user_id: int, res: dict[str, Any]) -> None:
    import time
    _MEMORY[user_id] = (time.monotonic(), res)


def recall_analysis(user_id: int) -> dict[str, Any] | None:
    import time
    row = _MEMORY.get(user_id)
    if not row:
        return None
    saved_at, res = row
    if time.monotonic() - saved_at > _MEMORY_TTL:
        _MEMORY.pop(user_id, None)
        return None
    return res


def as_text(res: dict[str, Any], limit: int = 5) -> str:
    """Topilgan muammolarni o'qish uchun qulay ko'rinishga keltiradi."""
    problems = res["muammolar"]
    icons = {1: "🔴", 2: "🟡", 3: "🔵"}

    lines = [
        f"🔍 <b>Tahlil — oxirgi {res['davr_kun']} kun</b>",
        f"<i>{res['sana']:%d.%m.%Y %H:%M}</i>",
        "",
    ]

    if not problems:
        lines.append("✅ Jiddiy muammo topilmadi.")
        if res["xatolar"]:
            lines.append("")
            lines.append("<i>⚠️ Ba'zi ma'lumotlar olinmadi:</i>")
            lines += [f"<i>• {e}</i>" for e in res["xatolar"]]
        return "\n".join(lines)

    for p in problems[:limit]:
        icon = icons.get(p["daraja"], "•")
        lines.append(f"{icon} <b>{p['sarlavha']}</b>")

        if p["kod"] == "zarar":
            lines.append(f"   Jami zarar: <b>{_fmt(p['jami_zarar'])}</b> so'm")
            for r in p["royxat"][:3]:
                lines.append(
                    f"   • {r['tovar']}: −{_fmt(r['zarar'])} so'm ({r['sotilgan']} dona)"
                )

        elif p["kod"] == "tugash":
            for r in p["royxat"][:4]:
                mark = "🔴" if r["necha_kunga_yetadi"] <= DAYS_LEFT_CRITICAL else "🟡"
                lines.append(
                    f"   {mark} {r['tovar']}: {r['qoldiq']} dona — "
                    f"<b>{r['necha_kunga_yetadi']} kunga</b>"
                )

        elif p["kod"] == "bloklangan":
            lines.append(f"   Jami: <b>{p['jami_dona']}</b> dona sotilmay turibdi")
            for r in p["royxat"][:3]:
                lines.append(f"   • {r['tovar']}: {r['sabab']}")

        elif p["kod"] == "olik_qoldiq":
            lines.append(f"   Jami: <b>{p['jami_dona']}</b> dona qotib turibdi")
            for r in p["royxat"][:3]:
                lines.append(f"   • {r['tovar']}: {r['qoldiq']} dona")

        elif p["kod"] == "kechikish":
            for r in p["kechikkan"][:3]:
                lines.append(
                    f"   🔴 {r['raqam']} — {abs(r['soat']):.0f} soat kechikdi "
                    f"({r['bosqich']})"
                )
            for r in p["yaqin"][:2]:
                lines.append(f"   🟡 {r['raqam']} — {r['soat']:.0f} soat qoldi")

        elif p["kod"] == "tannarxsiz":
            lines.append(
                f"   Daromadi: {_fmt(p['jami_daromad'])} so'm — foydasi noma'lum"
            )
            for r in p["royxat"][:3]:
                lines.append(f"   • {r['tovar']}")

        lines.append(f"   <i>→ {p['tavsiya']}</i>")
        lines.append("")

    if len(problems) > limit:
        lines.append(f"<i>… va yana {len(problems) - limit} ta muammo.</i>")

    if res["xatolar"]:
        lines.append("")
        lines.append("<i>⚠️ Ba'zi ma'lumotlar olinmadi:</i>")
        lines += [f"<i>• {e}</i>" for e in res["xatolar"]]

    return "\n".join(lines)
