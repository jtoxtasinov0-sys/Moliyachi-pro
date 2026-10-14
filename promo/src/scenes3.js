// v3 scenes — app-first. Telegram only where it is needed (opening «📊 Ilova», worker side, notifications).
const DRAFT_APP = `📝 <b>Ish topshirish</b>\n📅 Sana: 14.10.2026 (bugun)\n👷 Ishchi: <b>Alisher</b>\n• A-12 — 50 ta × 5 000 so'm\n• B-5 — 30 ta × 4 000 so'm\n💰 Jami: <b class="jami" style="border-radius:8px">370 000 so'm</b>\n\n<b class="tog" style="border-radius:8px">To'g'rimi?</b>`;
const HEARD = '🗣 «Alisher A-12 modeldan 50 ta, B-5 dan 30 ta topshirdi»';
function composeSheet(tRel, tDraft, tSave) {
  return `<h3>Yangi yozuv</h3>
    <div class="a-cnote busy" data-at="${tRel}" data-until="${tDraft}">⏳ Tahlil qilinmoqda…</div>
    <div data-at="${tDraft}" data-until="${tSave ?? 1e9}"><div class="a-cnote heard">${HEARD}</div><div class="a-draft">${DRAFT_APP}</div>
      <div class="a-kbrow"><div class="a-kbbtn main bsave">✅ Saqlash</div></div><div class="a-kbrow"><div class="a-kbbtn bfix">✏️ Tuzatish</div><div class="a-kbbtn">❌ Bekor</div></div><div class="a-kbrow"><div class="a-kbbtn">📅 Bugun</div><div class="a-kbbtn">📅 Kecha</div></div></div>
    ${tSave ? `<div class="a-cnote saved" data-at="${tSave}">✅ <b>Saqlandi</b>\n#127 · A-12 — <b>50 ta</b> · 250 000 so'm\n#128 · B-5 — <b>30 ta</b> · 120 000 so'm</div>` : ''}
    <div class="a-recbtn">🎤 ${tSave ? 'Bosib turing va gapiring' : 'Tuzatish yoki javob — bosib turing'}</div>
    <div class="a-imgrow"><div class="a-imgbtn bcam">📷 Suratga olish</div><div class="a-imgbtn">🖼 Skrinshot</div></div>`;
}
function stageApp(root, app, S, opt = {}) {
  const wrap = $('#pp', root); wrap.appendChild(app.el);
  cue(S + .4, 'phonein', .65);
  return t => phoneMotion(app.el, t, S + .35, opt);
}

// SCENE 0 — hook
scene(...W(0), `
  <div class="abs center" id="q1" style="top:200px;font-size:78px;font-weight:800;letter-spacing:-.035em;line-height:1.08">Ishchilar hisobi<br>hali ham <span class="grad-g">daftarda</span>mi?</div>
  ${notebookHTML('nb', 'left:220px;top:560px')}
  <div class="abs center" id="q2" style="top:860px;font-size:150px;font-weight:800;letter-spacing:-.05em">Hojat yo'q.</div>
  <div class="ring" id="r1" style="left:390px;top:560px;width:300px;height:300px"></div>
  <div class="ring" id="r2" style="left:390px;top:560px;width:300px;height:300px;border-color:#ffc24a"></div>
  <div class="logo-tile" id="lg" style="left:390px;top:560px;width:300px;height:300px;border-radius:80px"><img src="${ICON}"></div>
  <div class="abs center" id="nm" style="top:920px;font-size:130px;font-weight:800;letter-spacing:-.04em">Moliyachi</div>
  <div class="abs center" id="tgp" style="top:1110px"><span class="tgpill">✈️ Telegram'dagi hisobchingiz</span></div>
  ${PHONE_WRAP('pp', 420)}
  <div class="chip navy" id="k1" style="left:36px;top:1000px">🎙 Siz gapirasiz</div>
  <div class="chip" id="k2" style="right:36px;top:1240px">✍️ U yozadi</div>
  <div class="chip gold" id="k3" style="left:36px;top:1480px">🧮 Hisoblaydi</div>
`, (root, S) => {
  const tE = T(0, 'endi'), tM = T(0, 'moliyachi'), tTg = T(0, "telegram'dagi"), tHis = T(0, 'hisobchingiz', 1, true), tS = T(0, 'siz'), tG = T(0, 'gapirasiz'), tY = T(0, 'yozib'), tH = T(0, 'hisoblab');
  const tOpen = tHis + .05, tRec0 = tS + .05, tRec1 = tG + .75;
  const tg = makeTG({ ilovaTap: tOpen - .2, msgs: [{ t: -99, html: `Assalomu alaykum, <b>Abdurahmon</b>!\n\n🎙 Ovozli yoki matnli xabar yuboring…` }] });
  const app = makeApp({
    openAt: tOpen, views: { home: homeView() }, rec: [tRec0, tRec1],
    taps: [{ t: tRec0, sel: '.m-fab', hold: tRec1, noRipple: true }],
    sheets: [{ t0: tRec1 + .2, html: composeSheet(tRec1 + .2, tY) }],
    hls: [{ t0: tH, t1: tH + 1.6, sel: '.jami' }],
  });
  const wrap = $('#pp', root); wrap.appendChild(tg.el); wrap.appendChild(app.el);
  root._measure = () => { tg.measure(); app.measure(); };
  const q1 = $('#q1', root), nb = $('#nb', root), q2 = $('#q2', root), lg = $('#lg', root), nm = $('#nm', root), tgp = $('#tgp', root);
  const rows = $$('.note .r', root), cross = $('.note .cross', root), rings = ['#r1', '#r2'].map(s => $(s, root)), ks = ['#k1', '#k2', '#k3'].map(s => $(s, root));
  cue(.3, 'paper', .7); cue(tE - .05, 'crumple', .9); cue(tE + .1, 'whoosh', .7); cue(T(0, 'hojat'), 'impact', .5);
  cue(tM - .25, 'riser', .5); cue(tM, 'impact', .9); cue(tTg, 'phonein', .6); cue(tY, 'shimmer', .4); [tG, tY, tH].forEach(x => cue(x, 'pop', .6));
  return t => {
    A(q1, t, { ...IN(.2, { from: { y: 40, blur: 16 } }), o: [[.2, 0], [.6, 1], [tE - .1, 1], [tE + .2, 0]] });
    A(nb, t, { o: [[.1, 0], [.4, 1], [tE + .3, 1], [tE + .5, 0]], y: [[.1, 300], [.9, 0, 'out5'], [tE, 0], [tE + .5, 520, 'in']], r: [[.1, -14], [.9, -5, 'out'], [tE, -5], [tE + .5, 38, 'in']], s: [[tE, 1], [tE + .5, .45, 'in']], blur: [[tE + .2, 0], [tE + .5, 12]] });
    rows.forEach((r, i) => { const a = .7 + i * .38; r.style.clipPath = `inset(0 ${(100 - 100 * kf(t, [[a, 0], [a + .4, 1, 'out']])).toFixed(1)}% 0 0)`; });
    A(cross, t, { sx: [[2.5, 0], [2.75, 1, 'out']] });
    A(q2, t, { ...IN(tE + .15, { d: .5, e: 'back', from: { s: 1.3, blur: 20 } }), o: [[tE + .15, 0], [tE + .4, 1], [tM - .25, 1], [tM, 0]] });
    A(lg, t, { o: [[tM, 0], [tM + .2, 1], [tTg - .1, 1], [tTg + .25, 0]], s: [[tM, .2], [tM + .6, 1, 'back'], [tTg - .1, 1], [tTg + .3, .6]], y: [[tTg - .1, 0], [tTg + .3, -300]], r: [[tM, -16], [tM + .7, 0, 'out']] });
    rings.forEach((r, i) => A(r, t, { o: [[tM + .1 + i * .12, 0], [tM + .15 + i * .12, .9], [tM + 1 + i * .12, 0, 'out']], s: [[tM + .1 + i * .12, 1], [tM + 1 + i * .12, 2.4 + i * .4, 'out']] }));
    A(nm, t, { o: [[tM + .25, 0], [tM + .55, 1], [tTg - .1, 1], [tTg + .25, 0]], y: [[tM + .25, 40], [tM + .8, 0, 'out'], [tTg - .1, 0], [tTg + .3, -400]], blur: [[tM + .25, 16], [tM + .8, 0]] });
    A(tgp, t, { o: [[tTg, 0], [tTg + .25, 1], [tS - .1, 1], [tS + .25, 0]], s: [[tTg, .7], [tTg + .5, 1, 'back']], y: [[tTg, 0], [tTg + .5, -840, 'out']] });
    if (t > tTg - .1) { phoneMotion(tg.el, t, tTg - .1, { noCue: true }); phoneMotion(app.el, t, tTg - .1, { noCue: true }); } else { tg.el.style.opacity = 0; app.el.style.opacity = 0; }
    tg.update(t); app.update(t);
    ks.forEach((k, i) => chipAnim(k, t, [tG, tY, tH][i], undefined, i === 1 ? -1 : 1));
  };
});

// SCENE 1 — models (app: Jamoa → Modellar → + Qo'shish)
scene(...W(1), headHTML('1-QADAM', 'Modellar va narx', '') + PHONE_WRAP('pp', 420) + `
  <div class="chip gold" id="c1" style="left:36px;top:1745px">① Model nomi</div>
  <div class="chip navy" id="c2" style="right:36px;top:1745px">② 1 dona uchun ish haqi</div>`,
(root, S) => {
  const p = 1, tMen = T(p, 'menyudan'), tMod = T(p, 'modellarni'), tAdd = T(p, 'model'), tNom = T(p, 'nomini'), tHaq = T(p, 'haqini'), tA = T(p, "a-o'n"), tBesh = T(p, 'besh'), tEnd = T(p, "so'm", 1, true);
  const tSave = Math.max(tEnd - .05, tBesh + .75);
  const app = makeApp({
    views: {
      home: homeView(),
      models: `${teamSeg(1)}${pageHead('Modellar')}<div class="a-hint">Narx — bir dona uchun ish haqi</div>
        ${rowM('A-12', "5 000 so'm", ` data-at="${tSave + .3}"`)}${rowM('B-5', "4 000 so'm")}${rowM('C-7', "6 000 so'm")}`,
    },
    view: [[0, 'home'], [tMen + .25, 'models']],
    taps: [{ t: tMen, sel: '[data-tab="team"]' }, { t: tMod, sel: '[data-v="models"] [data-team="1"]' }, { t: tAdd, sel: '[data-v="models"] .a-pill' }, { t: tSave, sel: '.a-sheet .a-primary' }],
    sheets: [{ t0: tAdd + .2, t1: tSave + .15, html: `<h3>Yangi model</h3>
      <div class="a-field"><label>Model nomi</label><div class="a-input in-name">Masalan: A-12</div></div>
      <div class="a-field"><label>Bir dona uchun ish haqi</label><div class="a-frow"><div class="a-input in-price">Masalan: 5000</div><div class="a-sel">so'm <span style="color:#6b7591">▾</span></div></div></div>
      <div class="a-primary">Qo'shish</div>` }],
    types: [{ t0: tA, t1: tA + .5, sel: '.in-name', text: 'A-12' }, { t0: tBesh, t1: tBesh + .55, sel: '.in-price', text: '5000' }],
    hls: [{ t0: tNom, t1: tHaq - .1, sel: '.in-name' }, { t0: tHaq, t1: tA - .2, sel: '.in-price' }],
    toasts: [{ t: tSave + .35, text: 'Saqlandi' }],
  });
  const run = stageApp(root, app, S);
  root._measure = () => app.measure();
  const c1 = $('#c1', root), c2 = $('#c2', root);
  return t => { headAnim(root, t, S + .25); run(t); app.update(t); chipAnim(c1, t, tNom, tHaq - .1); chipAnim(c2, t, tHaq, tA + 1.2, -1); };
});

