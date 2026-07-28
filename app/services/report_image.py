"""
Hisobotni RASM (PNG) qilib chizish.

PDF'dan farqi: foydalanuvchi hech qanday dastur ochmasdan, Telegramning
o'zida ko'radi. Xuddi shu tuzilish — Market Plus namunasidagi jadval.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Any

from app.services.report import fmt

log = logging.getLogger(__name__)

# Ranglar (0-255)
BG = (255, 255, 255)
INK = (33, 33, 38)
MUTED = (115, 115, 128)
LINE = (222, 222, 230)
HEAD_BG = (241, 241, 245)
GREEN = (23, 140, 77)
RED = (199, 36, 48)
BLUE = (38, 89, 184)

# Kirill harflarini qo'llab-quvvatlaydigan shrift qidiriladigan joylar
FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]


def _fonts(size_title: int, size_head: int, size_body: int, size_small: int):
    from PIL import ImageFont

    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular):
            b = bold if os.path.exists(bold) else regular
            return {
                "title": ImageFont.truetype(regular, size_title, index=0)
                if False else ImageFont.truetype(b, size_title),
                "head": ImageFont.truetype(b, size_head),
                "body": ImageFont.truetype(regular, size_body),
                "body_b": ImageFont.truetype(b, size_body),
                "small": ImageFont.truetype(regular, size_small),
            }

    log.warning("Unicode shrift topilmadi — kirill harflari ko'rinmasligi mumkin")
    d = ImageFont.load_default()
    return {k: d for k in ("title", "head", "body", "body_b", "small")}


def render(rep: dict[str, Any]) -> bytes | None:
    """
    Hisobotni PNG rasm qilib qaytaradi.

    MUHIM: «Sof foyda» va «ROI %» ko'rsatilmaydi — foydalanuvchi
    talabiga ko'ra butunlay olib tashlangan. Faqat Soni, Daromad,
    Chiqarishga ustunlari bor.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        log.error("Pillow o'rnatilmagan — rasm chizilmaydi")
        return None

    items = rep["items"]

    W = 900
    PAD = 26
    ROW_H = 30
    HEAD_H = 34
    TOP = 78

    # Balandlikni keng ajratib, chizib bo'lgach haqiqiy balandlikka
    # qirqamiz — pastki qism hech qachon kesilib qolmasin.
    H = TOP + HEAD_H + len(items) * ROW_H + 140

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f = _fonts(24, 15, 14, 12)

    # Ustunlar (o'ngdan)
    x_name = PAD
    x_qty = W - 260
    x_rev = W - 140
    x_pay = W - PAD

    def right(x: int, y: int, text: str, font, fill) -> None:
        w = d.textlength(text, font=font)
        d.text((x - w, y), text, font=font, fill=fill)

    # Sarlavha
    title = rep.get("title") or f"{rep['date']:%d.%m.%Y}"
    d.text((x_name, 24), f"Hisobot : {title}", font=f["title"], fill=INK)

    # Jadval boshi
    y = TOP
    d.rectangle([PAD - 6, y, W - PAD + 6, y + HEAD_H], fill=HEAD_BG)
    ty = y + 8
    d.text((x_name, ty), "Tovar nomi", font=f["head"], fill=INK)
    for x, t in ((x_qty, "Soni"), (x_rev, "Daromad"), (x_pay, "Chiqarishga")):
        right(x, ty, t, f["head"], INK)

    y += HEAD_H

    for it in items:
        ty = y + 6
        name = it["name"][:44]
        d.text((x_name, ty), name, font=f["body"], fill=INK)
        nw = d.textlength(name, font=f["body"])
        d.text((x_name + nw + 8, ty + 1), it["sku"][:20], font=f["small"], fill=MUTED)

        right(x_qty, ty, str(it["qty"]), f["body"], INK)
        right(x_rev, ty, fmt(it["revenue"]), f["body_b"], INK)
        right(x_pay, ty, fmt(it["payout"]), f["body"], INK)

        y += ROW_H
        d.line([(PAD - 6, y), (W - PAD + 6, y)], fill=LINE, width=1)

    # Jami
    y += 10
    t = rep["total"]
    ty = y
    right(x_qty - 90, ty, "Umumiy:", f["body_b"], INK)
    right(x_qty, ty, str(t["qty"]), f["body_b"], INK)
    right(x_rev, ty, fmt(t["revenue"]), f["title"], INK)
    right(x_pay, ty, fmt(t["payout"]), f["body_b"], INK)

    y += 34
    d.line([(PAD - 6, y), (W - PAD + 6, y)], fill=LINE, width=2)

    out = io.BytesIO()
    img = img.crop((0, 0, W, y + 30))
    img.save(out, format="PNG")
    return out.getvalue()
