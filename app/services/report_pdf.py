"""
Hisobotni PDF qilib chizish.

Namuna sifatida Market Plus hisoboti olingan: oq fon, ingichka chiziqlar,
foyda yashil, zarar qizil, pastda jami va yakuniy summa.
"""
from __future__ import annotations

import io
import logging
from typing import Any

from app.services.report import fmt

log = logging.getLogger(__name__)

# Shriftlar. MUHIM: standart Helvetica kirill harflarini ko'rsatmaydi,
# mahsulot nomlari esa ruscha ("Деревянный набор..."). Shuning uchun
# Unicode shrift qidiriladi.
FONT = "Helvetica"
FONT_B = "Helvetica-Bold"

FONT_PATHS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
]


def _setup_fonts() -> None:
    """Kirill harflarini qo'llab-quvvatlaydigan shriftni ro'yxatdan o'tkazadi."""
    global FONT, FONT_B
    if FONT != "Helvetica":
        return
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold in FONT_PATHS:
        if os.path.exists(regular):
            try:
                pdfmetrics.registerFont(TTFont("Rep", regular))
                pdfmetrics.registerFont(
                    TTFont("Rep-B", bold if os.path.exists(bold) else regular)
                )
                FONT, FONT_B = "Rep", "Rep-B"
                log.info("PDF shrifti: %s", regular)
                return
            except Exception as e:
                log.warning("Shrift yuklanmadi (%s): %s", regular, e)

    log.warning(
        "Unicode shrift topilmadi — kirill harflari ko'rinmasligi mumkin. "
        "Windows'da odatda C:/Windows/Fonts/arial.ttf bo'ladi."
    )


# Ranglar — hisobotdagi kabi
INK = (0.13, 0.13, 0.15)
MUTED = (0.45, 0.45, 0.50)
LINE = (0.87, 0.87, 0.90)
HEAD_BG = (0.945, 0.945, 0.96)
GREEN = (0.09, 0.55, 0.30)
RED = (0.78, 0.14, 0.19)
BLUE = (0.15, 0.35, 0.72)