// SCENE 2 — workers (app: Ishchilar → + Qo'shish → invite link)
scene(...W(2), headHTML('2-QADAM', 'Ishchilarni ulang', '') + PHONE_WRAP('pp', 420) + `
  <div class="card" id="ct" style="right:24px;top:1250px;width:300px;padding:24px;text-align:center;z-index:46">
    <div style="width:100px;height:100px;margin:0 auto 12px;border-radius:50%;background:linear-gradient(140deg,#16307f,#3d63e0);color:#fff;display:grid;place-items:center;font-size:48px;font-weight:800">A</div>
    <div style="font-size:32px;font-weight:800">Alisher</div><div style="font-size:22px;color:#6b7591;font-weight:600;margin-top:2px">Telegram</div></div>
  <div class="chip gold" id="lk" style="left:330px;top:1330px;font-size:24px;padding:14px 22px">🔗 taklif havolasi</div>`,
(root, S) => {
  const p = 2, tIsh = T(p, 'ishchilar', 2), tAdd = T(p, "qo'shishni"), tIsm = T(p, 'ismini'), tBot = T(p, 'bot'), tHav = T(p, 'havola'), tYub = T(p, 'yuboring'), tUl = T(p, 'ulanadi');
  const tSave = tIsm + .9;
  const app = makeApp({
    views: {
      models: `${teamSeg(1)}${pageHead('Modellar')}<div class="a-hint">Narx — bir dona uchun ish haqi</div>${rowM('A-12', "5 000 so'm")}${rowM('B-5', "4 000 so'm")}${rowM('C-7', "6 000 so'm")}`,
      workers: `${teamSeg(0)}${pageHead('Ishchilar')}
        <div data-at="${tSave + .25}">${rowW('Alisher', `<span class="sw" data-at="${tUl.toFixed(2)}" style="display:inline-grid"><span style="grid-area:1/1">${TAG.wait}</span><span style="grid-area:1/1;opacity:0">${TAG.ok}</span></span>`, 0, 'shu oy')}</div>
        ${rowW('Dilnoza', TAG.ok, 95, "470 000 so'm")}${rowW('Vali', TAG.ok, 100, "560 000 so'm")}`,
    },
    view: [[0, 'models'], [tIsh + .2, 'workers']],
    taps: [{ t: tIsh, sel: '[data-v="models"] [data-team="0"]' }, { t: tAdd, sel: '[data-v="workers"] .a-pill' }, { t: tSave, sel: '.a-sheet[data-s="0"] .a-primary' }, { t: tYub, sel: '.a-sheet[data-s="1"] .a-primary' }],
    sheets: [
      { t0: tAdd + .2, t1: tSave + .15, html: `<h3>Yangi ishchi</h3><div class="a-field"><label>Ismi</label><div class="a-input in-w">Masalan: Alisher</div></div><div class="a-primary">Qo'shish</div>` },
      { t0: tSave + .45, t1: tUl - .2, html: `<h3>Alisher</h3><div class="a-stat"><div><b>0 ta</b><small>shu oy topshirgan</small></div><div><b>—</b><small>shu oy ish haqi</small></div></div>
        <div class="a-field"><label>Taklif havolasi (bir martalik)</label><div class="a-link lnk">https://t.me/moliyachi_bot?start=inv_7Kq2xR</div></div>
        <div class="a-primary">📤 Ishchiga yuborish</div><div class="a-secondary">Nusxalash</div>` },
    ],
    types: [{ t0: tIsm, t1: tIsm + .6, sel: '.in-w', text: 'Alisher' }],
    hls: [{ t0: tHav, t1: tYub - .1, sel: '.lnk' }],
    toasts: [{ t: tSave + .3, text: "Qo'shildi — havolani ishchiga yuboring", d: 1.4 }],
    banners: [{ t: tUl - .05, hide: tUl + 2.4, html: '👷 Alisher botga ulandi.' }],
  });
  const run = stageApp(root, app, S);
  root._measure = () => app.measure();
  const ct = $('#ct', root), lk = $('#lk', root);
  cue(tYub + .15, 'whoosh', .5); cue(tYub + .85, 'pop', .6);
  return t => {
    headAnim(root, t, S + .25); run(t); app.update(t);
    A(ct, t, { o: [[tYub - .2, 0], [tYub + .1, 1]], x: [[tYub - .2, 120], [tYub + .35, 0, 'back']], blur: [[tYub - .2, 12], [tYub + .2, 0]], s: [[tYub - .2, .8], [tYub + .35, 1, 'back'], [tUl, 1], [tUl + .15, 1.06, 'out'], [tUl + .45, 1, 'out']] });
    const q = E.io(clamp((t - tYub - .1) / .75));
    A(lk, t, { o: Math.min(kf(t, [[tYub + .1, 0], [tYub + .2, 1]]), kf(t, [[tYub + .75, 1], [tYub + .9, 0]])), x: 360 * q, y: -110 * Math.sin(Math.PI * q), s: 1 - .4 * q });
  };
});

