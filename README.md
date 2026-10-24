# Moliyachi bot

Ovozli/matnli xabardan moliyaviy hisob yurituvchi Telegram bot + grafikali Mini App.

**Holat:** bot, ovoz → matn, tasdiqlash, ishchilar bo'limi, mijozlar (optom savdo: komplekt bilan mol
berish, to'lovlar, qoldiq), shaxsiy xarajat/daromad va qarzlar, Mini App (hisobot grafiklari, «Mijozlar»,
«Hamyon», Telegramdagidek bosib turib yoziladigan ovozli xabar). Brauzerda ochish uchun botga `/web` yozing.

**🏷 Kasblar va narxlar:** har bir ishchiga kasb beriladi — Kroychi (materialni kesadi), Brsovkachi (kesilganini
tikishga tayyorlab shiladi), Tikuvchi, Tortuvchi (padoshni yopishtiradi), Pardozchi (sotuvga tayyorlaydi) yoki egasi
qo'lda qo'shgan boshqa kasb («👷 Ishchilar → 🏷 Kasblar», ilovada «Jamoa»). Har bir modelga kasb bo'yicha bir dona
narxi qo'yiladi: «🧵 Modellar → 💰 A-12» da tugma bilan yoki «brsovka 2000, kroy 1500» deb yozib, ilovada model
oynasida yoki istalgan joyda ovoz/matn bilan: «A-12 modelga brsovka 2000dan». Ish topshirilganda kasb aytilsa
(«A-12 dan 40 ta brsovka qildim») — o'sha kasb narxi, aytilmasa — ishchining o'z kasbi narxi olinadi; kasb narxi
yozilmagan bo'lsa — kasbning o'z narxi, u ham bo'lmasa — modelning umumiy narxi. Hisobotda har bir model qaysi
bosqichdan (kroy, brsovka, tikuv...) qancha o'tgani ko'rinadi.

**✂️ Kroy va brsovka — modellar narxi yoki modelsiz:** har bir kasbning o'z narxlari bor — tikuvchi va kroychining
narxi alohida. Kasbga umumiy narx (hamma modellar uchun): «kroy 2000dan», botda «👷 Ishchilar → 🏷 Kasblar → Kroychi →
💰 Umumiy narx». Modellar bo'yicha narx (Loafer kroyi — 1500, Sapog kroyi — 2500, Loafer tikuvi esa — 6000): botda
«🏷 Kasblar → Kroychi → 🧵 Modellar narxi» (modelni bosib son yozasiz yoki bir yo'la «Loafer 1500, Sapog 2500»),
ovoz bilan «Loafer kroy 1500dan», ilovada «Jamoa → kasb» oynasida har bir model qatori yoki model oynasida.
Kroychi «10 ta kroy qildim» desa: modellarga Kroychi narxi qo'yilgan bo'lsa — narxi bilan variantlar chiqadi
(«Loafer · 1 500 so'm», «Sapog BG · 2 500 so'm», «✂️ Modelsiz · 2 000 so'm»), tanlangani o'z narxida hisoblanadi;
qo'yilmagan bo'lsa — model so'ralmay umumiy narx bilan darhol yoziladi (50 × 2000 = 100 000 so'm). Bu — kasb
kartasidagi «✂️ Modelsiz ham bo'ladi» (Kroychi va Brsovkachida sukut bo'yicha yoqiq); o'chirilsa — har doim model
so'raladi, Tikuvchi va Tortuvchidagidek. Narx tartibi: modelning shu kasb narxi → kasbning umumiy narxi → modelning
umumiy narxi. Narx yozilmagan bo'lsa ham ish saqlanadi va egasiga eslatma chiqadi. Hisobotda modelsiz ishlar
«Modelsiz» bo'lib ko'rinadi.

