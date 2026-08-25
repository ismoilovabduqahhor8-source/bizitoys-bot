"""AI YORDAMCHI — erkin matndan amal (intent) aniqlash.

Xodim buyruq yozmaydi, oddiy tilda so'raydi:
  «hodimlar ro'yxati»      -> xodimlar ko'rsatiladi
  «QR kodlar kerak»        -> yorliqlar (QR + etiketka) yuboriladi
  «akt kerak»              -> postavka aktlari ko'rsatiladi
  «bugungi fbs rasmini tashla» -> bugungi hisobot RASMI yuboriladi
  «Aziz rolini yig'uvchi qil»  -> rol o'zgartiriladi (faqat admin)
  «bugungi savdo qancha?»  -> AI savdo konteksti bilan javob beradi

Qoida: aniqlangan amal ANIQ va xavfsiz bajariladi (mavjud buyruqlar
orga). AI raqam hisoblamaydi — hisoblash kodda.
"""
from __future__ import annotations

import re
from typing import Any

from app.db import repo

# ---------------------------------------------------------------
#  INTENT'LAR — kalit so'zlar (tartib muhim: avval aniqrog'i)
# ---------------------------------------------------------------
_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    # Rol o'zgartirish — eng aniq, birinchi tekshiriladi
    ("rol", ("rolini", "rolni", "roli", "rol o'zgartir", "rol o'zgarsin",
             "rolini o'zgartir", "rol o'zgar", "roli o'zgarsin")),
    # Xodimlar ro'yxati
    ("employees", ("hodimlar", "hodim", "xodimlar", "kimlar ishlayapti",
                   "kimlar ishlaydi", "jamoa", "employees", "xodimlar ro'yxati")),
    # QR / yorliq / etiketka
    ("yorliqlar", ("qr", "yorliq", "yorliqlar", "etiketka", "etiket",
                   "shtrix", "shtrix-kod")),
    # Akt / yuk xat / nakladnoy
    ("aktlar", ("aktlar", "akt", "yuk xat", "nakladnoy", "nakladnaya")),
    # Hisobot yoki RASM so'ralsa — bugungi hisobot rasmi yuboriladi
    ("hisobot", ("hisobot", "rasm", "rasmini", "surat", "suratini", "otchet")),
    # Uzum ombor qoldig'i
    ("qoldiq", ("qoldiq", "ostatka", "kam qolgan", "kam qolyapti", "zapas")),
    # Shoshilinch / muddat
    ("shosh", ("shoshilinch", "muddati", "kechik", "urgent", "tez qolgan")),
    # Buyurtmalar ro'yxati
    ("orders", ("buyurtma", "buyurtmalar", "zakaz", "nechta qoldi",
                "ish qolgan", "ish qolyapti")),
    # Postavka
    ("postavka", ("postavka", "pvz", "punkt")),
]

# So'z boshlanishida xato aniqlanmasligi uchun istisnolar
_EXCLUDES: dict[str, tuple[str, ...]] = {
    "aktlar": ("aktiv", "aktivlash", "aktivlashtir"),
    "yorliqlar": ("yorliqchasi",),  # zaxira
}

# Pul/savdo haqida so'ralsa — AI kontekstga bugungi savdo qo'shiladi
_FINANCE_WORDS = (
    "savdo", "summa", "pul", "daromad", "foyda", "tushum", "chiqarish",
    "naqd", "moliya", "olgan", "to'lov", "toʻlov", "qancha pul",
)

# Rol so'zlari (o'zgaruvchan yozilishi bilan)
_ROLE_SYNONYMS: dict[str, str] = {
    "admin": repo.ROLE_ADMIN,
    "administrator": repo.ROLE_ADMIN,
    "sklad": repo.ROLE_SKLAD,
    "skladchi": repo.ROLE_SKLAD,
    "ombor": repo.ROLE_SKLAD,
    "yiguvchi": repo.ROLE_PICKER,
    "yig'uvchi": repo.ROLE_PICKER,
    "picker": repo.ROLE_PICKER,
    "haydovchi": repo.ROLE_DRIVER,
    "haydovchisi": repo.ROLE_DRIVER,
    "xodim": repo.ROLE_EMPLOYEE,
    "employee": repo.ROLE_EMPLOYEE,
}

# Rol so'zidan keyin olib tashlanadigan yordamchi so'zlar (ismni topish uchun)
_STOPWORDS = {
    "rolini", "rolni", "roli", "rol", "o'zgartir", "o'zgarsin", "o'zgar",
    "o'zgartirib", "o'zgartirsang", "o'zgartirish", "qil", "qilib", "qilsin",
    "qiling", "kerak", "ber", "bering", "bog'la", "bog'lab", "bog'lasin",
    "xodim", "xodimning", "xodimni", "ishchi", "ishchining", "endi", "buni",
    "uning", "siz", "yangi", "iltimos", "raqamini", "raqam", "id",
}


def detect(text: str) -> str | None:
    """
    Erkin matndan amalni aniqlaydi.

    Qaytaradi: intent nomi ("employees", "yorliqlar", "aktlar", ...) yoki None.
    """
    t = (text or "").strip().lower()
    if not t:
        return None

    for intent, keys in _INTENT_RULES:
        ex = _EXCLUDES.get(intent)
        if ex and any(e in t for e in ex):
            continue
        for key in keys:
            if key in t:
                return intent
    return None


def is_finance(text: str) -> bool:
    """Pul/savdo haqida so'ralyaptimi? (AI kontekst uchun)"""
    t = (text or "").lower()
    return any(w in t for w in _FINANCE_WORDS)


def parse_role_change(text: str) -> tuple[str, str] | None:
    """
    «Aziz rolini yig'uvchi qil» -> ("aziz", "yiguvchi")

    Qaytaradi: (ism_hint, rol_kodi) yoki None (rol so'zi topilmasa).
    """
    t = (text or "").strip().lower()

    role_found = None
    for syn, code in _ROLE_SYNONYMS.items():
        if syn in t:
            role_found = code
            break
    if role_found is None:
        return None

    words = []
    for w in re.findall(r"[a-zа-яё'ʻ\-]+", t):
        if w in _ROLE_SYNONYMS or w in _STOPWORDS:
            continue
        if w in ("qil", "qilib", "qilsin", "qiling", "ber", "kerak"):
            continue
        words.append(w)

    name_hint = " ".join(words).strip()
    return (name_hint, role_found)


def find_employee_by_name(people: list[dict[str, Any]], hint: str) -> dict[str, Any] | None:
    """
    Xodimlar ro'yxatidan ism yoki username bo'yicha qidiradi.
    hint — kichik harfli ism/username fragmenti.
    """
    hint = (hint or "").strip().lower()
    if not hint:
        return None

    # Avval ANIQ mos (ism yoki username to'liq teng)
    for p in people:
        full = (p.get("full_name") or "").lower()
        uname = (p.get("username") or "").lower()
        if full == hint or uname == hint:
            return p

    # Keyin qisman mos — eng qisqa ismli (eng aniq) tanlanadi
    matches = [
        p for p in people
        if hint in (p.get("full_name") or "").lower()
        or hint in (p.get("username") or "").lower()
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: len(p.get("full_name") or ""))
    return matches[0]
