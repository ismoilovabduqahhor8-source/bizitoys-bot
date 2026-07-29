"""
SUN'IY INTELLEKT — erkin savollarga javob berish.

Nima uchun kerak? Xodim «/orders» o'rniga oddiy tilda so'raydi:
    «bugun nima qoldi?»
    «Sergeliga nechta bor?»
    «eng shoshilinchi qaysi?»

Bot bu savollarni tushunib, o'z ma'lumotlaridan javob beradi.

MUHIM: AI faqat SHU maqsad uchun. Hisob-kitob (nechta qoldi, qachon
tugaydi, kim kechikdi) oddiy kod bilan qilinadi — u bepul, aniq va
har safar bir xil natija beradi. AI ga faqat "tushunish" va
"gapga aylantirish" topshiriladi.

Kalitsiz ham bot to'liq ishlaydi — bu qism ixtiyoriy.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """Sen BiziToys o'yinchoq do'konining ichki Telegram-botisan.
Xodimlar Uzum Market FBS buyurtmalari ustida ishlaydi.

QOIDALAR:
1. Faqat o'zbek tilida javob ber. Qisqa va aniq — 2-4 gap.
2. Faqat berilgan MA'LUMOT asosida javob ber. O'ylab topma.
3. Ma'lumotda javob bo'lmasa, ochiq ayt: "Bu haqda ma'lumotim yo'q".
4. Raqamlarni o'zgartirma, aynan berilganini yoz.
5. Emoji ishlatishing mumkin, lekin ko'p emas.

Bosqichlar ma'nosi:
- Yangi: qabul qilish kerak
- Skladda: sklad tovar chiqarmoqda
- Yig'ilmoqda: yig'uvchi ishlayapti
- Yig'ilgan: postavka ochilishi kerak
- Postavkada: haydovchi olib ketishi kerak"""


# Tahlil uchun alohida ko'rsatma.
#
# MUHIM: barcha raqamlar va muammolar KOD tomonidan hisoblangan holda
# beriladi. AI'ning vazifasi — hisoblash emas, IZOHLASH: qaysi muammo
# muhimroq, ular o'zaro qanday bog'liq, nimadan boshlash kerak.
SYSTEM_ANALYST = """Sen BiziToys o'yinchoq do'konining biznes-tahlilchisisan.
Egasi Uzum Market'da FBS va FBO orqali savdo qiladi.

Senga TAYYOR HISOBLANGAN muammolar ro'yxati beriladi.

QOIDALAR:
1. Faqat o'zbek tilida yoz.
2. Raqamlarni O'ZGARTIRMA va yangi raqam O'YLAB TOPMA.
   Faqat berilganini ishlat.
3. Hisob-kitob qilma — u allaqachon qilingan.
4. Vazifang: qaysi muammo eng muhim va NEGA — shuni tushuntirish.
5. Muammolar o'zaro bog'liq bo'lsa, buni ko'rsat.
   (masalan: tovar tugab qolgan + shu tovar zarardagi —
    demak qayta buyurtma berishdan oldin narxni ko'rib chiqish kerak)
6. Oxirida ANIQ bitta harakat taklif qil: "bugun nimadan boshlash kerak".
7. Umumiy gaplardan qoch. "Sotuvni oshirish kerak" — foydasiz maslahat.
   "Falon tovarning narxini ko'tarish kerak" — foydali.
8. Uzunligi: 5-8 gap. Ortiqcha yozma.

Agar muammo topilmagan bo'lsa, buni ochiq ayt va tabriklab qo'y."""


class AIClient:
    def __init__(self) -> None:
        self.key = settings.ai_key
        self.model = settings.ai_model

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    async def _send(
        self, system: str, prompt: str, max_tokens: int = 500
    ) -> str | None:
        """Umumiy so'rov yuboruvchi — ask() ham, analyze() ham shundan foydalanadi."""
        if not self.enabled:
            return None

        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                resp = await client.post(
                    API_URL,
                    headers={
                        "x-api-key": self.key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": max_tokens,
                        "system": system,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
        except Exception as e:
            log.warning("AI so'rovi yuborilmadi: %s", e)
            return None

        if resp.status_code != 200:
            log.warning("AI xatosi: HTTP %s %s", resp.status_code, resp.text[:200])
            return None

        try:
            blocks = resp.json().get("content") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text.strip() or None
        except Exception as e:
            log.warning("AI javobi o'qilmadi: %s", e)
            return None

    async def ask(self, question: str, data: dict[str, Any]) -> str | None:
        """
        Savolga bot ma'lumotlari asosida javob beradi.

        data — botning hozirgi holati (buyurtmalar, bosqichlar, muddatlar).
        AI uni o'qib, savolga mos qismini gapga aylantiradi.
        """
        prompt = (
            f"MA'LUMOT (bot bazasidan):\n"
            f"{json.dumps(data, ensure_ascii=False, indent=1, default=str)}\n\n"
            f"XODIM SAVOLI: {question}"
        )
        return await self._send(SYSTEM, prompt)

    async def analyze(
        self, problems: dict[str, Any], question: str | None = None
    ) -> str | None:
        """
        Topilgan muammolarni izohlaydi.

        DIQQAT: problems ichidagi barcha raqamlar KOD tomonidan
        hisoblangan. AI ularni faqat o'qiydi va tushuntiradi —
        qayta hisoblamaydi.
        """
        prompt = (
            "HISOBLANGAN MUAMMOLAR:\n"
            f"{json.dumps(problems, ensure_ascii=False, indent=1, default=str)}\n\n"
        )
        prompt += (
            f"EGASINING SAVOLI: {question}"
            if question else
            "Shu muammolarni tahlil qil: qaysi biri eng muhim va nega? "
            "Ular o'zaro bog'liqmi? Bugun nimadan boshlash kerak?"
        )
        return await self._send(SYSTEM_ANALYST, prompt, max_tokens=800)


ai = AIClient()
