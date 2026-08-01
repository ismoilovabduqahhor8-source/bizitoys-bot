"""
Uzum Market Seller API — integratsiya.

Hujjat: https://api-seller.uzum.uz/api/seller-openapi/swagger/api-docs

TAJRIBADA ANIQLANGAN MUHIM NARSALAR (hujjatda noto'g'ri yoki yo'q):

1. Kalit `Authorization` sarlavhasida to'g'ridan-to'g'ri yuboriladi — "Bearer" YO'Q.

2. `/v2/fbs/orders` status'siz so'ralsa faqat CREATED qaytaradi.
   Har bir status uchun alohida so'rov kerak.

3. `drop-off-points` parametri `customerOrderIds` deb ataladi, lekin
   SOTUVCHI buyurtma raqamini (Номер заказа) kutadi. publicId prefiksi
   bo'sh ro'yxat qaytaradi.

4. Yorliq va hujjatlar Base64 matn ko'rinishida keladi (PDF ham, havola ham emas).

5. Uzum sekundiga atigi 2 ta so'rovga ruxsat beradi.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.integrations import mock_data
from app.integrations.base import ApiError, BaseClient

log = logging.getLogger(__name__)

PREFIX = "/api/seller-openapi"
TZ = ZoneInfo(settings.timezone)

# Uzum chegarasi: 2 so'rov/sekund. Xavfsizlik uchun sekinroq yuramiz.
MIN_INTERVAL = 1.0
CACHE_TTL = 120

ENDPOINTS = {
    "orders": f"{PREFIX}/v2/fbs/orders",
    "orders_count": f"{PREFIX}/v2/fbs/orders/count",
    "order": f"{PREFIX}/v1/fbs/order/{{order_id}}",
    "confirm": f"{PREFIX}/v1/fbs/order/{{order_id}}/confirm",
    "cancel": f"{PREFIX}/v1/fbs/order/{{order_id}}/cancel",
    "labels": f"{PREFIX}/v1/fbs/order/{{order_id}}/labels/print",
    "shops": f"{PREFIX}/v1/shops",
    "stocks": f"{PREFIX}/v3/fbs/sku/stocks",
    "products": f"{PREFIX}/v1/product/shop/{{shop_id}}",
    "barcode_types": f"{PREFIX}/v1/product/barcodes/types",
    "expenses": f"{PREFIX}/v1/finance/expenses",
    "finance_orders": f"{PREFIX}/v1/finance/orders",
    "barcodes_print": f"{PREFIX}/v1/product/shop/{{shop_id}}/barcodes/print",
    "invoices": f"{PREFIX}/v1/fbs/invoice",
    "invoice": f"{PREFIX}/v1/fbs/invoice/{{invoice_id}}",
    "invoice_orders": f"{PREFIX}/v1/fbs/invoice/{{invoice_id}}/orders",
    "invoice_print": f"{PREFIX}/v1/fbs/invoice/{{invoice_id}}/print",
    "drop_off_points": f"{PREFIX}/v1/fbs/invoice/dop/drop-off-points",
    "time_slots": f"{PREFIX}/v1/fbs/invoice/dop/time-slot",
    # FBO yuk xatlari — bular FBS invoice'dan BOSHQA narsa: Uzum
    # omboriga jo'natiladigan yuk xatlari (накладные поставки FBO)
    "fbo_invoices": f"{PREFIX}/v1/shop/{{shop_id}}/invoice",
    "fbo_invoice_products": f"{PREFIX}/v1/shop/{{shop_id}}/invoice/products",
    # Yagona manzil — BARCHA do'konlarni bitta so'rovda qaytaradi va
    # mahsulotlarni ham o'zida olib keladi (alohida so'rov shart emas).
    # Bundan tashqari "deliveryCertificate" (yetkazishni tasdiqlovchi
    # HUJJAT) maydoni ham shu yerda — bu Uzumning o'z hujjati.
    "invoices_all": f"{PREFIX}/v1/invoice",
}

# ============================================================
#  BUYURTMA STATUSLARI (api-docs dan, to'liq ro'yxat)
# ============================================================
S_CREATED = "CREATED"                    # Новые
S_PACKING = "PACKING"                    # В сборке
S_PENDING_DELIVERY = "PENDING_DELIVERY"  # В поставке
S_DELIVERING = "DELIVERING"              # yo'lda
S_DELIVERED = "DELIVERED"                # qabul punktida
S_ACCEPTED_AT_DP = "ACCEPTED_AT_DP"      # Приняты Uzum
S_AT_CUSTOMER_DP = "DELIVERED_TO_CUSTOMER_DELIVERY_POINT"   # Ждут выдачи
S_COMPLETED = "COMPLETED"                # Выданы
S_CANCELED = "CANCELED"                  # Отменены
S_PENDING_CANCELLATION = "PENDING_CANCELLATION"
S_RETURNED = "RETURNED"                  # Возвраты

ALL_STATUSES = [
    S_CREATED, S_PACKING, S_PENDING_DELIVERY, S_DELIVERING, S_DELIVERED,
    S_ACCEPTED_AT_DP, S_AT_CUSTOMER_DP, S_COMPLETED, S_CANCELED,
    S_PENDING_CANCELLATION, S_RETURNED,
]

STATUS_TABS = {
    S_CREATED: "Новые",
    S_PACKING: "В сборке",
    S_PENDING_DELIVERY: "В поставке",
    S_DELIVERING: "Yo'lda",
    S_DELIVERED: "Qabul punktida",
    S_ACCEPTED_AT_DP: "Приняты Uzum",
    S_AT_CUSTOMER_DP: "Ждут выдачи",
    S_COMPLETED: "Выданы",
    S_CANCELED: "Отменены",
    S_PENDING_CANCELLATION: "Bekor qilinmoqda",
    S_RETURNED: "Возвраты",
}

# Xodim ish qilishi kerak bo'lgan statuslar
ACTIVE_STATUSES = [S_CREATED, S_PACKING, S_PENDING_DELIVERY, S_DELIVERING]
REPORT_STATUSES = ACTIVE_STATUSES

# Uzum statusi -> ichki bosqich
STATUS_MAP = {
    S_CREATED: "new",
    S_PACKING: "packing",
    S_PENDING_DELIVERY: "in_postavka",
    S_DELIVERING: "to_pvz",
    S_DELIVERED: "delivered",
    S_ACCEPTED_AT_DP: "done",
    S_AT_CUSTOMER_DP: "done",
    S_COMPLETED: "done",
    S_CANCELED: "cancelled",
    S_PENDING_CANCELLATION: "cancelled",
    S_RETURNED: "cancelled",
}

# ============================================================
#  AKT (nakladnoy) STATUSLARI — FbsInvoiceStatus
# ============================================================
INV_CREATED = "CREATED"
INV_IN_PROGRESS = "ACCEPTANCE_IN_PROGRESS"
INV_ACCEPTED = "ACCEPTED"
INV_CANCELLED = "CANCELLED"
INVOICE_STATUSES = [INV_CREATED, INV_IN_PROGRESS, INV_ACCEPTED, INV_CANCELLED]

# Ish qolgan aktlar — kabinetdagi «Создана» va qabul jarayonidagilar.
# «Принята» (ACCEPTED) tugagan: Uzum qabul qilgan, xodimga vazifa yo'q.
# Ularni ko'rsatish ro'yxatni keraksiz to'ldiradi.
ACTIVE_INVOICE_STATUSES = [INV_CREATED, INV_IN_PROGRESS]

# Moliya statuslari — /v1/finance/orders uchun.
# Buyurtmalar bilan bo'lgani kabi, status berilmasa Uzum BO'SH javob beradi.
FIN_TO_WITHDRAW = "TO_WITHDRAW"          # pul chiqarishga tayyor
FIN_PROCESSING = "PROCESSING"            # jarayonda
FIN_CANCELED = "CANCELED"
FIN_PARTIALLY_CANCELLED = "PARTIALLY_CANCELLED"

# Daromadga kiradiganlar (bekor qilinganlar hisobga olinmaydi)
FINANCE_STATUSES = [FIN_TO_WITHDRAW, FIN_PROCESSING]

INVOICE_LABELS = {
    INV_CREATED: "📝 Tuzilgan",
    INV_IN_PROGRESS: "⏳ Qabul qilinmoqda",
    INV_ACCEPTED: "✅ Qabul qilindi",
    INV_CANCELLED: "❌ Bekor qilindi",
}


# ------------------------------------------------------------------
#  SANA YORDAMCHILARI
# ------------------------------------------------------------------
def _ms_to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, TZ)
    except (TypeError, ValueError, OSError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    """Uzum sanani ham millisekundda, ham ISO matnda yuboradi."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return _ms_to_dt(value)
    txt = str(value)
    if txt.isdigit():
        return _ms_to_dt(int(txt))
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def _sort_key(value: Any) -> float:
    dt = _parse_dt(value)
    return dt.timestamp() if dt else 0.0


