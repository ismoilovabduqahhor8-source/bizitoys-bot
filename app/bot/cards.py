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


async def send_new_order_card(bot: Bot, chat_id: int, group: dict[str, Any]) -> None:
    """
    YANGI buyurtma guruhini yuboradi: HAR mahsulotning rasmi + bitta
    «Qabul qilish» tugmasi.

    Bu ikki joyda ishlatiladi — FBS menyusidagi «Yangilar» bo'limi va
    guruhga avtomatik yuboriladigan «yangi buyurtma» e'loni. Ikkalasi
    ham bir xil ko'rinishda bo'lishi kerak: ortiqcha «To'liq ko'rish»
    yoki «Skladga berish» tugmalarisiz, faqat qabul qilish.
    """
    from aiogram.types import InlineKeyboardButton, InputMediaPhoto

    with_photo, _ = grouping.items_with_photos(group["orders"])

    if with_photo:
        media = [
            InputMediaPhoto(media=r["photo"],
                            caption=f"<b>{r['sku']}</b>\nSoni: {r['qty']}",
                            parse_mode="HTML")
            for r in with_photo[:10]
        ]
        try:
            await bot.send_media_group(chat_id, media)
        except Exception as e:
            log.warning("Rasm albomi yuborilmadi: %s", e)
            for r in with_photo[:10]:
                await bot.send_message(chat_id, f"<b>{r['sku']}</b>\nSoni: {r['qty']}")

    text = grouping.format_group(group)
    gid = group["gid"]
    await bot.send_message(
        chat_id, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"✅ Qabul qilish ({len(group['orders'])} ta)",
                callback_data=f"fbsok:{gid}",
            )
        ]]),
    )
