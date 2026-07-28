"""
Billz API integratsiyasi (ombor qoldig'i + sotuv tahlili).

⚠️ Billz odatda ikki bosqichli autentifikatsiya ishlatadi:
   1) secret_token yuborib, vaqtinchalik access token olinadi;
   2) keyingi so'rovlarda "Authorization: Bearer <access_token>".
Aniq yo'llar Billz menejeri bergan hujjatda bo'ladi — ENDPOINTS'ni shunga moslang.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.config import settings
from app.integrations import mock_data
from app.integrations.base import ApiError, BaseClient

log = logging.getLogger(__name__)

ENDPOINTS = {
    "auth": "/auth/login",
    "products": "/products",
    "orders": "/orders",
}


class BillzClient(BaseClient):
    service_name = "Billz"

    def __init__(self) -> None:
        super().__init__(
            base_url=settings.billz_base_url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # -------------------- autentifikatsiya --------------------
    async def _ensure_token(self) -> None:
        if self._access_token and time.time() < self._token_expires_at:
            return
        data = await self.post(ENDPOINTS["auth"], json={"secret_token": settings.billz_secret})
        token = (data.get("data") or {}).get("access_token") or data.get("access_token")
        if not token:
            raise ApiError(self.service_name, "access_token olinmadi — secret_token'ni tekshiring")
        self._access_token = token
        self._token_expires_at = time.time() + 50 * 60  # ~50 daqiqa
        self.headers["Authorization"] = f"Bearer {token}"
        log.info("Billz tokeni yangilandi")

    # -------------------- ommaviy metodlar --------------------
    async def get_stock(self) -> list[dict[str, Any]]:
        """Ombordagi mahsulotlar va ularning qoldig'i."""
        if settings.billz_mock:
            log.info("MOCK rejim: Billz qoldig'i soxta ma'lumotdan olindi")
            return mock_data.mock_stock()

        await self._ensure_token()
        raw = await self.get(ENDPOINTS["products"], params={"limit": 500, "page": 1})
        items = (raw.get("data") or {}).get("products") or raw.get("products") or []
        out = []
        for p in items:
            out.append(
                {
                    "sku": str(p.get("sku") or p.get("barcode") or p.get("id") or "?"),
                    "name": p.get("name") or p.get("title") or "—",
                    "qty": int(_first_number(p, ("quantity", "stock", "total_quantity")) or 0),
                    "price": int(_first_number(p, ("retail_price", "price", "sale_price")) or 0),
                }
            )
        return out

    async def get_top_sales(self, days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
        """Eng ko'p sotilgan mahsulotlar."""
        if settings.billz_mock:
            return mock_data.mock_top_sales(days)[:limit]

        await self._ensure_token()
        raw = await self.get(ENDPOINTS["orders"], params={"days": days, "limit": 500})
        orders = (raw.get("data") or {}).get("orders") or raw.get("orders") or []

        agg: dict[str, dict[str, Any]] = {}
        for order in orders:
            for item in order.get("items") or []:
                sku = str(item.get("sku") or item.get("product_id") or "?")
                qty = int(item.get("quantity") or 1)
                price = int(_first_number(item, ("price", "sale_price")) or 0)
                row = agg.setdefault(
                    sku, {"sku": sku, "name": item.get("name") or "—", "sold_qty": 0, "revenue": 0}
                )
                row["sold_qty"] += qty
                row["revenue"] += qty * price

        rows = sorted(agg.values(), key=lambda r: r["sold_qty"], reverse=True)
        return rows[:limit]


def _first_number(d: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str) and v.replace(".", "", 1).isdigit():
            return float(v)
    return None


billz = BillzClient()
