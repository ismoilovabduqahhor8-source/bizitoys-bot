"""
Guruh kartochkasini yuborish — rasm bilan.

Nega alohida modul? Kartochka uch joyda yuboriladi: /orders, yangi
buyurtma e'loni va eslatmalar. Kod bir joyda tursin.

Telegram cheklovlari:
  • rasm ostidagi matn (caption) — 1024 belgigacha
  • bitta rasmga tugma biriktirish mumkin, albomga — yo'q

Shuning uchun: bitta rasm + kartochka matni + tugmalar.
Rasm mahsulotlardan eng ko'p sonlisidan olinadi.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.services import grouping

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1000


def pick_photo(group: dict[str, Any]) -> str | None:
    """Guruhdagi eng ko'p sonli mahsulotning rasmi."""
    with_photo, _ = grouping.items_with_photos(group["orders"])
    return with_photo[0]["photo"] if with_photo else None


async def send_group_card(
    bot: Bot,
    chat_id: int,
    group: dict[str, Any],
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Kartochkani yuboradi — rasm bo'lsa rasm bilan, bo'lmasa matn."""
    text = grouping.format_group(group)
    photo = pick_photo(group)

    if photo:
        try:
            await bot.send_photo(
                chat_id,
                photo=photo,
                caption=text[:CAPTION_LIMIT],
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            # Rasm yuklanmasa (havola eskirgan, format noto'g'ri) — matn bilan
            log.warning("Rasm yuborilmadi, matn bilan davom etamiz: %s", e)

    await bot.send_message(chat_id, text, reply_markup=keyboard)
