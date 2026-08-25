"""
KUNLIK MOLIYAVIY HISOBOT.

Uzumdan olingan raqamlardan quyidagi jadval quriladi:

    Tovar nomi · Soni · Daromad · Chiqarishga · Sof foyda · ROI %

Ustunlar ma'nosi:
    Daromad      — mijoz to'lagan summa
    Chiqarishga  — Uzum sizga to'laydigan summa (komissiya, logistika chegirilgan)
    Sof foyda    — Chiqarishga minus tovarning tannarxi
    ROI %        — Sof foyda ni tannarxga nisbati

Tannarx ko'rsatilmagan tovarlarda ROI hisoblanmaydi (0.00%).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.integrations.uzum import uzum

log = logging.getLogger(__name__)
TZ = ZoneInfo(settings.timezone)


KV_TODAY_SALES = "today_sales_cache"


async def today_sales_cached(force: bool = False) -> dict[str, Any]:
    """
    Bugungi kun sotuvi — AI savollari uchun.

    Kesh: 1 soat (moliya API sekin — har savolda so'ralmaydi).
    Soatlik vazifa (notifications.hourly_report) keshni yangilab turadi,
    shuning uchun ish kuni davomida deyarli har doim tayyor bo'ladi.
    """
    import json

    from app.db import repo

    now = datetime.now(TZ)
    if not force:
        raw = await repo.kv_get(KV_TODAY_SALES)
        if raw:
            try:
                data = json.loads(raw)
                age = (now - datetime.fromisoformat(data["ts"])).total_seconds()
                if age < 3600:
                    return data["data"]
            except Exception:
                pass

    day_start, _ = day_bounds(now)
    rep = await build_between(day_start, now, "Bugun")
    total = rep.get("total", {})
    data = {
        "qty": total.get("qty", 0),
        "revenue": total.get("revenue", 0),
        "payout": total.get("payout", 0),
        "count": total.get("count", 0),
    }
    await repo.kv_set(
        KV_TODAY_SALES, json.dumps({"ts": now.isoformat(), "data": data})
    )
    return data


def day_bounds(day: datetime | None = None) -> tuple[datetime, datetime]:
    """Kunning boshi va oxiri."""
    d = (day or datetime.now(TZ)).astimezone(TZ)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def fmt(v: int | float) -> str:
    """99990 -> '99 990'"""
    try:
        return f"{int(round(v)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def roi(profit: int, cost: int) -> float:
    """Tannarx nomalum bo'lsa 0 qaytaradi — soxta raqam ko'rsatmaymiz."""
    return (profit / cost * 100) if cost > 0 else 0.0


async def from_orders(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """
    Zaxira manba: buyurtmalardan hisobot.

    Moliya bo'limi bo'sh bo'lganda ishlatiladi. Buyurtmada narx va soni
    bor, lekin tannarx va komissiya yo'q — shuning uchun faqat daromad
    va soni ko'rsatiladi, foyda esa noma'lum qoladi.

    Noto'g'ri raqam ko'rsatishdan ko'ra, "noma'lum" deb qo'yish halolroq.
    """
    from app.integrations.uzum import S_ACCEPTED_AT_DP, S_COMPLETED, S_DELIVERED

    rows: list[dict[str, Any]] = []
    for scheme in ("FBS", "FBO"):
        try:
            orders = await uzum.get_orders(
                statuses=[S_DELIVERED, S_ACCEPTED_AT_DP, S_COMPLETED],
                use_cache=False,
                scheme=scheme,
            )
        except Exception as e:
            log.warning("Zaxira hisobot (%s): %s", scheme, e)
            continue

        for o in orders:
            created = o.get("created_at")
            if created and not (start <= created < end):
                continue
            for it in o.get("items") or []:
                qty = int(it.get("qty") or 1)
                price = int(it.get("price") or 0)
                rows.append({
                    "sku": it.get("sku") or "—",
                    "name": it.get("name") or "—",
                    "qty": qty,
                    "revenue": price * qty,
                    "payout": 0,
                    "cost": 0,
                    "commission": 0,
                    "logistics": 0,
                    "returns": 0,
                })
    return rows


async def build_between(start: datetime, end: datetime, title: str) -> dict[str, Any]:
    """
    Hisobot ma'lumotlarini berilgan sana oralig'i uchun yig'adi.

    Bitta kun (build) ham, butun oy (build_period) ham shu funksiyadan
    foydalanadi — farqi faqat qaysi start/end uzatilishida.
    """
    rows = await uzum.get_finance_orders(start, end)
    source = "moliya"

    if not rows:
        # Moliya bo'lmasa — buyurtmalardan quramiz
        rows = await from_orders(start, end)
        source = "buyurtmalar"

    expenses = await uzum.get_expenses(start, end)
    log.info("Hisobot %s: %d ta yozuv, %d ta xarajat",
             title, len(rows), len(expenses))

    # NOM bo'yicha jamlaymiz — rang/variant qat'i nazar bir tovar
    # bir qatorda tursin. Bekor qilinganlar hisobga olinmaydi.
    #
    # MUHIM: kalit — MAHSULOT NOMI (SKU emas). Sabab: bitta tovar
    # turli rangda sotilsa, har biri boshqa SKU oladi
    # (LEGOTOY-GOLUB, LEGOTOY-BEJEVIY-MELANJ), lekin nomi bir xil
    # ("Lego konstruktor"). Nom bo'yicha guruhlasak, rang farqidan
    # qat'i nazar bitta qatorda ko'rinadi.
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("status") in ("CANCELED", "PARTIALLY_CANCELLED") or r.get("cancelled"):
            continue
        key = str(r["name"]).strip().upper()
        a = agg.setdefault(key, {
            "name": r["name"], "skus": set(), "qty": 0,
            "revenue": 0, "payout": 0, "cost": 0, "has_cost": False,
        })
        a["skus"].add(r["sku"])
        a["qty"] += r["qty"]
        a["revenue"] += r["revenue"]
        a["payout"] += r["payout"]
        a["cost"] += r["cost"]
        a["has_cost"] = a["has_cost"] or r.get("has_cost", False)

    items = []
    for a in agg.values():
        variants = sorted(a.pop("skus"))
        # Bitta variant bo'lsa — SKU ko'rsatamiz, bir nechtasi
        # birlashgan bo'lsa — nechta xil rang/variant borligini
        a["sku"] = variants[0] if len(variants) == 1 else f"{len(variants)} xil variant"
        # Tannarx kiritilmagan bo'lsa, "foyda" = butun to'lov bo'lib
        # ko'rinadi va bu yolg'on. Shunday tovarlarda foyda ko'rsatilmaydi.
        if a["has_cost"]:
            profit = a["payout"] - a["cost"]
            r_ = roi(profit, a["cost"])
        else:
            profit, r_ = 0, 0.0
        items.append({**a, "profit": profit, "roi": r_})

    # Kam sonlilar yuqorida — hisobotdagi tartib shunday
    items.sort(key=lambda x: (x["qty"], x["revenue"]))

    total_qty = sum(i["qty"] for i in items)
    total_rev = sum(i["revenue"] for i in items)
    total_pay = sum(i["payout"] for i in items)
    total_cost = sum(i["cost"] for i in items)
    # Jami foyda faqat tannarxi ma'lum tovarlardan hisoblanadi
    total_profit = sum(i["profit"] for i in items if i["has_cost"])
    no_cost = [i for i in items if not i["has_cost"]]

    extra = sum(e["amount"] for e in expenses if e["outcome"])
    refund = sum(e["amount"] for e in expenses if not e["outcome"])
    final = total_profit - extra + refund

    return {
        "date": start,
        "title": title,
        "source": source,
        "raw_rows": len(rows),
        "raw_expenses": len(expenses),
        "items": items,
        "total": {
            "qty": total_qty,
            "revenue": total_rev,
            "payout": total_pay,
            "cost": total_cost,
            "profit": total_profit,
            "roi": roi(total_profit, total_cost),
        },
        "no_cost": len(no_cost),
        "expenses": expenses,
        "extra": extra,
        "refund": refund,
        "final": final,
        "final_roi": roi(final, total_cost),
    }


async def build(day: datetime | None = None) -> dict[str, Any]:
    """Bitta kunlik hisobot — orqaga moslik uchun saqlanadi."""
    start, end = day_bounds(day)
    return await build_between(start, end, f"{start:%d.%m.%Y}")


PERIOD_LABELS = {
    "today": "📅 Bugun", "yesterday": "📆 Kecha",
    "month": "🗓 Bu oy", "last_month": "🗓 O'tgan oy",
}


def period_bounds(kind: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """
    Davr chegaralarini hisoblaydi.

    "month" — oyning 1-kunidan HOZIRGACHA (bugungacha), yopilgan oy
    emas. Shuning uchun 1-avgustda bu avtomatik 01.08 dan boshlanadi —
    qo'lda o'zgartirish shart emas.

    "last_month" — O'TGAN, TO'LIQ yopilgan oy (1-kundan oxirgi
    kunigacha). Masalan bugun avgustda bo'lsa, bu — butun iyul oyi.
    """
    now = now or datetime.now(TZ)
    if kind == "today":
        start, end = day_bounds(now)
        title = f"{start:%d.%m.%Y}"
    elif kind == "yesterday":
        start, end = day_bounds(now - timedelta(days=1))
        title = f"{start:%d.%m.%Y}"
    elif kind == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
        title = f"{start:%d.%m.%Y} – {now:%d.%m.%Y}"
    elif kind == "last_month":
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = this_month_start  # o'tgan oyning oxiri = shu oyning boshi
        # O'tgan oyning 1-kuni: bir kun orqaga surib, keyin 1-kunga qaytamiz
        last_day_prev = this_month_start - timedelta(days=1)
        start = last_day_prev.replace(day=1)
        title = f"{start:%d.%m.%Y} – {end - timedelta(seconds=1):%d.%m.%Y}"
    else:
        raise ValueError(f"noma'lum davr: {kind}")
    return start, end, title


async def build_period(kind: str) -> dict[str, Any]:
    """Tugma orqali tanlangan davr (bugun/kecha/bu oy) uchun hisobot."""
    start, end, title = period_bounds(kind)
    return await build_between(start, end, title)


def as_summary_text(rep: dict[str, Any]) -> str:
    """
    Qisqa, toza hisobot matni.

    MUHIM: bu yerda YO'Q narsalar ataylab yo'q —
      • salomlashish ("Assalomu aleykum")
      • Sof foyda / ROI (foydalanuvchi so'rovi bilan olib tashlangan)
      • "Kecha tushdi / bugun tushishi kerak" — bular taxminiy edi
        va rasm bilan bir xil manbadan kelmagani uchun raqamlar
        farq qilib, chalkashlik keltirardi

    Ko'rsatiladigan raqamlar RASMDAGI bilan AYNAN bir xil manbadan —
    ikkalasi ham shu `rep` obyektidan olinadi, shuning uchun hech
    qachon bir-biridan farq qilmaydi.
    """
    t = rep["total"]
    title = rep.get("title") or f"{rep['date']:%d.%m.%Y}"
    partial = rep.get("source") == "buyurtmalar"

    lines = [
        f"📊 <b>{title}</b>",
        "",
        f"📦 Sotilgan: <b>{t['qty']}</b> dona",
        f"💰 Daromad: <b>{fmt(t['revenue'])}</b> so'm",
    ]
    if not partial:
        lines.append(f"🏦 Chiqarishga: <b>{fmt(t['payout'])}</b> so'm")
    else:
        lines.append("<i>ℹ️ Moliyaviy ma'lumot hali yo'q — faqat sotuv soni.</i>")

    top = sorted(rep["items"], key=lambda x: -x["revenue"])[:5]
    if top:
        lines += ["", "<b>🔝 Eng ko'p sotilgan</b>"]
        for i in top:
            lines.append(f"   {i['sku'][:24]} — {i['qty']} dona · {fmt(i['revenue'])}")

    return "\n".join(lines)


def as_text(rep: dict[str, Any], limit: int = 12) -> str:
    """Telegram xabari uchun qisqa ko'rinish."""
    t = rep["total"]
    partial = rep.get("source") == "buyurtmalar"

    lines = [
        f"📊 <b>Hisobot — {rep['date']:%d.%m.%Y}</b>",
        "",
        f"📦 Sotilgan: <b>{t['qty']}</b> dona",
        f"💰 Daromad: <b>{fmt(t['revenue'])}</b> so'm",
    ]
    if partial:
        lines += [
            "",
            "<i>ℹ️ Foyda hisoblanmadi — Uzum moliyaviy ma'lumotni",
            "hali bermayapti. Faqat sotuv ko'rsatilgan.</i>",
        ]
    else:
        lines += [
            f"🏦 Chiqarishga: {fmt(t['payout'])} so'm",
            f"📈 Sof foyda: <b>{fmt(t['profit'])}</b> so'm",
            f"🎯 ROI: <b>{t['roi']:.2f}%</b>",
        ]
        if rep.get("no_cost"):
            lines += [
                "",
                f"⚠️ <i>{rep['no_cost']} ta tovarda tannarx kiritilmagan —",
                "ularning foydasi hisoblanmadi.</i>",
            ]

    # Xarajat va yakuniy summa faqat to'liq hisobotda ma'noga ega
    if not partial:
        if rep["expenses"]:
            lines += ["", "<b>Xarajatlar</b>"]
            for e in rep["expenses"]:
                if e["outcome"]:
                    lines.append(f"   ➖ {e['name']}: {fmt(e['amount'])}")
            if rep["refund"]:
                lines.append(f"   ➕ Qaytarilgan: {fmt(rep['refund'])}")

        lines += [
            "",
            f"💵 <b>Yakuniy: {fmt(rep['final'])} so'm</b>",
            f"🎯 ROI: <b>{rep['final_roi']:.2f}%</b>",
        ]

    if partial:
        top = sorted(rep["items"], key=lambda x: -x["revenue"])[:8]
        if top:
            lines += ["", "<b>🔝 Eng ko'p sotilgan</b>"]
            for i in top:
                lines.append(f"   {i['sku'][:24]} — {i['qty']} dona · {fmt(i['revenue'])}")
        return "\n".join(lines)

    best = sorted(rep["items"], key=lambda x: -x["profit"])[:5]
    if best:
        lines += ["", "<b>🔝 Eng foydali</b>"]
        for i in best:
            lines.append(f"   {i['sku'][:22]} — {fmt(i['profit'])} ({i['roi']:.0f}%)")

    bad = [i for i in rep["items"] if i["profit"] < 0]
    if bad:
        lines += ["", f"<b>⚠️ Zararda: {len(bad)} ta</b>"]
        for i in sorted(bad, key=lambda x: x["profit"])[:5]:
            lines.append(f"   {i['sku'][:22]} — {fmt(i['profit'])} ({i['roi']:.0f}%)")

    return "\n".join(lines)


async def build_with_fallback(day: datetime | None = None) -> dict[str, Any]:
    """
    Hisobot. Bugun bo'sh bo'lsa — kechagisini oladi.

    Nega? Uzum moliyaviy ma'lumotni kun davomida to'ldiradi.
    Ertalab bugungi hisobot bo'sh bo'lishi tabiiy, kechagisi esa tayyor.
    """
    rep = await build(day)
    if rep["items"] or day is not None:
        return rep

    yesterday = datetime.now(TZ) - timedelta(days=1)
    rep2 = await build(yesterday)
    rep2["fallback"] = True
    return rep2


# ------------------------------------------------------------------
#  TO'LIQ KUNLIK HISOBOT — Uzum Market uslubida
#
#  Bitta so'rovdan (status filtrisiz, chunki u ishlamaydi) kelgan
#  yozuvlar `status` maydoni bo'yicha ajratiladi:
#    TO_WITHDRAW           -> "Qabul qilingan" (pul chiqarishga tayyor)
#    PROCESSING            -> "Olib ketilgan" (hali jarayonda)
#    CANCELED / qisman      -> "Bekor qilingan"
#
#  Xarajat va qaytarilganlar /v1/finance/expenses dan, `source`
#  maydoni bo'yicha nomlanadi.
# ------------------------------------------------------------------

SOURCE_LABELS = {
    "LOGISTICS": "Logistika", "LOGISTIC": "Logistika", "DELIVERY": "Logistika",
    "STORAGE": "Sklad", "WAREHOUSE": "Sklad",
    "MARKETING": "Marketing", "ADS": "Marketing", "ADVERTISING": "Marketing",
    "PENALTY": "Shtraf FBS", "FBS_PENALTY": "Shtraf FBS", "FINE": "Shtraf FBS",
    "REFUND": "Qaytarilgan", "RETURN": "Qaytarilgan",
}


def _source_label(e: dict[str, Any]) -> str:
    """
    Xarajatni toifaga ajratadi.

    MUHIM: har bir yozuvning `name` maydoni buyurtma raqami bilan
    NOYOB bo'ladi ("Buyurtma № 118341928 uchun logistika..."). Agar
    shu nomni kalit sifatida ishlatsak, har bir buyurtma alohida
    qator bo'lib, ro'yxat o'nlab qatorga cho'zilib ketadi.

    Shuning uchun avval `source` maydoniga, u bo'lmasa `name` ichidagi
    KALIT SO'ZGA qarab toifalashtiramiz — natijada bir nechta aniq
    guruh chiqadi (Logistika, Shtraf FBS, Marketing, Sklad).
    """
    src = (e.get("source") or "").upper()
    if src in SOURCE_LABELS:
        return SOURCE_LABELS[src]

    name = (e.get("name") or "").lower()
    if "jarima" in name or "shtraf" in name or "penalty" in name:
        return "Shtraf FBS"
    if "logistik" in name:
        return "Logistika"
    if "marketing" in name or "reklama" in name:
        return "Marketing"
    if "sklad" in name or "ombor" in name or "saqlash" in name:
        return "Sklad"
    if "qayta" in name or "refund" in name or "vozvrat" in name:
        return "Qaytarilgan"

    return e.get("source") or "Boshqa"


def _bucket(rows: list[dict[str, Any]], statuses: set[str]) -> dict[str, Any]:
    """Status bo'yicha yig'indi: nechta buyurtma, soni, daromad, foyda."""
    sel = [r for r in rows if r.get("status") in statuses]
    order_ids = {r["order_id"] for r in sel if r.get("order_id")}
    with_cost = [r for r in sel if r.get("has_cost")]
    return {
        "count": len(order_ids) or len(sel),
        "qty": sum(r["qty"] for r in sel),
        "revenue": sum(r["revenue"] for r in sel),
        "payout": sum(r["payout"] for r in sel),
        "profit": sum(r["payout"] - r["cost"] for r in with_cost),
    }


async def build_full_between(start: datetime, end: datetime, title: str) -> dict[str, Any]:
    """
    Uzum Market uslubidagi to'liq hisobot — berilgan sana oralig'i uchun.

    Diqqat: "Kecha sizga taxminan ... tushdi" va "Bugun ... tushishi
    kerak" — bular TAXMINIY qiymatlar (nomida ham "taxminan" bor).
    Ular navbatga qo'yilgan (accepted) va jarayondagi (completed)
    summalardan hisoblanadi — Uzum aniq to'lov jadvalini alohida
    bermaydi.
    """
    rows = await uzum.get_finance_orders(start, end)
    expenses = await uzum.get_expenses(start, end)

    accepted = _bucket(rows, {"TO_WITHDRAW"})
    completed = _bucket(rows, {"PROCESSING"})

    canceled_rows = [
        r for r in rows
        if r.get("status") in ("CANCELED", "PARTIALLY_CANCELLED") or r.get("cancelled")
    ]
    canceled_ids = {r["order_id"] for r in canceled_rows if r.get("order_id")}
    canceled_value = sum(r["revenue"] for r in canceled_rows)

    # Sabab bo'yicha — faqat aniq sabab ko'rsatilganlar alohida sanaladi
    point_canceled = sum(
        1 for r in canceled_rows
        if r.get("return_cause") and "POINT" in str(r["return_cause"]).upper()
    )
    other_reason = sum(
        1 for r in canceled_rows
        if r.get("return_cause") and "POINT" not in str(r["return_cause"]).upper()
    )

    out_by_source: dict[str, int] = {}
    in_by_source: dict[str, int] = {}
    for e in expenses:
        label = _source_label(e)
        if e["outcome"]:
            out_by_source[label] = out_by_source.get(label, 0) + e["amount"]
        else:
            in_by_source[label] = in_by_source.get(label, 0) + e["amount"]

    return {
        "date": start,
        "title": title,
        "accepted": accepted,
        "completed": completed,
        "canceled": {
            "count": len(canceled_ids) or len(canceled_rows),
            "value": canceled_value,
            "point": point_canceled,
            "other": other_reason,
        },
        # Taxminiy — yuqoridagi izohga qarang
        "income_yesterday": {"amount": accepted["payout"], "count": accepted["count"]},
        "income_today_expected": {"amount": completed["payout"], "count": completed["count"]},
        "expenses_by_source": out_by_source,
        "expenses_total": sum(out_by_source.values()),
        "refunds_by_source": in_by_source,
        "refunds_total": sum(in_by_source.values()),
    }


async def build_full(day: datetime | None = None) -> dict[str, Any]:
    """Bitta kunlik to'liq hisobot — orqaga moslik uchun saqlanadi."""
    start, end = day_bounds(day)
    return await build_full_between(start, end, f"{start:%d.%m.%Y}")


async def build_full_period(kind: str) -> dict[str, Any]:
    """Tugma orqali tanlangan davr uchun to'liq (Uzum Market uslubidagi) hisobot."""
    start, end, title = period_bounds(kind)
    return await build_full_between(start, end, title)


def as_full_text(rep: dict[str, Any]) -> str:
    """
    Uzum Market uslubidagi qisqa matn.

    MUHIM: "Qabul qilingan" va "Olib ketilgan" endi ALOHIDA
    ko'rsatilmaydi — ular bitta umumiy summaga qo'shiladi. Sabab:
    ular Uzumning ichki statuslari (TO_WITHDRAW/PROCESSING) bo'yicha
    bo'lingan edi, va ko'pincha biri 0 chiqib, foydasiz chalkashlik
    keltirardi. Endi shu son RASMDAGI jadval bilan bir xil bo'ladi.

    Foydalanuvchi so'rovi bilan olib tashlangan qismlar: salomlashuv,
    "kecha/bugun pul tushdi/tushishi kerak" taxminlari, xarajat va
    qaytarilgan mablag' ro'yxati — булар taxminiy va chalkashtirardi.
    """
    a, c = rep["accepted"], rep["completed"]
    cn = rep["canceled"]
    title = rep.get("title") or f"{rep['date']:%d.%m.%Y}"

    count = a["count"] + c["count"]
    qty = a["qty"] + c["qty"]
    revenue = a["revenue"] + c["revenue"]
    payout = a["payout"] + c["payout"]

    lines = [
        f"📅 <b>{title}</b> uchun hisobot",
        "",
        f"📥 Buyurtmalar soni: <b>{count}</b>",
        f"📦 Sotilgan tovarlar soni: <b>{qty}</b>",
        f"💰 Daromad: <b>{fmt(revenue)}</b> so'm",
        f"🏦 Chiqarishga: <b>{fmt(payout)}</b> so'm",
    ]

    if cn["count"]:
        lines += [
            "",
            f"❌ Bekor qilinganlar soni: <b>{cn['count']}</b>",
            f"   Qiymati: {fmt(cn['value'])} so'm",
        ]
        if cn["point"]:
            lines.append(f"📍 Punktda bekor qilinganlar: <b>{cn['point']}</b>")
        if cn["other"]:
            lines.append(f"❔ Boshqa sababli bekor qilindi: <b>{cn['other']}</b>")

    return "\n".join(lines)
