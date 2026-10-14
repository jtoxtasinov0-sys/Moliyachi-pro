// Mini App (Moliyachi «📊 Ilova») phone: views, bottom nav, bottom sheets, taps, typing, highlights, toasts.
const STATUS_APP = STATUS;
const NAV_ITEMS = [['home', 'Asosiy', NAV_SVG.home], ['clients', 'Mijozlar', NAV_SVG.bag], null, ['wallet', 'Hamyon', NAV_SVG.wallet], ['team', 'Jamoa', NAV_SVG.team]];
const navHTML = () => `<div class="m-nav">${NAV_ITEMS.map(it => it ? `<div class="it" data-tab="${it[0]}"><svg viewBox="0 0 24 24">${it[2]}</svg>${it[1]}</div>` : `<div style="position:relative;height:100%"><div class="m-fab"><svg viewBox="0 0 24 24">${NAV_SVG.mic}</svg></div></div>`).join('')}</div>`;

// ---- view builders --------------------------------------------------------
const TOP = `<div class="m-top"><div class="m-brand"><div class="m-logo"><img src="${ICON}"></div><div><div class="m-bn">MOLIYACHI</div><div class="m-hello">Salom, Abdurahmon!</div></div></div><div class="m-acts"><div class="m-ib cam">📷</div><div class="m-ib">⚙️</div></div></div>`;
const SEG = (on = 2) => `<div class="m-seg">${['Bugun', 'Hafta', 'Oy', 'Yil'].map((s, i) => `<span data-p="${i}"${i === on ? ' class="on"' : ''}>${s}</span>`).join('')}</div>`;
const MONEY_HERO = `<div class="m-hero"><div class="c1"></div><div class="c2"></div><div class="m-hl">Pullarim holati · qoldiq</div><div class="m-ha">+4 250 000<small>so'm</small></div>
  <div class="m-row two"><div class="m-st"><span>9 800 000</span><small>💰 daromad</small></div><div class="m-st"><span>5 550 000</span><small>💸 xarajat</small></div></div>
  <div class="m-rg">1-oktabr — 31-oktabr 2026</div><div class="m-tip">💡 Maslahat: xarajatga va jamg'armaga qancha ajratish ›</div></div>`;
const WORK_HERO = (fund = '1 400 000', qty = '275', days = '10', avg = '28', label = 'Ish haqi fondi') => `<div class="m-hero" id="mWork"><div class="c1"></div><div class="c2"></div><div class="m-hl">${label}</div><div class="m-ha"><span class="mwv">${fund}</span><small>so'm</small></div>
  <div class="m-row"><div class="m-st"><span class="mq">${qty}</span><small>dona</small></div><div class="m-st"><span class="md">${days}</span><small>ish kuni</small></div><div class="m-st"><span class="ma">${avg}</span><small>o'rtacha / kun</small></div></div>
  <div class="m-rg mr">1-oktabr — 31-oktabr 2026</div></div>`;
