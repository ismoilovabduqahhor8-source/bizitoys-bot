"""
Ma'lumotlar bazasi bilan ishlash qatlami (repository).
Handler'lar SQL yozmaydi — faqat shu yerdagi funksiyalarni chaqiradi.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from app.config import settings

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

ROLE_ADMIN = "admin"
ROLE_SKLAD = "sklad"          # skladdan tovar chiqaradi
ROLE_PICKER = "yiguvchi"      # tekshiradi, yig'adi, QR yopishtiradi
ROLE_DRIVER = "haydovchi"     # PVZga eltadi
ROLE_EMPLOYEE = "employee"    # umumiy

ROLE_LABELS = {
    ROLE_ADMIN: "👑 Admin",
    ROLE_SKLAD: "🏬 Sklad",
    ROLE_PICKER: "📦 Yig'uvchi",
    ROLE_DRIVER: "🚚 Haydovchi",
    ROLE_EMPLOYEE: "👤 Xodim",
}

ATT_PRESENT = "present"
ATT_ABSENT = "absent"
ATT_LATE = "late"

ATT_LABELS = {
    ATT_PRESENT: "✅ Ishda",
    ATT_ABSENT: "❌ Yo'q",
    ATT_LATE: "🕐 Kechikdi",
}

STATUS_FLOW = ["new", "packing", "packed", "shipped", "done"]
STATUS_LABELS = {
    "new": "🆕 Yangi",
    "packing": "📦 Yig'ilmoqda",
    "packed": "✅ Tayyorlandi",
    "shipped": "🚚 PVZga jo'natildi",
    "done": "🎉 Yetkazildi",
    "cancelled": "❌ Bekor qilindi",
}


async def init_db() -> None:
    """Baza faylini va jadvallarni yaratadi, adminlarni ro'yxatga qo'shadi."""
    db_file = Path(settings.db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(settings.db_path) as db:
        await db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Yengil migratsiya: eski bazalarga yangi ustunni qo'shamiz
        async with db.execute("PRAGMA table_info(order_assignments)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        for col, ddl in (("shop_id", "INTEGER"), ("notified_at", "TEXT")):
            if col not in cols:
                await db.execute(f"ALTER TABLE order_assignments ADD COLUMN {col} {ddl}")
                log.info("Bazaga %s ustuni qo'shildi", col)
        await db.commit()

    # .env dagi ADMIN_IDS avtomatik admin bo'ladi
    for admin_id in settings.admin_ids:
        await upsert_employee(admin_id, None, f"Admin {admin_id}", ROLE_ADMIN)

    log.info("Baza tayyor: %s", settings.db_path)


async def _conn() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    return db


# ------------------------------------------------------------------
#  XODIMLAR
# ------------------------------------------------------------------
async def upsert_employee(
    telegram_id: int, username: Optional[str], full_name: str, role: str = ROLE_EMPLOYEE
) -> None:
    db = await _conn()
    try:
        await db.execute(
            """
            INSERT INTO employees (telegram_id, username, full_name, role, is_active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username  = COALESCE(excluded.username, employees.username),
                full_name = excluded.full_name,
                role      = excluded.role,
                is_active = 1
            """,
            (telegram_id, username, full_name, role),
        )
        await db.commit()
    finally:
        await db.close()


async def get_employee(telegram_id: int) -> Optional[dict[str, Any]]:
    db = await _conn()
    try:
        async with db.execute(
            "SELECT * FROM employees WHERE telegram_id = ? AND is_active = 1", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    finally:
        await db.close()


async def list_employees(only_active: bool = True) -> list[dict[str, Any]]:
    db = await _conn()
    try:
        sql = "SELECT * FROM employees"
        if only_active:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY role DESC, full_name"
        async with db.execute(sql) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def deactivate_employee(telegram_id: int) -> bool:
    db = await _conn()
    try:
        cur = await db.execute(
            "UPDATE employees SET is_active = 0 WHERE telegram_id = ?", (telegram_id,)
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


# ------------------------------------------------------------------
#  BUYURTMALAR
# ------------------------------------------------------------------
async def ensure_order(order_id: str, order_date: str, shop_id: int | None = None) -> None:
    """Uzumdan kelgan yangi buyurtmani bazaga qo'shadi (bor bo'lsa — tegmaydi)."""
    db = await _conn()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO order_assignments (order_id, order_date, shop_id)"
            " VALUES (?, ?, ?)",
            (order_id, order_date, shop_id),
        )
        await db.commit()
    finally:
        await db.close()


async def assign_order(order_id: str, employee_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            """
            UPDATE order_assignments
               SET employee_id = ?, updated_at = datetime('now')
             WHERE order_id = ?
            """,
            (employee_id, order_id),
        )
        await db.commit()
    finally:
        await db.close()


async def set_local_status(order_id: str, status: str, employee_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            """
            UPDATE order_assignments
               SET local_status = ?, updated_at = datetime('now'), reminded_at = NULL
             WHERE order_id = ?
            """,
            (status, order_id),
        )
        await db.execute(
            "INSERT INTO task_log (order_id, employee_id, action) VALUES (?, ?, ?)",
            (order_id, employee_id, f"status:{status}"),
        )
        await db.commit()
    finally:
        await db.close()


async def get_order(order_id: str) -> Optional[dict[str, Any]]:
    db = await _conn()
    try:
        async with db.execute(
            "SELECT * FROM order_assignments WHERE order_id = ?", (order_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    finally:
        await db.close()


async def orders_by_date(
    order_date: str, employee_id: Optional[int] = None
) -> list[dict[str, Any]]:
    db = await _conn()
    try:
        sql = "SELECT * FROM order_assignments WHERE order_date = ?"
        params: list[Any] = [order_date]
        if employee_id is not None:
            sql += " AND employee_id = ?"
            params.append(employee_id)
        sql += " ORDER BY created_at"
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def stale_orders(hours: int) -> list[dict[str, Any]]:
    """Belgilangan soatdan ko'p vaqtdan beri qotib qolgan buyurtmalar."""
    db = await _conn()
    try:
        async with db.execute(
            """
            SELECT * FROM order_assignments
             WHERE local_status IN ('new', 'packing', 'packed')
               AND updated_at <= datetime('now', ?)
               AND (reminded_at IS NULL OR reminded_at <= datetime('now', '-3 hours'))
             ORDER BY updated_at
            """,
            (f"-{hours} hours",),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def mark_reminded(order_id: str) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE order_assignments SET reminded_at = datetime('now') WHERE order_id = ?",
            (order_id,),
        )
        await db.commit()
    finally:
        await db.close()


