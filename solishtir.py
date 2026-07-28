"""
SOLISHTIRISH — loyihangiz asl paket bilan bir xilmi?

Ishlatish:  python solishtir.py

Nima qiladi:
  • har bir fayl o'zgartirilganmi tekshiradi
  • ortiqcha fayllarni topadi (boshqa vositalar qo'shgan bo'lishi mumkin)
  • yetishmayotganlarini ko'rsatadi

Nega kerak? Bir loyiha ustida bir necha vosita ishlaganda, kim nimani
o'zgartirganini bilish qiyin bo'lib qoladi. Bu dastur aniq javob beradi.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

OK, BAD, WARN = "  ✅", "  ❌", "  ⚠️"

# Loyihaga tegishli, lekin paketda bo'lmasligi normal
EXPECTED_EXTRA = {
    ".env", ".env.example", ".gitignore", "manifest.json", "solishtir.py",
    "requirements.txt", "botni_yoqish.bat", "yangilash.bat",
    "QANDAY_YANGILASH.md", "README.md",
}


def digest(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> None:
    print("=" * 60)
    print("  Loyihani solishtirish")
    print("=" * 60)

    mf = pathlib.Path("manifest.json")
    if not mf.exists():
        print(f"{BAD} manifest.json topilmadi — yangi paketni ko'chiring.")
        return

    manifest: dict[str, str] = json.loads(mf.read_text(encoding="utf-8"))

    changed, missing = [], []
    for name, want in manifest.items():
        p = pathlib.Path(name)
        if not p.exists():
            missing.append(name)
        elif digest(p) != want:
            changed.append(name)

    # Ortiqcha fayllar
    mine = set(manifest)
    extra = []
    for p in pathlib.Path("app").rglob("*.py"):
        s = str(p).replace("\\", "/")
        if s not in mine:
            extra.append(s)
    for p in pathlib.Path(".").glob("*.py"):
        s = str(p).replace("\\", "/").lstrip("./")
        if s not in mine and s not in EXPECTED_EXTRA:
            extra.append(s)

    print(f"\n1) Tekshirilgan: {len(manifest)} ta fayl")

    if changed:
        print(f"\n{WARN} O'ZGARTIRILGAN ({len(changed)} ta):")
        for n in changed:
            print(f"      {n}")
        print("      -> boshqa vosita tahrirlagan bo'lishi mumkin")
    else:
        print(f"{OK} Hech biri o'zgartirilmagan")

    if missing:
        print(f"\n{BAD} YETISHMAYDI ({len(missing)} ta):")
        for n in missing:
            print(f"      {n}")

    if extra:
        print(f"\n{WARN} BEGONA fayllar ({len(extra)} ta):")
        for n in sorted(extra):
            print(f"      {n}")
        print()
        print("      Bular paketda yo'q — boshqa vosita qo'shgan bo'lishi mumkin.")
        print("      Ular tekshiruvda ogohlantirish beradi va chalkashlik keltiradi.")
        print("      Kerak bo'lmasa o'chiring.")

    print("\n" + "=" * 60)
    if not (changed or missing):
        print("  ✅ Kod asl holatida")
    else:
        print("  ⚠️ Farqlar bor — yangilash.bat ni ishga tushiring")
    print("=" * 60)


if __name__ == "__main__":
    main()
