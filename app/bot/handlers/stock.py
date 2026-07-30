"""
Ombor va sotuv tahlili buyruqlari (Billz).
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.integrations.base import ApiError
from app.config import settings
from app.db import repo
from app.integrations.billz import billz
from app.integrations.uzum import STATUS_TABS, uzum
from app.services import stock as stock_service

router = Router(name="stock")
log = logging.getLogger(__name__)


@router.message(Command("stock"))
@router.message(F.text == "📦 Ombor qoldig'i")
async def cmd_stock(message: Message) -> None:
    try:
        items = await billz.get_stock()
    except ApiError as e:
        await message.answer(f"⚠️ Billz'dan ma'lumot olinmadi.\n<code>{e}</code>")
        return
    await message.answer(stock_service.format_stock_table(items))


@router.message(Command("low"))
async def cmd_low(message: Message) -> None:
    try:
        items = await stock_service.low_stock_items()
    except ApiError as e:
        await message.answer(f"⚠️ Billz'dan ma'lumot olinmadi.\n<code>{e}</code>")
        return
    await message.answer(stock_service.format_low_stock(items))


@router.message(Command("top"))
@router.message(F.text == "🔥 Top sotuvlar")
async def cmd_top(message: Message) -> None:
    # /top 30  ->  oxirgi 30 kun
    days = 7
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].isdigit():
        days = min(int(parts[1]), 365)

    try:
        rows = await stock_service.top_sales(days=days)
    except ApiError as e:
        await message.answer(f"⚠️ Billz'dan ma'lumot olinmadi.\n<code>{e}</code>")
        return
    await message.answer(stock_service.format_top_sales(rows, days))


def _fmt(v: int) -> str:
    return f"{int(v):,}".replace(",", " ")


@router.message(Command("qoldiq"))
@router.message(F.text == "📉 Uzum qoldiq")
async def cmd_uzum_stock(message: Message) -> None:
    """Uzum FBS omboridagi qoldiq — kam qolganlari birinchi."""
    wait = await message.answer("⏳ Uzumdan qoldiq so'ralmoqda…")
    try:
        items = await uzum.get_stocks()
    except ApiError as e:
        await wait.edit_text(f"⚠️ Qoldiq olinmadi.\n<code>{e}</code>")
        return

    if not items:
        await wait.edit_text(
            "Qoldiq ma'lumoti kelmadi.\n\n"
            "Uzum API'sida bu bo'lim boshqacha nomlangan bo'lishi mumkin — "
            "loglarni tekshiring."
        )
        return

    limit = settings.default_min_stock
    low = [p for p in items if p["qty"] <= limit]

    lines = [f"<b>📉 Uzum FBS qoldig'i</b> — jami {len(items)} ta SKU", ""]
    if low:
        lines.append(f"<b>⚠️ Kam qolgan ({len(low)} ta, chegara {limit}):</b>")
        for p in low[:20]:
            icon = "🔴" if p["qty"] == 0 else "🟡"
            lines.append(f"{icon} {p['name'][:40]} — <b>{p['qty']}</b> dona")
            lines.append(f"     <i>{p['shop_name']}</i>")
        if len(low) > 20:
            lines.append(f"\n… va yana {len(low) - 20} ta.")
    else:
        lines.append("✅ Hamma mahsulot yetarli.")

    await wait.edit_text("\n".join(lines))


@router.message(Command("sanoq"))
@router.message(F.text == "🔢 FBS / FBO")
async def cmd_scheme_counts(message: Message) -> None:
    """Sxemalar bo'yicha buyurtmalar soni."""
    from app.integrations.uzum import STATUS_TABS as TABS

    wait = await message.answer("⏳ Sanalmoqda… (~15 soniya)")
    try:
        counts = await uzum.get_scheme_counts()
    except ApiError as e:
        await wait.edit_text(f"⚠️ Olinmadi.\n<code>{e}</code>")
        return

    if not counts:
        await wait.edit_text("Ma'lumot olinmadi.")
        return

    labels = {"FBS": "🏬 FBS — sizning omboringizdan",
              "DBS": "🚗 DBS — o'zingiz yetkazasiz"}
    lines = ["<b>🔢 Buyurtmalar soni</b>", ""]

    for scheme in ("FBS", "DBS"):
        data = counts.get(scheme)
        if data is None:
            continue
        total = sum(data.values())
        lines.append(f"<b>{labels[scheme]}</b> — jami {total} ta")
        if total:
            for st, n in data.items():
                if n:
                    lines.append(f"   {TABS.get(st, st)}: {n}")
        else:
            lines.append("   <i>buyurtma yo'q</i>")
        lines.append("")

    fbs = sum((counts.get("FBS") or {}).values())
    lines.append(f"👷 Xodimlar ishlaydi: <b>{fbs}</b> ta")
    lines += [
        "",
        "<i>ℹ️ FBO buyurtmalari bu yerda yo'q — Uzum API'si ularni",
        "bermaydi. FBO tovar Uzum omborida bo'ladi va ular buyurtma",
        "sifatida ko'rsatilmaydi. FBO bo'yicha faqat yuk xatlari bor:",
        "/aktlar</i>",
    ]
    await wait.edit_text("\n".join(lines))