# ------------------------------------------------------------------
#  OMBOR CHEGARALARI
# ------------------------------------------------------------------
async def set_threshold(sku: str, min_qty: int, product_name: str | None = None) -> None:
    db = await _conn()
    try:
        await db.execute(
            """
            INSERT INTO stock_thresholds (sku, product_name, min_qty)
            VALUES (?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                min_qty      = excluded.min_qty,
                product_name = COALESCE(excluded.product_name, stock_thresholds.product_name),
                updated_at   = datetime('now')
            """,
            (sku, product_name, min_qty),
        )
        await db.commit()
    finally:
        await db.close()


async def all_thresholds() -> dict[str, int]:
    db = await _conn()
    try:
        async with db.execute("SELECT sku, min_qty FROM stock_thresholds") as cur:
            return {r["sku"]: r["min_qty"] for r in await cur.fetchall()}
    finally:
        await db.close()


async def should_alert(sku: str, cooldown_hours: int = 24) -> bool:
    """Bir mahsulot bo'yicha kuniga bir marta ogohlantirish yuborish uchun."""
    db = await _conn()
    try:
        async with db.execute(
            "SELECT last_sent_at FROM stock_alerts_sent WHERE sku = ?"
            " AND last_sent_at > datetime('now', ?)",
            (sku, f"-{cooldown_hours} hours"),
        ) as cur:
            if await cur.fetchone():
                return False
        await db.execute(
            "INSERT INTO stock_alerts_sent (sku, last_sent_at) VALUES (?, datetime('now'))"
            " ON CONFLICT(sku) DO UPDATE SET last_sent_at = datetime('now')",
            (sku,),
        )
        await db.commit()
        return True
    finally:
        await db.close()