// SCENE 3 — entering work (app: hold 🎤 → «To'g'rimi?» → Saqlash; 📷)
const QUOTE = ["«Alisher", "A-o'n", 'ikki', 'modeldan', 'ellik', 'ta,', 'Be-besh', 'modeldan', "o'ttiz", 'ta', 'topshirdi».'];
const QDISP = ['Alisher', 'A-12', '', 'modeldan', '50', 'ta,', 'B-5', 'modeldan', '30', 'ta', 'topshirdi.'];
scene(...W(3), headHTML('3-QADAM', 'Ishni kiriting', '') + PHONE_WRAP('pp', 590) + `
  <div class="kara" id="kr" style="top:400px">🎙 ${QDISP.map(w => `<span class="word">${w}</span>`).join('')}</div>
  <div class="kara" id="kx" style="top:400px;font-size:36px">✏️ Xato bo'lsa — ayting: <span style="color:#ffc24a">«soni 45 ta»</span><div style="font-size:30px;color:rgba(255,255,255,.75);margin-top:10px;font-weight:600">• A-12 — <s style="opacity:.6">50 ta</s> → <b style="color:#fff">45 ta</b> ✓</div></div>
  <div class="kara" id="kp" style="top:400px;font-size:36px">📷 Daftar rasmini yuborsangiz ham bo'ladi</div>`,
(root, S) => {
  const p = 3;
  const tBos = T(p, 'bosib'), tQ0 = T(p, 'alisher'), tQ1 = T(p, 'topshirdi', 1, true), tKar = T(p, 'kartochka'), tTo = T(p, "to'g'rimi"), tSaq = T(p, 'saqlashni'), tX = T(p, 'xato'), tS45 = T(p, 'soni'), tD = T(p, 'daftaringiz'), tRas = T(p, 'rasmini');
  const qw = WORDS.filter(w => w.para === p && w.t0 + OFF >= tQ0 - .01 && w.t0 + OFF <= tQ1);
  const tRel = tQ1 + .25;
  const app = makeApp({
    views: { home: homeView() }, rec: [tBos, tRel],
    taps: [{ t: tBos, sel: '.m-fab', hold: tRel, noRipple: true }, { t: tSaq, sel: '.bsave' }, { t: tRas + .2, sel: '.bcam' }],
    sheets: [{ t0: tRel + .15, html: composeSheet(tRel + .15, tKar, tSaq + .45) }],
    hls: [{ t0: tTo, t1: tTo + 1.2, sel: '.tog' }, { t0: tX + .2, t1: tS45 + 1.4, sel: '.a-recbtn' }, { t0: tD, t1: tRas + .2, sel: '.bcam' }],
    toasts: [{ t: tSaq + .5, text: 'Saqlandi' }],
    flashAt: tRas + .45,
  });
  const run = stageApp(root, app, S);
  root._measure = () => app.measure();
  const kr = $('#kr', root), kx = $('#kx', root), kp = $('#kp', root), kws = $$('#kr .word', root);
  cue(tKar - .05, 'shimmer', .4); cue(tX, 'swish', .6); cue(tS45, 'pop', .6); cue(tD, 'swish', .6);
  return t => {
    headAnim(root, t, S + .25); run(t); app.update(t);
    A(kr, t, IN(tQ0 - .5, { from: { y: 30, blur: 12 }, out: tX - .6 }));
    kws.forEach((w, i) => { const on = qw[i] && t >= qw[i].t0 + OFF; w.classList.toggle('on', !!on); w.classList.toggle('key', !!on && /\d/.test(w.textContent)); });
    A(kx, t, IN(tX, { from: { y: 30, blur: 12 }, out: tD - .5 }));
    A($('#kx div', root), t, IN(tS45 + .4, { from: { x: -30, blur: 8 } }));
    A(kp, t, IN(tD, { from: { y: 30, blur: 12 } }));
  };
});

