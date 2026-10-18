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
yozilmagan bo'lsa — modelning umumiy narxi. Hisobotda har bir model qaysi bosqichdan (kroy, brsovka, tikuv...)
qancha o'tgani ko'rinadi.

**🧾 Ishchi bilan hisob-kitob (avans, ish haqi):** SMS'dagidek qisqa yozish kifoya. Ishchi o'zi: «500 ming oldim»,
«15 loaf 15 sapog, 500.000 oldim» — olgan puli hisobidan ayriladi, topshirgan ishlari qo'shiladi. Egasi: «Robiyaga
500 ming berdim», «Robiya 200 ming qaytardi», «Robiyaga qolganini berdim». Hisob holatini ham aytish mumkin
(«Robiyaga 900 ming qarzman», «Robiya bilan hisob yopildi») — qoldiq shu summaga teng bo'ladi va keyingi ishlar
hamda pullar undan davom etib hisoblanadi. Qoldiq = tasdiqlangan ishlar − olingan pul + qaytarilgan pul: musbat —
egasi ishchiga berishi kerak, manfiy — ishchi ortiqcha olgan (avans). Ishchi «oldim» yoki «qaytardim» desa —
«⚙️ Sozlamalar → Pul yozuvlarini tasdiqlash» yoqilgan bo'lsa (sukut bo'yicha) egasi tasdiqlagach, o'chiq bo'lsa
darhol hisobga kiradi (egasiga xabar boradi); hisob holatini ishchi yozsa — har doim egasi tasdiqlagach. Bu
sozlama ish yozuvlarini tasdiqlashdan («Ishchi yozuvlarini tasdiqlash») alohida. Egasi yozsa — ishchiga xabar
boradi. Ko'rish: ishchida — botda «🧾 Hisob-kitob», ilovada bosh sahifadagi karta (kunma-kun ishlar, pul va har kun
oxiridagi qoldiq, «💵 Oldim» tugmasi); egasida — «👷 Ishchilar» → ishchi → «🧾 Hisob-kitob», ilovada ishchi oynasi
(«💵 Berdim», «✅ Qolganini berdim», «🧾 Qoldiq shu»). Ishchi bilan yozishma skrinshotini yuborsangiz ham har bir
kun alohida o'qiladi.
⚠️ Avvaldan ishlatayotgan bo'lsangiz: pul yozilmagan ishchining qoldig'i — barcha ishlari summasi. Har bir ishchi
uchun bir marta hisobni boshlang: «Robiya bilan hisob yopildi» yoki «Robiyaga 900 ming qarzman».

**🧱 Xom ashyo:** teri, material, podklad, kley, padosh, ip, furnitura xaridlari alohida bo'limda yuritiladi
(shaxsiy xarajatga qo'shilmaydi). Ovoz yoki matn yuboring: «1 list teri oldim», «10 millionlik teri oldim, nomi
Shirin gul», «Lider terisidan 5 list, listi 300 ming», «Alisherdan 20 kg kley oldim» — bot turini, nomini (markasi),
miqdori va birligini, narxini (bir birlik yoki jami) va kimdan olinganini ajratib «To'g'rimi?» deb so'raydi; narx
aytilmasa ham yoziladi. Botda «🧱 Xom ashyo» — kun/hafta/oy bo'yicha turlari va summasi, ilovada «Jamoa → Xom
ashyo» va bosh sahifadagi karta.

**Rasmdan yozish:** botga yoki ilovaga (yuqoridagi 📷 tugma — «Suratga olish» yoki «Galereyadan», yozish
oynasida ham 📷 / 🖼, kompyuterda Ctrl+V yoki sudrab tashlash) bank ilovasidan skrinshot, chek yoki daftar rasmini
yuboring — Claude rasmni o'qib (o'zbekcha lotin va kirill, ruscha, koreyscha), har bir tranzaksiyani sanasi
bilan xarajat/daromad kategoriyalariga ajratadi. Bir martada 5 tagacha rasm (albom). Avval kiritilgan yozuv (shu kun,
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
     Aisha ishlamasa yoki balansi tugasa, ovoz shu orqali matnga aylanadi
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
  services/matcher.py  o'xshash ismlarni topish
  jobs.py            kasblar: standart ro'yxat, «brsovka» → Brsovkachi, «kroy 1500, tikuv 5000» narx matni
  drafts.py          qoralama: «To'g'rimi?» kartochkasi
  advisor.py         byudjet maslahatchisi: 50/30/20 reja, kunlik limit, saboqlar
  reminders.py       kundalik eslatma (va serverni uyg'oq ushlab turish)
  monitor/           kuzatuv boti: harakatlarni yozish (hooks, tracker), statistika, admin boti
  handlers/          Telegram handlerlar
  web/server.py      veb-server: Mini App, API, webhook
  web/api.py         Mini App uchun JSON API
  web/auth.py        Telegram initData tekshiruvi
  web/static/        Mini App (HTML/CSS/JS, Chart.js)
render.yaml          Render sozlamasi
```
