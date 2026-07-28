-- ============================================================
--  BiziToys ichki bot — ma'lumotlar bazasi sxemasi (SQLite)
-- ============================================================

-- Xodimlar va ularning huquqlari
CREATE TABLE IF NOT EXISTS employees (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'employee',  -- 'admin' | 'employee'
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Buyurtmalarni xodimlarga biriktirish va holatini kuzatish
CREATE TABLE IF NOT EXISTS order_assignments (
    order_id      TEXT PRIMARY KEY,          -- Uzum buyurtma raqami
    employee_id   INTEGER,                   -- kimga biriktirilgan (NULL = hali yo'q)
    local_status  TEXT NOT NULL DEFAULT 'new',
                  -- new | packing | packed | shipped | done | cancelled
    order_date    TEXT,                      -- YYYY-MM-DD
    shop_id       INTEGER,                   -- qaysi Uzum do'koni
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    reminded_at   TEXT,                      -- oxirgi eslatma yuborilgan vaqt
    notified_at   TEXT,                      -- yangi buyurtma xabari yuborilgan vaqt
    FOREIGN KEY (employee_id) REFERENCES employees(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_employee ON order_assignments(employee_id);
CREATE INDEX IF NOT EXISTS idx_orders_date     ON order_assignments(order_date);

-- Har bir harakat tarixi (kim, qachon, nima qildi)
CREATE TABLE IF NOT EXISTS task_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      TEXT,
    employee_id   INTEGER,
    action        TEXT NOT NULL,             -- masalan: 'status:packed'
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_log_created ON task_log(created_at);

-- Mahsulot bo'yicha minimal qoldiq chegarasi (admin sozlaydi)
CREATE TABLE IF NOT EXISTS stock_thresholds (
    sku           TEXT PRIMARY KEY,
    product_name  TEXT,
    min_qty       INTEGER NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Bir xil ogohlantirish takror yuborilmasligi uchun
CREATE TABLE IF NOT EXISTS stock_alerts_sent (
    sku           TEXT PRIMARY KEY,
    last_sent_at  TEXT NOT NULL
);

-- Umumiy sozlamalar (key-value)
CREATE TABLE IF NOT EXISTS settings_kv (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL
);

-- Davomat: kim bugun ishda
CREATE TABLE IF NOT EXISTS attendance (
    work_date     TEXT NOT NULL,             -- YYYY-MM-DD
    telegram_id   INTEGER NOT NULL,
    status        TEXT NOT NULL,             -- present | absent | late
    note          TEXT,
    marked_at     TEXT NOT NULL DEFAULT (datetime('now')),
    marked_by     INTEGER,                   -- o'zi yoki admin
    PRIMARY KEY (work_date, telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(work_date);
