"""
BiziToys ichki boshqaruv boti — kirish nuqtasi.

Ishga tushirish:
    python main.py
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from app.bot.handlers import (admin, attendance, common, fbs, invoices, orders,
                              postavka, stock)
from app.bot.middlewares import AuthMiddleware
from app.bot.throttle import Throttle
from app import VERSION
from app.config import settings
from app.db import repo
from app.scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bizitoys")


async def main() -> None:
    problems = settings.validate()
    if problems:
        log.error("Sozlamalarda xato:")
        for p in problems:
            log.error("  • %s", p)
        log.error(".env faylini to'g'rilang (namuna: .env.example)")
        sys.exit(1)

    await repo.init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Telegram tezlik chegarasidan himoya — barcha chiquvchi
    # so'rovlar shu yerdan o'tadi va kerak bo'lsa qayta yuboriladi
    bot.session.middleware(Throttle())

    dp = Dispatcher()

    # Middleware — barcha xabar va tugma bosishlar uchun
    # MUHIM: outer_middleware — filtrlardan OLDIN ishlaydi.
    # Oddiy middleware() filtrlardan KEYIN ishlaydi, natijada
    # IsAdmin kabi filtrlar `employee` ma'lumotini ko'rmaydi
    # va barcha admin buyruqlari jim qoladi.
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())

    # Router'lar tartibi muhim: admin birinchi, umumiy oxirida
    dp.include_router(admin.router)
    dp.include_router(attendance.router)
    dp.include_router(fbs.router)
    dp.include_router(postavka.router)
    dp.include_router(invoices.router)
    dp.include_router(orders.router)
    dp.include_router(stock.router)
    dp.include_router(common.router)

    # Xatolar yashirin qolmasin: aiogram odatda ularni faqat logga yozadi,
    # foydalanuvchi esa "hech nima bo'lmadi" deb o'ylaydi.
    @dp.error()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("Ishlov berilmagan xato", exc_info=event.exception)
        chat_id = None
        upd = event.update
        if getattr(upd, "message", None):
            chat_id = upd.message.chat.id
        elif getattr(upd, "callback_query", None) and upd.callback_query.message:
            chat_id = upd.callback_query.message.chat.id
        if chat_id:
            try:
                await bot.send_message(
                    chat_id,
                    "⚠️ <b>Kutilmagan xato</b>\n\n"
                    f"<code>{type(event.exception).__name__}: "
                    f"{str(event.exception)[:250]}</code>\n\n"
                    "<i>Shu matnni Claude'ga yuboring.</i>",
                )
            except Exception:
                pass
        return True

    setup_scheduler(bot)

    me = await bot.get_me()
    log.info("Bot ishga tushdi: @%s", me.username)
    log.info("Versiya: %s", VERSION)
    log.info("Rejim -> %s", settings.mode_report())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot to'xtatildi.")