// SCENE 4 — approval: worker in Telegram, owner in the app
scene(...W(4), headHTML('4-QADAM', 'Ishchi yozadi —<br>siz tasdiqlaysiz', '') + `
  <div class="lbl" style="left:30px;width:484px;top:590px">Ishchi · Telegram</div><div class="lbl" style="left:566px;width:484px;top:590px">Siz · ilovada</div>
  ${PHONE_WRAP('pw', 645, 30)}${PHONE_WRAP('po', 645, 566)}
  <div class="dot2" id="d1"></div><div class="dot2" id="d2"></div>
  <div class="abs center" id="cap" style="top:1690px;font-size:56px;font-weight:800;letter-spacing:-.025em;line-height:1.15">Faqat <span class="grad-g">tasdiqlangan</span> ish<br>hisobotga kiradi</div>`,
(root, S) => {
  const p = 4, tYoz = T(p, 'yozishi'), tMum = T(p, 'mumkin'), tYub = T(p, 'yuborgan'), tTas = T(p, 'tasdiq'), tKel = T(p, 'keladi'), tBos = T(p, 'tasdiqlashni'), tQo = T(p, "qo'shiladi"), tEs = T(p, 'esda');
  const pw = makeTG({ msgs: [
    { t: -99, html: 'Xush kelibsiz, <b>Vali</b>!' },
    { t: tMum - .2, typed: tYoz - .5, me: true, html: 'A-12 dan 40 ta topshirdim' },
    { t: tMum + .5, html: `✅ <b>Saqlandi</b>\n⏳ Egasining tasdig'ini kutmoqda.` },
    { t: tQo + .2, html: `Yozuvingiz ✅ tasdiqlandi:\n\n<b>#129</b> · 14.10.2026\n🧵 A-12 — <b>40 ta</b>\n💰 200 000 so'm` },
  ] });
  const entry = `<div class="a-chips"><span class="on">Hammasi</span><span>⏳ Kutilmoqda</span><span>✅ Tasdiqlangan</span></div>
    <div class="a-entry"><div class="a-em"><div class="a-eb">A-12</div><div class="a-ei"><b>40 dona</b><small>👷 Vali</small><small>5 000 so'm / dona</small></div>
      <div class="a-er"><b>200 000 so'm</b><span class="sw" data-at="${(tBos + .25).toFixed(2)}" style="display:inline-grid"><span style="grid-area:1/1" class="a-st" ><span class="a-st" style="background:#fdf1df;color:#e8972a;margin:0">Kutilmoqda</span></span><span style="grid-area:1/1;opacity:0"><span class="a-st" style="background:#e3f6ee;color:#12a26b;margin:0">Tasdiqlangan</span></span></span></div></div>
      <div class="a-raw">«A-12 dan 40 ta topshirdim»</div>
      <div class="a-acts"><div class="a-act ok">✓ Tasdiqlash</div><div class="a-act no">Rad etish</div><div class="a-act del">🗑</div></div></div>
    <div class="a-entry"><div class="a-em"><div class="a-eb">A-12</div><div class="a-ei"><b>50 dona</b><small>👷 Alisher</small><small>5 000 so'm / dona</small></div><div class="a-er"><b>250 000 so'm</b><span class="a-st" style="background:#e3f6ee;color:#12a26b">Tasdiqlangan</span></div></div></div>`;
  const po = makeApp({
    views: { home: `${TOP}${SEG()}<div class="m-sec">🧵 Ishxona</div>${WORK_HERO()}<div data-at="${tTas}">${PENDING}</div>${chartsHTML()}`, entries: `<div class="a-ptitle" style="margin:10px 0 18px">Yozuvlar</div>${entry}` },
    view: [[0, 'home'], [tKel + .35, 'entries']],
    taps: [{ t: tKel + .1, sel: '.a-pending' }, { t: tBos, sel: '.a-act.ok' }],
    banners: [{ t: tTas - .1, hide: tKel, html: '🔔 Yangi yozuv tasdiq kutmoqda' }],
    toasts: [{ t: tBos + .3, text: 'Tasdiqlandi' }],
  });
  $('#pw', root).appendChild(pw.el); $('#po', root).appendChild(po.el);
  root._measure = () => { pw.measure(); po.measure(); };
  const d1 = $('#d1', root), d2 = $('#d2', root), cap = $('#cap', root), lbls = $$('.lbl', root);
  cue(S + .4, 'phonein', .6); cue(tYub, 'whoosh', .5); cue(tQo, 'whoosh', .4); cue(tQo + .05, 'ding', .55); cue(tEs, 'riser', .35); cue(tEs + .55, 'impact', .45);
  const arc = (t, t0, t1, a, b, lift) => { const q = E.io(clamp((t - t0) / (t1 - t0))); return [a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q - Math.sin(Math.PI * q) * lift]; };
  return t => {
    headAnim(root, t, S + .25);
    lbls.forEach((l, i) => A(l, t, IN(S + .7 + i * .12)));
    pw.el.style.transformOrigin = '0 0'; po.el.style.transformOrigin = '0 0';
    A(pw.el, t, { o: [[S + .35, 0], [S + .65, 1]], s: .78, y: [[S + .35, 700], [S + 1.35, 0, 'out5']], ry: [[S + .35, 30], [S + 1.4, 10, 'out']] });
    A(po.el, t, { o: [[S + .5, 0], [S + .8, 1]], s: .78, y: [[S + .5, 700], [S + 1.5, 0, 'out5']], ry: [[S + .5, -30], [S + 1.55, -10, 'out']] });
    pw.update(t); po.update(t);
    let [x, y] = arc(t, tYub, tTas - .1, [280, 1220], [820, 1120], 260);
    d1.style.left = x + 'px'; d1.style.top = y + 'px';
    A(d1, t, { o: [[tYub - .05, 0], [tYub + .05, 1], [tTas - .15, 1], [tTas - .05, 0]], s: [[tYub, .5], [tYub + .3, 2.2], [tTas, 1]] });
    [x, y] = arc(t, tQo - .1, tQo + .25, [820, 1150], [280, 1260], 220);
    d2.style.left = x + 'px'; d2.style.top = y + 'px'; d2.style.background = 'radial-gradient(circle,#fff 0%,#ffd77a 45%,rgba(255,160,30,0) 72%)';
    A(d2, t, { o: [[tQo - .15, 0], [tQo - .05, 1], [tQo + .2, 1], [tQo + .3, 0]], s: [[tQo - .1, .5], [tQo + .1, 2.2], [tQo + .3, 1]] });
    A(cap, t, IN(tEs + .5, { d: .6, e: 'back', from: { s: .85, blur: 14 } }));
  };
});