const PENDING = `<div class="a-pending"><div class="pi">⏳</div><div class="pt"><b>1</b> ta yozuv tasdiq kutmoqda<small>Ko'rish va tasdiqlash</small></div><div class="pa">→</div></div>`;
function chartsHTML() {
  const C = 2 * Math.PI * 84; let acc = 0;
  const arcs = MODELS.map(([, v, c]) => { const len = v / 275 * C; const s = `<circle cx="120" cy="120" r="84" fill="none" stroke="${c}" stroke-width="34" stroke-dasharray="0 ${C}" data-len="${len}" stroke-dashoffset="${-acc}"/>`; acc += len; return s; }).join('');
  return `<div class="m-card"><div class="m-ch"><h3>Dinamika</h3><div class="m-mseg"><span class="on">Dona</span><span>Summa</span></div></div>
      <div class="m-bars">${DAYS.map(d => `<i style="height:${(d / 35 * 100).toFixed(0)}%"></i>`).join('')}</div><div class="m-days">${DAYS.map((_, i) => `<span>${i + 1}</span>`).join('')}</div></div>
    <div class="m-card" id="mDonut"><div class="m-ch"><h3>Modellar bo'yicha</h3><span class="mut">3 ta model</span></div>
      <div class="m-donut"><div class="m-dbox"><svg viewBox="0 0 240 240"><circle cx="120" cy="120" r="84" fill="none" stroke="#eef2fb" stroke-width="34"/>${arcs}</svg><div class="m-dc"><b>275</b><small>dona</small></div></div>
      <ul class="m-leg">${MODELS.map(([n, v, c]) => `<li><span class="d" style="background:${c}"></span>${n}<span class="p">${Math.round(v / 275 * 100)}%</span></li>`).join('')}</ul></div></div>
    <div class="m-card" id="mRank"><div class="m-ch"><h3>Ishchilar reytingi</h3><span class="mut">3 ishchi</span></div>
      <ol class="m-rank">${RANK.map(([a, n, s, q, md]) => `<li><div class="m-av">${a}<span class="md">${md}</span></div><div><div class="nm">${n}</div><div class="m-bar"><i style="width:${(s / 560000 * 100).toFixed(0)}%"></i></div></div><div class="vl"><span class="rv">${fmt(s)}</span><small class="rq">${q} dona</small></div></li>`).join('')}</ol></div>`;
}
const homeView = (o = {}) => `${TOP}${SEG()}${o.noMoney ? '' : MONEY_HERO}<div class="m-sec">🧵 Ishxona</div>${WORK_HERO()}${o.pending ? PENDING : ''}${chartsHTML()}`;
const workerHome = () => `${TOP.replace('Abdurahmon', 'Alisher')}${SEG()}${WORK_HERO('370 000', '80', '6', '13', 'Ish haqingiz')}
  <div class="m-card"><div class="m-ch"><h3>Modellar bo'yicha</h3></div><div class="a-mrow"><span class="a-mb">A-12</span><b>50 dona</b><span>250 000 so'm</span></div><div class="a-mrow"><span class="a-mb">B-5</span><b>30 dona</b><span>120 000 so'm</span></div></div>`;
const rowW = (name, tag, qty, pay, extra = '') => `<div class="a-row"${extra}><div class="a-av">${name[0]}</div><div class="a-info"><b>${name}</b>${tag}</div><div class="a-side">${qty} ta<small>${pay}</small></div></div>`;
const TAG = { ok: '<span class="a-tag ok">Ulangan</span>', wait: '<span class="a-tag wait">Havola kutilmoqda</span>' };
const rowM = (name, price, extra = '') => `<div class="a-row"${extra}><div class="a-mbadge">🧵</div><div class="a-info"><b>${name}</b></div><div class="a-side">${price}<small>bir dona</small></div></div>`;
const teamSeg = on => `<div class="m-seg two">${['Ishchilar', 'Modellar'].map((s, i) => `<span data-team="${i}"${i === on ? ' class="on"' : ''}>${s}</span>`).join('')}</div>`;
const pageHead = title => `<div class="a-phead"><div class="a-ptitle">${title}</div><div class="a-pill">+ Qo'shish</div></div>`;

