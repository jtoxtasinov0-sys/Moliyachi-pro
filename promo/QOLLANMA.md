# Moliyachi Pro — to'liq qo'llanma

Moliyachi — Telegram bot va ilova (Mini App). Tikuv sexi hisobini (ishchilar, modellar, ish haqi, mijozlar)
va egasining shaxsiy moliyasini (xarajat, daromad, qarz) yuritadi. Hamma narsa **ovoz, matn yoki rasm**
bilan kiritiladi: bot gapni o'zi tushunadi, «To'g'rimi?» deb so'raydi, tasdiqlagach hisobga qo'shadi.

---

## 1. Rollar

| Kim | Nima qila oladi |
|---|---|
| **Ega** (`OWNER_TG_ID`) | Hamma narsa: ishchilar, modellar va narxlar, tasdiqlash, hisobotlar, mijozlar, xarajat/qarz, sozlamalar |
| **Ishchi** (taklif havolasi bilan ulangan) | Faqat o'z ishini kiritadi, o'z yozuvlarini tahrirlaydi/o'chiradi, faqat **o'z** ish haqini ko'radi |

Har bir ega — alohida kabinet. Bir ega boshqasining ma'lumotini ko'rmaydi.

---

## 2. Boshlang'ich sozlash (bir marta)

### 1-qadam. Modellar va narx
`🧵 Modellar` → `➕ Model qo'shish` → nomini yozing (`A-12`) → **bir dona uchun ish haqini** yozing (`5000`).