// SCENE 5 — how pay is calculated
scene(...W(5), headHTML('5-QADAM', 'Ish haqi qanday<br>hisoblanadi?', '') + `
  <div class="abs center" id="f1" style="top:560px"><span class="tok w">Soni</span><span class="op">×</span><span class="tok w">1 dona narxi</span></div>
  <div class="abs center" id="f2" style="top:700px"><span class="op">=</span><span class="tok g">Ish haqi</span></div>
  <div class="card" id="gl" style="left:56px;right:56px;top:880px;padding:36px 46px 40px">
    <div style="font-size:30px;color:#6b7591;font-weight:700;margin-bottom:4px">👷 Alisher · bugun</div>
    <div class="prow" id="pr1"><span class="m">A-12</span><span class="eq"><span class="q">50</span> <span class="x">× 5 000</span></span><span class="v">= <span id="v1">250 000</span></span></div>
    <div class="prow" id="pr2"><span class="m">B-5</span><span class="eq"><span class="q">30</span> <span class="x">× 4 000</span></span><span class="v">= <span id="v2">120 000</span></span></div>
    <div id="tot" style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px">
      <div style="font-size:32px;color:#6b7591;font-weight:700">Jami<br><b style="color:#0f1733">80 ta</b></div>
      <div style="font-size:100px;font-weight:800;letter-spacing:-.04em;line-height:1;color:#16307f;font-variant-numeric:tabular-nums"><span id="vt">370 000</span><span style="font-size:42px;font-weight:700"> so'm</span></div></div></div>
  <div class="card navy" id="lk" style="left:56px;right:56px;top:1500px;padding:28px 40px">
    <div style="display:flex;align-items:center;gap:22px"><div id="lki" style="font-size:64px">🔒</div><div style="flex:1">
      <div style="font-size:26px;opacity:.75;font-weight:700">Model narxi o'zgarsa ham</div>
      <div style="font-size:34px;font-weight:800;margin-top:6px">A-12: <span class="sw" id="psw" style="display:inline-grid"><span style="grid-area:1/1">5 000</span><span style="grid-area:1/1;color:#ffc24a;opacity:0">6 000</span></span> so'm · eski yozuv: <span style="color:#6ef0b5">5 000</span></div></div></div></div>`,
(root, S) => {
  const p = 5;
  const tSoni = T(p, 'soni'), tNar = T(p, 'narxiga'), tKo = T(p, "ko'paytiriladi"), tE1 = T(p, 'ellik', 1), tK1 = T(p, 'karra', 1), tI1 = T(p, 'ikki', 1), tM1 = T(p, 'ming', 2, true),
    tO = T(p, "o'ttiz"), tK2 = T(p, 'karra', 2), tB2 = T(p, 'bir', 2), tM2 = T(p, 'ming', 4, true), tJ = T(p, 'jami'), tSo = T(p, "so'm", 1, true), tMod = T(p, 'model'), tOz = T(p, "o'zgarsa"), tQ = T(p, 'qoladi');
  const f1 = $$('#f1 > span', root), f2 = $$('#f2 > span', root), gl = $('#gl', root), pr1 = $('#pr1', root), pr2 = $('#pr2', root), tot = $('#tot', root), lk = $('#lk', root);
  const v1 = $('#v1', root), v2 = $('#v2', root), vt = $('#vt', root);
  $('#psw', root).dataset.at = tOz.toFixed(2);
  [tSoni, tNar, tNar + .15, tKo, tKo + .15].forEach(x => cue(x, 'pop', .6));
  cue(tE1 - .3, 'whoosh', .4); cue(tE1, 'tick', .7); cue(tK1, 'tick', .7); cue(tI1, 'roll', .5, { d: tM1 - tI1 });
  cue(tO, 'tick', .7); cue(tK2, 'tick', .7); cue(tB2, 'roll', .5, { d: tM2 - tB2 }); cue(tJ, 'roll', .7, { d: tSo - tJ }); cue(tSo, 'ding', .8);
  cue(tMod, 'swish', .5); cue(tOz, 'flip', .6); cue(tQ, 'lock', .9);
  return t => {
    headAnim(root, t, S + .25);
    A(f1[0], t, IN(tSoni, { e: 'back', from: { s: .4, y: 30, blur: 10 } }));
    A(f1[1], t, IN(tNar, { e: 'back', from: { s: .4, blur: 10 } })); A(f1[2], t, IN(tNar + .15, { e: 'back', from: { s: .4, y: 30, blur: 10 } }));
    A(f2[0], t, IN(tKo, { e: 'back', from: { s: .4, blur: 10 } })); A(f2[1], t, IN(tKo + .15, { e: 'back', from: { s: .4, y: 30, blur: 10 } }));
    A(gl, t, IN(tE1 - .35, { d: .6, from: { y: 120, s: .94, blur: 16 } }));
    A(pr1, t, IN(tE1, { from: { x: -60, blur: 10 } })); A($('.x', pr1), t, IN(tK1, { from: { x: -20, blur: 8 } })); A($('.v', pr1), t, IN(tI1, { from: { x: 20, blur: 8 } }));
    v1.textContent = fmt(kf(t, [[tI1, 0], [tM1, 250000, 'out']]));
    A(pr2, t, IN(tO, { from: { x: -60, blur: 10 } })); A($('.x', pr2), t, IN(tK2, { from: { x: -20, blur: 8 } })); A($('.v', pr2), t, IN(tB2, { from: { x: 20, blur: 8 } }));
    v2.textContent = fmt(kf(t, [[tB2, 0], [tM2, 120000, 'out']]));
    A(tot, t, IN(tJ - .1, { from: { y: 30, blur: 10 } }));
    vt.textContent = fmt(Math.round(kf(t, [[tJ, 0], [tSo, 370000, 'out5']]) / 1000) * 1000);
    A(vt.parentElement, t, { s: [[tSo - .05, 1], [tSo + .1, 1.08, 'out'], [tSo + .45, 1, 'out']] });
    A(lk, t, IN(tMod, { d: .6, e: 'back', from: { s: .85, y: 40, blur: 12 } }));
    $$('.sw', root).forEach(sw => { const at = +sw.dataset.at, q = kf(t, [[at, 0], [at + .3, 1]]); sw.children[0].style.opacity = 1 - q; sw.children[1].style.opacity = q; });
    A($('#lki', root), t, { s: [[tQ, 1], [tQ + .12, 1.35, 'out'], [tQ + .5, 1, 'out']], r: [[tQ, 0], [tQ + .1, -12], [tQ + .3, 8], [tQ + .5, 0]] });
  };
});

