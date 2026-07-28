"""
TELEGRAM TEZLIK TIYISHI.

Telegram cheklovlari:
  • bitta chatga ~1 xabar/soniya
  • guruhga ~20 xabar/daqiqa
  • umumiy ~30 xabar/soniya

Chegaradan oshsa, Telegram `TelegramRetryAfter` xatosini beradi va
navbatdagi xabarlar yo'qoladi. Bot esa "yubordim" deb o'ylaydi.

Bu modul ikkita ish qiladi:
  1. Har chatga xabarlar orasida eng kam oraliq saqlaydi
  2. Chegaraga urilsa — kutadi va QAYTA yuboradi, tashlab yubormaydi

Nega alohida modul? Bu himoya butun botga kerak: e'lonlar, yorliqlar,
tashxis buyruqlari — hammasi shu yerdan o'tadi.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    NextRequestMiddlewareType,
    RequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import Response, TelegramMethod

log = logging.getLogger(__name__)

# Bitta chatga xabarlar orasidagi eng kam oraliq (soniya)
MIN_GAP = 0.65
# Chegaraga urilganda necha marta qayta urinish
MAX_RETRY = 3


class Throttle(RequestMiddlewareType):
    """Chiqayotgan har bir so'rov shu yerdan o'tadi."""

    def __init__(self) -> None:
        self._last: dict[Any, float] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _chat_of(method: TelegramMethod) -> Any:
        return getattr(method, "chat_id", None)

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType,
        bot: Bot,
        method: TelegramMethod,
    ) -> Response:
        chat = self._chat_of(method)

        if chat is not None:
            async with self._lock:
                wait = MIN_GAP - (time.monotonic() - self._last.get(chat, 0))
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last[chat] = time.monotonic()

        for attempt in range(MAX_RETRY):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as e:
                pause = e.retry_after + 0.5
                log.warning(
                    "Telegram chegarasi: %s soniya kutamiz (%d/%d)",
                    pause, attempt + 1, MAX_RETRY,
                )
                await asyncio.sleep(pause)
                if chat is not None:
                    self._last[chat] = time.monotonic()

        # Oxirgi urinish — xato chiqsa, yuqoriga uzatiladi
        return await make_request(bot, method)
