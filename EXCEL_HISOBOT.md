# 📊 Excel Hisobot - To'liq Yo'riqnoma

## ✅ QILGAN ISHLAR:

### 1️⃣ Do'konlar muammosi hal qilindi
`.env` faylida barcha 5 ta do'kon ID'lari qo'shildi:
```env
UZUM_SHOP_IDS=77165,77419,79873,97583,123074
```

### 2️⃣ Excel formatida hisobot qo'shildi
Endi hisobotni:
- ✅ Telegram xabarida ko'rish
- ✅ Excel faylda yuklab olish

---

## 📥 O'RNATISH:

### 1. Kutubxonalarni o'rnatish:
```bash
cd c:\Users\Абдукаххор\Downloads\files\bizitoys_bot\bizitoys_bot
pip install openpyxl pillow
```

Yoki:
```bash
pip install -r requirements.txt
```

### 2. Botni qayta ishga tushirish:
```bash
Ctrl + C
python main.py
```

---

## 💻 BUYRUQLAR:

### Matn formatida:
```
/yesterday              ← kechagi savdo (matn)
/yesterday 2026-07-20   ← o'sha kun (matn)
```

### Excel formatida:
```
/yesterday_excel              ← kechagi savdo (Excel)
/yesterday_excel 2026-07-20   ← o'sha kun (Excel)
```

---

## 📊 EXCEL FAYLIDA NIMA BOR:

### ✨ To'liq batafsil jadval:

1. **Umumiy ma'lumot:**
   - Jami buyurtmalar
   - Jami mahsulotlar soni
   - Jami summa

2. **Do'konlar bo'yicha:**
   - Har bir do'kon nomi
   - Buyurtmalar soni
   - Mahsulotlar soni
   - Jami summa

3. **Mahsulotlar (batafsil):**
   - Do'kon nomi
   - Mahsulot nomi
   - SKU kodi
   - Soni
   - Narxi
   - Jami summa
   - ROI % (hozircha 0)

4. **Yakun:**
   - Umumiy jami

### 🎨 Dizayn:

- ✅ Rangli sarlavhalar
- ✅ Chegara chiziqlari
- ✅ To'g'ri formatlangan raqamlar
- ✅ Keng ustunlar (matn to'liq ko'rinadi)
- ✅ Do'konlar bo'yicha guruhlash

---

## 🚀 TEST QILISH:

### 1-qadam: Botga yuboring
```
/yesterday_excel
```

### 2-qadam: Faylni yuklab oling
Bot sizga `.xlsx` fayl yuboradi.

### 3-qadam: Excel'da oching
- Windows: Microsoft Excel
- Mac: Numbers yoki Excel
- Linux: LibreOffice Calc

---

## 🔍 MUAMMOLAR VA YECHIMLAR:

### ❌ "Excel yaratish uchun kutubxona o'rnatilmagan"

**Yechim:**
```bash
pip install openpyxl
```

### ❌ Faqat 1-2 ta do'kon ko'rinayapti

**Yechim:**  
`.env` faylida `UZUM_SHOP_IDS` ni tekshiring:
```env
UZUM_SHOP_IDS=77165,77419,79873,97583,123074
```

Botni qayta ishga tushiring!

### ❌ Excel faylda xato

**Yechim:**  
Terminal loglarini ko'ring - xato haqida ma'lumot chiqadi.

---

## 📱 AVTOMATIK YUBORISH:

Har kuni soat **08:00** da:
- ✅ Matn formatida (Telegram'da)
- ❌ Excel yo'q (qo'lda yuklash kerak)

Agar Excel ham avtomatik kerak bo'lsa, aytasiz - qo'shamiz!

---

## 💡 QO'SHIMCHA IMKONIYATLAR:

### 1. Haftalik hisobot:
```
/week_excel   ← haftalik savdo Excel'da
```

### 2. Oylik hisobot:
```
/month_excel   ← oylik savdo Excel'da
```

### 3. Muayyan oraliq:
```
/report_excel 2026-07-01 2026-07-31
```

Kerak bo'lsa - qo'shamiz! 🚀

---

## 🎯 FOYDASI:

**Oldin:**
- Telegram'da faqat matn
- Faqat 1-2 ta do'kon
- Nusxa olish qiyin

**Hozir:**
- ✅ Barcha 5 ta do'kon
- ✅ Excel formatida
- ✅ To'liq batafsil jadval
- ✅ Ranglar va formatlash
- ✅ Yuklab olish mumkin
- ✅ Hisobotlarda ishlatish oson

---

**Yaratildi:** 23-iyul-2026  
**Versiya:** BiziToys Bot v2.1  
**Kutubxonalar:** openpyxl 3.1+, Pillow 10.0+