// SCENE 6 — report (app: Asosiy + period, rating; worker sees only own pay)
scene(...W(6), headHTML('HISOBOT', 'Hisobot — bir bosishda', '') + PHONE_WRAP('pp', 420) + PHONE_WRAP('pq', 560, 560) + `
  <div class="chip navy" id="hc" style="left:36px;top:1745px">📊 Botda «Hisobot» · ilovada «Asosiy»</div>`,
(root, S) => {
  const p = 6, tHb = T(p, 'hisobotni', 2), tBug = T(p, 'bugun'), tHaf = T(p, 'hafta'), tOy = T(p, 'oy'), tNe = T(p, 'nechta'), tQa = T(p, 'qancha'), tUm = T(p, 'umumiy'), tKor = T(p, "ko'rsatadi", 1, true), tIsh = T(p, 'ishchi', 2);
  const P = { 0: ['370 000', '80', '1', '80', '14-oktabr'], 1: ['960 000', '190', '4', '48', '13 — 19-oktabr'], 2: ['1 400 000', '275', '10', '28', '1-oktabr — 31-oktabr 2026'] };
  const segKeys = [[0, 2], [tBug, 0], [tHaf, 1], [tOy, 2]];
  const app = makeApp({
    views: { home: `${TOP}<div class="m-seg" data-onkeys='${JSON.stringify(segKeys)}'>${['Bugun', 'Hafta', 'Oy', 'Yil'].map(s => `<span>${s}</span>`).join('')}</div><div class="m-sec" style="margin-top:6px">🧵 Ishxona</div>${WORK_HERO()}${chartsHTML()}` },
    taps: [{ t: tBug, sel: '.m-seg span:nth-child(1)' }, { t: tHaf, sel: '.m-seg span:nth-child(2)' }, { t: tOy, sel: '.m-seg span:nth-child(3)' }],
    scroll: { home: [[tNe - .5, 1280, .9], [tUm - .5, 0, .9]] },
    hls: [{ t0: tNe, t1: tQa, sel: '.rq' }, { t0: tQa, t1: tUm - .2, sel: '.rv' }, { t0: tUm + .2, t1: tKor + .6, sel: '#mWork' }],
    custom: (t, ph) => {
      let k = 2; segKeys.forEach(([tt, i]) => { if (t >= tt + .05) k = i; });
      const v = P[k]; $('.mwv', ph).textContent = v[0]; $('.mq', ph).textContent = v[1]; $('.md', ph).textContent = v[2]; $('.ma', ph).textContent = v[3]; $('.mr', ph).textContent = v[4];
      A($('#mWork', ph), t, { s: [[tBug, 1], [tBug + .1, .97], [tBug + .35, 1, 'back'], [tHaf, 1], [tHaf + .1, .97], [tHaf + .35, 1, 'back'], [tOy, 1], [tOy + .1, .97], [tOy + .35, 1, 'back']] });
      chartAnim(ph, t, { bars: S + .9, donut: S + 1.1, rank: tNe - .3 });
    },
  });
  const wapp = makeApp({ views: { home: workerHome() } });
  $('#pp', root).appendChild(app.el); $('#pq', root).appendChild(wapp.el);
  root._measure = () => { app.measure(); wapp.measure(); };
  const hc = $('#hc', root);
  cue(S + .4, 'phonein', .65); cue(tIsh - .1, 'whoosh', .5); cue(tIsh + .3, 'phonein', .4);
  return t => {
    headAnim(root, t, S + .25);
    phoneMotion(app.el, t, S + .35, { x: kf(t, [[tIsh - .2, 0], [tIsh + .5, -200, 'io']]) });
    app.update(t);
    wapp.el.style.transformOrigin = '0 0';
    A(wapp.el, t, { o: [[tIsh, 0], [tIsh + .3, 1]], s: .78, x: [[tIsh, 500], [tIsh + .8, 0, 'out5']], ry: [[tIsh, -40], [tIsh + .9, -12, 'out']] });
    wapp.update(t);
    chipAnim(hc, t, tHb, tNe - .4);
  };
});