**⚡ Darhol saqlash:** ovoz yoki matn to'liq tushunilsa (ishchi, model, mijoz topildi, soni yoki summasi aytildi)
bot «To'g'rimi?» deb so'ramay darhol saqlaydi va nima saqlanganini ko'rsatadi — xato bo'lsa, kartadagi «✏️ Tahrirlash»
yoki «🗑 O'chirish». Masalan, Robiyaning 1 mln so'm haqqi bo'lsa va «Robiyaga 500 ming berdim» deyilsa — Robiya
topiladi, qoldig'idan 500 ming ayriladi (500 ming qoladi), egasi ham, Robiya ham xabar oladi. «Robiya 10 ta sapog
topshirdi» — ish Robiya hisobiga qo'shiladi va Robiyaga xabar boradi. Mijoz («Tohir 2 million to'ladi», «Tohirga 10
komplekt berdim») va xom ashyo («20 kg kley oldim») ham shunday. Ism ovozda buzilsa ham topiladi: «Robiyaga»,
«Robiya opa», «Rabiya», «Робия» — Robiya, «Toxir» — Tohir, «sapok» — Sapog; Claude ishchi yoki mijozni «qarz» deb
ajratib yuborsa ham — to'g'ri bo'limga o'tkaziladi. Ikki o'xshash ishchi bo'lsa yoki ism topilmasa — bot so'raydi,
tugma bosilgach darhol saqlaydi va tanlovni eslab qoladi. Ishchi o'zi yozsa ham darhol saqlanadi (tasdiq sozlamalari
bo'yicha), egasiga xabar boradi. «⚙️ Sozlamalar → Ovoz va matnni darhol saqlash» o'chirilsa — avvalgidek har safar
«✅ Saqlash» bosiladi. Rasm (skrinshot, chek, daftar) uchun — alohida sozlama (pastda).

