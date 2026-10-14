# Moliyachi bot

Ovozli/matnli xabardan moliyaviy hisob yurituvchi Telegram bot + grafikali Mini App.

**Holat:** bot, ovoz → matn, tasdiqlash, ishchilar bo'limi, mijozlar (optom savdo: komplekt bilan mol
berish, to'lovlar, qoldiq), shaxsiy xarajat/daromad va qarzlar, Mini App (hisobot grafiklari, «Mijozlar»,
«Hamyon», Telegramdagidek bosib turib yoziladigan ovozli xabar). Brauzerda ochish uchun botga `/web` yozing.

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
   - `ELEVENLABS_API_KEY` — elevenlabs.io → Profile → API Keys (**Speech to Text** ruxsati yoqilgan bo'lsin)
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
2. So'ralgan maxfiy qiymatlarni kiriting: `BOT_TOKEN`, `OWNER_TG_ID`, `DATABASE_URL`, `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY`.
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
  services/stt.py    ElevenLabs Scribe
  services/llm.py    Claude: matn yoki rasm → JSON
  services/matcher.py  o'xshash ismlarni topish
  drafts.py          qoralama: «To'g'rimi?» kartochkasi
  advisor.py         byudjet maslahatchisi: 50/30/20 reja, kunlik limit, saboqlar
  reminders.py       kundalik eslatma (va serverni uyg'oq ushlab turish)
  handlers/          Telegram handlerlar
  web/server.py      veb-server: Mini App, API, webhook
  web/api.py         Mini App uchun JSON API
  web/auth.py        Telegram initData tekshiruvi
  web/static/        Mini App (HTML/CSS/JS, Chart.js)
render.yaml          Render sozlamasi
```