@router.message(Command("moliyamaydon"))
async def cmd_finance_fields(message: Message, employee: dict) -> None:
    """
    TASHXIS: moliya yozuvida qanday maydonlar borligini ko'rsatadi.

    FinanceItemEntity hujjatda bo'sh — narx, komissiya va foyda qaysi
    nom bilan atalishini faqat haqiqiy javobdan bilamiz.
    """
    if employee["role"] != repo.ROLE_ADMIN:
        return

    import json
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    wait = await message.answer("⏳ Moliya yozuvi olinmoqda…")

    rows = await uzum.get_finance_orders(now - timedelta(days=3), now)
    sample = uzum._finance_sample

    if not sample:
        await wait.edit_text(
            f"{len(rows)} ta yozuv keldi, lekin namuna saqlanmadi.\n"
            "<i>Terminal logida «Moliya maydonlari: [...]» qatorini toping.</i>"
        )
        return

    await wait.edit_text(f"🔬 <b>Moliya: {len(rows)} ta yozuv</b>")

    keys = sorted(sample.keys())
    await message.answer("<b>Maydonlar:</b>\n<code>" + ", ".join(keys) + "</code>")

    # Faqat oddiy qiymatlar — rasm va ichma-ich lug'atlar kerak emas
    flat = {k: v for k, v in sample.items() if not isinstance(v, (dict, list))}
    await message.answer(
        "<b>Namuna:</b>\n<code>"
        + json.dumps(flat, ensure_ascii=False, indent=1)[:700] + "</code>"
    )

    # Bot qaysi maydonlarni ishlatyapti va topdimi
    checks = [
        ("Soni", ("amount", "quantity", "qty")),
        ("Narx", ("sellerPrice", "price", "sellPrice")),
        ("To'lov", ("withdrawnProfit", "sellerProfit", "profit", "payout")),
        ("Tannarx", ("purchasePrice", "costPrice", "cost")),
        ("Komissiya", ("commission",)),
    ]
    lines = ["<b>Bot nimani topdi:</b>", ""]
    for label, names in checks:
        found = next((n for n in names if n in sample), None)
        mark = "✅" if found else "❌"
        lines.append(f"{mark} {label}: <code>{found or 'topilmadi'}</code>")
    await message.answer("\n".join(lines))
    await message.answer("<i>Shu uch xabarni Claude'ga yuboring.</i>")




