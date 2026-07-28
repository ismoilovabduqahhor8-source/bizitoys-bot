"""
Barcha tashqi API'lar uchun umumiy HTTP klient.
Retry (qayta urinish), timeout va log — bir joyda.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ApiError(Exception):
    """Tashqi API xatosi — foydalanuvchiga tushunarli xabar berish uchun."""

    def __init__(self, service: str, message: str, status: int | None = None):
        self.service = service
        self.status = status
        super().__init__(f"[{service}] {message}")


class BaseClient:
    """
    Sodda async REST klient.
    Yangi integratsiya qo'shish uchun shundan meros olib, endpoint'larni yozing.
    """

    service_name = "api"

    def __init__(self, base_url: str, headers: dict[str, str], timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.timeout = timeout

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retries: int = 2,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_err: Exception | None = None

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(
                        method, url, headers=self.headers, params=params, json=json
                    )
                log.debug("%s %s -> %s", method, url, resp.status_code)

                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue

                if resp.status_code == 401:
                    raise ApiError(self.service_name, "Token noto'g'ri yoki muddati tugagan", 401)
                if resp.status_code >= 400:
                    raise ApiError(
                        self.service_name,
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                        resp.status_code,
                    )
                if not resp.content:
                    return {}

                # Javob JSON bo'lmasligi ham mumkin: ba'zi manzillar
                # to'g'ridan-to'g'ri PDF qaytaradi. Uni matn deb o'qishga
                # urinsak, UnicodeDecodeError chiqadi.
                ctype = resp.headers.get("content-type", "").lower()
                if "json" not in ctype:
                    if resp.content[:4] == b"%PDF" or "pdf" in ctype:
                        return resp.content
                    if any(x in ctype for x in ("image", "octet-stream", "zip")):
                        return resp.content
                try:
                    return resp.json()
                except (ValueError, UnicodeDecodeError):
                    # Sarlavha yolg'on bo'lsa ham baytlarni qaytaramiz
                    return resp.content

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise ApiError(self.service_name, f"Serverga ulanib bo'lmadi: {e}") from e

        raise ApiError(self.service_name, f"Noma'lum xato: {last_err}")

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)
