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


class AIClient:
    def __init__(self) -> None:
        self.key = settings.ai_key
        self.model = settings.ai_model

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    async def ask(self, question: str, data: dict[str, Any]) -> str | None:
        """
        Savolga bot ma'lumotlari asosida javob beradi.

        data — botning hozirgi holati (buyurtmalar, bosqichlar, muddatlar).
        AI uni o'qib, savolga mos qismini gapga aylantiradi.
        """
        if not self.enabled:
            return None

        prompt = (
            f"MA'LUMOT (bot bazasidan):\n"
            f"{json.dumps(data, ensure_ascii=False, indent=1, default=str)}\n\n"
            f"XODIM SAVOLI: {question}"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    API_URL,
                    headers={
                        "x-api-key": self.key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": 500,
                        "system": SYSTEM,
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


ai = AIClient()
