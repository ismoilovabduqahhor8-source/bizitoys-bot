"""
TEKSHIRUV — botni ishga tushirishdan oldin hamma narsa joyidami?

Ishlatish:  python tekshir.py

Nima qiladi:
  1. .env sozlamalarini tekshiradi
  2. Har bir kod faylini yuklab ko'radi (import xatolarini topadi)
  3. Bot qismlarini yig'ib ko'radi (Telegramga ulanmasdan)

Bu 5 soniyalik tekshiruv botni 10 daqiqa qayta-qayta ishga tushirishdan tez.
"""
from __future__ import annotations

import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
OK, BAD = "  ✅", "  ❌"


def check_imports() -> int:
    print("1) Kod fayllari")
    mods = sorted(
        str(p.with_suffix("")).replace("\\", ".").replace("/", ".")
        for p in pathlib.Path("app").rglob("*.py")
        if p.name != "__init__.py"
    )
    bad = 0
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad += 1
            print(f"{BAD} {m}\n       {type(e).__name__}: {e}")
    print(f"{OK} {len(mods) - bad}/{len(mods)} fayl toza" if not bad else f"     {bad} ta xato")
    return bad


def check_settings() -> int:
    print("\n2) Sozlamalar (.env)")
    from app.config import settings
    problems = settings.validate()
    for p in problems:
        print(f"{BAD} {p}")
    if not problems:
        print(f"{OK} {settings.mode_report()}")
    return len(problems)


def check_libs() -> int:
    """Kutubxonalar o'rnatilganmi."""
    print("\n3) Kutubxonalar")
    bad = 0
    for mod, why in [
        ("aiogram", "Telegram"),
        ("httpx", "API so'rovlari"),
        ("aiosqlite", "baza"),
        ("apscheduler", "vaqt jadvali"),
        ("pypdf", "PDF birlashtirish"),
    ]:
        try:
            __import__(mod)
        except ImportError:
            bad += 1
            print(f"{BAD} {mod} yo'q ({why})")
    if not bad:
        print(f"{OK} hammasi o'rnatilgan")
    else:
        print("     Tuzatish: pip install -r requirements.txt")
    return bad


def check_names() -> int:
    """
    Kod tozaligi.

    XATO   — aniqlanmagan nom (import unutilgan). Bot qulaydi.
    OGOHLANTIRISH — takroriy import va shunga o'xshash. Ishlashga
                    xalaqit bermaydi, lekin tartibsizlik belgisi.
    """
    print("\n4) Kod tozaligi")
    try:
        import io

        from pyflakes.api import checkRecursive
        from pyflakes.reporter import Reporter

        out, err = io.StringIO(), io.StringIO()
        checkRecursive(["app"], Reporter(out, err))
        lines = out.getvalue().splitlines()

        errors = [l for l in lines if "undefined name" in l]
        warns = [l for l in lines if "redefinition" in l]

        for l in errors:
            print(f"{BAD} {l}")
        for l in warns[:5]:
            print(f"     ⚠️  {l}")

        if not errors:
            note = f" ({len(warns)} ta ogohlantirish)" if warns else ""
            print(f"{OK} aniqlanmagan nom yo'q{note}")

        # Faqat HAQIQIY xatolar hisobga olinadi
        return len(errors)
    except ImportError:
        print("     (pyflakes o'rnatilmagan — bu tekshiruv o'tkazilmadi)")
        return 0


def check_bot() -> int:
    print("\n5) Bot qismlari")
    try:
        from aiogram import Dispatcher
        from app.bot.handlers import (admin, attendance, common, fbs, invoices, orders,
                                      postavka, stock)
        from app.bot.middlewares import AuthMiddleware

        dp = Dispatcher()
        dp.message.outer_middleware(AuthMiddleware())
        for r in (admin.router, attendance.router, fbs.router, postavka.router,
                  invoices.router, orders.router, stock.router, common.router):
            dp.include_router(r)
        print(f"{OK} Router'lar: {[r.name for r in dp.sub_routers]}")
        return 0
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {e}")
        return 1


def main() -> None:
    print("=" * 58)
    print("  BiziToys bot — tekshiruv")
    print("=" * 58 + "\n")
    total = check_imports()
    if total == 0:
        total += check_settings()
        total += check_libs()
        total += check_names()
        total += check_bot()

    print("\n" + "=" * 58)
    if total == 0:
        print("  ✅ HAMMASI JOYIDA — botni ishga tushirsangiz bo'ladi")
    else:
        print(f"  ❌ {total} ta muammo — yuqoridagi xatolarni tuzating")
    print("=" * 58)


if __name__ == "__main__":
    main()
