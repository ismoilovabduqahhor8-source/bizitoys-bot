# 📊 Kechagi Savdo Hisoboti - Sozlash va Tekshirish

## ✅ Nima qo'shildi:

1. **Avtomatik hisobot** - har kuni soat 08:00 da kechagi savdo hisoboti yuboriladi
2. **Qo'lda test buyrug'i** - `/yesterday` buyrug'i adminlar uchun

---

## 🔧 SOZLASH:

### 1. `.env` faylida vaqtni tekshiring:

```env
DAILY_SALES_AT=08:00
```

Bu vaqt Toshkent vaqti bo'yicha (Asia/Tashkent).

---

## 🧪 TEST QILISH:

### 1-qadam: O'zingiz admin ekanligingizni tekshiring

Botga `/employees` buyrug'ini yuboring va o'z ID'ngiz yonida **"Admin"** yozuvini ko'ring.

Agar admin bo'lmasangiz:
```
/add_admin 7155596109 Sizning ismingiz
```

### 2-qadam: `/yesterday` buyrug'ini sinang

**Oddiy ko'rinish:**
```
/yesterday
```
→ Kechagi savdo hisobotini ko'rasiz

**Muayyan sana:**
```
/yesterday 2026-07-22
```
→ 22-iyul kunidagi savdo hisobotini ko'rasiz

---

## ❌ Agar buyruq ishlamasa:

### Variant 1: Admin emasman
Botga `/id` yozing va ID'ngizni oling, keyin:
```
/add_admin SIZNING_ID_NI Sizning ismingiz
```

### Variant 2: Bot qayta ishga tushirilmagan
Botni to'xtating va qayta ishga tushiring:
```bash
# Terminal'da:
Ctrl+C  # botni to'xtatish
python main.py  # qayta ishga tushirish
```

### Variant 3: Xato chiqayapti
Botning loglarini ko'ring - terminal'da xato xabarlari chiqishi kerak.

---

## 📝 XABAR FORMATI:

```
💰 22-Jul-2026 - Kunlik savdo hisoboti

📦 Jami buyurtmalar: 15 ta
💵 Jami summa: 3 450 000 so'm

🏪 Do'konlar bo'yicha:
   • SENSOR o'yinchoqlar
      8 ta · 1 800 000 so'm
   • BiziToys Premium  
      7 ta · 1 650 000 so'm
```

---

## ⏰ AVTOMATIK YUBORILISH:

Har kuni soat **08:00** da bu hisobot avtomatik yuboriladi:
- ✅ Guruh chatiga (`GROUP_CHAT_ID`)
- ✅ Barcha adminlarga shaxsiy

---

## 🔍 TEKSHIRISH:

### Loglarni ko'rish:

Bot ishga tushganda quyidagi qatorni ko'rishingiz kerak:

```
Scheduler ishga tushdi (Asia/Tashkent):
  • kechagi savdo hisoboti:   08:00
  • yangi buyurtma tekshiruvi: har 5 daqiqada
  ...
```

Agar ko'rmaysiz - bot qayta ishga tushirilmagan.

---

## 💡 MASLAHATLAR:

1. **Test vaqti:** Hozir soat 08:00 bo'lmasa, test uchun `/yesterday` ishlatying
2. **Vaqtni o'zgartirish:** `.env` da `DAILY_SALES_AT=09:00` ga o'zgartiring
3. **Mock rejimda:** Agar `MOCK_MODE=true` bo'lsa, soxta ma'lumot keladi

---

## 🆘 YORDAM KERAKMI?

Agar muammo bo'lsa:
1. Bot terminalidagi loglarni ko'ring
2. `/health` buyrug'ini yuboring - tizim holati
3. `/employees` - o'zingiz admin ekanligingizni tekshiring
4. Botni qayta ishga tushiring

---

**Yaratildi:** 23-iyul-2026  
**Versiya:** BiziToys Bot v1.0+