@router.message(Command("hisobot"))
@router.message(F.text == "📊 Hisobot")
async def cmd_report(message: Message, employee: dict) -> None:
    """
    Moliyaviy hisobot — davrni tanlash menyusi.

    Ilgari bu buyruq darrov bugungi hisobotni qurar edi. Endi avval
    uch tugma ko'rsatiladi: Bugun / Kecha / Bu oy — xodim kerakli
    davrni tanlaydi.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from app.services.report import PERIOD_LABELS

    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"hp:{kind}")]
        for kind, label in PERIOD_LABELS.items()
    ]
    await message.answer(
        "<b>📊 Moliyaviy hisobot</b>\n\nQaysi davr uchun?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("hp:"))
async def cb_report_period(callback: CallbackQuery, employee: dict) -> None:
    """Tanlangan davr (bugun/kecha/bu oy) uchun hisobotni quradi."""
    from app.services import report, report_image
    from app.services.report import PERIOD_LABELS

    kind = callback.data.split(":", 1)[1]
    await callback.answer(f"{PERIOD_LABELS.get(kind, kind)} tayyorlanmoqda…")

    wait = await callback.message.answer("⏳ Hisobot tayyorlanmoqda…")
    try:
        rep = await report.build_period(kind)
    except ApiError as e:
        await wait.edit_text(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    if not rep["items"]:
        await wait.edit_text(
            f"<b>{rep['title']}</b> uchun moliyaviy ma'lumot yo'q.\n\n"
            f"<b>Tashxis:</b>\n"
            f"• Uzumdan kelgan yozuvlar: <b>{rep.get('raw_rows', 0)}</b>\n"
            f"• Xarajat yozuvlari: <b>{rep.get('raw_expenses', 0)}</b>\n\n"
            "<i>Uzum moliyaviy ma'lumotni kun davomida to'ldiradi — "
            "ertalab bo'sh bo'lishi normal.</i>"
        )
        return

    await wait.delete()

    # 1) RASM — tovarlar jadvali, tepada
    img = report_image.render(rep)
    if img:
        await callback.message.answer_photo(
            BufferedInputFile(img, filename=f"hisobot_{rep['date']:%Y-%m-%d}.png"),
        )
    else:
        await callback.message.answer(
            "⚠️ Rasm chizilmadi — <code>Pillow</code> o'rnatilmagan.\n"
            "<code>pip install -r requirements.txt</code>"
        )

    # 2) MATN — qisqa va toza, rasm bilan bir xil raqamlar
    await callback.message.answer(report.as_summary_text(rep))


@router.message(Command("tahlil"))
@router.message(F.text == "🔍 Tahlil")
async def cmd_analysis(message: Message, employee: dict) -> None:
    """
    Biznes tahlili — muammolarni topadi va izohlaydi.

    Ikki bosqichda ishlaydi:
      1. Muammolar ODDIY HISOB bilan topiladi (aniq, bepul)
      2. AI ularni izohlaydi va maslahat beradi (agar ulangan bo'lsa)

    Bu ajratish muhim: raqamlar har doim to'g'ri bo'ladi, AI esa
    faqat tushuntirish uchun ishlatiladi.
    """
    from app.integrations.ai import ai
    from app.services import analytics

    # /tahlil 30 — boshqa davr uchun
    days = 7
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].isdigit():
        days = min(int(parts[1]), 90)

    wait = await message.answer(
        f"🔍 Oxirgi {days} kun tahlil qilinmoqda…\n"
        "<i>Uzumdan ma'lumot olinmoqda, biroz vaqt oladi</i>"
    )

    try:
        res = await analytics.find_problems(days=days)
    except Exception as e:
        log.exception("Tahlil xatosi")
        await wait.edit_text(
            f"⚠️ Tahlil qilinmadi.\n<code>{type(e).__name__}: {str(e)[:200]}</code>"
        )
        return

    await wait.edit_text(analytics.as_text(res))

    # Saqlab qo'yamiz — foydalanuvchi keyin savol bera oladi
    analytics.remember_analysis(message.from_user.id, res)

    # AI izohi — ixtiyoriy qism
    if not res["muammolar"]:
        return

    if not ai.enabled:
        await message.answer(
            "<i>💡 AI ulanmagan. Ulansa, bot bu muammolarni izohlab, "
            "nimadan boshlash kerakligini aytadi.\n"
            "Buning uchun .env faylida AI_KEY kiritilishi kerak.</i>"
        )
        return

    thinking = await message.answer("🤔 Tahlil qilinmoqda…")
    comment = await ai.analyze(res)
    if comment:
        await thinking.edit_text(f"💡 <b>Tahlil</b>\n\n{comment}")
    else:
        await thinking.delete()


@router.message(Command("bloklangan"))
@router.message(F.text == "🚫 Bloklangan")
async def cmd_blocked(message: Message) -> None:
    """
    Uzum tomonidan bloklangan (sotuvga chiqarilmagan) mahsulotlar.

    Bular "yo'qolgan tovar" kabi ko'rinadi: omborda bor, lekin
    sotilmaydi, chunki Uzum ularni bloklagan (rasm, hujjat,
    taqiqlangan toifa sababli).
    """
    from app.services import analytics

    wait = await message.answer("⏳ Tekshirilmoqda…")
    try:
        stats = await uzum.get_product_stats()
    except ApiError as e:
        await wait.edit_text(f"⚠️ Ma'lumot olinmadi.\n<code>{e}</code>")
        return

    found = analytics.find_blocked(stats)
    if not found:
        await wait.edit_text("✅ Bloklangan mahsulot yo'q.")
        return

    lines = [
        f"🚫 <b>{found['sarlavha']}</b>",
        f"Jami: <b>{found['jami_dona']}</b> dona sotilmay turibdi",
        "",
    ]
    for r in found["royxat"]:
        lines.append(f"🔴 <b>{r['tovar']}</b>")
        lines.append(f"   SKU: <code>{r['sku']}</code> · qoldiq: {r['qoldiq']}")
        lines.append(f"   Sabab: {r['sabab']}")
        if r["xabar"]:
            lines.append(f"   <i>{r['xabar']}</i>")
        lines.append("")

    lines.append(f"<i>→ {found['tavsiya']}</i>")
    await wait.edit_text("\n".join(lines))
