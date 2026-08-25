"""
Loyiha sozlamalari. Barcha maxfiy kalitlar .env faylidan o'qiladi.
Kodga hech qachon token yozilmaydi!
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = _env(key).lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "ha", "on")


def _env_list(key: str) -> list[int]:
    raw = _env(key)
    if not raw:
        return []
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


def _slug(text: str) -> str:
    """«Abduqahhor» -> «abduqahhor», «Abdu Qahhor» -> «abdu_qahhor»."""
    out = []
    for ch in text.strip().lower():
        out.append(ch if ch.isalnum() else "_")
    return "".join(out).strip("_")


@dataclass(frozen=True)
class Account:
    """Bitta do'kon egasi — o'z Uzum tokeni va do'konlari bilan."""

    key: str          # texnik identifikator (masalan "abduqahhor")
    name: str         # ko'rinadigan nom (masalan "Abduqahhor")
    token: str        # Uzum API tokeni
    shop_ids: list[int]  # do'konlar; bo'sh bo'lsa — token orqali barchasi


def _parse_accounts() -> list[Account]:
    """
    UZUM_ACCOUNTS:  Nomi|Token|Do'konID,Do'konID ; Nomi2|Token2|...

    Misol:
      UZUM_ACCOUNTS=Abduqahhor|TOKEN1|77165,77419 ; Kamoliddin|TOKEN2|11111,22222

    OSON USUL: UZUM_ACCOUNTS ga faqat YANGI egasini yozish kifoya —
    eski UZUM_TOKEN (agar ro'yxatda bo'lmasa) avtomatik birinchi egasi
    sifatida qo'shiladi. Shunda hech narsa buzilmaydi:
      UZUM_ACCOUNTS=Kamoliddin|TOKEN2|
      UZUM_ACCOUNT_NAME=Abduqahhor   (eski tokeningizning nomi)
    """
    raw = _env("UZUM_ACCOUNTS")
    out: list[Account] = []
    if raw:
        for i, part in enumerate(raw.split(";")):
            part = part.strip()
            if not part:
                continue
            seg = [s.strip() for s in part.split("|")]
            if len(seg) < 2 or not seg[0] or not seg[1]:
                continue
            name = seg[0]
            shops: list[int] = []
            if len(seg) > 2 and seg[2]:
                shops = [int(x) for x in seg[2].split(",") if x.strip().isdigit()]
            key = _slug(name) or f"acct{i + 1}"
            out.append(Account(key=key, name=name, token=seg[1], shop_ids=shops))

    # Eski UZUM_TOKEN ham egasi sifatida qo'shiladi — agar yuqoridagi
    # ro'yxatda bo'lmasa (ko'chib o'tishda hech narsa buzilmaydi).
    legacy_token = _env("UZUM_TOKEN")
    if legacy_token and not any(a.token == legacy_token for a in out):
        name = _env("UZUM_ACCOUNT_NAME", "Asosiy") or "Asosiy"
        out.append(
            Account(
                key=_slug(name) or "main",
                name=name,
                token=legacy_token,
                shop_ids=_env_list("UZUM_SHOP_IDS"),
            )
        )

    if not out:
        # Hech qanday token yo'q — soxta rejim uchun bitta egasi
        name = _env("UZUM_ACCOUNT_NAME", "Asosiy") or "Asosiy"
        out.append(
            Account(
                key=_slug(name) or "main",
                name=name,
                token=legacy_token,
                shop_ids=_env_list("UZUM_SHOP_IDS"),
            )
        )
    return out