# ------------------------------------------------------------------
#  KEY-VALUE SOZLAMALAR
# ------------------------------------------------------------------
async def kv_get(key: str, default: str | None = None) -> str | None:
    db = await _conn()
    try:
        async with db.execute("SELECT value FROM settings_kv WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row["value"] if row else default
    finally:
        await db.close()


async def kv_set(key: str, value: str) -> None:
    db = await _conn()
    try:
        await db.execute(
            "INSERT INTO settings_kv (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()
    finally:
        await db.close()


# ------------------------------------------------------------------
#  YANGI BUYURTMA XABARLARI
# ------------------------------------------------------------------
async def is_notified(order_id: str) -> bool:
    """Bu buyurtma haqida allaqachon xabar yuborilganmi?"""
    db = await _conn()
    try:
        async with db.execute(
            "SELECT notified_at FROM order_assignments WHERE order_id = ?", (order_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row["notified_at"])
    finally:
        await db.close()


async def mark_notified(order_id: str) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE order_assignments SET notified_at = datetime('now') WHERE order_id = ?",
            (order_id,),
        )
        await db.commit()
    finally:
        await db.close()


# ------------------------------------------------------------------
#  DAVOMAT
# ------------------------------------------------------------------
def today_str() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(settings.timezone)).date().isoformat()


async def mark_attendance(
    telegram_id: int, status: str, marked_by: int | None = None,
    note: str | None = None, work_date: str | None = None,
) -> None:
    work_date = work_date or today_str()
    db = await _conn()
    try:
        await db.execute(
            """
            INSERT INTO attendance (work_date, telegram_id, status, note, marked_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(work_date, telegram_id) DO UPDATE SET
                status    = excluded.status,
                note      = excluded.note,
                marked_at = datetime('now'),
                marked_by = excluded.marked_by
            """,
            (work_date, telegram_id, status, note, marked_by),
        )
        await db.commit()
    finally:
        await db.close()


async def get_attendance(work_date: str | None = None) -> dict[int, dict[str, Any]]:
    """Bugungi davomat: {telegram_id: {...}}"""
    work_date = work_date or today_str()
    db = await _conn()
    try:
        async with db.execute(
            "SELECT * FROM attendance WHERE work_date = ?", (work_date,)
        ) as cur:
            return {r["telegram_id"]: dict(r) for r in await cur.fetchall()}
    finally:
        await db.close()


async def is_present(telegram_id: int, work_date: str | None = None) -> bool:
    att = await get_attendance(work_date)
    row = att.get(telegram_id)
    return bool(row and row["status"] in (ATT_PRESENT, ATT_LATE))


async def employees_by_role(role: str, only_present: bool = False) -> list[dict[str, Any]]:
    """Rol bo'yicha xodimlar. only_present=True bo'lsa faqat bugun ishdagilari."""
    people = [p for p in await list_employees() if p["role"] == role]
    if not only_present:
        return people
    att = await get_attendance()
    return [
        p for p in people
        if (att.get(p["telegram_id"]) or {}).get("status") in (ATT_PRESENT, ATT_LATE)
    ]


# ------------------------------------------------------------------
#  FBO YUK XATLARI — qabul qilinganini bir marta xabar berish uchun
# ------------------------------------------------------------------
async def get_fbo_invoice_status(invoice_id: str) -> str | None:
    """Oxirgi ma'lum status. Birinchi tekshiruvda None qaytadi."""
    db = await _conn()
    try:
        async with db.execute(
            "SELECT status_value FROM fbo_invoice_state WHERE invoice_id = ?",
            (str(invoice_id),),
        ) as cur:
            row = await cur.fetchone()
            return row["status_value"] if row else None
    finally:
        await db.close()


async def was_fbo_notified(invoice_id: str) -> bool:
    db = await _conn()
    try:
        async with db.execute(
            "SELECT notified FROM fbo_invoice_state WHERE invoice_id = ?",
            (str(invoice_id),),
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row["notified"])
    finally:
        await db.close()


async def set_fbo_invoice_state(
    invoice_id: str, shop_id: int, status_value: str, notified: bool = False
) -> None:
    db = await _conn()
    try:
        await db.execute(
            """INSERT INTO fbo_invoice_state (invoice_id, shop_id, status_value, notified, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(invoice_id) DO UPDATE SET
                   status_value = excluded.status_value,
                   notified = excluded.notified,
                   updated_at = excluded.updated_at""",
            (str(invoice_id), shop_id, status_value, int(notified)),
        )
        await db.commit()
    finally:
        await db.close()


# ------------------------------------------------------------------
#  MAHSULOT HOLATI — bloklanish/pullik saqlash/kam qoldiq o'zgarishini
#  kuzatish uchun (bir marta xabar berish).
# ------------------------------------------------------------------
async def get_sku_state(sku_id: str) -> dict[str, int] | None:
    db = await _conn()
    try:
        async with db.execute(
            "SELECT * FROM sku_state WHERE sku_id = ?", (str(sku_id),)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    finally:
        await db.close()


async def set_sku_state(
    sku_id: str, blocked: bool, paid_storage: bool, low_stock: bool
) -> None:
    db = await _conn()
    try:
        await db.execute(
            """INSERT INTO sku_state (sku_id, blocked, paid_storage, low_stock, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(sku_id) DO UPDATE SET
                   blocked = excluded.blocked,
                   paid_storage = excluded.paid_storage,
                   low_stock = excluded.low_stock,
                   updated_at = excluded.updated_at""",
            (str(sku_id), int(blocked), int(paid_storage), int(low_stock)),
        )
        await db.commit()
    finally:
        await db.close()
