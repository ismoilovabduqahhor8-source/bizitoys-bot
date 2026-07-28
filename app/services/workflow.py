"""
ISH OQIMI — buyurtmaning skladdan PVZgacha bo'lgan yo'li.

Bu «holat mashinasi» (state machine) deb ataladi: buyurtma har doim
bitta bosqichda turadi va faqat ruxsat etilgan keyingi bosqichga o'tadi.

Nega shunday? Chunki tartibsizlikning oldini oladi. Yig'uvchi tekshirmasdan
«qadoqlandi» deb belgilay olmaydi — tugma shunchaki ko'rinmaydi.

Oqim:
    new → sklad → sklad_ready → checking → picking → packed → to_pvz → delivered
                       ↑            │
                       └── shortage ┘   (kam chiqsa skladga qaytadi)
"""
from __future__ import annotations

from typing import Any, NamedTuple

from app.db import repo


class Stage(NamedTuple):
    code: str
    label: str
    role: str | None          # kim harakat qiladi (None = admin/hamma)
    actions: list[tuple[str, str]]   # (keyingi_bosqich, tugma_matni)
    rank: int                 # oldinga siljish darajasi
    note: str = ""            # guruhga yoziladigan izoh


WORKFLOW: dict[str, Stage] = {
    "new": Stage(
        "new", "🆕 Yangi", None,
        [("sklad", "🏬 Skladga berish")],
        0, "buyurtma keldi",
    ),
    "sklad": Stage(
        "sklad", "🏬 Skladda", repo.ROLE_SKLAD,
        [("sklad_ready", "✅ Tovar tayyor")],
        1, "sklad tovar chiqarmoqda",
    ),
    "shortage": Stage(
        "shortage", "⚠️ Kam chiqdi", repo.ROLE_SKLAD,
        [("sklad_ready", "✅ To'ldirdim")],
        1, "tovar to'liq emas — sklad to'ldirmoqda",
    ),
    "sklad_ready": Stage(
        "sklad_ready", "📥 Tovar tayyor", repo.ROLE_PICKER,
        [("checking", "🔍 Tekshiryapman")],
        2, "yig'uvchi tekshirishi kerak",
    ),
    "checking": Stage(
        "checking", "🔍 Tekshirilmoqda", repo.ROLE_PICKER,
        [("picking", "✅ To'liq, yig'aman"), ("shortage", "⚠️ Kam chiqdi")],
        3, "tekshirilmoqda",
    ),
    "picking": Stage(
        "picking", "📦 Yig'ilmoqda", repo.ROLE_PICKER,
        [("packed", "✅ Qadoqlandi, QR yopishtirildi")],
        4, "yig'ilmoqda",
    ),
    "packed": Stage(
        "packed", "🎁 Yig'ilgan — postavka ochilmagan", None,
        [],   # keyingi qadam — admin postavka ochadi (/postavka)
        5, "yig'ilgan, postavka ochilishini kutmoqda",
    ),
    "in_postavka": Stage(
        "in_postavka", "📋 Postavkada — haydovchi kutilmoqda", repo.ROLE_DRIVER,
        [("to_pvz", "🚚 PVZga olib ketdim")],
        6, "postavka ochilgan, haydovchi olib ketishi kerak",
    ),
    "to_pvz": Stage(
        "to_pvz", "🚚 Yo'lda — PVZga eltilmoqda", repo.ROLE_DRIVER,
        [("delivered", "✅ PVZga topshirildi")],
        7, "yo'lda",
    ),
    "delivered": Stage(
        "delivered", "📍 PVZga topshirildi", None, [], 8,
        "PVZga topshirildi — Uzum qabul qilishini kutmoqda",
    ),
    "done": Stage(
        "done", "🎉 Yetkazildi", None, [], 9, "yakunlandi",
    ),
    "cancelled": Stage(
        "cancelled", "❌ Bekor qilindi", None, [], 10, "bekor qilindi",
    ),
}

# Uzumdan kelgan holatni bizning bosqichga bog'lash.
#
# MUHIM: Uzumdagi «В поставке» (DELIVERED) — bu PVZga topshirilgan
# degani EMAS. Mahsulot yig'ilgan va postavkaga qo'shilgan, lekin hali
# omborda turibdi. Haydovchi olib ketishi kerak.
# Shuning uchun u bizning «packed» bosqichiga to'g'ri keladi.
#
# Haqiqiy topshirish «Приняты Uzum» bo'lganda sodir bo'ladi — u API'da
# COMPLETED bo'lib keladi.
UZUM_TO_STAGE = {
    "new": "new",                    # CREATED           — Новые
    "packing": "sklad",              # PACKING           — В сборке
    "in_postavka": "in_postavka",    # PENDING_DELIVERY  — В поставке (ochilgan)
    "to_pvz": "to_pvz",              # DELIVERING        — yo'lda
    "delivered": "delivered",        # DELIVERED         — qabul punktida
    "done": "done",                  # ACCEPTED_AT_DP va keyingilari
    "cancelled": "cancelled",
}

# Xodim hali ishlashi kerak bo'lgan bosqichlar
ACTIVE_STAGES = [
    "new", "sklad", "shortage", "sklad_ready", "checking", "picking", "packed",
    "in_postavka", "to_pvz",
]

# Uzumdagi PACKING («В сборке») ga to'g'ri keladigan ichki bosqichlar.
# Postavka faqat SHULARDAN ochiladi — «В поставке» dagilar uchun
# postavka allaqachon ochilgan.
PACKING_STAGES = ["sklad", "sklad_ready", "checking", "picking", "packed"]


def get(code: str) -> Stage:
    return WORKFLOW.get(code) or WORKFLOW["new"]


def label(code: str) -> str:
    return get(code).label


def rank(code: str) -> int:
    return get(code).rank


def is_active(code: str) -> bool:
    return code in ACTIVE_STAGES


def next_actions(code: str) -> list[tuple[str, str]]:
    return get(code).actions


def can_act(stage_code: str, employee: dict[str, Any]) -> bool:
    """
    Bu xodim shu bosqichda harakat qila oladimi?

    Admin — har doim. Boshqalar — faqat o'z roli mos kelsa.
    Umumiy 'employee' roli hamma bosqichda ishlay oladi (kichik jamoa uchun).
    """
    if employee["role"] == repo.ROLE_ADMIN:
        return True
    stage = get(stage_code)
    if stage.role is None:
        return False
    if employee["role"] == repo.ROLE_EMPLOYEE:
        return True
    return employee["role"] == stage.role


def responsible_role(code: str) -> str | None:
    return get(code).role


def progress_bar(code: str) -> str:
    """Vizual ko'rsatkich: ▰▰▰▱▱▱▱"""
    if code in ("cancelled",):
        return "❌"
    total = 8
    done = min(rank(code), total)
    return "▰" * done + "▱" * (total - done)


def merge_with_uzum(local_stage: str, uzum_status: str) -> str:
    """
    Uzumdagi holat bilan bizning bosqichni birlashtirish.

    Qoida: Uzum oldinga ketgan bo'lsa — unga ergashamiz (masalan buyurtma
    bekor qilingan). Aks holda o'z bosqichimizni saqlaymiz, chunki u
    batafsilroq (Uzum «DELIVERING» deydi, biz esa aynan qaysi
    bosqichda ekanini bilamiz).
    """
    mapped = UZUM_TO_STAGE.get(uzum_status, "new")
    if uzum_status == "cancelled":
        return "cancelled"
    return mapped if rank(mapped) > rank(local_stage) else local_stage
