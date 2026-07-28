# 🤖 BiziToys — ichki boshqaruv Telegram-boti

Uzum Market va Billz bilan integratsiyalashgan xodimlar uchun ichki bot.
Texnik topshiriqdagi (TZ) barcha 3 bosqich shu loyihada amalga oshirilgan.

**Texnologiyalar:** Python 3.11+ · aiogram 3 · SQLite · APScheduler · httpx

---

## ⚡ 5 daqiqada ishga tushirish (test rejimi)

API kalitlarsiz ham botni to'liq sinab ko'rish mumkin — `MOCK_MODE=true` bo'lganda
bot soxta buyurtma va ombor ma'lumotlari bilan ishlaydi.

```bash
# 1. Kutubxonalar
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Sozlamalar
cp .env.example .env
nano .env                          # BOT_TOKEN va ADMIN_IDS ni to'ldiring

# 3. Ishga tushirish
python main.py
```

**BOT_TOKEN** — Telegramda [@BotFather](https://t.me/BotFather) ga `/newbot` yozing.
**ADMIN_IDS** — botga `/id` yozing, u sizga raqamingizni aytadi.

Ishlayotganini tekshirish uchun:

```bash
python test_smoke.py    # butun logikani Telegramsiz sinaydi
```

---

## 🧭 Loyiha tuzilishi

```
bizitoys_bot/
├── main.py                  ← kirish nuqtasi
├── .env                     ← maxfiy kalitlar (Git'ga TUSHMAYDI!)
│
├── app/
│   ├── config.py            ← .env dan sozlamalarni o'qish
│   ├── scheduler.py         ← avtomatik hisobot va eslatmalar
│   │
│   ├── db/                  ← MA'LUMOT QATLAMI
│   │   ├── schema.sql       ← jadvallar
│   │   └── repo.py          ← barcha SQL shu yerda
│   │
│   ├── integrations/        ← TASHQI API QATLAMI
│   │   ├── base.py          ← umumiy HTTP klient (retry, timeout)
│   │   ├── uzum.py          ← Uzum Seller API
│   │   ├── billz.py         ← Billz API
│   │   └── mock_data.py     ← test uchun soxta ma'lumot
│   │
│   ├── services/            ← BIZNES-LOGIKA QATLAMI
│   │   ├── orders.py        ← buyurtmalarni birlashtirish, hisobot
│   │   └── stock.py         ← kam qoldiq, top sotuvlar
│   │
│   └── bot/                 ← TELEGRAM QATLAMI
│       ├── middlewares.py   ← ruxsat tekshiruvi
│       ├── keyboards.py     ← tugmalar
│       └── handlers/        ← buyruqlar
│
└── deploy/                  ← VPS uchun fayllar
```

### Nega shunday bo'lingan?

Bu **qatlamli arxitektura** (layered architecture) — professional loyihalarda
eng ko'p ishlatiladigan yondashuv. Har bir qatlam faqat bitta ish bilan shug'ullanadi:

| Qatlam | Vazifasi | Misol |
|---|---|---|
| `integrations/` | Tashqi API bilan gaplashish | Uzumdan JSON olish |
| `services/` | Ma'lumotni qayta ishlash | «7 tasi hali tayyor emas» |
| `bot/handlers/` | Foydalanuvchiga ko'rsatish | Chiroyli xabar + tugmalar |
| `db/` | Saqlash | Kim qaysi buyurtmani oldi |

**Foydasi:** Uzum API'si o'zgarsa, faqat `integrations/uzum.py` ni tuzatasiz —
qolgan 15 ta fayl umuman tegilmaydi. Bu **adapter pattern** deyiladi.

---

## 🔑 Uzum va Billz API'larini ulash

> ⚠️ **Muhim haqiqat:** Uzum Seller API'ning ochiq hujjati yo'q — endpoint yo'llari
> faqat sotuvchi kabinetida beriladi. Shuning uchun kodda joylar aniq belgilab
> qo'yilgan: hujjatni olgach, 5 daqiqada ulaysiz.

### 1-qadam — kalitlarni olish

| Xizmat | Qayerdan |
|---|---|
| Uzum | `seller.uzum.uz` → Sozlamalar → API kalitlar |
| Billz | Billz menejeringizga yozing: «API kirish va hujjat kerak» |

### 2-qadam — `.env` ni to'ldirish

```env
MOCK_MODE=false
UZUM_TOKEN=sizning_tokeningiz
BILLZ_SECRET_TOKEN=sizning_tokeningiz
```

### 3-qadam — endpoint'larni to'g'rilash

`app/integrations/uzum.py` faylining boshida:

```python
ENDPOINTS = {
    "orders": "/api/seller/v1/fbs/orders",   # ← hujjatdagi yo'lni yozing
    ...
}
```

Agar API javobidagi maydon nomlari boshqacha bo'lsa (`orderId` o'rniga `order_number`
kabi), `_normalize_order()` funksiyasida shuni moslashtiring. Butun loyihada
faqat shu ikki joy o'zgaradi.

