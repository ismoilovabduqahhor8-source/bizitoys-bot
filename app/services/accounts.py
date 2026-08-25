"""Do'kon egasi (akkaunt) bilan ishlash — umumiy logika.

Bitta bot orqali bir nechta do'kon egasi ishlashi mumkin:
  • Abduqahhor — o'z tokeni va do'konlari bilan
  • Kamoliddin — o'z tokeni va do'konlari bilan

Har bir egasi uchun ALOHIDA UzumClient yashaydi (o'z keshiga ega).
Qaysi egasi ishlatilishi asyncio konteksti (ContextVar) orqali aniqlanadi —
`set_account(key)` chaqirilgach, barcha keyingi Uzum so'rovlari o'sha
egasining tokeni bilan boradi.
"""
from __future__ import annotations

from typing import Any

from app.config import Account, settings
from app.db import repo
from app.integrations.uzum import (
    current_account,
    default_account_key,
    set_account,
)

__all__ = [
    "Account",
    "all_accounts",
    "account",
    "by_name",
    "for_employee",
    "allowed_keys",
    "select",
    "current",
    "is_multi",
    "remember",
    "user_account",
    "forget",
]

# Foydalanuvchi oxirgi marta qaysi egasini tanlagan (xotira).
# Tanlash faqat bitta bosish uchun emas — FBS menyusi kabi ko'p bosqichli
# oqimlarda ham o'sha egasi ishlatilishi uchun saqlanadi. Yangi buyruq
# kelganda yana tanlash so'raladi («har safar»).
_USER_ACCOUNT: dict[int, str] = {}


def remember(telegram_id: int, key: str) -> None:
    """Foydalanuvchining tanlovini eslab qoladi."""
    if key in {a.key for a in all_accounts()}:
        _USER_ACCOUNT[telegram_id] = key


def user_account(telegram_id: int) -> str | None:
    """Foydalanuvchi oxirgi tanlagan egasi (bo'lmasa None)."""
    return _USER_ACCOUNT.get(telegram_id)


def forget(telegram_id: int) -> None:
    _USER_ACCOUNT.pop(telegram_id, None)


def all_accounts() -> list[Account]:
    """Barcha egasi ro'yxati (config'dan)."""
    return list(settings.uzum_accounts)


def account(key: str) -> Account:
    """Kalit bo'yicha egasini topadi; topilmasa birinchisini qaytaradi."""
    for a in settings.uzum_accounts:
        if a.key == key:
            return a
    return settings.uzum_accounts[0]


def by_name(name: str) -> Account | None:
    """Nom yoki kalit bo'yicha egasini topadi (admin buyruqlari uchun)."""
    name = name.strip().lower()
    for a in settings.uzum_accounts:
        if a.name.strip().lower() == name or a.key == name:
            return a
    return None


def for_employee(employee: dict[str, Any] | None) -> list[Account]:
    """Foydalanuvchi ko'rishi mumkin bo'lgan egasi ro'yxati.

    Admin — hammasini ko'radi. Oddiy xodim — faqat o'ziga biriktirilganini
    (employees.account_key). Biriktirilmagan xodim — hammasini ko'radi
    (eski xatti-harakat saqlanib qoladi).
    """
    if not employee:
        return []
    if employee.get("role") == repo.ROLE_ADMIN or not employee.get("account_key"):
        return all_accounts()
    return [a for a in all_accounts() if a.key == employee["account_key"]]


def allowed_keys(employee: dict[str, Any] | None) -> list[str]:
    return [a.key for a in for_employee(employee)]


def is_multi() -> bool:
    """Bir nechta egasi bormi? (faqat shunda tanlash tugmasi kerak)"""
    return len(settings.uzum_accounts) > 1


def current() -> Account:
    """Hozirgi kontekstdagi egasi."""
    return account(current_account())


def select(key: str | None = None) -> Account:
    """Egasini tanlab, kontekstga o'rnatadi va qaytaradi.

    key berilmasa — asosiy (birinchi) egasi tanlanadi.
    """
    key = key or default_account_key()
    set_account(key)
    return account(key)