@dataclass
class Settings:
    # ---------- Telegram ----------
    bot_token: str = field(default_factory=lambda: _env("BOT_TOKEN"))
    admin_ids: list[int] = field(default_factory=lambda: _env_list("ADMIN_IDS"))
    group_chat_id: int = field(default_factory=lambda: _env_int("GROUP_CHAT_ID", 0))

    # ---------- Uzum Seller API ----------
    uzum_base_url: str = field(
        default_factory=lambda: _env("UZUM_BASE_URL", "https://api-seller.uzum.uz")
    )
    uzum_token: str = field(default_factory=lambda: _env("UZUM_TOKEN"))
    # Bo'sh qoldirilsa — barcha do'konlar avtomatik olinadi
    uzum_shop_ids: list[int] = field(default_factory=lambda: _env_list("UZUM_SHOP_IDS"))
    # Ko'p do'kon egasi:  Nomi|Token|Do'konIDlar ; Nomi2|Token2|...
    # To'ldirilmasa — UZUM_TOKEN bitta egasi sifatida ishlayveradi.
    uzum_accounts: list[Account] = field(default_factory=_parse_accounts)
    # Yorliq o'lchami: LARGE (58x40mm) yoki BIG (43x25mm)
    label_size: str = field(default_factory=lambda: _env("LABEL_SIZE", "LARGE") or "LARGE")
    # Mahsulot QR yorlig'i o'lchami (nomining bir qismi, masalan "58")
    barcode_type: str = field(default_factory=lambda: _env("BARCODE_TYPE"))

    # Afzal ko'riladigan qabul punktlari (tuman yoki ko'cha nomi, vergul bilan).
    # Uzumda 180 dan ortiq punkt bor — ro'yxatni shu bilan qisqartiramiz.
    pvz_preferred: list[str] = field(
        default_factory=lambda: [
            x.strip() for x in _env(
                "PVZ_PREFERRED", "Uchtepa,Chilonzor,Eshonguzar,Sergeli"
            ).split(",") if x.strip()
        ]
    )

    # ---------- Sun'iy intellekt (ixtiyoriy) ----------
    # console.anthropic.com dan olinadi. Bo'sh qoldirilsa — bot AI'siz ishlaydi.
    ai_key: str = field(default_factory=lambda: _env("AI_KEY"))

    # Qaysi AI provayder ishlatilsin:
    #   anthropic — to'g'ridan-to'g'ri Anthropic API (tez, kalit kerak)
    #   make      — Make.com webhook orqali (sekinroq, Make sozlamasi kerak)
    ai_provider: str = field(
        default_factory=lambda: (_env("AI_PROVIDER", "anthropic") or "anthropic").lower()
    )
    # Make.com stsenariysining webhook manzili
    make_ai_webhook: str = field(default_factory=lambda: _env("MAKE_AI_WEBHOOK"))
    ai_model: str = field(default_factory=lambda: _env("AI_MODEL", "claude-haiku-4-5-20251001"))

    # ---------- Billz API ----------
    billz_base_url: str = field(
        default_factory=lambda: _env("BILLZ_BASE_URL", "https://api-admin.billz.uz/v1")
    )
    billz_secret: str = field(default_factory=lambda: _env("BILLZ_SECRET_TOKEN"))

    # ---------- Ish rejimi ----------
    # MOCK_MODE=true  -> hamma narsa soxta (kalitlarsiz test)
    # MOCK_MODE=false -> har bir xizmat MUSTAQIL: kaliti bor bo'lsa haqiqiy,
    #                    yo'q bo'lsa o'sha xizmat soxta ishlaydi.
    # Shuning uchun faqat Uzum kaliti bilan ham ishga tushirish mumkin.
    mock_mode: bool = field(default_factory=lambda: _env_bool("MOCK_MODE", True))

    # ---------- Rejalashtirilgan vazifalar ----------
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Asia/Tashkent"))
    morning_report_at: str = field(default_factory=lambda: _env("MORNING_REPORT_AT", "09:30"))
    evening_report_at: str = field(default_factory=lambda: _env("EVENING_REPORT_AT", "18:30"))
    late_check_every_min: int = field(default_factory=lambda: _env_int("LATE_CHECK_EVERY_MIN", 60))
    # Yangi buyurtmalarni necha daqiqada bir tekshirish
    new_order_check_min: int = field(default_factory=lambda: _env_int("NEW_ORDER_CHECK_MIN", 5))
    # "Hali ish bor" eslatmasi necha daqiqada bir
    nudge_every_min: int = field(default_factory=lambda: _env_int("NUDGE_EVERY_MIN", 90))
    # Ish vaqti — tunda bezovta qilmaslik uchun (soat)
    work_from: int = field(default_factory=lambda: _env_int("WORK_FROM", 9))
    work_to: int = field(default_factory=lambda: _env_int("WORK_TO", 20))
    stock_check_at: str = field(default_factory=lambda: _env("STOCK_CHECK_AT", "10:00"))
    # Sklad xodimlariga yig'ish ro'yxati yuboriladigan vaqt
    sklad_list_at: str = field(default_factory=lambda: _env("SKLAD_LIST_AT", "11:00"))
    # Kunlik moliyaviy hisobot vaqti (adminlarga)
    money_report_at: str = field(default_factory=lambda: _env("MONEY_REPORT_AT", "08:00"))

    # ---------- Biznes qoidalari ----------
    # Buyurtma necha soatdan keyin "kechikdi" hisoblanadi
    # Muddat tugashiga necha soat qolganda ogohlantirish yuborilsin
    warn_before_hours: int = field(default_factory=lambda: _env_int("WARN_BEFORE_HOURS", 6))
    late_after_hours: int = field(default_factory=lambda: _env_int("LATE_AFTER_HOURS", 4))
    # Ombordagi standart minimal qoldiq chegarasi
    default_min_stock: int = field(default_factory=lambda: _env_int("DEFAULT_MIN_STOCK", 5))

    # ---------- Baza ----------
    db_path: str = field(
        default_factory=lambda: _env("DB_PATH", str(BASE_DIR / "data" / "bot.db"))
    )

    @property
    def uzum_mock(self) -> bool:
        """Uzum to'liq soxta rejimdami? (hech bir egasida haqiqiy token yo'q)"""
        if self.mock_mode:
            return True
        return not any(a.token for a in self.uzum_accounts)

    @property
    def billz_mock(self) -> bool:
        """Billz soxta rejimdami?"""
        return self.mock_mode or not self.billz_secret

    def validate(self) -> list[str]:
        """Ishga tushishdan oldin sozlamalarni tekshiradi."""
        problems: list[str] = []
        if not self.bot_token or ":" not in self.bot_token:
            problems.append("BOT_TOKEN yo'q yoki noto'g'ri (@BotFather dan oling).")
        if not self.admin_ids:
            problems.append("ADMIN_IDS bo'sh — kamida bitta admin Telegram ID kerak.")
        # Kalit yo'qligi XATO emas — o'sha xizmat shunchaki soxta ishlaydi.
        return problems

    def mode_report(self) -> str:
        """Ishga tushganda qaysi xizmat qanday rejimda ekanini ko'rsatadi."""
        parts = []
        for a in self.uzum_accounts:
            mode = "🧪 TEST" if (self.mock_mode or not a.token) else "🚀 REAL"
            parts.append(f"{a.name}: {mode}")
        b = "🧪 TEST" if self.billz_mock else "🚀 REAL"
        return "  |  ".join(parts) + f"  |  Billz: {b}"


settings = Settings()