def render(rep: dict[str, Any]) -> bytes | None:
    """Hisobotni A4 (yotiq) PDF qilib qaytaradi."""
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError:
        log.error("reportlab o'rnatilmagan — PDF chizilmaydi")
        return None

    _setup_fonts()

    buf = io.BytesIO()
    W, H = landscape(A4)
    c = canvas.Canvas(buf, pagesize=landscape(A4))

    # Ustunlar: o'ng chetdan boshlab joylashadi
    x_name = 14 * mm
    x_qty = W - 128 * mm
    x_rev = W - 100 * mm
    x_pay = W - 68 * mm
    x_prof = W - 36 * mm
    x_roi = W - 8 * mm

    partial = rep.get("source") == "buyurtmalar"

    def head(page_no: int) -> float:
        c.setFillColorRGB(*INK)
        c.setFont(FONT_B, 14)
        c.drawString(x_name, H - 16 * mm, f"Hisobot : {rep['date']:%d.%m.%Y}")
        if page_no > 1:
            c.setFont(FONT, 8)
            c.setFillColorRGB(*MUTED)
            c.drawRightString(x_roi, H - 16 * mm, f"{page_no}-bet")

        y = H - 26 * mm
        c.setFillColorRGB(*HEAD_BG)
        c.rect(x_name - 3 * mm, y - 2 * mm, W - 22 * mm, 7 * mm, stroke=0, fill=1)
        c.setFillColorRGB(*INK)
        c.setFont(FONT_B, 8)
        c.drawString(x_name, y, "Tovar nomi")
        cols = ((x_qty, "Soni"), (x_rev, "Daromad"))
        if not partial:
            cols += ((x_pay, "Chiqarishga"), (x_prof, "Sof foyda"), (x_roi, "ROI %"))
        for x, t in cols:
            c.drawRightString(x, y, t)
        if partial:
            c.setFont(FONT, 7)
            c.setFillColorRGB(*MUTED)
            c.drawRightString(x_roi, y, "foyda ma'lumoti yo'q")
            c.setFillColorRGB(*INK)
            c.setFont(FONT_B, 8)
        return y - 7 * mm

    def line(y: float) -> None:
        c.setStrokeColorRGB(*LINE)
        c.setLineWidth(0.4)
        c.line(x_name - 3 * mm, y, W - 8 * mm, y)

    page = 1
    y = head(page)

    for it in rep["items"]:
        if y < 22 * mm:
            c.showPage()
            page += 1
            y = head(page)

        c.setFillColorRGB(*INK)
        c.setFont(FONT, 8)
        name = it["name"][:62]
        c.drawString(x_name, y, name)
        c.setFillColorRGB(*MUTED)
        c.setFont(FONT, 7)
        c.drawString(x_name + c.stringWidth(name, FONT, 8) + 3, y, it["sku"][:26])

        c.setFillColorRGB(*INK)
        c.setFont(FONT, 8)
        c.drawRightString(x_qty, y, str(it["qty"]))
        c.drawRightString(x_rev, y, fmt(it["revenue"]))
        if not partial:
            c.drawRightString(x_pay, y, fmt(it["payout"]))
            c.drawRightString(x_prof, y, fmt(it["profit"]))
            c.setFillColorRGB(*(RED if it["roi"] < 0 else GREEN))
            c.setFont(FONT_B, 8)
            c.drawRightString(x_roi, y, f"{it['roi']:.2f}%")

        y -= 6 * mm
        line(y + 2 * mm)

    # ---------- Jami ----------
    if y < 40 * mm:
        c.showPage()
        page += 1
        y = head(page)

    t = rep["total"]
    y -= 2 * mm
    c.setFillColorRGB(*INK)
    c.setFont(FONT_B, 9)
    c.drawRightString(x_qty - 12 * mm, y, "Umumiy:")
    c.drawRightString(x_qty, y, str(t["qty"]))
    c.drawRightString(x_rev, y, fmt(t["revenue"]))
    if not partial:
        c.drawRightString(x_pay, y, fmt(t["payout"]))
        c.drawRightString(x_prof, y, fmt(t["profit"]))
        c.setFillColorRGB(*(RED if t["roi"] < 0 else GREEN))
        c.drawRightString(x_roi, y, f"{t['roi']:.2f}%")

    y -= 8 * mm
    c.setStrokeColorRGB(*LINE)
    c.setLineWidth(0.8)
    c.line(x_name - 3 * mm, y + 3 * mm, W - 8 * mm, y + 3 * mm)

    # ---------- Qo'shimchalar ----------
    if partial:
        c.setFont(FONT, 8)
        c.setFillColorRGB(*MUTED)
        c.drawString(x_name, y - 2 * mm,
                     "Foyda va ROI hisoblanmadi — Uzum moliyaviy ma'lumotni "
                     "hali bermayapti.")
        c.setFont(FONT, 6.5)
        c.drawRightString(x_roi, 10 * mm, "BiziToys bot")
        c.showPage()
        c.save()
        return buf.getvalue()

    c.setFont(FONT_B, 8)
    c.setFillColorRGB(*BLUE)
    c.drawString(x_name, y, "Qo'shimcha xarajatlar:")
    c.setFillColorRGB(*RED)
    c.drawRightString(x_roi, y, f"- {fmt(rep['extra'])}")

    y -= 6 * mm
    c.setFillColorRGB(*BLUE)
    c.drawString(x_name, y, "Qaytarilgan mablag':")
    c.setFillColorRGB(*GREEN)
    c.drawRightString(x_roi, y, f"+ {fmt(rep['refund'])}")

    y -= 7 * mm
    c.setStrokeColorRGB(*BLUE)
    c.setLineWidth(1)
    c.line(x_name - 3 * mm, y + 3 * mm, W - 8 * mm, y + 3 * mm)

    c.setFillColorRGB(*INK)
    c.setFont(FONT_B, 11)
    c.drawString(x_name, y - 1 * mm, "Yakuniy sof summa:")
    c.drawRightString(x_roi, y - 1 * mm, fmt(rep["final"]))

    y -= 8 * mm
    c.setFont(FONT_B, 9)
    c.setFillColorRGB(*BLUE)
    c.drawString(x_name, y, "ROI %:")
    c.setFillColorRGB(*(RED if rep["final_roi"] < 0 else GREEN))
    c.drawRightString(x_roi, y, f"{rep['final_roi']:.2f}%")

    c.setFont(FONT, 6.5)
    c.setFillColorRGB(*MUTED)
    c.drawRightString(x_roi, 10 * mm, "BiziToys bot")

    c.showPage()
    c.save()
    return buf.getvalue()