- Valyuta yozilmasa — asosiy valyuta (so'm). `2 $` yoki `3000 won` ham bo'ladi.
- «Narxsiz qoldirish» mumkin — keyin `💰 A-12` tugmasi bilan narx qo'yasiz.
- Yangi model ovozdan ham qo'shiladi: bot tanimagan model nomini eshitsa, `➕ Yangi: D-3` tugmasini taklif qiladi.

### 2-qadam. Ishchilarni ulash
`👷 Ishchilar` → `➕ Ishchi qo'shish` → ismini yozing (`Alisher`). Bot **bir martalik havola** beradi —
uni ishchiga Telegram'da yuboring. Ishchi havolani bosishi bilan ulanadi, sizga «👷 Alisher botga ulandi» xabari keladi.

- Holatlar: `✅ ulangan`, `🔗 havola kutilmoqda` (yangi havola olish mumkin), `⛔ o'chirilgan`.
- `⛔ O'chirish` — ishchi endi botdan foydalana olmaydi (eski yozuvlari hisobotda qoladi).

### Sozlamalar (`⚙️ Sozlamalar`)
- **Ishchi yozuvlarini tasdiqlash** — sukut bo'yicha *yoqilgan*: ishchi kiritgan ish egasi tasdiqlaguncha hisobotga tushmaydi.
- **Valyutalar** — so'm, $, won; ⭐ — asosiy valyuta.
- **Rasmdagi xarajatlarni darhol saqlash**, **kundalik eslatma vaqti** (19:00–22:00 yoki o'chiq).

---

## 3. Hisobot (ish) qanday kiritiladi

### Uch usul
1. **🎙 Ovoz** — mikrofonni bosib turib gapiring: *«Alisher A-12 modeldan 50 ta, B-5 dan 30 ta topshirdi»*.
2. **⌨️ Matn** — xuddi shunday yozing.
3. **📷 Rasm** — daftar sahifasi rasmini yuboring (bir martada 5 tagacha).

Ishchining o'zi yozsa ism shart emas: *«A-12 modeldan 50 ta topshirdim»*.

### Misollar
- `Alisher A-12 modeldan 50 ta topshirdi`
- `Kecha Vali B-5 dan 30 ta, C-7 dan 20 ta` — bir xabarda bir nechta yozuv
- `O'tgan juma Dilnoza C-7 dan 45 ta` — sana ham tushuniladi

### «To'g'rimi?» kartochkasi
Bot gapni tushunib qoralama ko'rsatadi:

```
📝 Ish topshirish
🗣 «Alisher A-12 modeldan 50 ta, B-5 dan 30 ta topshirdi»
📅 Sana: 14.10.2026 (bugun)
👷 Ishchi: Alisher
• A-12 — 50 ta × 5 000 so'm
• B-5 — 30 ta × 4 000 so'm
To'g'rimi?
[✅ Saqlash]  [✏️ Tuzatish] [❌ Bekor]  [📅 Bugun] [📅 Kecha]
```

- Xato bo'lsa — shunchaki tuzatishni ayting: *«soni 45 ta»*, *«kecha edi»*, *«Vali emas, Alisher»*.
- Ism yoki model noaniq eshitilsa (*«alishir»*) — bot variantlarni tugma qilib ko'rsatadi; tanlaganingizni **eslab qoladi**, keyingi safar o'zi to'g'rilaydi.
- Son aytilmasa — «A-12 dan nechta?» deb so'raydi.

### Tasdiqlash (ishchi kiritganda)
1. Ishchi saqlaydi → unga «⏳ Egasining tasdig'ini kutmoqda».
2. Egaga keladi: «🔔 Yangi yozuv tasdiq kutmoqda» + `✅ Tasdiqlash` / `🚫 Rad etish`.
3. Natija ishchiga yuboriladi: «Yozuvingiz ✅ tasdiqlandi» yoki «🚫 rad etildi».
4. Hammasi bir joyda: `⏳ Tasdiq kutayotganlar`.

Ishchi yozuvini tahrirlasa — u yana tasdiqqa tushadi. Tasdiqlangan yozuvni o'chirsa — egaga xabar boradi.

### Tahrirlash va o'chirish
`🕘 Oxirgi yozuvlar` → yozuv ostida `✏️ Tahrirlash` (ovoz/matn bilan ayting: *«soni 45 ta»*) yoki `🗑 O'chirish`.

---

## 4. Ishchilarning ish haqi qanday hisoblanadi

**Formula:**

> **Ish haqi = Soni × 1 dona narxi** (model narxi), keyin ishchi bo'yicha jamlanadi.

**Misol — Alisher, bugun:**

| Model | Soni | 1 dona narxi | Summa |
|---|---|---|---|
| A-12 | 50 | 5 000 | 250 000 |
| B-5 | 30 | 4 000 | 120 000 |
| **Jami** | **80 ta** | | **370 000 so'm** |

**Qoidalar:**
- **Faqat tasdiqlangan** yozuvlar hisoblanadi. Tasdiq kutayotganlar hisobot ostida alohida ko'rsatiladi: «⏳ … (hisobotga kirmagan)».
- **Narx yozuv saqlangan paytdagisi bo'yicha** qoladi. Model narxi keyin o'zgarsa ham eski hisob buzilmaydi (yozuvdagi model almashtirilsa — yangi model narxi olinadi).
- Narxi yo'q model uchun dona soni hisoblanadi, summa chiqmaydi.
- Turli valyutadagi modellar alohida qo'shiladi: `370 000 so'm + 20 $`.
- O'chirilgan yozuvlar hisobga kirmaydi.

---

## 5. Hisobotni ko'rish

`📊 Hisobot` → `Bugun` · `Shu hafta` (dushanba–yakshanba) · `Shu oy` (1-sanadan oy oxirigacha).

**Ega ko'radi** — har bir ishchi, modellar bo'yicha jami va ish haqi fondi:

```
📊 Shu oy (01.10 – 31.10)

👷 Alisher — 80 ta
   A-12: 50, B-5: 30
   💰 370 000 so'm
👷 Dilnoza — 95 ta
   B-5: 50, C-7: 45
   💰 470 000 so'm
👷 Vali — 100 ta
   A-12: 40, C-7: 60
   💰 560 000 so'm

🧵 Modellar bo'yicha
• A-12 — 90 ta  • B-5 — 80 ta  • C-7 — 105 ta

Jami: 275 ta
Ish haqi fondi: 1 400 000 so'm
```

**Ishchi ko'radi** — faqat o'zini: modellar, jami dona va o'z ish haqi.

`📱 Ilovani ochish` — xuddi shu ma'lumot grafiklar bilan (kunlar, ishchilar, modellar kesimida).

---

## 6. Qo'shimcha bo'limlar (ega uchun)

| Bo'lim | Qanday kiritiladi | Nima hisoblaydi |
|---|---|---|
| 🛍 **Mijozlar** | *«Tohirga 10 komplekt maksima berdim, 140 mingdan»*, *«Tohir 2 million to'ladi»*, *«Tohir 3 komplekt qaytardi»* | Komplekt × narx (1 komplekt = 5 dona, sozlanadi); qoldiq = berilgan mol − to'lov − qaytgan mol |
| 💸 **Xarajatlar** | *«Benzinga 200 ming»*, *«oylik tushdi 10 million»*, chek/bank skrinshoti | Kategoriyalar bo'yicha; rasmdagi takroriy yozuv qayta qo'shilmaydi |
| 🤝 **Qarzlar** | *«Abbosdan 100 ming won oldim»*, *«Valiga 50 dollar berdim»*, *«Hayrulloda 65 million haqqim bor»* | Har bir odam bo'yicha: kim kimga qancha qarz |
| 💡 **Maslahat** | Avtomatik | 50/30/20 reja, oy oxirigacha kunlik limit, 34 ta saboq, 🤖 shaxsiy maslahat |
| 🔔 **Eslatma** | Avtomatik | Har kuni kechqurun (sukut 21:00) «bugungi pulingizni kiriting» |

---

## 7. Qisqa eslatma

1. Modellarga **bir dona narxi**ni qo'ying.
2. Ishchilarni **havola** orqali ulang.
3. Ishni **ovoz / matn / rasm** bilan kiriting → «To'g'rimi?» → `✅ Saqlash`.
4. Ishchi kiritganini **tasdiqlang**.
5. `📊 Hisobot` → ish haqi **o'zi hisoblangan**.
