"""
YORLIQLAR — ikki xil, ikki manzildan.

Kabinetdagi «Печатать этикетки» menyusi:
    ├─ Этикетка заказа   -> GET  /v1/fbs/order/{id}/labels/print?size=...
    └─ QR-код товара     -> POST /v1/product/shop/{shopId}/barcodes/print

Bular ALOHIDA hujjatlar:
  • buyurtma yorlig'i — mijoz, sana, buyurtma kodi  -> qutiga
  • mahsulot QR       — SKU nomi, QR, raqam         -> tovarning o'ziga
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.base import ApiError
from app.integrations.uzum import uzum
from app.services import pdfmerge

log = logging.getLogger(__name__)


async def order_labels(
    order_ids: list[str], limit: int = 60
) -> tuple[bytes | None, list[bytes], int]:
    """
    Buyurtma yorliqlari.

    Qaytaradi: (birlashtirilgan_pdf, alohida_pdflar, olinmagan_soni)

    Nega alohida ro'yxat ham qaytariladi? Birlashtirish `pypdf`
    kutubxonasiga bog'liq. U bo'lmasa fayllar YO'QOLMASLIGI kerak —
    chaqiruvchi ularni birma-bir yuboradi.
    """
    pdfs: list[bytes] = []
    fail = 0
    for oid in order_ids[:limit]:
        try:
            content, _ = await uzum.get_label(oid)
        except ApiError as e:
            log.warning("Yorliq olinmadi (%s): %s", oid, e)
            content = None
        if content:
            pdfs.append(content)
        else:
            fail += 1
    return pdfmerge.merge(pdfs), pdfs, fail


async def product_qr(
    orders: list[dict[str, Any]]
) -> tuple[bytes | None, dict[str, int], str]:
    """
    Mahsulot QR kodlari — bitta PDF.

    Har SKU uchun buyurtmalardagi umumiy soncha nusxa so'raladi:
    bir tovar 3 ta buyurtmada bo'lsa, 3 ta QR chiqadi.

    Qaytaradi: (pdf, {sku: soni}, tashxis)
    """
    # Kalit sifatida SHTRIX-KOD ishlatiladi.
    #
    # Nega? Buyurtmada mahsulot "SEENSOR-GIPS-АЛЫЙ" deb ataladi
    # (sotuvchi artikuli), mahsulotlar ro'yxatida esa "АЛЫЙ"
    # (variant nomi). Nomlar mos kelmaydi.
    # Shtrix-kod (1000066098736) esa ikkalasida bir xil.
    by_shop: dict[int, dict[str, int]] = {}
    for o in orders:
        shop_id = o.get("shop_id")
        if not shop_id:
            continue
        bucket = by_shop.setdefault(shop_id, {})
        for it in o.get("items") or []:
            key = it.get("barcode") or it.get("sku")
            if key:
                bucket[str(key)] = bucket.get(str(key), 0) + int(it.get("qty") or 1)

    if not by_shop:
        return None, {}, "buyurtmalarda do'kon raqami yo'q"

    pdfs: list[bytes] = []
    total: dict[str, int] = {}
    notes: list[str] = []

    for shop_id, skus in by_shop.items():
        try:
            pdf, note = await uzum.get_product_qr(shop_id, list(skus.items()))
        except ApiError as e:
            notes.append(f"do'kon {shop_id}: {str(e)[:90]}")
            continue
        if pdf:
            pdfs.append(pdf)
            total.update(skus)
        if note:
            notes.append(note)

    return pdfmerge.merge(pdfs), total, "\n".join(notes)


async def combined(
    orders: list[dict[str, Any]], limit: int = 40
) -> tuple[bytes | None, list[bytes], int, int, str]:
    """
    BITTA PDF: har buyurtmadan keyin darrov o'sha buyurtmaning QR kodi.

        1-bet  buyurtma yorlig'i   (mijoz, shahar, kod)
        2-bet  mahsulot QR         (SKU, shtrix-kod)
        3-bet  keyingi buyurtma yorlig'i
        4-bet  uning QR kodi
        ...

    Nega shunday? Yig'uvchi dastani chop etadi va ketma-ket ishlaydi:
    yorliqni qutiga, keyingi betdagi QR'ni tovarga. Ikki dastani
    solishtirib o'tirish shart emas.

    Qaytaradi: (birlashgan_pdf, alohida_betlar, buyurtmalar, xatolar, tashxis)
    """
    parts: list[bytes] = []
    ok = fail = 0
    notes: list[str] = []

    for o in orders[:limit]:
        oid = o["order_id"]

        # 1) Buyurtma yorlig'i
        try:
            label, _ = await uzum.get_label(oid)
        except ApiError as e:
            log.warning("Yorliq olinmadi (%s): %s", oid, e)
            label = None

        # 2) Shu buyurtmaning mahsulot QR kodi
        qr = None
        shop_id = o.get("shop_id")
        if shop_id:
            sku_keys = [
                (str(it.get("barcode") or it.get("sku")), int(it.get("qty") or 1))
                for it in (o.get("items") or [])
                if it.get("barcode") or it.get("sku")
            ]
            if sku_keys:
                try:
                    qr, note = await uzum.get_product_qr(shop_id, sku_keys)
                    if note and note not in notes:
                        notes.append(note)
                except ApiError as e:
                    if str(e)[:60] not in notes:
                        notes.append(str(e)[:60])

        if label:
            parts.append(label)
            ok += 1
        else:
            fail += 1
        if qr:
            parts.append(qr)

    if not pdfmerge.available() and parts:
        notes.append(
            "pypdf o'rnatilmagan — birlashtirilmadi. "
            "Tuzatish: pip install -r requirements.txt"
        )

    # parts ham qaytariladi: birlashtirib bo'lmasa, chaqiruvchi
    # fayllarni birma-bir yuboradi. Ish YO'QOLMASLIGI kerak.
    return pdfmerge.merge(parts), parts, ok, fail, "\n".join(notes)
