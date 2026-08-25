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
        """
        AI ishlashga tayyormi?

        Provayderga qarab turli narsa kerak:
          anthropic — API kaliti
          gemini    — Google API kaliti (bepul)
          make      — webhook manzili
        """
        if settings.ai_provider == "make":
            return bool(settings.make_ai_webhook)
        if settings.ai_provider == "gemini":
            return bool(settings.gemini_api_key)
        return bool(self.key)

    async def _send(
        self, system: str, prompt: str, max_tokens: int = 500
    ) -> str | None:
        """
        Umumiy so'rov yuboruvchi.

        Uch provayderni qo'llab-quvvatlaydi:
          anthropic — to'g'ridan-to'g'ri API (tez, bitta qadam)
          gemini    — Google Gemini (BEPUL daraja bor)
          make      — Make.com webhook orqali (sekinroq, lekin Make'dagi
                      operatsiyalardan foydalanadi)

        Provayder .env dagi AI_PROVIDER bilan tanlanadi. Shuning uchun
        birini ikkinchisiga almashtirish uchun kodni o'zgartirish
        shart emas — bitta qator yetarli.
        """
        if not self.enabled:
            return None

        if settings.ai_provider == "make":
            return await self._send_via_make(system, prompt)
        if settings.ai_provider == "gemini":
            return await self._send_via_gemini(system, prompt, max_tokens)
        return await self._send_via_anthropic(system, prompt, max_tokens)

    async def _send_via_make(self, system: str, prompt: str) -> str | None:
        """
        Make.com webhook orqali yuboradi.

        Make stsenariysi shunday bo'lishi kerak:
            1. Webhook (Custom webhook)      — so'rovni qabul qiladi
            2. AI moduli                      — {{1.system}} va {{1.prompt}}
            3. Webhook response               — javob matnini qaytaradi

        Javob oddiy matn yoki {"text": "..."} ko'rinishida bo'lishi mumkin —
        ikkalasi ham qo'llab-quvvatlanadi.
        """
        url = settings.make_ai_webhook
        if not url:
            log.warning("AI_PROVIDER=make, lekin MAKE_AI_WEBHOOK kiritilmagan")
            return None

        try:
            # Make sekinroq ishlaydi (webhook -> AI -> javob), shuning
            # uchun kutish vaqti uzunroq
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    url,
                    json={"system": system, "prompt": prompt},
                )
        except Exception as e:
            log.warning("Make.com so'rovi yuborilmadi: %s", e)
            return None

        if resp.status_code != 200:
            log.warning(
                "Make.com xatosi: HTTP %s %s", resp.status_code, resp.text[:200]
            )
            return None

        text = resp.text.strip()
        if not text:
            return None

        # JSON qaytargan bo'lsa, ichidan matnni olamiz
        try:
            data = resp.json()
            if isinstance(data, dict):
                for key in ("text", "result", "answer", "javob", "content"):
                    if isinstance(data.get(key), str):
                        return data[key].strip() or None
            if isinstance(data, str):
                return data.strip() or None
        except Exception:
            pass

        return text

    async def _send_via_gemini(
        self, system: str, prompt: str, max_tokens: int
    ) -> str | None:
        """Google Gemini API'ga so'rov (bepul darajasi bor)."""
        key = settings.gemini_api_key
        if not key:
            log.warning("AI_PROVIDER=gemini, lekin GEMINI_API_KEY kiritilmagan")
            return None

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent?key={key}"
        )
        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "system_instruction": {
                            "parts": [{"text": system}]
                        },
                        "contents": [
                            {"role": "user", "parts": [{"text": prompt}]}
                        ],
                        "generationConfig": {
                            "maxOutputTokens": max_tokens,
                            "temperature": 0.4,
                        },
                    },
                )
        except Exception as e:
            log.warning("Gemini so'rovi yuborilmadi: %s", e)
            return None

        if resp.status_code != 200:
            log.warning(
                "Gemini xatosi: HTTP %s %s", resp.status_code, resp.text[:200]
            )
            return None

        try:
            data = resp.json()
            cand = (data.get("candidates") or [{}])[0]
            parts = cand.get("content", {}).get("parts") or []

            # MUHIM: Gemini 2.5+/3.x — fikrlaydigan (reasoning) model.
            # U javobdan oldin ICHKI fikrlashni ham yozadi — bu qismlar
            # "thought": true belgisi bilan keladi. Ularni JAVOBGA
            # qo'shish MUMKIN EMAS (avval xato: fikrlash matni chiqib
            # qolardi). Faqat haqiqiy javob matnini olamiz.
            text = "".join(
                p.get("text", "") for p in parts if not p.get("thought")
            )

            # Agar javob to'liq bo'lmasa (token tugadi) — logga yozamiz,
            # keyingi safar budjetni ko'paytirishni bilamiz.
            reason = cand.get("finishReason") or ""
            if reason and reason != "STOP":
                log.warning(
                    "Gemini javobi to'liq emas: finishReason=%s (matn %d belgi)",
                    reason, len(text),
                )

            return text.strip() or None
        except Exception as e:
            log.warning("Gemini javobi o'qilmadi: %s", e)
            return None

    async def _send_via_anthropic(
        self, system: str, prompt: str, max_tokens: int
    ) -> str | None:
        """Anthropic API'ga to'g'ridan-to'g'ri so'rov."""
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
        # max_tokens katta: Gemini 3.x fikrlash uchun ham token sarflaydi,
        # kichik budjetda javob kesilib qolardi.
        return await self._send(SYSTEM, prompt, max_tokens=1024)

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
        return await self._send(SYSTEM_ANALYST, prompt, max_tokens=1500)


ai = AIClient()