### 4-qadam — tekshirish

Botda `/health` yozing — u ikkala API ham javob berayotganini aytadi.

---

## 💬 Buyruqlar

### Barcha xodimlar uchun

| Buyruq | Nima qiladi |
|---|---|
| `/orders` | Bugungi buyurtmalar + holat o'zgartirish tugmalari |
| `/report` | Qisqa hisobot: nechtasi tayyor, nechtasi yo'q |
| `/stock` | Ombor qoldig'i |
| `/low` | Qoldig'i kam mahsulotlar |
| `/top` yoki `/top 30` | Eng ko'p sotilganlar (7 yoki 30 kun) |
| `/id` | O'z Telegram ID'sini bilish |

Buyruq yozish shart emas — oddiy savol ham tushuniladi:
> «Bugungi buyurtmalar PVZga oborildimi?» → bot sanab javob beradi.

### Faqat admin uchun

| Buyruq | Nima qiladi |
|---|---|
| `/add_employee 123456789 Aziz Karimov` | Xodim qo'shish |
| `/add_admin 123456789 Ism` | Admin qo'shish |
| `/employees` | Ro'yxatni ko'rish |
| `/remove_employee 123456789` | Tizimdan chiqarish |
| `/set_min BT-1001 10` | Mahsulot uchun minimal qoldiq chegarasi |
| `/health` | Tizim va API holati |
| `/yesterday` yoki `/yesterday 2026-07-22` | Kechagi savdo hisoboti (test uchun) |
| `/yesterday_excel` yoki `/yesterday_excel 2026-07-22` | Excel formatida yuklab olish |
| `/yesterday_image` yoki `/yesterday_image 2026-07-22` | Rasm formatida yuklab olish |

---

## 🔐 Huquqlar qanday ishlaydi

`app/bot/middlewares.py` — har bir xabar handler'ga yetib borishidan **oldin**
shu yerdan o'tadi:

```
Xabar → AuthMiddleware → bazadan tekshiradi → ruxsat bormi?
                                              ├─ Yo'q → to'xtatiladi
                                              └─ Ha  → handler ishlaydi
```

- **Admin** — barcha buyurtmalar, barcha xodimlar, sozlamalar.
- **Xodim** — faqat o'ziga biriktirilgan buyurtmalar.
- **Ro'yxatda yo'q odam** — hech nima. Botga yozsa, faqat o'z ID'sini oladi.

Bu TZ'ning 2-bo'limidagi talab — va xavfsizlikning eng muhim qismi.

---

## ⏰ Avtomatik vazifalar

`.env` orqali sozlanadi:

| Vazifa | Sozlama | Standart |
|---|---|---|
| Kechagi savdo hisoboti | `DAILY_SALES_AT` | 08:00 |
| Ertalabki hisobot | `MORNING_REPORT_AT` | 09:30 |
| Kechqurungi hisobot | `EVENING_REPORT_AT` | 18:30 |
| Kechikkan vazifa tekshiruvi | `LATE_CHECK_EVERY_MIN` | har 60 daq. |
| Ombor tekshiruvi | `STOCK_CHECK_AT` | 10:00 |
| «Kechikdi» chegarasi | `LATE_AFTER_HOURS` | 4 soat |