// SCENE 7 — «📊 Ilova» from Telegram → charts → 🎤
scene(...W(7), headHTML('ILOVA', 'Grafiklar ilovada', '') + PHONE_WRAP('pp', 420),
(root, S) => {
  const p = 7, tIl = T(p, 'ilova'), tBu = T(p, 'bu'), tFo = T(p, 'fondi'), tMo = T(p, 'modellar'), tIsh = T(p, 'ishchilar'), tMik = T(p, 'mikrofon'), tYoz = T(p, 'mumkin', 1, true);
  const tg = makeTG({ ilovaTap: tIl, msgs: [{ t: -99, html: `📊 <b>Shu oy</b>\nJami: <b>275 ta</b>\nIsh haqi fondi: <b>1 400 000 so'm</b>` }] });
  const app = makeApp({
    openAt: tIl + .2, views: { home: homeView() }, rec: [tMik + .2, tYoz + .1],
    taps: [{ t: tMik + .2, sel: '.m-fab', hold: tYoz + .1, noRipple: true }],
    scroll: { home: [[tBu - .3, 700, .8], [tMo - .4, 1420, .9], [tIsh - .5, 1900, .9], [tMik - .7, 0, 1.0]] },
    hls: [{ t0: tFo - .2, t1: tFo + 1.2, sel: '#mWork' }],
    custom: (t, ph) => {
      chartAnim(ph, t, { bars: tBu + .3, donut: tMo, rank: tIsh });
    },
  });
  const wrap = $('#pp', root); wrap.appendChild(tg.el); wrap.appendChild(app.el);
  root._measure = () => { tg.measure(); app.measure(); };
  cue(S + .4, 'phonein', .65); cue(tMo, 'shimmer', .4); cue(tIsh, 'tick3', .5); cue(tFo - .2, 'shimmer', .35);
  return t => {
    headAnim(root, t, S + .25);
    phoneMotion(tg.el, t, S + .35); phoneMotion(app.el, t, S + .35, { noCue: true });
    tg.update(t); app.update(t);
  };
});