def _derive_status(o: dict[str, Any]) -> str:
    """Holatni status matnidan, topilmasa sanalardan aniqlaydi."""
    mapped = STATUS_MAP.get(str(o.get("status") or "").upper())
    if mapped:
        return mapped
    if o.get("dateCancelled") or o.get("returnDate"):
        return "cancelled"
    if o.get("completedDate"):
        return "done"
    if o.get("deliveredToDeliveryPointDate"):
        return "delivered"
    if o.get("acceptedDate"):
        return "packing"
    return "new"


class UzumClient(BaseClient):
    service_name = "Uzum"

    def __init__(self) -> None:
        super().__init__(
            base_url=settings.uzum_base_url,
            headers={"Authorization": settings.uzum_token, "Accept": "application/json"},
        )
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._shops_cache: list[dict[str, Any]] | None = None
        self._cache: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}
        self._stock_variant: int | None = None
        self._count_works: bool | None = None
        self._barcode_type_id: int | None = None
        self._sku_maps: dict[int, dict[str, int]] = {}   # barcode -> skuId
        self._seller_id: int | None = None
        self._finance_sample: dict[str, Any] | None = None  # tashxis uchun               # yuridik shaxs ID
        self._invoice_statuses: list[str] | None = None  # akt statuslari

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        """Tezlik chegarasi: so'rovlar orasida pauza."""
        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await super().request(*args, **kwargs)
            finally:
                self._last_call = time.monotonic()

    # ---------------------------------------------------------------
    #  UMUMIY YORDAMCHILAR
    # ---------------------------------------------------------------
    @staticmethod
    def _extract_list(raw: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        """Javob turlicha o'ralgan bo'lishi mumkin — ro'yxatni topadi."""
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
        if not isinstance(raw, dict):
            return []
        for holder in (raw.get("payload"), raw):
            if isinstance(holder, list):
                return [r for r in holder if isinstance(r, dict)]
            if isinstance(holder, dict):
                for k in keys:
                    v = holder.get(k)
                    if isinstance(v, list):
                        return [r for r in v if isinstance(r, dict)]
        return []

    @staticmethod
    def _pick(d: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
        for k in keys:
            if d.get(k) is not None:
                return d[k]
        return default

    @staticmethod
    def _decode_document(raw: Any) -> bytes | None:
        """
        Hujjatni baytlarga o'giradi.

        Uzum ikki xil formatda qaytaradi:
          • JSON + Base64  -> {"payload": {"document": "JVBERi0..."}}
          • xom PDF        -> %PDF-1.4...
        Ikkalasi ham qo'llab-quvvatlanadi.
        """
        # Xom fayl bo'lsa — shundoq qaytaramiz
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw) or None

        if not isinstance(raw, dict):
            return None
        payload = raw.get("payload") or raw
        doc = payload.get("document") if isinstance(payload, dict) else None
        if not doc:
            return None
        for part in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(part, str) or not part.strip():
                continue
            try:
                return base64.b64decode(part, validate=False)
            except Exception:
                continue
        return None

    # ---------------------------------------------------------------
    #  DO'KONLAR
    # ---------------------------------------------------------------
    async def get_shops(self, refresh: bool = False) -> list[dict[str, Any]]:
        if settings.uzum_mock:
            return [{"id": i, "name": n} for i, n in mock_data.SHOPS]
        if self._shops_cache is not None and not refresh:
            return self._shops_cache

        raw = await self.get(ENDPOINTS["shops"], params={"page": 0, "size": 50})
        items = self._extract_list(raw, ("shops", "items"))
        self._shops_cache = [
            {"id": s.get("id"), "name": s.get("name") or "—"}
            for s in items if s.get("id")
        ]
        log.info("Uzum do'konlari: %d ta", len(self._shops_cache))
        return self._shops_cache

    async def shop_names(self) -> dict[int, str]:
        return {s["id"]: s["name"] for s in await self.get_shops()}

    async def _shop_ids(self, given: list[int] | None = None) -> list[int]:
        if given:
            return given
        if settings.uzum_shop_ids:
            return settings.uzum_shop_ids
        return [s["id"] for s in await self.get_shops()]

    # ---------------------------------------------------------------
    #  BUYURTMALAR
    # ---------------------------------------------------------------
    async def get_orders(
        self,
        day: str | None = None,
        shop_ids: list[int] | None = None,
        statuses: list[str] | None = None,
        use_cache: bool = True,
        scheme: str = "FBS",
    ) -> list[dict[str, Any]]:
        """
        Buyurtmalar ro'yxati.

        Har bir status uchun alohida so'rov yuboriladi (Uzum status'siz
        so'rovga faqat CREATED qaytaradi). shopIds massiv bo'lgani uchun
        barcha do'konlar bitta so'rovda so'raladi.
        """
        if settings.uzum_mock:
            names = dict(mock_data.SHOPS)
            return [self._normalize(o, names) for o in mock_data.mock_orders(day)]

        statuses = statuses or ACTIVE_STATUSES
        key = (tuple(shop_ids or []), tuple(statuses), scheme)

        if use_cache:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                return hit[1]

        ids = await self._shop_ids(shop_ids)
        names = await self.shop_names()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        for status in statuses:
            page = 0
            while page < 200:
                params = [("shopIds", str(i)) for i in ids] + [
                    ("scheme", "FBS"),
                    ("status", status),
                    ("page", str(page)),
                    ("size", "50"),
                ]
                try:
                    raw = await self.get(ENDPOINTS["orders"], params=params)
                except ApiError as e:
                    log.warning("Olinmadi (status=%s): %s", status, e)
                    break

                orders = ((raw or {}).get("payload") or {}).get("orders") or []
                if not orders:
                    break
                for o in orders:
                    norm = self._normalize(o, names)
                    if norm["order_id"] not in seen:
                        seen.add(norm["order_id"])
                        out.append(norm)
                if len(orders) < 50:
                    break
                page += 1

        out.sort(key=lambda o: o.get("deliver_until_ts") or 0)
        self._cache[key] = (time.monotonic(), out)
        log.info("Uzumdan %d ta FBS buyurtma (%d do'kon, %s)",
                 len(out), len(ids), ", ".join(statuses))
        return out

    def invalidate_cache(self) -> None:
        self._cache.clear()

    async def get_scheme_counts(self) -> dict[str, dict[str, int]]:
        """
        FBS va DBS bo'yicha buyurtmalar soni.

        MUHIM: Uzum API'sida FBO buyurtmalari YO'Q.
        /v2/fbs/orders da scheme faqat ['FBS', 'DBS'] qiymatlarini oladi.
        FBO — Uzum omborida bajariladi va bu yerda buyurtma sifatida
        berilmaydi. FBO bo'yicha faqat YUK XATLARI mavjud
        (/v1/shop/{shopId}/invoice).

        Sanoq manzili (/v2/fbs/orders/count) esa scheme'ni umuman
        qabul qilmaydi — shuning uchun sanoq ro'yxatdan olinadi.
        """
        if settings.uzum_mock:
            return {
                "FBS": {S_CREATED: 0, S_PACKING: 7, S_PENDING_DELIVERY: 3,
                        S_DELIVERING: 24},
                "DBS": {S_CREATED: 0, S_PACKING: 0, S_PENDING_DELIVERY: 0,
                        S_DELIVERING: 0},
            }

        result: dict[str, dict[str, int]] = {}
        for scheme in ("FBS", "DBS"):
            per: dict[str, int] = {}
            for status in ACTIVE_STATUSES:
                orders = await self.get_orders(
                    statuses=[status], scheme=scheme, use_cache=False
                )
                per[status] = len(orders)
            if any(per.values()) or scheme == "FBS":
                result[scheme] = per
        return result

    async def get_status_counts(self, shop_ids: list[int] | None = None) -> dict[str, int]:
        """Har bir status bo'yicha soni (arzon count manzili orqali)."""
        if settings.uzum_mock:
            return {S_CREATED: 0, S_PACKING: 7, S_PENDING_DELIVERY: 4,
                    S_ACCEPTED_AT_DP: 95, S_COMPLETED: 4006}

        ids = await self._shop_ids(shop_ids)
        out: dict[str, int] = {}
        for status in ALL_STATUSES:
            params = [("shopIds", str(i)) for i in ids] + [("status", status)]
            try:
                raw = await self.get(ENDPOINTS["orders_count"], params=params)
            except ApiError as e:
                log.warning("Sanoq olinmadi (%s): %s", status, e)
                continue
            n = self._extract_count(raw)
            if n is not None:
                out[status] = n
        return out

    @staticmethod
    def _extract_count(raw: Any) -> int | None:
        if isinstance(raw, int):
            return raw
        if not isinstance(raw, dict):
            return None
        for holder in (raw.get("payload"), raw):
            if isinstance(holder, int):
                return holder
            if isinstance(holder, dict):
                for k in ("count", "total", "totalAmount", "totalCount", "amount"):
                    if isinstance(holder.get(k), int):
                        return holder[k]
        return None

    async def confirm_order(self, order_id: str) -> tuple[bool, str]:
        """
        Buyurtmani qabul qilish (tasdiqlash).

        CREATED -> PACKING. Kabinetdagi «Принять» tugmasining o'zi.
        Muddat o'tib ketsa Uzum rad etadi (seller-order-03).
        """
        if settings.uzum_mock:
            return True, ""
        try:
            await self.post(ENDPOINTS["confirm"].format(order_id=order_id))
            return True, ""
        except ApiError as e:
            msg = str(e)
            if "seller-order-03" in msg:
                return False, "qabul muddati o'tib ketgan"
            if "seller-order-02" in msg:
                return False, "holati mos emas (allaqachon qabul qilingan?)"
            if "seller-order-01" in msg:
                return False, "buyurtma topilmadi"
            return False, msg[:80]

    # ---------------------------------------------------------------
    #  MOLIYA
    # ---------------------------------------------------------------
    async def get_finance_orders(
        self, date_from: datetime, date_to: datetime,
        shop_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Moliyaviy ma'lumot: har bir sotilgan tovar bo'yicha daromad,
        komissiya, sof foyda.

        FinanceItemEntity hujjatda bo'sh ko'rsatilgan, shuning uchun
        maydon nomlari moslashuvchan o'qiladi va birinchi so'rovda
        logga yoziladi.
        """
        if settings.uzum_mock:
            return mock_data.mock_finance()

        if shop_ids is None:
            shop_ids = settings.uzum_shop_ids or [s["id"] for s in await self.get_shops()]

        out: list[dict[str, Any]] = []
        seen: set[Any] = set()

        # MUHIM: moliya bo'limi sanani SONIYADA kutadi.
        # Buyurtmalar bo'limi esa millisekundda. Bir API ichida ikki xil
        # o'lchov — tajribada aniqlandi (/moliyatekshir).
        #
        # Status filtri ham qo'shilmaydi: u bilan javob bo'sh keladi.
        # MUHIM: ilgari bu yerda "page < 30" degan qattiq chegara bor edi
        # (ko'pi bilan 3000 ta yozuv). Ko'p buyurtmali oylarda bu yozuvlarni
        # JIM ravishda kesib tashlar edi — hisobot Uzum saytidagidan kam
        # chiqishining sababi shu edi. Endi chegara ancha oshirildi va
        # baribir yetmasa, ANIQ ogohlantirish chiqadi.
        page = 0
        MAX_PAGES = 300
        while page < MAX_PAGES:
            params = [("shopIds", str(i)) for i in shop_ids] + [
                ("dateFrom", str(int(date_from.timestamp()))),
                ("dateTo", str(int(date_to.timestamp()))),
                ("page", str(page)),
                ("size", "100"),
            ]
            try:
                raw = await self.get(ENDPOINTS["finance_orders"], params=params)
            except ApiError as e:
                log.warning("Moliya olinmadi: %s", e)
                break

            rows = self._extract_list(raw, ("orderItems", "items", "content"))
            if not rows:
                break
            if page == 0:
                # Tashxis uchun saqlab qo'yamiz: FinanceItemEntity hujjatda
                # bo'sh, maydon nomlarini faqat haqiqiy javobdan bilamiz.
                self._finance_sample = rows[0]
                log.info("Moliya maydonlari: %s", sorted(rows[0].keys()))

            for r in rows:
                key = r.get("id") or (r.get("orderId"), r.get("skuTitle"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(self._normalize_finance(r))

            if len(rows) < 100:
                break
            page += 1
        else:
            log.warning(
                "Moliya: %d-sahifa chegarasiga yetildi (%d ta yozuv) — "
                "MA'LUMOT TO'LIQ BO'LMASLIGI MUMKIN! MAX_PAGES ni oshiring.",
                MAX_PAGES, len(out),
            )

        log.info("Moliya: %d ta yozuv", len(out))
        return out

    @classmethod
    def _normalize_finance(cls, r: dict[str, Any]) -> dict[str, Any]:
        """
        Moliyaviy yozuvni yagona formatga keltiradi.

        Haqiqiy maydonlar (/moliyamaydon bilan aniqlangan):
            sellPrice            108 990   mijoz to'lagan
            commission          - 19 073   Uzum komissiyasi
            logisticDeliveryFee -  8 500   yetkazish
            ------------------------------
            sellerProfit          81 417   Uzum sizga to'laydi
            purchasePrice         80 000   tovarning tannarxi
            ------------------------------
            sof foyda              1 417

        MUHIM: `withdrawnProfit` — pul HAQIQATAN chiqarilgan summa.
        U yetkazilgunga qadar 0 bo'ladi, shuning uchun undan foydalanib
        bo'lmaydi. To'g'ri maydon — `sellerProfit`.

        DIQQAT: so'rovda sana SONIYADA, javobda MILLISEKUNDDA keladi.
        """
        qty = int(cls._pick(r, ("amount", "quantity", "qty"), 1) or 1)
        price = int(cls._pick(r, ("sellPrice", "sellerPrice", "price"), 0) or 0)

        # sellerProfit — Uzum to'laydigan summa. 0 bo'lsa,
        # narxdan komissiya va logistikani ayirib hisoblaymiz.
        payout = int(r.get("sellerProfit") or 0)
        if not payout:
            payout = int(r.get("withdrawnProfit") or 0)
        if not payout and price:
            payout = price - int(r.get("commission") or 0) \
                     - int(r.get("logisticDeliveryFee") or 0)

        # purchasePrice null bo'lishi mumkin — tannarx kiritilmagan.
        # 0 deb hisoblamaymiz, chunki u ROI ni cheksiz qilib yuboradi.
        raw_cost = cls._pick(r, ("purchasePrice", "costPrice", "cost"))
        cost = int(raw_cost) if raw_cost else 0
        has_cost = bool(raw_cost)
        returns = int(r.get("amountReturns") or 0)
        cancelled = bool(r.get("cancelled"))

        return {
            "sku": str(cls._pick(r, ("skuTitle", "sku", "barcode"), "—")),
            "name": str(cls._pick(r, ("productTitle", "title", "name"), "—")),
            "qty": qty,
            "revenue": price * qty,
            "payout": payout,
            # purchasePrice bitta dona uchun — soniga ko'paytiramiz
            "cost": cost * qty,
            "has_cost": has_cost,
            "commission": int(r.get("commission") or 0),
            "logistics": int(r.get("logisticDeliveryFee") or 0),
            "returns": returns,
            "cancelled": cancelled,
            "status": r.get("status") or "",
        }

    async def get_expenses(
        self, date_from: datetime, date_to: datetime,
        shop_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Xarajatlar va qaytarilgan mablag'lar."""
        if settings.uzum_mock:
            return mock_data.mock_expenses()

        if shop_ids is None:
            shop_ids = settings.uzum_shop_ids or [s["id"] for s in await self.get_shops()]

        # Moliya bo'limi sanani soniyada kutadi
        base = [
            ("dateFrom", str(int(date_from.timestamp()))),
            ("dateTo", str(int(date_to.timestamp()))),
            ("page", "0"), ("size", "200"),
        ]
        variants = [
            [("shopIds", str(i)) for i in shop_ids] + base,
            [("shopId", str(shop_ids[0]))] + base if shop_ids else [],
            base,
        ]

        rows = []
        for params in variants:
            if not params:
                continue
            try:
                raw = await self.get(ENDPOINTS["expenses"], params=params)
            except ApiError as e:
                log.warning("Xarajatlar olinmadi: %s", e)
                continue
            rows = self._extract_list(raw, ("payments", "items", "content"))
            if rows:
                break
        if rows:
            log.info("Xarajat maydonlari: %s", list(rows[0].keys()))

        return [
            {
                "name": r.get("name") or r.get("source") or "—",
                "source": r.get("source") or "",
                "amount": int(r.get("paymentPrice") or 0),
                # OUTCOME — chiqim, INCOME — qaytgan pul
                "outcome": str(r.get("type", "")).upper() == "OUTCOME",
                "status": r.get("status") or "",
            }
            for r in rows
        ]

    async def get_product_stats(
        self, shop_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """
        Har bir SKU bo'yicha to'liq statistika — /v1/product/shop/{id} dan.

        Bu yerda FBO qoldig'i uchun ALOHIDA maydon yo'q (Uzum buni
        bermaydi). Lekin ikkita haqiqiy maydon bor:
            quantityActive — umumiy faol qoldiq (barcha joylarda)
            quantityFbs    — faqat FBS omboringizda turgan qism

        Demak farqi — Uzumning o'z omborida (FBO) turgan qism:
            FBO qoldig'i = quantityActive − quantityFbs

        "Top sotilgan" uchun ham haqiqiy maydon bor: avgdsales —
        SKU'ning kunlik o'rtacha sotuvi. 7 kunlik taxminiy sotuvni
        shundan chiqaramiz: avgdsales × 7.
        """
        if settings.uzum_mock:
            demo = [
                ("BT-1001", "Yumshoq ayiqcha", 14, 9, False, "", ""),
                ("BT-1002", "Konstruktor", 3, 22, False, "", ""),
                ("BT-1003", "Mashina", 0, 5, False, "", ""),
                ("BT-1009", "Sorter (rasm buzuq)", 8, 0, True,
                 "Rasm sifati talabga javob bermaydi",
                 "Mahsulot rasmini almashtiring va qayta yuboring"),
            ]
            return [
                {"sku": s, "name": n, "fbo_qty": q, "sold_7d": sold,
                 "blocked": b, "block_reason": r, "block_message": m}
                for (s, n, q, sold, b, r, m) in demo
            ]

        if shop_ids is None:
            shop_ids = settings.uzum_shop_ids or [s["id"] for s in await self.get_shops()]

        out: list[dict[str, Any]] = []
        for shop_id in shop_ids:
            page = 0
            while page < 200:
                try:
                    raw = await self.get(
                        ENDPOINTS["products"].format(shop_id=shop_id),
                        params={"page": page, "size": 100},
                    )
                except ApiError as e:
                    log.warning("Mahsulot statistikasi olinmadi (%s): %s", shop_id, e)
                    break

                products = self._extract_list(raw, ("productList", "products", "items"))
                if not products:
                    break

                for prod in products:
                    for sku in prod.get("skuList") or []:
                        active = int(sku.get("quantityActive") or 0)
                        fbs = int(sku.get("quantityFbs") or 0)
                        fbo = max(active - fbs, 0)
                        avg = float(sku.get("avgdsales") or 0)

                        # Bloklangan mahsulot — Uzum sotuvni to'xtatgan.
                        # Sabab ko'pincha: rasm buzilishi, taqiqlangan
                        # mahsulot, hujjat yetishmasligi.
                        block = sku.get("skuBlockReason") or {}
                        out.append({
                            "sku": sku.get("skuTitle") or sku.get("article") or "—",
                            "name": sku.get("productTitle") or prod.get("title") or "—",
                            "fbo_qty": fbo,
                            "sold_7d": round(avg * 7),
                            "blocked": bool(sku.get("blocked")),
                            "block_reason": (
                                block.get("title")
                                or sku.get("blockingReason")
                                or ""
                            ),
                            "block_message": block.get("message") or "",
                        })

                if len(products) < 100:
                    break
                page += 1

        return out

    # ---------------------------------------------------------------
    #  FBO YUK XATLARI
    #
    #  Bular FBS aktidan BUTUNLAY BOSHQA narsa: bu — sizning tovaringiz
    #  Uzum omboriga (FBO) jo'natilganda ochiladigan yuk xati. FBS
    #  aktida "buyurtma"lar bo'ladi, bu yerda esa "mahsulot" (qancha
    #  dona jo'natilgan, qanchasi qabul qilingan).
    # ---------------------------------------------------------------
    async def get_fbo_invoices(
        self, shop_ids: list[int] | None = None, size: int = 50
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        FBO yuk xatlari — BARCHA do'konlar bo'yicha, BITTA so'rovda.

        /v1/invoice manzili do'kon bo'yicha ajratilmagan — barcha
        do'konlarning aktlarini birga qaytaradi, va har bir akt ichida
        mahsulotlar ro'yxati (productForInvoiceDto) ham keladi —
        alohida so'rov shart emas.

        "deliveryCertificate" — bu Uzumning O'ZI bergan hujjat
        (yetkazishni tasdiqlovchi), botning o'zi yasagan narsa emas.

        Qaytaradi: (yuk_xatlari, tashxis — sahifalar bo'yicha).
        """
        if settings.uzum_mock:
            return mock_data.mock_fbo_invoices(), []

        names = await self.shop_names()
        out: list[dict[str, Any]] = []
        diag: list[str] = []

        page = 0
        while page < 200:
            try:
                raw = await self.get(
                    ENDPOINTS["invoices_all"],
                    params={"page": page, "size": size},
                )
            except ApiError as e:
                diag.append(f"{page}-sahifa: xato — {str(e)[:90]}")
                log.warning("FBO yuk xatlari olinmadi: %s", e)
                break

            rows = raw if isinstance(raw, list) else self._extract_list(
                raw, ("content", "items", "invoices")
            )
            if not rows:
                break

            for r in rows:
                status = r.get("invoiceStatus") or {}
                shop_id = r.get("shopId")
                # Nomer sifatida TASHQI raqamni afzal ko'ramiz — bu
                # jismoniy hujjatdagi bilan mos keladigan raqam.
                number = (
                    r.get("externalNumber")
                    or r.get("invoiceNumber")
                    or r.get("id")
                )
                products = [
                    {
                        "sku": p.get("skuTitle") or "—",
                        "name": p.get("productTitle") or "—",
                        "to_stock": int(p.get("quantityToStock") or 0),
                        "accepted": int(p.get("quantityAccepted") or 0),
                        "purchase_price": int(p.get("purchasePrice") or 0),
                    }
                    for p in (r.get("productForInvoiceDto") or [])
                ]
                out.append({
                    "id": r.get("id"),
                    "number": number,
                    "internal_number": r.get("invoiceNumber"),
                    "shop_id": shop_id,
                    "shop_name": r.get("shopTitle") or names.get(shop_id, ""),
                    "status_value": status.get("value") or r.get("status") or "",
                    "status_label": status.get("text") or r.get("status") or "—",
                    "total_price": int(r.get("fullPrice") or 0),
                    "total_to_stock": int(r.get("totalToStock") or 0),
                    "total_accepted": int(r.get("totalAccepted") or 0),
                    "date_created": r.get("dateCreated"),
                    "date_accepted": r.get("dateAccepted"),
                    # Uzumning o'z hujjati — yetkazishni tasdiqlovchi.
                    # Bo'sh bo'lsa, Uzum hali bu akt uchun hujjat
                    # tayyorlamagan (odatda qabul qilingandan keyin
                    # paydo bo'ladi).
                    "delivery_certificate": r.get("deliveryCertificate") or "",
                    "products": products,
                })

            diag.append(f"{page}-sahifa: {len(rows)} ta akt")
            if len(rows) < size:
                break
            page += 1

        # Faqat shop_ids berilgan bo'lsa filtrlaymiz (odatda berilmaydi)
        if shop_ids:
            out = [i for i in out if i["shop_id"] in shop_ids]

        out.sort(key=lambda x: x.get("date_created") or "", reverse=True)
        return out, diag

    async def get_fbo_invoice_products(
        self, shop_id: int, invoice_id: Any
    ) -> list[dict[str, Any]]:
        """
        Bitta FBO yuk xatidagi mahsulotlar — soni bilan.

        DIQQAT: get_fbo_invoices() endi mahsulotlarni O'ZIDA olib
        keladi (invoice["products"]) — bu metod faqat zaxira sifatida
        qoldirilgan, agar kelajakda alohida so'rov kerak bo'lsa.
        """
        if settings.uzum_mock:
            return mock_data.mock_fbo_invoice_products()

        try:
            raw = await self.get(
                ENDPOINTS["fbo_invoice_products"].format(shop_id=shop_id),
                params={"invoiceId": invoice_id},
            )
        except ApiError as e:
            log.warning("FBO yuk xati mahsulotlari olinmadi: %s", e)
            return []

        rows = raw if isinstance(raw, list) else self._extract_list(
            raw, ("content", "items")
        )
        return [
            {
                "sku": r.get("skuTitle") or "—",
                "name": r.get("productTitle") or "—",
                "to_stock": int(r.get("quantityToStock") or 0),
                "accepted": int(r.get("quantityAccepted") or 0),
                "purchase_price": int(r.get("purchasePrice") or 0),
            }
            for r in rows
        ]

    async def get_binary_url(self, url: str) -> bytes | None:
        """
        Ixtiyoriy URL'dan xom baytlarni oladi — masalan
        deliveryCertificate havolasi PDF fayl bo'lsa.

        Avval Uzum tokeni bilan sinaydi (agar bu Uzum domenida
        bo'lsa), keyin tokensiz — havola boshqa (masalan bulutli
        saqlash) xizmatga tegishli bo'lishi mumkin.
        """
        import httpx

        for headers in (self.headers, {}):
            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    r = await client.get(url, headers=headers)
                if r.status_code == 200 and r.content:
                    return r.content
            except Exception as e:
                log.warning("Binary URL olinmadi (%s): %s", url[:60], e)
        return None

    async def check_token(self) -> bool:
        if settings.uzum_mock:
            return True
        try:
            await self.get(ENDPOINTS["shops"], params={"page": 0, "size": 1})
            return True
        except ApiError as e:
            log.warning("Uzum kaliti ishlamadi: %s", e)
            return False

    # ---------------------------------------------------------------
    #  NORMALIZATSIYA
    # ---------------------------------------------------------------
    @classmethod
    def _normalize(
        cls, o: dict[str, Any], shop_names: dict[int, str] | None = None
    ) -> dict[str, Any]:
        shop_names = shop_names or {}
        items = []
        for it in o.get("orderItems") or []:
            items.append({
                "sku": str(it.get("skuTitle") or it.get("barcode") or "—"),
                # Shtrix-kod — mahsulotlar ro'yxati bilan bog'lash uchun
                # yagona ishonchli kalit. skuTitle ikki manzilda turlicha
                # yoziladi ("SEENSOR-GIPS-АЛЫЙ" va "АЛЫЙ"), barcode esa bir xil.
                "barcode": str(it.get("barcode") or ""),
                "name": it.get("title") or it.get("productTitle") or "—",
                "qty": int(it.get("amount") or 1),
                "price": int(it.get("price") or it.get("sellerPrice") or 0),
                "photo": cls._first_photo(it),
            })

        shop_id = o.get("shopId")
        stock = o.get("stock") or {}
        delivery = o.get("deliveryInfo") or {}

        return {
            "order_id": str(o.get("id") or "?"),
            "public_id": o.get("publicId") or str(o.get("id") or "?"),
            "status": _derive_status(o),
            "raw_status": o.get("status") or "",
            "created_at": _ms_to_dt(o.get("dateCreated")),
            "accept_until": _ms_to_dt(o.get("acceptUntil")),
            "deliver_until": _ms_to_dt(o.get("deliverUntil")),
            "deliver_until_ts": o.get("deliverUntil") or 0,
            "invoice_number": o.get("invoiceNumber"),
            "shop_id": shop_id,
            "shop_name": shop_names.get(shop_id, f"Do'kon {shop_id}"),
            "items": items,
            "total": int(o.get("price") or 0),
            "pickup_point": stock.get("title") or o.get("place") or "—",
            "pickup_address": stock.get("address") or "",
            "customer": delivery.get("customerFullname") or "—",
            "scheme": o.get("scheme") or "FBS",
        }

    @staticmethod
    def _first_photo(item: dict[str, Any]) -> str | None:
        """
        Rasm ikki xil tuzilishda keladi:
          buyurtmalarda — photo.photo["240"].high
          aktlarda      — photo.high (tekis)
        """
        holder = item.get("photo") or item.get("productImage") or {}
        if not isinstance(holder, dict):
            return None
        if isinstance(holder.get("high"), str):
            return holder["high"]
        photos = holder.get("photo") or {}
        if isinstance(photos, dict):
            if isinstance(photos.get("high"), str):
                return photos["high"]
            # Eng katta o'lchamdan boshlab qidiramiz — sifat uchun.
            # Ilgari "240" birinchi tekshirilardi, shuning uchun rasmlar
            # xira chiqardi.
            for size in ("800", "720", "540", "480", "240", "120", "80", "60"):
                entry = photos.get(size)
                if isinstance(entry, dict) and entry.get("high"):
                    return entry["high"]
        return None

    # ---------------------------------------------------------------
    #  YORLIQ
    # ---------------------------------------------------------------
    async def get_label(self, order_id: str) -> tuple[bytes | None, str | None]:
        """
        Buyurtma yorlig'i. Javob: payload.document — Base64 PDF'lar ro'yxati.
        `size` majburiy: LARGE 58x40mm, BIG 43x25mm.
        Yorliqda buyurtma kodi ham, mahsulot QR kodi ham bor.
        """
        if settings.uzum_mock:
            return None, None
        raw = await self.get(
            ENDPOINTS["labels"].format(order_id=order_id),
            params={"size": settings.label_size},
        )
        return self._decode_document(raw), None

    # ---------------------------------------------------------------
    #  OMBOR QOLDIG'I
    # ---------------------------------------------------------------
    async def get_stocks(self, shop_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """FBS ombordagi SKU qoldiqlari (RestSellerSkuFbsAmountDto)."""
        if settings.uzum_mock:
            return [
                {"sku": s, "name": n, "qty": q, "shop_id": 0, "shop_name": "Test"}
                for s, n, q in [("BT-1001", "Yumshoq ayiqcha", 3),
                                ("BT-1002", "Konstruktor", 12),
                                ("BT-1003", "Mashina", 0)]
            ]

        names = await self.shop_names()
        out: list[dict[str, Any]] = []
        page = 0
        while page < 200:
            try:
                raw = await self.get(ENDPOINTS["stocks"],
                                     params={"page": page, "size": 100})
            except ApiError as e:
                log.warning("Qoldiq olinmadi: %s", e)
                break
            rows = self._extract_list(raw, ("skuAmountList", "skus", "items"))
            if not rows:
                break
            for r in rows:
                qty = self._pick(r, ("amount", "quantity", "qty"))
                if qty is None:
                    continue
                shop_id = self._pick(r, ("shopId", "shop_id"), 0) or 0
                out.append({
                    "sku": str(self._pick(r, ("skuTitle", "barcode", "skuId"), "—")),
                    "name": str(self._pick(r, ("productTitle", "title", "skuTitle"), "—")),
                    "qty": int(qty),
                    "shop_id": shop_id,
                    "shop_name": names.get(shop_id, ""),
                })
            if len(rows) < 100:
                break
            page += 1

        out.sort(key=lambda x: x["qty"])
        log.info("Uzum FBS qoldig'i: %d ta SKU", len(out))
        return out

    # ---------------------------------------------------------------
    #  AKTLAR
    # ---------------------------------------------------------------
    async def get_invoices(
        self, limit: int = 20, all_statuses: bool = False
    ) -> list[dict[str, Any]]:
        """
        Aktlar ro'yxati — eng yangisi birinchi.

        Sukut bo'yicha faqat ISH QOLGANLARI: «Создана» va qabul
        jarayonidagilar. «Принята» tugagan — xodimga vazifa yo'q.
        """
        if settings.uzum_mock:
            demo = [
                {"id": 120001196374, "number": "120001196374", "status": INV_CREATED,
                 "status_label": INVOICE_LABELS[INV_CREATED],
                 "shop_id": 79873, "shop_name": "SENSOR o'yinchoqlar",
                 "orders": 7, "orders_accepted": 0,
                 "total": 1_400_000, "total_accepted": 0,
                 "drop_off": "Eshonguzar, Hamza ko'chasi 10",
                 "time_slot": "24-Jul 11:00–16:00", "created": None, "created_ts": 2},
                {"id": 120001193300, "number": "120001193300", "status": INV_ACCEPTED,
                 "status_label": INVOICE_LABELS[INV_ACCEPTED],
                 "shop_id": 79873, "shop_name": "SENSOR o'yinchoqlar",
                 "orders": 9, "orders_accepted": 8,
                 "total": 1_800_000, "total_accepted": 1_600_000,
                 "drop_off": "Eshonguzar", "time_slot": "", "created": None,
                 "created_ts": 1},
            ]
            if all_statuses:
                return demo
            return [d for d in demo if d["status"] in ACTIVE_INVOICE_STATUSES]

        names = await self.shop_names()
        seen: set[Any] = set()
        out: list[dict[str, Any]] = []

        statuses = INVOICE_STATUSES if all_statuses else ACTIVE_INVOICE_STATUSES
        for st in statuses:
            try:
                raw = await self.get(
                    ENDPOINTS["invoices"],
                    params=[("statuses", st), ("page", "0"), ("size", "20")],
                )
            except ApiError as e:
                log.warning("Aktlar olinmadi (%s): %s", st, e)
                continue
            for inv in self._extract_list(raw, ("invoices", "items", "content")):
                inv_id = inv.get("id")
                if inv_id is None or inv_id in seen:
                    continue
                seen.add(inv_id)
                out.append(self._normalize_invoice(inv, names))

        out.sort(key=lambda x: x.get("created_ts") or 0, reverse=True)
        log.info("Aktlar: %d ta", len(out))
        return out[:limit]

    @classmethod
    def _normalize_invoice(
        cls, inv: dict[str, Any], names: dict[int, str]
    ) -> dict[str, Any]:
        """FbsInvoiceDto -> loyihaning formati."""
        status_obj = inv.get("status") or {}
        if isinstance(status_obj, dict):
            code = status_obj.get("value") or ""
            text = status_obj.get("text") or ""
        else:
            code, text = str(status_obj), ""

        stock = inv.get("stock") or {}
        dop = inv.get("dropOffPoint") or {}
        ts = inv.get("timeSlot") or {}
        a, b = _parse_dt(ts.get("timeFrom")), _parse_dt(ts.get("timeTo"))

        return {
            "id": inv.get("id"),
            "number": str(inv.get("number") or inv.get("id") or "—"),
            "status": code,
            "status_label": INVOICE_LABELS.get(code, text or code),
            "shop_id": inv.get("shopId"),
            "shop_name": names.get(inv.get("shopId"), ""),
            "orders": int(inv.get("numberOrders") or 0),
            "orders_accepted": int(inv.get("numberAcceptedOrders") or 0),
            "total": int(inv.get("fullPrice") or 0),
            "total_accepted": int(inv.get("acceptedPrice") or 0),
            "drop_off": dop.get("address") or stock.get("title") or "—",
            "time_slot": f"{a:%d-%b %H:%M}–{b:%H:%M}" if a and b else "",
            "created": _parse_dt(inv.get("dateCreated")),
            "created_ts": _sort_key(inv.get("dateCreated")),
        }

    async def get_invoice_items(self, invoice_id: Any) -> list[dict[str, Any]]:
        """Akt ichidagi mahsulotlar, SKU bo'yicha jamlangan."""
        if settings.uzum_mock:
            return [
                {"sku": "SEENSOR-PIMADKI-AMETIC", "name": "Plastilin to'plami",
                 "qty": 96, "accepted": 64, "not_accepted": 32, "price": 32_000,
                 "photo": None},
            ]

        raw = await self.get(ENDPOINTS["invoice_orders"].format(invoice_id=invoice_id))
        orders = self._extract_list(raw, ("orders", "items", "content"))

        agg: dict[str, dict[str, Any]] = {}
        for order in orders:
            for it in order.get("items") or []:
                sku = str(it.get("skuTitle") or it.get("barcode") or "—")
                row = agg.setdefault(sku, {
                    "sku": sku,
                    "name": it.get("title") or it.get("skuTitle") or "—",
                    "qty": 0, "accepted": 0, "not_accepted": 0,
                    "price": int(it.get("price") or 0),
                    "photo": None,
                })
                qty = int(it.get("amount") or 1)
                row["qty"] += qty
                if it.get("status") == "ACCEPTED":
                    row["accepted"] += qty
                elif it.get("status") == "NOT_ACCEPTED":
                    row["not_accepted"] += qty
                if not row["photo"]:
                    row["photo"] = self._first_photo(it)

        out = sorted(agg.values(), key=lambda r: -r["qty"])
        log.info("Akt %s: %d xil mahsulot", invoice_id, len(out))
        return out

    async def get_invoice_order_ids(self, invoice_id: Any) -> list[str]:
        """Aktdagi buyurtma raqamlari — yorliqlarni olish uchun."""
        if settings.uzum_mock:
            return ["118464581", "118439721"]
        raw = await self.get(ENDPOINTS["invoice_orders"].format(invoice_id=invoice_id))
        rows = self._extract_list(raw, ("orders", "items", "content"))
        out = []
        for r in rows:
            oid = r.get("orderId") or r.get("id")
            if oid:
                out.append(str(oid))
        return out

    async def get_invoice_pdf(self, invoice_id: Any) -> tuple[bytes | None, str | None]:
        if settings.uzum_mock:
            return None, None
        raw = await self.get(ENDPOINTS["invoice_print"].format(invoice_id=invoice_id))
        return self._decode_document(raw), None

    # ---------------------------------------------------------------
    #  POSTAVKA OCHISH
    # ---------------------------------------------------------------
    @staticmethod
    def _order_ids(orders: list[dict[str, Any]]) -> list[str]:
        """
        Parametr `customerOrderIds` deb atalsa ham, SOTUVCHI buyurtma
        raqamini kutadi (Номер заказа). Tajribada aniqlangan.
        """
        return [str(o["order_id"]) for o in orders]

    async def get_seller_id(self, shop_ids: list[int] | None = None) -> int | None:
        """
        Sotuvchining (yuridik shaxsning) ID'si.

        MUHIM: sellerId va shopId — turli narsalar. Postavka yaratishda
        aynan sellerId kerak, do'kon raqami emas.

        Uni /v1/shops bermaydi. Yagona joy — /v1/finance/expenses javobidagi
        SellerPaymentDto, unda sellerId ham, shopId ham alohida turadi.
        """
        if self._seller_id is not None:
            return self._seller_id
        if settings.uzum_mock:
            return 12345

        if shop_ids is None:
            shop_ids = settings.uzum_shop_ids or [s["id"] for s in await self.get_shops()]

        params = [("shopIds", str(i)) for i in shop_ids] + [
            ("page", "0"), ("size", "5")
        ]
        try:
            raw = await self.get(ENDPOINTS["expenses"], params=params)
        except ApiError as e:
            log.warning("sellerId olinmadi: %s", e)
            return None

        rows = self._extract_list(raw, ("payments", "items", "content"))
        for r in rows:
            sid = r.get("sellerId")
            if sid:
                self._seller_id = int(sid)
                log.info("sellerId topildi: %s (shopId dan farqli)", self._seller_id)
                return self._seller_id

        log.warning("sellerId topilmadi — xarajatlar ro'yxati bo'sh")
        return None

    async def probe_time_slots(
        self, dop_uuid: str, orders: list[dict[str, Any]]
    ) -> str:
        """TASHXIS: vaqt oynalari javobini XOM holida qaytaradi."""
        import httpx

        url = f"{self.base_url}{ENDPOINTS['time_slots']}"
        params = [("dopId", dop_uuid)] + [
            ("sellerOrderIds", str(o["order_id"])) for o in orders
        ]
        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    r = await client.get(
                        url,
                        headers={**self.headers, "Accept-Language": "uz"},
                        params=params,
                    )
                return f"HTTP {r.status_code}\n{r.text[:900]}"
            except Exception as e:
                return f"xato: {e}"
            finally:
                self._last_call = time.monotonic()

    async def get_drop_off_points(
        self, orders: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """
        Mos qabul punktlari. Qaytaradi: (punktlar, sig'gan buyurtmalar, izoh).

        Punkt qaytariladi faqat agar sig'imi yetsa va muddatdan oldin
        bo'sh vaqt oynasi bo'lsa. Shuning uchun ko'p buyurtma birdan
        so'ralsa bo'sh kelishi mumkin — kichikroq to'plamni sinaymiz.
        """
        if settings.uzum_mock:
            return [
                {"uuid": "dop-1", "address": "Toshkent sh., Sergeli tumani, Nilufar 77/7"},
                {"uuid": "dop-2", "address": "Toshkent sh., Chilonzor tumani, Katta halqa 1"},
                {"uuid": "dop-3", "address": "Toshkent sh., Uchtepa tumani, Shirin 27"},
            ], orders, ""

        ordered = sorted(orders, key=lambda o: o.get("deliver_until_ts") or 0)
        sizes = [n for n in sorted({len(ordered), 20, 10, 5, 3, 1}, reverse=True)
                 if 0 < n <= len(ordered)]

        notes = []
        for n in sizes:
            batch = ordered[:n]
            try:
                raw = await self.get(
                    ENDPOINTS["drop_off_points"],
                    params=[("customerOrderIds", i) for i in self._order_ids(batch)],
                )
            except ApiError as e:
                notes.append(f"{n} ta: {str(e)[:90]}")
                continue

            points = self._extract_list(raw, ("dropOffPoints", "points", "items"))
            if points:
                clean = [
                    {
                        "uuid": p.get("uuid"),
                        "address": p.get("address") or "—",
                        "type": p.get("type") or "",
                        "large": bool(p.get("dimensionalGroupIsLarge")),
                    }
                    for p in points if p.get("uuid")
                ]
                note = "" if n == len(ordered) else (
                    f"⚠️ {len(ordered)} tadan <b>{n}</b> tasi sig'di"
                )
                log.info("Qabul punkti: %d ta buyurtma -> %d ta punkt", n, len(clean))
                return clean, batch, note
            notes.append(f"{n} ta: mos punkt yo'q")

        return [], [], "\n".join(f"• {x}" for x in notes)

    async def get_time_slots(
        self, dop_uuid: str, orders: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Tanlangan punkt uchun bo'sh vaqt oynalari."""
        if settings.uzum_mock:
            now = datetime.now(TZ)
            return [
                {"uuid": f"slot-{i}",
                 "from": int((now + timedelta(days=1, hours=i * 2)).timestamp() * 1000),
                 "to": int((now + timedelta(days=1, hours=i * 2 + 2)).timestamp() * 1000),
                 "capacity": 63,
                 "remaining": [53, 4, 20][i - 1],
                 "last_day": True,
                 "raw": {}}
                for i in range(1, 4)
            ]

        params = [("dopId", dop_uuid)] + [
            ("sellerOrderIds", i) for i in self._order_ids(orders)
        ]
        raw = await self.get(ENDPOINTS["time_slots"], params=params)
        slots = self._extract_list(raw, ("timeSlots", "slots", "items"))
        if slots:
            log.info("Vaqt oynasi maydonlari: %s", list(slots[0].keys()))

        return [
            {
                "uuid": str(self._pick(sl, ("uuid", "id", "timeSlotUuid", "slotUuid"))
                            or "") or None,
                "from": sl.get("timeFrom"),
                "to": sl.get("timeTo"),
                "raw": sl,
            }
            for sl in slots
        ]

    async def create_invoice(
        self,
        orders: list[dict[str, Any]],
        dop_uuid: str,
        slot_uuid: str,
        seller_id: int,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Postavka ochadi.

        `orderIds` va `sellerId` qaysi qiymatni kutishi hujjatda aniq emas,
        shuning uchun bir necha variantni ketma-ket sinaymiz. Xato bo'lsa
        hech nima yaratilmaydi — bu xavfsiz. Birinchi muvaffaqiyatda
        to'xtaymiz, ikkita postavka ochilib qolmasin.
        """
        if settings.uzum_mock:
            return {"ok": True, "id": 999, "number": 120009999999,
                    "used": "mock", "mock": True}

        seller_ids = [int(o["order_id"]) for o in orders]
        cust_ids = []
        for o in orders:
            head = str(o.get("public_id") or "").split("-")[0]
            if head.isdigit():
                cust_ids.append(int(head))

        variants: list[tuple[str, list[int]]] = [("Номер заказа", seller_ids)]
        if cust_ids and cust_ids != seller_ids:
            variants.append(("ID заказа (publicId prefiksi)", cust_ids))

        attempts: list[str] = []
        for i, (label, ids) in enumerate(variants):
            body = {
                "orderIds": ids,
                "dropOffPointUuid": dop_uuid,
                "timeSlotUuid": slot_uuid,
                "sellerId": int(seller_id),
            }
            if idempotency_key:
                body["idempotencyKey"] = f"{idempotency_key}-{i}"

            log.info("Postavka urinishi (%s): %d ta buyurtma", label, len(ids))
            try:
                raw = await self.post(ENDPOINTS["invoices"], json=body)
            except ApiError as e:
                attempts.append(f"{label}: {str(e)[:170]}")
                continue

            payload = (raw or {}).get("payload") or {}
            if payload.get("id") or payload.get("number"):
                log.info("Postavka ochildi (%s): № %s", label, payload.get("number"))
                return {
                    "ok": True,
                    "id": payload.get("id"),
                    "number": payload.get("number"),
                    "used": label,
                    "raw": raw,
                }
            attempts.append(f"{label}: javob bo'sh")

        raise ApiError(self.service_name,
                       "\n".join(f"• {a}" for a in attempts) or "noma'lum xato")

    async def probe_drop_off_points(
        self, orders: list[dict[str, Any]]
    ) -> list[tuple[str, str]]:
        """TASHXIS: turli variantlarni sinab, haqiqiy javobni qaytaradi."""
        import httpx

        url = f"{self.base_url}{ENDPOINTS['drop_off_points']}"
        seller = [str(o["order_id"]) for o in orders]
        public = [str(o.get("public_id") or "") for o in orders]
        cust = [p.split("-")[0] for p in public if p.split("-")[0].isdigit()]

        variants = [
            ("sotuvchi ID (takror)", [("customerOrderIds", i) for i in seller]),
            ("sotuvchi ID (vergul)", [("customerOrderIds", ",".join(seller))]),
            ("publicId prefiksi", [("customerOrderIds", i) for i in cust]),
            ("bitta, sotuvchi ID", [("customerOrderIds", seller[0])] if seller else []),
        ]

        out = []
        for label, params in variants:
            if not params:
                continue
            async with self._lock:
                wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    async with httpx.AsyncClient(timeout=25.0) as client:
                        r = await client.get(
                            url,
                            headers={**self.headers, "Accept-Language": "uz"},
                            params=params,
                        )
                    out.append((label, f"HTTP {r.status_code} · {r.text[:250]}"))
                except Exception as e:
                    out.append((label, f"xato: {str(e)[:120]}"))
                finally:
                    self._last_call = time.monotonic()
        return out

    # ---------------------------------------------------------------
    #  MAHSULOT QR (yorliqda bo'lmasa, alohida)
    # ---------------------------------------------------------------
    async def get_barcode_type_id(self) -> int | None:
        if self._barcode_type_id is not None:
            return self._barcode_type_id
        if settings.uzum_mock:
            return 1
        try:
            raw = await self.get(ENDPOINTS["barcode_types"])
        except ApiError as e:
            log.warning("Yorliq turlari olinmadi: %s", e)
            return None
        types = self._extract_list(raw, ("barcodeLabelTypes", "types", "items"))
        if not types:
            return None
        chosen = types[0]
        if settings.barcode_type:
            for t in types:
                if settings.barcode_type.lower() in str(t.get("title", "")).lower():
                    chosen = t
                    break
        self._barcode_type_id = chosen.get("id")
        log.info("QR yorliq turi: %s (id=%s)", chosen.get("title"), self._barcode_type_id)
        return self._barcode_type_id

    async def get_sku_map(self, shop_id: int) -> dict[str, int]:
        """barcode / skuTitle -> skuId moslik jadvali."""
        if shop_id in self._sku_maps:
            return self._sku_maps[shop_id]
        if settings.uzum_mock:
            return {}

        mapping: dict[str, int] = {}
        page = 0
        while page < 200:
            try:
                raw = await self.get(ENDPOINTS["products"].format(shop_id=shop_id),
                                     params={"page": page, "size": 100})
            except ApiError as e:
                log.warning("Mahsulotlar olinmadi (%s): %s", shop_id, e)
                break
            products = self._extract_list(raw, ("productList", "products", "items"))
            if not products:
                break
            for prod in products:
                for sku in prod.get("skuList") or []:
                    sku_id = sku.get("skuId")
                    if not sku_id:
                        continue
                    # Har xil nom bilan izlash mumkin bo'lsin.
                    # Buyurtmada artikul ("SEENSOR-GIPS-АЛЫЙ") keladi,
                    # bu yerda esa variant nomi ("АЛЫЙ") — mos kelmaydi.
                    # Shtrix-kod ikkalasida bir xil, shuning uchun u asosiy.
                    for key in (
                        sku.get("barcode"),
                        sku.get("article"),
                        sku.get("sellerItemCode"),
                        sku.get("skuFullTitle"),
                        sku.get("skuTitle"),
                    ):
                        if key:
                            mapping[str(key)] = sku_id
            if len(products) < 100:
                break
            page += 1

        self._sku_maps[shop_id] = mapping
        log.info("Do'kon %s: %d ta SKU moslik", shop_id, len(mapping))
        return mapping

    async def get_product_qr(
        self, shop_id: int, sku_keys: list[tuple[str, int]]
    ) -> tuple[bytes | None, str]:
        """
        Mahsulot QR / shtrix-kod yorliqlari.

        Uch bosqich, har biri to'xtashi mumkin:
          1. yorliq o'lchamlari    GET  /v1/product/barcodes/types
          2. SKU -> skuId jadvali  GET  /v1/product/shop/{shopId}
          3. chop etish            POST /v1/product/shop/{shopId}/barcodes/print

        Qaytaradi: (pdf, tashxis). Tashxis qaysi bosqichda
        to'xtaganini aniq aytadi — taxmin qilish shart bo'lmasin.
        """
        if settings.uzum_mock:
            return None, "test rejimi"
        if not sku_keys:
            return None, "SKU ro'yxati bo'sh"

        # --- 1-bosqich: yorliq o'lchami ---
        try:
            type_id = await self.get_barcode_type_id()
        except ApiError as e:
            return None, f"1) yorliq turlari: {str(e)[:90]}"
        if not type_id:
            return None, "1) yorliq turlari: ro'yxat bo'sh keldi"

        # --- 2-bosqich: skuId moslik jadvali ---
        try:
            mapping = await self.get_sku_map(shop_id)
        except ApiError as e:
            return None, f"2) mahsulotlar: {str(e)[:90]}"
        if not mapping:
            return None, f"2) do'kon {shop_id} mahsulotlari bo'sh keldi"

        data, topilmadi = [], []
        for key, amount in sku_keys[:100]:
            sku_id = mapping.get(str(key))
            if sku_id:
                data.append({
                    "skuId": sku_id,
                    "amount": min(int(amount) or 1, 100),
                    "barcodeTypeId": type_id,
                })
            else:
                topilmadi.append(str(key))

        if not data:
            return None, (
                "2) skuId topilmadi\n"
                f"   qidirilgan: {topilmadi[:3]}\n"
                f"   jadvalda bor: {list(mapping.keys())[:3]}\n"
                f"   (jami {len(mapping)} ta yozuv)"
            )

        # --- 3-bosqich: chop etish ---
        try:
            raw = await self.post(
                ENDPOINTS["barcodes_print"].format(shop_id=shop_id),
                json={"data": data},
            )
        except ApiError as e:
            return None, f"3) chop etish: {str(e)[:120]}"

        pdf = self._decode_document(raw)
        if not pdf:
            keys = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
            return None, f"3) javobda hujjat yo'q. Maydonlar: {keys}"

        return pdf, (f"⚠️ {len(topilmadi)} ta SKU topilmadi" if topilmadi else "")


uzum = UzumClient()