// ---- phone engine ---------------------------------------------------------
function makeApp(o) {
  const views = Object.entries(o.views).map(([k, v]) => `<div class="a-view" data-v="${k}"><div class="a-in">${v}</div></div>`).join('');
  const sheets = (o.sheets || []).map((s, i) => `<div class="a-sheet" data-s="${i}"><div class="a-grip"></div>${s.html}</div>`).join('');
  const ph = h(`<div class="phone"><div class="screen" style="background:transparent"><div class="a-layer">${STATUS_APP}${views}${navHTML()}
      <div class="m-recbar"><span class="dot"></span><span class="rt">0:00</span><span style="color:#6b7591">Gapiring… qo'yib yuborsangiz — yuboriladi</span></div>
      <div class="a-back"></div>${sheets}<div class="a-toast"></div><div class="a-flash"></div></div>
      ${(o.banners || []).map(b => `<div class="toast a-banner"><img src="${ICON}"><div style="flex:1"><div class="tt"><span>Moliyachi Bot</span><span>hozir</span></div>${b.html}</div></div>`).join('')}
      <div class="finger"></div></div></div>`);
  const screen = $('.screen', ph), layer = $('.a-layer', ph), finger = $('.finger', ph), back = $('.a-back', ph), toastEl = $('.a-toast', ph), flash = $('.a-flash', ph);
  const V = Object.fromEntries($$('.a-view', ph).map(v => [v.dataset.v, v]));
  const SH = $$('.a-sheet', ph);
  const viewKeys = o.view || [[0, Object.keys(o.views)[0]]];
  const viewAt = t => { let v = viewKeys[0][1], since = -99; for (const [tt, n] of viewKeys) if (t >= tt) { v = n; since = tt; } return [v, since]; };
  const prevView = t => { let p = null, cur = viewKeys[0][1]; for (const [tt, n] of viewKeys) if (t >= tt) { p = cur; cur = n; } return p; };
  const TAB = o.tabs || { home: 'home', team: 'team', models: 'team', workers: 'team', entries: null };
  const taps = (o.taps || []).map(tp => ({ ...tp, el: typeof tp.sel === 'string' ? $(tp.sel, ph) : tp.sel(ph) }));
  taps.forEach(tp => { if (!tp.el) throw new Error('tap target not found: ' + tp.sel); if (!$('.ripple', tp.el) && !tp.noRipple) { tp.el.style.position = tp.el.style.position || 'relative'; tp.el.style.overflow = 'hidden'; tp.el.appendChild(h('<span class="ripple" style="background:rgba(61,99,224,.25)"></span>')); } });
  const types = (o.types || []).map(ty => ({ ...ty, el: $(ty.sel, ph) }));
  types.forEach(ty => { ty.ph = ty.el.textContent; });
  const hls = (o.hls || []).map(x => ({ ...x, els: $$(x.sel, ph) }));
  const appear = $$('[data-at]', ph);
  const swaps = $$('.sw', ph);
  const banners = $$('.a-banner', ph);
  function pos(el) {
    let x = 0, y = 0, e = el;
    while (e && e !== screen) { x += e.offsetLeft; y += e.offsetTop + (e._ty || 0); e = e.offsetParent; }
    return [x + el.offsetWidth / 2, y + el.offsetHeight / 2];
  }
  function measure() { SH.forEach(s => { s._h = s.offsetHeight; }); Object.values(V).forEach(v => { v._max = Math.max(0, $('.a-in', v).offsetHeight - SCREEN_H + 40); }); }
  function update(t) {
    // open (slides up over Telegram)
    if (o.openAt !== undefined) {
      const ty = kf(t, [[o.openAt, SCREEN_H + 40], [o.openAt + .6, 0, 'out5']]);
      layer._ty = ty; layer.style.transform = `translateY(${ty.toFixed(1)}px)`;
      ph.style.visibility = t >= o.openAt ? 'visible' : 'hidden';
    }
    // views
    const [cur, since] = viewAt(t), prev = prevView(t), p = clamp((t - since) / .35);
    Object.entries(V).forEach(([k, v]) => {
      let op = 0, x = 0;
      if (k === cur) { op = since < 0 ? 1 : E.out(p); x = since < 0 ? 0 : 60 * (1 - E.out(p)); }
      else if (k === prev && p < 1) { op = 1 - E.out(p); x = -40 * E.out(p); }
      v.style.display = op > 0 ? 'block' : 'none'; v.style.opacity = op.toFixed(3); v.style.transform = `translateX(${x.toFixed(1)}px)`;
      const inner = $('.a-in', v), sk = (o.scroll || {})[k];
      if (sk) { const keys = [[0, 0]]; sk.forEach(([tt, y, d]) => keys.push([tt, keys[keys.length - 1][1]], [tt + (d || .8), Math.min(v._max, y), 'io'])); inner._ty = -kf(t, keys); }
      else inner._ty = 0;
      inner.style.transform = `translateY(${inner._ty.toFixed(1)}px)`;
    });
    // nav
    const tab = TAB[cur];
    $$('.m-nav .it', ph).forEach(it => it.classList.toggle('on', it.dataset.tab === tab));
    // seg / tab "on" states driven by data-on-at: "t:index,..."
    $$('[data-onkeys]', ph).forEach(g => { const ks = JSON.parse(g.dataset.onkeys); let on = ks[0][1]; ks.forEach(([tt, i]) => { if (t >= tt) on = i; }); [...g.children].forEach((c, i) => c.classList.toggle('on', i === on)); });
    // appearing elements
    appear.forEach(el => { const a = +el.dataset.at, u = el.dataset.until ? +el.dataset.until : 1e9; const on = t >= a && t < u; el.style.display = on ? '' : 'none'; if (on) A(el, t, { o: [[a, 0], [a + .3, 1]], s: [[a, .94], [a + .45, 1, 'back']] }); });
    swaps.forEach(sw => { const at = +sw.dataset.at, q = kf(t, [[at, 0], [at + .3, 1]]); sw.children[0].style.opacity = 1 - q; sw.children[1].style.opacity = q; });
    // sheets
    let bo = 0;
    SH.forEach((s, i) => {
      const S = o.sheets[i], hgt = s._h || 800;
      const ty = Math.max(kf(t, [[S.t0, hgt + 60], [S.t0 + .5, 0, 'out5']]), S.t1 !== undefined ? kf(t, [[S.t1, 0], [S.t1 + .35, hgt + 60, 'in']]) : 0);
      s._ty = ty; s.style.transform = `translateY(${ty.toFixed(1)}px)`;
      s.style.display = t >= S.t0 - .05 && (S.t1 === undefined || t < S.t1 + .4) ? 'block' : 'none';
      if (s.style.display === 'block') bo = Math.max(bo, 1 - ty / (hgt + 60));
    });
    back.style.opacity = (bo * .9).toFixed(3); back.style.display = bo > 0 ? 'block' : 'none';
    // typing
    types.forEach(ty => {
      if (t < ty.t0) { ty.el.textContent = ty.ph; ty.el.classList.remove('typed'); ty.el.classList.remove('focus'); return; }
      const n = Math.floor(clamp((t - ty.t0) / (ty.t1 - ty.t0)) * ty.text.length);
      ty.el.classList.add('typed'); ty.el.classList.toggle('focus', t < ty.t1 + .4);
      ty.el.innerHTML = ty.text.slice(0, n) + (t < ty.t1 + .4 ? '<span class="caret"></span>' : '');
    });
    // highlights
    hls.forEach(x => { const q = Math.min(kf(t, [[x.t0, 0], [x.t0 + .25, 1]]), kf(t, [[x.t1, 1], [x.t1 + .35, 0]])); x.els.forEach(el => { el.style.boxShadow = q > .01 ? `0 0 0 ${(6 * q).toFixed(1)}px rgba(255,194,74,${(.95 * q).toFixed(2)}), 0 0 ${(40 * q).toFixed(0)}px rgba(255,194,74,${(.5 * q).toFixed(2)})` : ''; }); });
    // record (fab)
    if (o.rec) {
      const [r0, r1] = o.rec, rp = Math.min(kf(t, [[r0, 0], [r0 + .25, 1]]), kf(t, [[r1, 1], [r1 + .25, 0]]));
      const fab = $('.m-fab', ph), rb = $('.m-recbar', ph);
      fab.style.transform = `scale(${(1 + .3 * rp + .05 * rp * Math.sin(t * 8)).toFixed(3)})`;
      fab.style.background = rp > .5 ? 'radial-gradient(circle at 30% 25%,#ff7486,#e0475b 70%)' : '';
      fab.style.boxShadow = rp > .5 ? `0 0 0 11px var(--app-bg),0 0 0 ${(11 + 30 * ((t * 1.2) % 1)).toFixed(1)}px rgba(224,71,91,${(.45 * (1 - (t * 1.2) % 1)).toFixed(2)})` : '';
      A(rb, t, { o: rp, y: (1 - rp) * 30 });
      const el = Math.max(0, t - r0); $('.rt', rb).textContent = `0:0${Math.floor(el)}`;
    }
    // toasts
    let to = 0;
    (o.toasts || []).forEach(x => { const q = Math.min(kf(t, [[x.t, 0], [x.t + .2, 1]]), kf(t, [[x.t + (x.d || 1.6), 1], [x.t + (x.d || 1.6) + .3, 0]])); if (q > to) { to = q; toastEl.textContent = x.text; } });
    A(toastEl, t, { o: to, y: (1 - to) * 24 });
    // camera flash
    if (o.flashAt) flash.style.opacity = kf(t, [[o.flashAt, 0], [o.flashAt + .05, .95], [o.flashAt + .5, 0, 'out']]).toFixed(3);
    banners.forEach((b, i) => { const B2 = o.banners[i]; A(b, t, { o: [[B2.t, 0], [B2.t + .25, 1], [B2.hide, 1], [B2.hide + .3, 0]], y: [[B2.t, -170], [B2.t + .55, 0, 'back'], [B2.hide, 0], [B2.hide + .35, -170, 'in']] }); });
    // taps (finger)
    let fo = 0;
    taps.forEach(tp => {
      const rp = $('.ripple', tp.el);
      if (rp) A(rp, t, { o: [[tp.t, 0], [tp.t + .05, .9], [tp.t + .6, 0, 'out']], s: [[tp.t, 0], [tp.t + .6, 1.8, 'out']] });
      if (!tp.hold && !tp.noPress) A(tp.el, t, { s: [[tp.t - .05, 1], [tp.t + .07, .95, 'out'], [tp.t + .3, 1, 'out']] });
      const until = tp.hold || tp.t + .2;
      if (t > tp.t - .6 && t < until + .35) {
        fo = Math.max(fo, kf(t, [[tp.t - .6, 0], [tp.t - .35, 1], [until, 1], [until + .3, 0]]));
        const [x, y] = pos(tp.el);
        finger.style.left = x + 'px'; finger.style.top = y + 'px';
        finger.style.transform = `scale(${kf(t, [[tp.t - .6, 1.3], [tp.t - .35, 1, 'out'], [tp.t, .8, 'out'], [tp.t + .2, tp.hold ? .8 : 1]]).toFixed(3)})`;
      }
    });
    finger.style.opacity = fo.toFixed(3);
    if (o.custom) o.custom(t, ph);
  }
  // sounds
  taps.forEach(tp => cue(tp.t, 'tap', 1));
  (o.sheets || []).forEach(s => { cue(s.t0, 'sheet', .6); if (s.t1 !== undefined) cue(s.t1, 'kbdown', .5); });
  types.forEach(ty => cue(ty.t0, 'type', .55, { d: +(ty.t1 - ty.t0).toFixed(2) }));
  (o.toasts || []).forEach(x => cue(x.t, 'pop', .45));
  (o.banners || []).forEach(x => cue(x.t, 'notify', .8));
  viewKeys.slice(1).forEach(([tt]) => cue(tt, 'swish', .35));
  if (o.rec) { cue(o.rec[0], 'recstart', .8); cue(o.rec[1], 'recstop', .8); }
  if (o.flashAt) cue(o.flashAt, 'shutter', .8);
  if (o.openAt !== undefined) cue(o.openAt, 'sheet', .8);
  return { el: ph, measure, update, screen };
}
function chartAnim(ph, t, M) {
  $$('.m-bars i', ph).forEach((b, i) => A(b, t, { sy: [[M.bars + i * .05, 0], [M.bars + .6 + i * .05, 1, 'back']] }));
  $$('circle[data-len]', ph).forEach((c, i) => { const len = +c.dataset.len, C = 2 * Math.PI * 84, p = kf(t, [[M.donut + i * .2, 0], [M.donut + .7 + i * .2, 1, 'out']]); c.setAttribute('stroke-dasharray', `${(len * p).toFixed(2)} ${C}`); });
  $$('.m-bar i', ph).forEach((b, i) => A(b, t, { sx: [[M.rank + i * .15, 0], [M.rank + .8 + i * .15, 1, 'out']] }));
}