// SCENE 8 — more
const MORE = [['🛍', 'Mijozlar', "Komplekt × narx, to'lov va qoldiq o'zi hisoblanadi", 'mijozlar'], ['💸', 'Xarajatlar', "Chek yoki bank skrinshotidan o'zi o'qiydi", 'xarajatlar'], ['🤝', 'Qarzlar', "Kim kimga qancha qarz — aniq ro'yxat", 'qarzlar'], ['🔔', 'Eslatma', 'Har kuni kechqurun: «bugungi pulingizni kiriting»', 'kechqurun']];
scene(...W(8), headHTML('YANA', 'Bundan tashqari', '') +
  MORE.map(([e, a, b], i) => `<div class="bcard${i === 3 ? ' navy' : ''}" style="left:${i % 2 ? 560 : 80}px;top:${470 + Math.floor(i / 2) * 430}px"><div class="e">${e}</div><h4>${a}</h4><p>${b}</p></div>`).join('') +
  `<div class="card" id="nt" style="left:60px;right:60px;top:1380px;display:flex;gap:22px;align-items:center;padding:26px 30px;border-radius:44px;background:rgba(250,250,252,.96)">
    <img src="${ICON}" style="width:96px;height:96px;border-radius:24px"><div style="flex:1;font-family:Inter,'Noto Color Emoji'"><div style="display:flex;justify-content:space-between;font-size:24px;color:#777;font-weight:600"><span>Moliyachi Bot</span><span>21:00</span></div>
    <div style="font-size:29px;line-height:1.35;margin-top:4px;color:#000">🔔 Bugun ishlatgan yoki topgan pulingizni kiriting</div></div></div>`,
(root, S) => {
  const p = 8, ts = MORE.map(m => T(p, m[3])), tEs = T(p, 'eslatib');
  const cards = $$('.bcard', root), nt = $('#nt', root);
  ts.forEach(x => cue(x, 'pop', .7)); cue(tEs, 'notify', .8);
  return t => {
    headAnim(root, t, S + .25);
    cards.forEach((c, i) => A(c, t, { o: [[ts[i], 0], [ts[i] + .25, 1]], ry: [[ts[i], i % 2 ? -70 : 70], [ts[i] + .7, 0, 'out']], s: [[ts[i], .8], [ts[i] + .7, 1, 'back']], y: [[ts[i] + .7, 0], [ts[i] + 8, i % 2 ? -10 : 10, 'sine']] }));
    A(nt, t, { o: [[tEs - .1, 0], [tEs + .15, 1]], y: [[tEs - .1, -120], [tEs + .5, 0, 'back']] });
  };
});

// SCENE 9 — end
scene(...W(9), `
  <div class="logo-tile" id="lg" style="left:420px;top:540px;width:240px;height:240px;border-radius:64px"><img src="${ICON}"></div>
  <div class="abs center" id="c1" style="top:860px;font-size:94px;font-weight:700;letter-spacing:-.035em">Siz gapiring —</div>
  <div class="abs center grad-g" id="c2" style="top:975px;font-size:168px;font-weight:800;letter-spacing:-.05em;line-height:1.12;filter:drop-shadow(0 0 40px rgba(255,170,40,.35))">Moliyachi</div>
  <div class="abs center" id="c3" style="top:1180px;font-size:104px;font-weight:300;letter-spacing:-.035em">hisoblaydi.</div>
  <div class="abs center" id="c4" style="top:1420px"><span class="tgpill">✈️ Telegram → Moliyachi Bot</span></div>`,
(root, S) => {
  const tS = T(9, 'siz'), tM = T(9, 'moliyachi'), tH = T(9, 'hisoblaydi');
  const lg = $('#lg', root), c = ['#c1', '#c2', '#c3', '#c4'].map(s => $(s, root));
  cue(tS, 'swish', .5); cue(tM - .3, 'riser', .5); cue(tM, 'impact', 1); cue(tH + .4, 'shimmer', .6);
  return t => {
    A(lg, t, { o: [[S + .2, 0], [S + .45, 1]], s: [[S + .2, .3], [S + .8, 1, 'back']], r: [[S + .2, 14], [S + .9, 0, 'out']], y: [[S + .9, 0], [S + 6, -12, 'sine']] });
    A(c[0], t, IN(tS, { from: { y: 40, blur: 14 } }));
    A(c[1], t, IN(tM, { d: .6, e: 'back', from: { s: 1.3, blur: 24 } }));
    A(c[2], t, IN(tH, { from: { y: 40, blur: 14 } }));
    A(c[3], t, IN(tH + .5, { d: .6, e: 'back', from: { s: .85, y: 30, blur: 12 } }));
  };
}, { noOut: true });

B.slice(1, -1).forEach(b => cue(b - .02, 'trans', .8));

// ---------- frame ----------
function renderAt(t) {
  drawBG(t);
  for (const S of SCENES) {
    const on = t >= S.start && t < S.end;
    S.root.style.display = on ? 'block' : 'none';
    if (!on) continue;
    const tin = S.start === 0 ? -1 : S.start, tout = S.opt.noOut ? 1e9 : S.end;
    const o = Math.min(kf(t, [[tin, 0], [tin + .4, 1, 'out']]), kf(t, [[tout - .38, 1], [tout, 0, 'in']]));
    const s = t < S.start + .6 ? kf(t, [[tin, 1.06], [tin + .6, 1, 'out']]) : kf(t, [[tout - .38, 1], [tout, .94, 'in']]);
    const b = Math.max(kf(t, [[tin, 14], [tin + .45, 0, 'out']]), kf(t, [[tout - .38, 0], [tout, 14, 'in']]));
    S.root.style.opacity = o.toFixed(4); S.root.style.transform = `scale(${s.toFixed(4)})`; S.root.style.filter = b > .05 ? `blur(${b.toFixed(2)}px)` : 'none';
    S.update(t);
  }
  let fl = 0; for (const b of B.slice(1, -1)) if (Math.abs(t - b) < .35) fl = Math.max(fl, Math.cos(Math.PI * (t - b) / .7) * .8);
  $('#flash').style.opacity = fl.toFixed(3);
  $('#stage').style.opacity = kf(t, [[DURATION - .7, 1], [DURATION, 0, 'in']]).toFixed(3);
  drawGrain(t);
}
async function init() {
  await document.fonts.ready;
  await Promise.all(['500', '600', '700', '800'].map(w => document.fonts.load(`${w} 40px Manrope`)).concat([document.fonts.load('600 40px Caveat'), document.fonts.load('400 30px Inter'), document.fonts.load('700 30px Inter')]));
  await Promise.all([...document.images].map(i => (i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))));
  for (const S of SCENES) { S.setup(); S.root.style.display = 'block'; if (S.root._measure) S.root._measure(); S.root.style.display = 'none'; }
  CUES.sort((a, b) => a.t - b.t);
  Object.assign(window, { CUES, DURATION, renderAt, B });
  renderAt(0);
  window.READY = true;
}
init().catch(e => { console.error(e); window.ERR = String(e) + '\n' + e.stack; });
