"""
Middleware — har bir xabar handler'ga yetib borishidan oldin shu yerdan o'tadi.
Bu yerda foydalanuvchi ro'yxatdan o'tganmi yo'qmi tekshiriladi.

Bu xavfsizlikning eng muhim qismi: ro'yxatda bo'lmagan odam
botning ichki buyruqlariga umuman kira olmaydi.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.db import repo

log = logging.getLogger(__name__)

PUBLIC_COMMANDS = {"/start", "/help", "/id", "/versiya"}


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        employee = await repo.get_employee(user.id)
        data["employee"] = employee

        # Ochiq buyruqlarni hamma ishlata oladi
        if isinstance(event, Message):
            text = (event.text or "").split()[0].split("@")[0] if event.text else ""
            if text in PUBLIC_COMMANDS:
                return await handler(event, data)

        if employee is None:
            log.warning("Ruxsatsiz urinish: id=%s username=%s", user.id, user.username)
            if isinstance(event, Message) and event.chat.type == "private":
                await event.answer(
                    "⛔️ Siz ro'yxatdan o'tmagansiz.\n\n"
                    f"Sizning Telegram ID'ingiz: <code>{user.id}</code>\n"
                    "Ushbu ID'ni adminga yuboring — u sizni tizimga qo'shadi."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return None  # handler chaqirilmaydi

        return await handler(event, data)