Hisobot guruhga **va** adminlarga shaxsiy yuboriladi.
Kechikkan buyurtma bo'lsa — mas'ul xodimga shaxsiy eslatma boradi.

---

## 👥 Guruh chatida ishlatish

1. Botni guruhga qo'shing.
2. @BotFather → `/setprivacy` → botni tanlang → **Disable**.

Privacy o'chirilmasa, bot guruhda faqat `/buyruq@bot_username` ko'rinishidagi
xabarlarni ko'radi va tabiiy savollarga javob bera olmaydi.

3. Guruhda `/id` yozing → chiqqan **Chat ID** ni `.env` dagi `GROUP_CHAT_ID` ga qo'ying
   (manfiy son bo'ladi, masalan `-1001234567890`).

---

## 🚀 VPS'ga o'rnatish (24/7 ishlashi uchun)

TZ'da talab qilingan: server uzilsa, bot avtomatik qayta ishga tushishi kerak.

```bash
# Serverga fayllarni yuklang
scp -r bizitoys_bot root@SERVER_IP:/opt/

# Serverda
ssh root@SERVER_IP
cd /opt/bizitoys_bot
cp .env.example .env && nano .env      # kalitlarni to'ldiring
chmod 600 .env                         # faqat egasi o'qiy oladi
bash deploy/install.sh
```

Skript hamma narsani qiladi: Python, virtual muhit, kutubxonalar, systemd xizmati.

**Boshqarish:**

```bash
systemctl status bizitoys-bot      # holat
systemctl restart bizitoys-bot     # qayta ishga tushirish
journalctl -u bizitoys-bot -f      # loglarni jonli ko'rish
```

`Restart=always` tufayli bot qulasa — 10 soniyada o'zi ko'tariladi.
Server qayta yuklansa ham avtomatik ishga tushadi.

PM2'ni afzal ko'rsangiz: `deploy/ecosystem.config.js` tayyor.

**VPS tanlash:** eng arzon tarif yetarli (1 GB RAM). Timeweb, Beget yoki
DigitalOcean — oyiga ~$4–6.

---

## 🗺 TZ bosqichlari bo'yicha holat

| Bosqich | Talab | Holat |
|---|---|---|
| 1 | Uzum integratsiyasi + buyurtma holati + huquqlar | ✅ Kod tayyor, token kutilmoqda |
| 2 | Avtomatik hisobot + kechikish eslatmalari | ✅ Tayyor |
| 3 | Billz — top sotuv + kam qoldiq ogohlantirishi | ✅ Kod tayyor, token kutilmoqda |
| 4 | Sinov, xatolarni tuzatish | ⏳ `MOCK_MODE=true` bilan bugunoq boshlash mumkin |

### Keyingi qadamlar

1. `MOCK_MODE=true` bilan botni ishga tushiring, xodimlarni qo'shing, 2–3 kun sinang.
2. Shu bilan birga Uzum va Billz kalitlarini so'rang.
3. Kalitlar kelgach — endpoint'larni to'g'rilab, `MOCK_MODE=false` qiling.
4. VPS'ga ko'chiring.

---

## 🧠 Keyinchalik qo'shish mumkin

- **Excel hisobot** — `/export` buyrug'i oylik hisobotni `.xlsx` qilib yuboradi
- **Xodimlar reytingi** — `task_log` jadvalida hamma harakat saqlanadi, undan
  «kim tez ishlaydi» statistikasi chiqadi
- **PostgreSQL** — xodim 20 tadan oshsa, SQLite'dan ko'chish kerak bo'ladi
- **Web-dashboard** — bazadagi ma'lumotni brauzerda ko'rsatish

---

## ⚠️ Xavfsizlik qoidalari

1. `.env` faylini **hech qachon** GitHub'ga yuklamang (`.gitignore` da bor).
2. Token tasodifan ochilib qolsa — darrov @BotFather'da `/revoke` qiling.
3. VPS'da: `chmod 600 .env`.
4. `data/bot.db` — xodimlar ro'yxati va ish tarixi. Vaqti-vaqti bilan nusxa oling:
   ```bash
   sqlite3 data/bot.db ".backup data/backup-$(date +%F).db"
   ```
