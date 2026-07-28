"""
PDF birlashtirish — bir aktdagi barcha yorliqlarni bitta faylga.

Nega kerak? 30 ta buyurtma = 30 ta alohida PDF = chatda 30 ta fayl.
Yig'uvchi ularni birma-bir ochib, birma-bir chop etishi kerak bo'ladi.
Bitta faylda esa hammasi ketma-ket — bir marta chop etish kifoya.
"""
from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)


def merge(pdfs: list[bytes]) -> bytes | None:
    """
    Bir nechta PDF'ni bitta faylga qo'shadi.

    Buzilgan fayllar o'tkazib yuboriladi — bittasi tufayli
    butun ish to'xtamasin.
    """
    if not pdfs:
        return None
    if len(pdfs) == 1:
        return pdfs[0]

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        log.error(
            "pypdf o'rnatilmagan! PDF'lar birlashtirilmaydi.\n"
            "Tuzatish:  pip install -r requirements.txt"
        )
        return None

    writer = PdfWriter()
    added = skipped = 0

    for data in pdfs:
        try:
            reader = PdfReader(io.BytesIO(data))
            for page in reader.pages:
                writer.add_page(page)
            added += 1
        except Exception as e:
            skipped += 1
            log.warning("PDF qo'shilmadi: %s", e)

    if not added:
        return None
    if skipped:
        log.info("PDF birlashtirildi: %d ta, %d tasi o'tkazildi", added, skipped)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def page_count(data: bytes) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


def available() -> bool:
    """pypdf o'rnatilganmi — tekshiruv uchun."""
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False