**🧾 Ishchi bilan hisob-kitob (avans, ish haqi):** SMS'dagidek qisqa yozish kifoya. Ishchi o'zi: «500 ming oldim»,
«15 loaf 15 sapog, 500.000 oldim» — olgan puli hisobidan ayriladi, topshirgan ishlari qo'shiladi. Egasi: «Robiyaga
500 ming berdim», «Robiya 200 ming qaytardi», «Robiyaga qolganini berdim». Hisob holatini ham aytish mumkin
(«Robiyaga 900 ming qarzman», «Robiya bilan hisob yopildi») — qoldiq shu summaga teng bo'ladi va keyingi ishlar
hamda pullar undan davom etib hisoblanadi. Qoldiq = tasdiqlangan ishlar − olingan pul + qaytarilgan pul: musbat —
egasi ishchiga berishi kerak, manfiy — ishchi ortiqcha olgan (avans). Ishchi «oldim» yoki «qaytardim» desa —
«⚙️ Sozlamalar → Pul yozuvlarini tasdiqlash» yoqilgan bo'lsa (sukut bo'yicha) egasi tasdiqlagach, o'chiq bo'lsa
darhol hisobga kiradi (egasiga xabar boradi); hisob holatini ishchi yozsa — har doim egasi tasdiqlagach. Bu
sozlama ish yozuvlarini tasdiqlashdan («Ishchi yozuvlarini tasdiqlash») alohida. Tasdiqlanmagan pul yozuvi bo'lsa —
egasiga har kuni ertalab (9:00) «🔔 Tasdiqlanmagan pul bor» xabari boradi, har bir yozuv «✅ Tasdiqlash» /
«🚫 Rad etish» tugmalari bilan. Pul yozilganda va tasdiqlanganda ikkala tomonga — egasiga ham, ishchiga ham — xabar
boradi: shu kuni qancha olingani va qancha qolgani («📅 20.10.2026 (bugun) — oldingiz: 50 000 so'm», «💰 Qoldi —
olishingiz kerak: 330 000 so'm»); tasdiq kutayotgan bo'lsa — «⏳ Tasdiqlasangiz — qoldi: 330 000 so'm». Ko'rish:
ishchida — botda «🧾 Hisob-kitob», ilovada bosh sahifadagi karta (kunma-kun ishlar, pul va har kun oxiridagi
qoldiq, «💵 Oldim» tugmasi); egasida — «👷 Ishchilar» → ishchi → «🧾 Hisob-kitob», ilovada ishchi oynasi
(«💵 Berdim», «✅ Qolganini berdim», «🧾 Qoldiq shu»). Kartadagi qatorga summani yoki gapni yozish mumkin: «500 ming
berdim» (ishchida «500 ming oldim») — darhol saqlanadi, murakkabrog'i («15 loaf, 500 ming oldim», «900 ming qarzman»)
— «To'g'rimi?» shu kartada chiqadi. Ishchi bilan yozishma skrinshotini yuborsangiz ham har bir kun alohida o'qiladi.
⚠️ Avvaldan ishlatayotgan bo'lsangiz: pul yozilmagan ishchining qoldig'i — barcha ishlari summasi. Har bir ishchi
uchun bir marta hisobni boshlang: «Robiya bilan hisob yopildi» yoki «Robiyaga 900 ming qarzman».

**🧱 Xom ashyo:** teri, material, podklad, kley, padosh, ip, furnitura xaridlari alohida bo'limda yuritiladi
(shaxsiy xarajatga qo'shilmaydi). Ovoz yoki matn yuboring: «1 list teri oldim», «10 millionlik teri oldim, nomi
Shirin gul», «Lider terisidan 5 list, listi 300 ming», «Alisherdan 20 kg kley oldim» — bot turini, nomini (markasi),
miqdori va birligini, narxini (bir birlik yoki jami) va kimdan olinganini ajratib «To'g'rimi?» deb so'raydi; narx
aytilmasa ham yoziladi. Botda «🧱 Xom ashyo» — kun/hafta/oy bo'yicha turlari va summasi, ilovada «Jamoa → Xom
ashyo» va bosh sahifadagi karta. Ta'minotchi (odam yoki firma) bilan oldi-berdi qarzga yoziladi: undan olingan xom
ashyo («Aslcharmdan 100 list teri oldim, listi 300 ming», «nasiyaga oldim») — xom ashyoda ham turadi, unga qarz
bo'lib umumiy «Qarzlar»ga ham qo'shiladi (qarzga deyilib kimdan olingani yoki summasi aytilmasa — bot so'raydi);
kartochkada «💳 Qarzga olindi». Qarzni to'lash: «Aslcharmga 10 million berdim» — qarzdan ayriladi. To'lab olingan
xarid («naqd oldim», «pulini berdim», chek), kimdan olingani yozilmagani yoki bozor/do'kondan olingani — oddiy
xarajat, faqat xom ashyoda. Avval yozilgan xaridlardan kimdan olingani va narxi borlari ham bir marta avtomatik
qarzga o'tkazilgan. Xato bo'lsa: botda xarid kartochkasidagi «💵 To'langan — qarzdan chiqarish» / «💳 Qarzga
qo'shish», ilovada xaridlar ro'yxatidagi va qarz tarixidagi «💳» tugmasi.

**🤝 Umumiy qarzlar:** botda «🤝 Qarzlar», ilovada «Hamyon → Qarzlar» va bosh sahifadagi karta — hamma qarz bir
joyda, bo'limlarga ajratilgan, har bir bo'limning o'z jami bilan: 🤝 odamlar bilan qarzlar; 🧱 xom ashyo —
ta'minotchilar (undan olingan xom ashyo va shu odamning qarz yozuvlari bitta qatorda; ro'yxatdagi ta'minotchi
bilan qarz ham shu yerda); 👷 ishchilar (ishchining haqqi — sizning qarzingiz, ortiqcha olgani — sizga qarz);
🛍 mijozlar (mijoz qarzi — sizga qarz, oldindan to'lagani — sizning qarzingiz). Tepadagi «sizga qarzlar» va
«sizning qarzingiz» — hamma bo'limlarning jami. Shu qarzlar o'z bo'limida ham alohida turadi: «Jamoa → Ishchilar»da
«🧾 Ishchilar bilan hisob» (berishingiz kerak / ortiqcha olgan), «Mijozlar»da mijozlar qarzi va oldindan
to'laganlar, «Jamoa → Xom ashyo»da va bosh sahifadagi xom ashyo kartasida «💳 Ta'minotchilar bilan qarz»; botda
ham «👷 Ishchilar», «🛍 Mijozlar» va «🧱 Xom ashyo» oxirida jami. Hamma ishchi avtomatik qo'shiladi: pul yoki hisob
holati yozilmagan ishchining qoldig'i — tasdiqlangan ishlari summasi (to'g'rilash uchun bir marta «Robiya bilan
hisob yopildi» yoki «Robiyaga 900 ming qarzman»). Hisobi yopilgan ishchi va mijoz ko'rsatilmaydi. Ilovada ishchi qatorini bossangiz — uning hisob-kitob oynasi, mijozni — mijoz oynasi,
ta'minotchini — qarz va undan olingan xom ashyo tarixi ochiladi.

**🏪 Ta'minotchilar:** xom ashyo kimdan olinishini ishchilardek ro'yxatga qo'shing: botda «🧱 Xom ashyo →
🏪 Ta'minotchilar → ➕ Ta'minotchi qo'shish», ilovada «Jamoa → Xom ashyo → Ta'minotchilar → + Qo'shish». Har biriga
doimiy kirish havolasi beriladi — ta'minotchi bosganda botga ulanadi (ishchi bo'lmaydi, faqat xabar oladi).
Keyin «Xayrulloh Uz charmdan 1 millionlik material oldim» desangiz, xarid xom ashyoga yoziladi va Xayrulloh Uz
charmga xabar boradi: «Jasur sizdan xom ashyo oldi — Material 1 000 000 so'm», egasiga esa «📨 Xabar yuborildi».
Ism qo'shimcha bilan yoki qisman aytilsa ham topiladi («Xayrullohdan», «Uz charmdan»); ikki o'xshash ta'minotchi
bo'lsa — bot so'raydi va tanlovni eslab qoladi. Ta'minotchi hali ulanmagan bo'lsa — egasiga eslatiladi; xarid
o'chirilsa — ta'minotchiga ham xabar boradi. Ta'minotchi botga yozsa — kimning ta'minotchisi ekani va undan oxirgi
olinganlar ko'rinadi. Ro'yxatda yo'q odam aytilsa — avvalgidek, faqat ismi yoziladi. Ta'minotchini qo'shganda
avval shu ism bilan yozilgan xaridlar ham unga bog'lanadi.

**🛍 Mijozlar bilan o'zaro hisob:** mijozni qo'shing — botda «🛍 Mijozlar → ➕ Mijoz qo'shish» (ismini yozasiz),
ilovada «Mijozlar → + Qo'shish» — bot doimiy kirish havolasini beradi (start=cli_…). Ovoz bilan yangi mijoz
aytilsa («Tohirga 10 komplekt berdim») ham — saqlangach havolasi chiqadi. Mijoz bosganda botga ulanadi (ishchi
bo'lmaydi) va ikkala tomon bir xil oldi-berdini ko'radi:
- Egasi yozsa («Tohirga 10 komplekt maksima berdim, 140 mingdan», «Tohir 2 million to'ladi») — mijozga xabar boradi:
  «📦 Mol oldingiz … 📌 Qarzingiz: 1 400 000 so'm», egasiga «📨 Xabar yuborildi: Tohir». O'chirilsa — mijozga ham xabar.
- Mijoz o'zi yozsa (ovoz yoki matn): «10 komplekt oldim», «Maksimadan 5 komplekt oldim, 140 mingdan» — mol,
  «2 million berdim», «pul tashladim» — to'lov, «3 komplekt qaytardim» — qaytgan mol. Darhol hisobga kiradi (qoldiq
  o'zgaradi) va egasiga «🛍 Tohir yozdi» xabari «🗑 O'chirish» tugmasi bilan boradi. Narx yoki soni aytilmasa — bot
  so'raydi. Mijoz o'zi yozganini o'zi o'chira oladi, egasi yozganini — faqat egasi. Yozuvlarda «✍️» — mijoz yozgan.
- Mijoz botda «🧾 Hisob-kitob» — kunma-kun oldi-berdi, har kun oxiridagi qoldiq va qarzi; egasida — «🛍 Mijozlar» →
  mijoz kartasi (xuddi shu ro'yxat, holati: ✅ ulangan / 🔗 ulanmagan, havola), ilovada mijoz oynasi.
- Bitta odam bir nechta korxonaning mijozi bo'lsa — yozganda qaysi biri bilan ekani so'raladi. Mijoz o'chirilsa —
  xabar bormaydi va u yoza olmaydi. Avvaldan bor mijozga havola: kartadagi «🔗 Kirish havolasi».
- Mijozning hamma hisobi bitta joyda: ro'yxatdagi mijoz bilan qarz ham mijoz hisobiga yoziladi, «Qarzlar»ga emas —
  «Xayrulloda 8 million haqqim bor», «Xayrulloga 1 million qarzga berdim» — 💳 qarz (qoldiqqa qo'shiladi),
  «Xayrullodan 2 million oldim» — to'lov. Ta'minotchi yoki ishchi bilan bir xil ismli bo'lsa — «Qarzlar»da qoladi.
- Mijoz qo'shilishidan oldin «Qarzlar»ga yozilgan qarzi («Hayrullo aka» — mijoz Xayrullo) mijoz kartasida
  «⚠️ «Qarzlar»da ham bor» bo'lib ko'rinadi (mol yozilganda saqlash xabarida ham): «➕ Mijoz hisobiga qo'shish»
  bosilsa — yozuvlar sanasi bilan mijoz hisobiga o'tadi (umumiy qarzlar jami o'zgarmaydi), ulangan mijozga xabar boradi.
- Ilovada mijoz oynasida «🗑 O'chirilganlar» — o'chirilgan (yoki «0 qilish» bilan o'chgan) yozuvlar, qachon o'chgani
  bilan; «↩️» — yozuv qaytadi va qoldiqqa yana kiradi, mijozga xabar boradi.

**🔗 Kirish havolasi — doimiy (ishchi, ta'minotchi, mijoz):** ismini bosing (ilovada — oyna, botda — karta →
«🔗 Kirish havolasi») — havola har doim chiqadi, ulangandan keyin ham. U chatni o'chirib yuborsa, telefonini yoki
Telegram akkauntini almashtirsa ham shu havola bilan qayta kiradi. Havola hech qachon o'zi almashmaydi va eskirmaydi —
qachon yuborilgan bo'lsa ham ishlaydi. Qarzlar bo'limida ta'minotchi ismini bosganda ham havola chiqadi. Boshqa
akkauntdan kirsa — egasiga xabar keladi (eski akkauntga endi xabar bormaydi); bu u bo'lmasa — xabardagi
«🔒 Bu u emas»: o'sha akkaunt uziladi va havola almashadi. O'chirilgan (⛔) odam havolani bossa — egasiga
«qayta faollashtiring» degan xabar boradi.

**📄 PDF hisobot:** har kim o'z hisobotini fayl qilib telefoniga saqlab oladi — ilova yopilsa ham, internet
bo'lmasa ham ochiladi. Ilovada yuqoridagi 📄 tugma → davr (bugun / shu hafta / shu oy / o'tgan oy / shu yil / hammasi — boshidan bugungacha) →
«📥 Telefonga yuklab olish» yoki «💬 Telegram chatga yuborish». Botda — `/pdf` yoki «📊 Hisobot» → «📄 PDF fayl».
Egada: ishxona (ishchilar, ish turlari, kunlar bo'yicha, har bir ishchining qoldig'i), ishchilarga berilgan pul,
daromad-xarajat, mijozlar, xom ashyo va umumiy qarzlar; ishchida — o'zi topshirgan ishlar, olgan pullari va qoldig'i.

**🧹 Hisobni 0 qilish:** ilovada har bir bo'lim pastida alohida tugma — «Mijozlar hisobini 0 qilish» (hamma
oldi-berdi, qarzlar 0), «Ishchilar hisobini 0 qilish» (hamma ish va pul yozuvlari, qoldiqlar 0), «Xom ashyo
hisobini 0 qilish» (hamma xaridlar, qarzga olinganlarining qarzi ham), «Hamyon → Xarajatlar»da «Xarajatlar hisobini 0 qilish» (hamma xarajat va
daromad yozuvlari; «Qarzlar» qoladi). Bitta ishchi uchun — ishchi oynasida, «🧾 Hisob-kitob» kartasi pastida
«🧹 Hisobini 0 qilish»: faqat shu ishchining ish va pul yozuvlari o'chadi, qoldig'i 0 bo'ladi va umumiy «Qarzlar»
jamidan ham ayriladi (haqqi bo'lsa — «sizning qarzingiz»dan, avans olgan bo'lsa — «sizga qarzlar»dan; so'rovda va
xabarda qancha ayrilishi ko'rinadi). Ilova ikki marta so'raydi, ikkalasida ham «OK» bosilsa — 0 qilinadi.
Mijozlar, ishchilar, kasblar, modellar va ta'minotchilar (havolalari bilan) qoladi; yozuvlar bazada o'chirilgan
belgisi bilan saqlanadi, ilovada qaytarib bo'lmaydi.

**Rasmdan yozish:** botga yoki ilovaga (yuqoridagi 📷 tugma — «Suratga olish» yoki «Galereyadan», yozish
oynasida ham 📷 / 🖼, kompyuterda Ctrl+V yoki sudrab tashlash) bank ilovasidan skrinshot, chek yoki daftar rasmini
yuboring — Claude rasmni o'qib (o'zbekcha lotin va kirill, ruscha, koreyscha), har bir tranzaksiyani sanasi
bilan xarajat/daromad kategoriyalariga ajratadi. Qog'ozga qo'lda yozilgani ham o'qiladi — kirill yoki lotin
(«20 та сапок, 15 та лофер» — ishchining topshirgan ishi). Rasm birdaniga tushunilmasa (xato yoki yozuv topilmasa),
undagi yozuv avval matnga ko'chiriladi va oddiy xabardek tahlil qilinadi — qoralamada nima o'qilgani ko'rinadi
(«🖼 Rasm: «20 та сапок…»»). Bir martada 5 tagacha rasm (albom). Avval kiritilgan yozuv (shu kun,
shu summa) qayta qo'shilmaydi. O'qilgani avval ro'yxat bo'lib ko'rsatiladi va «✅ Tasdiqlash» bosilgach hisobga
qo'shiladi (xato bo'lsa — «✏️ Tuzatish» yoki «❌ Bekor»). «⚙️ Sozlamalar → Rasmdagi xarajatlarni darhol saqlash»
yoqilsa — tasdiqlamasdan darhol qo'shiladi.

**💡 Maslahat (moliyaviy murabbiy):** bot topgan pulingizga qarab uni qanday taqsimlashni ko'rsatadi
(50/30/20 qoidasi: 50% — zarur xarajatlar, 30% — boshqa xarajatlar, 20% — jamg'arma; jamg'arma ulushi
10/20/30% tanlanadi), shu oy har bir qism bo'yicha qancha sarflanganini, oy oxirigacha kunlik limitni, zaxira
fond maqsadini (3–6 oylik xarajat) va qisqa maslahatlarni beradi. Daromad yozilganda — «shu puldan jamg'armaga
qancha, xarajatga qancha», xarajat yozilganda — qolgan kunlik limit. «📚 Keyingi saboq» — 34 ta qisqa
moliyaviy saboq (foizli omonat o'rniga halol investitsiya — biznesga sheriklik), «🤖 Shaxsiy maslahat» — Claude so'nggi oylardagi yozuvlaringizni tahlil qilib maslahat beradi.
Ilovada: «Hamyon» yoki «Asosiy»dagi summa kartasini bosing — reja, kunlik limit, saboqlar va eslatma vaqti
shu oynada.

**🔔 Kundalik eslatma:** har kuni kechqurun (sukut bo'yicha 21:00, «⚙️ Sozlamalar»da 19–22:00 yoki o'chiq)
bot «bugun ishlatgan yoki topgan pulingizni kiriting» deb yozadi, bugun nima yozilganini va kunlik limitni
ko'rsatadi. Render bepul tarifida server uxlab qolmasligi uchun bot har 10 daqiqada o'z `/health` manziliga
so'rov yuboradi (o'chirish: `KEEP_AWAKE=0`). Bepul tarifda oyiga 750 soat beriladi — bitta doim yoqiq
xizmatga yetadi (~744 soat).

**👁 Kuzatuv boti (admin uchun):** botni kimlar qanday ishlatayotganini ko'rish uchun alohida Telegram bot.
Har bir foydalanuvchi nima yozgani yoki aytgani (ovoz matni bilan), qaysi tugmani bosgani, bot nima deb javob
bergani, ilovada nima qilgani (ochdi, qaysi bo'limni ko'rdi, nima yozdi/o'chirdi) va xatolar keladi:

- **🔔 Jonli lenta** — bitta odamning suhbati (u yozgani + bot javoblari) ~25 soniya jimlikdan keyin bitta xabar
  bo'lib keladi; xatolar (Claude/Aisha/ElevenLabs ishlamay qolgani, bot qulagani, foydalanuvchi botni bloklagani) —
  darhol. Rejim: «Hammasi» / «Faqat xatolar» / «O'chiq». Har kuni 23:00 da kunlik hisobot.
- **👥 Foydalanuvchilar** — kim qachon oxirgi marta ishlatgan; har birining profili (harakatlar soni, kiritgan
  yozuvlari), 💬 suhbat tarixi, 🧾 oxirgi yozuvlari (o'chirganlari ham) va ✉️ unga bot nomidan xabar yozish.
- **📈 Statistika** — bugun / 7 kun / 30 kun: kim nechta harakat qildi, nechta yozuv saqladi, nechta xato.
- **⚠️ Xatolar** — oxirgi xatolar.

Tarix bazada (`activity_log`) 90 kun saqlanadi. Sozlash:
1. @BotFather → `/newbot` — yangi bot oching (masalan «Moliyachi Kuzatuv»), tokenini oling.
2. Render → xizmat → **Environment** ga qo'shing: `MONITOR_BOT_TOKEN` = shu token, `ADMIN_TG_ID` = sizning
   Telegram ID (Moliyachi botiga `/id` yozib bilasiz). `ADMIN_TG_ID` yozilmasa — `OWNER_TG_ID` dagi birinchi ID.
3. Saqlang (Render qayta ishga tushiradi), so'ng yangi botni Telegramda oching va **/start** bosing.

`MONITOR_BOT_TOKEN` bo'lmasa kuzatuv o'chiq — hech narsa yozilmaydi.

Har bir ega (`OWNER_TG_ID` dagi har bir ID) — alohida kabinet: o'z ishchilari, modellari, sozlamalari,
xarajatlari va qarzlari. Bir ega boshqasining ma'lumotini ko'rmaydi.

## Kompyuterda ishga tushirish (Windows)

1. **Python 3.12** — https://www.python.org/downloads/ (o'rnatishda «Add python.exe to PATH» belgisini qo'ying).
2. Loyiha papkasida terminal oching va:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

3. `.env.example` faylini `.env` nomi bilan nusxalang va to'ldiring:
   - `BOT_TOKEN` — @BotFather → /newbot
   - `OWNER_TG_ID` — botni ishga tushirib `/id` yozing (yoki @userinfobot). Bir nechta ega bo'lsa — vergul bilan: `111,222`
   - `AISHA_API_KEY` — voicelab.uz → API kalitlari (**Nutqdan matnga** ruxsati yoqilgan bo'lsin)
   - `ELEVENLABS_API_KEY` (ixtiyoriy, zaxira) — elevenlabs.io → Profile → API Keys (**Speech to Text** ruxsati bilan).
     Aisha ishlamasa yoki balansi tugasa, ovoz shu orqali matnga aylanadi. Aisha 4 soniyada javob bermasa ham
     ElevenLabs parallel ishga tushadi — qaysi biri oldin tayyor bo'lsa, o'sha olinadi (ovoz tezroq matnga aylanadi)
   - `ANTHROPIC_API_KEY` — console.anthropic.com → API Keys (balansga pul tashlash kerak)
   - `DATABASE_URL` — sinash uchun SQLite qoldiring; serverda PostgreSQL (Neon)

4. Ishga tushirish:

```
python -m bot
```

Kompyuterda bot **polling** rejimida ishlaydi, ilova esa http://localhost:8080/app/ da ochiladi.
Telegramsiz brauzerda sinash uchun `.env` ga `WEBAPP_DEV=1` qo'shing (serverda hech qachon yoqmang).

> ⚠️ Bot serverda (Render) ishlayotgan paytda uni kompyuterda ishga tushirmang — kompyuterdagi bot
> webhookni o'chirib qo'yadi va server xabar olmay qoladi. Shunday bo'lsa, Render'da «Manual Deploy → Restart» qiling.

## Serverga qo'yish (Render + Neon)

- **Neon** — PostgreSQL baza (bepul). Connect → ulanish manzili `DATABASE_URL` ga.
- **Render** — bot va Mini App. GitHub repodan avtomatik o'rnatiladi (`render.yaml`).

1. render.com → **New → Blueprint** → GitHub repo ni tanlang.
2. So'ralgan maxfiy qiymatlarni kiriting: `BOT_TOKEN`, `OWNER_TG_ID`, `DATABASE_URL`, `AISHA_API_KEY`, `ANTHROPIC_API_KEY` (zaxira uchun `ELEVENLABS_API_KEY` ham).
3. Deploy tugagach bot o'zi webhook va «📊 Ilova» menyu tugmasini sozlaydi.

Render `RENDER_EXTERNAL_URL` ni o'zi beradi. O'z domeningizga o'tsangiz — `PUBLIC_URL=https://sizning-domen.uz` qo'shing.

Bepul tarifda server 15 daqiqa ishlatilmasa uxlaydi; Telegram xabari kelganda uyg'onadi
(birinchi javob 30–50 soniya kechikishi mumkin).

## Tuzilma

```
bot/
  __main__.py        ishga tushirish (webhook yoki polling + veb-server)
  config.py          .env sozlamalari
  db/models.py       jadvallar (har bir egada alohida kabinet)
  db/repo.py         bazaga so'rovlar
  services/stt.py    ovoz → matn: Aisha (VoiceLab), zaxira — ElevenLabs Scribe
  services/llm.py    Claude: matn yoki rasm → JSON
  services/matcher.py  o'xshash ismlarni topish: qo'shimchasiz («Robiyaga»), talaffuz bo'yicha («Rabiya»)
  jobs.py            kasblar: standart ro'yxat, «brsovka» → Brsovkachi, «kroy 1500, tikuv 5000» narx matni
  drafts.py          qoralama: «To'g'rimi?» kartochkasi
  advisor.py         byudjet maslahatchisi: 50/30/20 reja, kunlik limit, saboqlar
  pdf_report.py      hisobot PDF fayl (shrift: assets/fonts — DejaVu, kirill va lotin)
  reminders.py       kundalik eslatma (va serverni uyg'oq ushlab turish)
  monitor/           kuzatuv boti: harakatlarni yozish (hooks, tracker), statistika, admin boti
  handlers/          Telegram handlerlar (clients.py — havola orqali ulangan mijozning o'zi)
  web/server.py      veb-server: Mini App, API, webhook
  web/api.py         Mini App uchun JSON API
  web/auth.py        Telegram initData tekshiruvi
  web/static/        Mini App (HTML/CSS/JS, Chart.js)
render.yaml          Render sozlamasi
```
