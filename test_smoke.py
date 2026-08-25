"""
BiziToys — tezkor sinov (Telegram'siz, MOCK rejimda).

Ishlatish:  python test_smoke.py

Bu skript butun biznes-logikani Telegram'ga ulanmasdan sinaydi:
buyurtmalar, guruhlash, ombor, hisobotlar (matn/rasm/PDF/Excel),
FBO holati, tahlil, ish oqimi va ko'p do'kon egasi (akkaunt).
"""
from __future__ import annotations

import asyncio
import os
import sys

# Ko'p do'kon egasi rejimini sinash uchun — config import qilinishidan
# OLDIN sozlanadi (config import paytida o'qiladi).
os.environ.setdefault("UZUM_ACCOUNTS",
                      "Abduqahhor|test_tok_1|77165,77419 ; Kamoliddin|test_tok_2|11111")
os.environ.setdefault("MOCK_MODE", "true")

PASS, FAIL = 0, 0


def check(name: str, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}\n     {type(e).__name__}: {e}")


async def main() -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.db import repo
    from app.config import settings
    from app.services import orders as order_service
    from app.services import report, report_image, report_pdf
    from app.services import grouping, workflow, analytics, stock as stock_service
    from app.services import fbo_excel
    from app.integrations.uzum import uzum
    from app.integrations import mock_data

    TZ = ZoneInfo(settings.timezone)

    print("1) Baza")
    await repo.init_db()

    print("\n2) Buyurtmalar (mock)")
    orders = await order_service.sync_today()
    check(f"sync_today: {len(orders)} ta buyurtma", lambda: None)
    check("summarize", lambda: order_service.summarize(orders))
    check("format_summary",
          lambda: order_service.format_summary(order_service.summarize(orders)))
    check("format_by_shop", lambda: order_service.format_by_shop(orders))
    check("format_order_card", lambda: order_service.format_order_card(orders[0]))

    print("\n3) Guruhlash")
    groups = grouping.build(orders)
    check(f"grouping.build: {len(groups)} guruh", lambda: None)
    check("format_group", lambda: [grouping.format_group(g) for g in groups])

    print("\n4) Ombor va sotuv (Billz mock)")
    low = await stock_service.low_stock_items()
    check(f"low_stock_items: {len(low)} ta", lambda: None)
    check("format_low_stock", lambda: stock_service.format_low_stock(low))
    top = await stock_service.top_sales()
    check("top_sales", lambda: stock_service.format_top_sales(top, 7))

    print("\n5) Hisobot (moliya mock)")
    now = datetime.now(TZ)
    rep = await report.build(now - timedelta(days=1))
    check(f"report.build: {len(rep['items'])} ta tovar", lambda: None)
    check("report.as_text", lambda: report.as_text(rep))
    check("report.as_summary_text", lambda: report.as_summary_text(rep))
    full = await report.build_full(now - timedelta(days=1))
    check("report.as_full_text", lambda: report.as_full_text(full))
    check("period month", lambda: report.period_bounds("month"))
    check("period last_month", lambda: report.period_bounds("last_month"))

    print("\n6) Rasm va PDF")
    img = report_image.render(rep)
    check(f"report_image.render: {len(img)} bayt" if img else "report_image.render",
          lambda: None)
    pdf = report_pdf.render(rep)
    check(f"report_pdf.render: {len(pdf)} bayt" if pdf else "report_pdf.render",
          lambda: None)

    print("\n7) FBO Excel")
    invs, _ = await uzum.get_fbo_invoices()
    xlsx = fbo_excel.build_many(invs, "25.08.2026")
    check(f"fbo_excel.build_many: {len(xlsx)} bayt" if xlsx else "fbo_excel.build_many",
          lambda: None)

    print("\n8) FBO holati va mahsulot holati")
    await repo.set_fbo_invoice_state("9002", 79873, "IN_PROGRESS")
    st = await repo.get_fbo_invoice_status("9002")
    check(f"fbo state saqlandi: {st}", lambda: None)
    stats = await uzum.get_product_stats()
    check(f"get_product_stats: {len(stats)} ta SKU", lambda: None)
    await repo.set_sku_state("5000", blocked=False, paid_storage=False, low_stock=True)
    sk = await repo.get_sku_state("5000")
    check(f"sku state saqlandi: low_stock={sk['low_stock']}", lambda: None)

    print("\n9) Tahlil")
    res = await analytics.find_problems(days=7)
    check(f"find_problems: {len(res['muammolar'])} muammo", lambda: None)
    check("analytics.as_text", lambda: analytics.as_text(res))

    print("\n10) Ish oqimi")
    check("workflow labels",
          lambda: [workflow.label(s) for s in workflow.WORKFLOW])
    check("can_act admin", lambda: workflow.can_act("packed", {"role": "admin"}))
    check("can_act yiguvchi sklad",
          lambda: not workflow.can_act("sklad", {"role": "yiguvchi"}))
    check("merge_with_uzum", lambda: workflow.merge_with_uzum("packed", "cancelled"))

    print("\n11) Mock ma'lumotlar")
    check("mock_orders", lambda: mock_data.mock_orders())
    check("mock_finance", lambda: mock_data.mock_finance())
    check("mock_expenses", lambda: mock_data.mock_expenses())

    print("\n12) Ko'p do'kon egasi (akkaunt)")
    from app.services import accounts as acct_service
    from app.integrations.uzum import get_client, current_account

    accts = acct_service.all_accounts()
    check(f"UZUM_ACCOUNTS: {len(accts)} ta egasi (Abduqahhor, Kamoliddin)",
          lambda: (len(accts) == 2
                   and {a.name for a in accts} == {"Abduqahhor", "Kamoliddin"}))

    admin = {"role": repo.ROLE_ADMIN, "account_key": None}
    emp_ab = {"role": repo.ROLE_EMPLOYEE, "account_key": "abduqahhor"}
    emp_kam = {"role": repo.ROLE_EMPLOYEE, "account_key": "kamoliddin"}
    check("admin hammasini ko'radi",
          lambda: len(acct_service.for_employee(admin)) == 2)
    check("xodim faqat o'z egasini ko'radi",
          lambda: (len(acct_service.for_employee(emp_ab)) == 1
                   and acct_service.for_employee(emp_ab)[0].key == "abduqahhor"
                   and acct_service.for_employee(emp_kam)[0].key == "kamoliddin"))

    c1 = get_client("abduqahhor")
    c2 = get_client("kamoliddin")
    check("har egasining clienti alohida (kesh ajratilgan)",
          lambda: c1 is not c2 and c1.account_key != c2.account_key)

    acct_service.select("kamoliddin")
    check("select -> current_account",
          lambda: current_account() == "kamoliddin")
    orders_kam = await order_service.sync_today()
    check(f"Kamoliddin clienti bilan buyurtmalar: {len(orders_kam)} ta",
          lambda: None)
    acct_service.select("abduqahhor")
    check("qayta tanlash ishlaydi",
          lambda: current_account() == "abduqahhor")

    check("by_name topish", lambda: acct_service.by_name("kamoliddin").name == "Kamoliddin")
    check("set_employee_account",
          lambda: None)
    await repo.set_employee_account(999999, "kamoliddin")
    saved = await repo.get_employee(999999)
    check("xodim-egasi bog'lash bazada",
          lambda: saved and saved["account_key"] == "kamoliddin")

    print("\n13) AI intent (erkin matndan amal aniqlash)")
    from app.services import ai_intent

    def intent_ok(text: str, want: str | None) -> None:
        got = ai_intent.detect(text)
        assert got == want, f"{text!r}: {got!r} != {want!r}"

    check("hodimlar ro'yxati -> employees",
          lambda: intent_ok("hodimlar ro'yxatini ko'rsat", "employees"))
    check("QR kodlar -> yorliqlar",
          lambda: intent_ok("QR kodlar kerak", "yorliqlar"))
    check("rasmini tashla -> hisobot",
          lambda: intent_ok("bugungi fbs buyurtmalar rasmini tashla", "hisobot"))
    check("akt -> aktlar",
          lambda: intent_ok("akt kerak", "aktlar"))
    check("savdo qancha -> AI (None)",
          lambda: intent_ok("bugungi savdo qancha", None))
    check("salom -> AI (None)",
          lambda: intent_ok("salom", None))
    check("aktiv -> akt EMAS",
          lambda: intent_ok("aktiv tovarlar qancha", None))
    check("rol parse",
          lambda: ai_intent.parse_role_change("aziz rolini yig'uvchi qil")
          == ("aziz", "yiguvchi"))


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n===== NATIJA: {PASS} OK, {FAIL} XATO =====")
    sys.exit(1 if FAIL else 0)
