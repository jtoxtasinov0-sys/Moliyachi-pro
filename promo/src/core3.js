// Moliyachi — qo'llanma v2. Timed to the user's voiceover (plan/words.json). Deterministic renderAt(t).
'use strict';
const ICON = '../assets/images/icon.png';
const OFF = 0.4;                       // voiceover starts at video time OFF
const VO_END = 131.08 + OFF;
const DURATION = VO_END + 1.9;

// ---------- easing / keyframes ----------
const E = {
  lin: x => x, out: x => 1 - Math.pow(1 - x, 3), out5: x => 1 - Math.pow(1 - x, 5), in: x => x * x * x,
  io: x => (x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2), sine: x => -(Math.cos(Math.PI * x) - 1) / 2,
  back: x => { const c1 = 1.3, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
};
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
function kf(t, keys) {
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [t1, v1, e] = keys[i];
    if (t <= t1) { const [t0, v0] = keys[i - 1]; return v0 + (v1 - v0) * E[e || 'io'](clamp((t - t0) / ((t1 - t0) || 1e-9))); }
  }
  return keys[keys.length - 1][1];
}
const val = (t, x) => (Array.isArray(x) ? kf(t, x) : x);
function A(el, t, p) {
  const g = k => (p[k] === undefined ? undefined : val(t, p[k]));
  const o = g('o'); if (o !== undefined) el.style.opacity = o.toFixed(4);
  const x = g('x') || 0, y = g('y') || 0, s = g('s'), r = g('r') || 0, rx = g('rx') || 0, ry = g('ry') || 0, sx = g('sx'), sy = g('sy');
  let tr = `translate3d(${x.toFixed(2)}px,${y.toFixed(2)}px,0)`;
  if (rx) tr += ` rotateX(${rx.toFixed(3)}deg)`;
  if (ry) tr += ` rotateY(${ry.toFixed(3)}deg)`;
  if (r) tr += ` rotate(${r.toFixed(3)}deg)`;
  if (s !== undefined) tr += ` scale(${s.toFixed(4)})`;
  if (sx !== undefined || sy !== undefined) tr += ` scale(${(sx ?? 1).toFixed(4)},${(sy ?? 1).toFixed(4)})`;
  el.style.transform = tr;
  const b = g('blur'); if (b !== undefined) el.style.filter = b > .05 ? `blur(${b.toFixed(2)}px)` : 'none';
}
function IN(t0, opt = {}) {
  const d = opt.d ?? .55, e = opt.e ?? 'out', from = opt.from ?? { y: 46, blur: 12 };
  const p = { o: [[t0, 0], [t0 + d * .7, 1, 'out']] };
  for (const k in from) p[k] = [[t0, from[k]], [t0 + d, k === 's' || k === 'sx' || k === 'sy' ? 1 : 0, e]];
  if (opt.out !== undefined) {
    const t1 = opt.out, d1 = opt.d1 ?? .35, to = opt.to ?? { y: -30, blur: 10 };
    p.o.push([t1, 1], [t1 + d1, 0, 'in']);
    for (const k in to) { const rest = k === 's' ? 1 : 0; if (!p[k]) p[k] = [[t1, rest]]; p[k].push([t1, rest], [t1 + d1, to[k], 'in']); }
  }
  return p;
}
function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(html) { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstElementChild; }
const fmt = n => Math.round(n).toLocaleString('en-US').replace(/,/g, ' ');
function splitWords(el) {
  const out = [];
  el.childNodes.forEach(n => {
    if (n.nodeType === 3) n.textContent.split(/(\s+)/).forEach(w => out.push(/^\s+$/.test(w) ? w : w ? `<span class="word">${w}</span>` : ''));
    else { n.classList.add('word'); out.push(n.outerHTML); }
  });
  el.innerHTML = out.join('');
  return $$('.word', el);
}

// ---------- voiceover word timing ----------
const norm = w => w.toLowerCase().replace(/[«»?.,:;!—]/g, '');
function T(p, sub, n = 1, end = false) {
  let c = 0;
  const s = sub.toLowerCase(), pre = s.endsWith('*');
  for (const w of WORDS) {
    if (w.para !== p) continue;
    const x = norm(w.w);
    if (pre ? x.startsWith(s.slice(0, -1)) : x === s) if (++c === n) return (end ? w.t1 : w.t0) + OFF;
  }
  throw new Error(`word not found: ${p} ${sub} ${n}`);
}
const PARA = i => WORDS.find(w => w.para === i).t0 + OFF;

// ---------- sound cues ----------
const CUES = [];
const cue = (t, type, g = 1, extra = {}) => CUES.push({ t: +t.toFixed(3), type, g, ...extra });

// ---------- scenes ----------
const SCENES = [];
function scene(start, end, html, setup, opt = {}) {
  const root = h(`<div class="scene">${html}</div>`);
  $('#scenes').appendChild(root);
  const S = { start, end, root, opt };
  S.setup = () => { S.update = setup(root, start); };
  SCENES.push(S);
  return S;
}

const gc = $('#grain'), gx = gc.getContext('2d'), gimg = gx.createImageData(540, 960);
function drawGrain(t) {
  const r = rng(Math.floor(t * 30) + 7), d = gimg.data;
  for (let i = 0; i < d.length; i += 4) { const v = r() * 255; d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255; }
  gx.putImageData(gimg, 0, 0); gc.style.width = '1080px'; gc.style.height = '1920px';
}

// ---------- Telegram phone ----------
const DOODLE = (() => {
  const s = `<svg xmlns='http://www.w3.org/2000/svg' width='260' height='260' fill='none' stroke='%23365a22' stroke-width='3' stroke-linecap='round'>
  <circle cx='40' cy='42' r='16'/><path d='M120 30q14-14 28 0t28 0'/><rect x='196' y='24' width='36' height='28' rx='6'/><path d='M30 130l20-20 20 20-20 20z'/>
  <path d='M110 120c10-20 40-20 40 4s-30 26-40 10'/><circle cx='210' cy='130' r='6'/><circle cx='226' cy='118' r='4'/><path d='M40 210h40M60 190v40'/>
  <path d='M130 200q20 30 50 0'/><path d='M206 200l14 30 14-30'/></svg>`;
  return `url("data:image/svg+xml;utf8,${s.replace(/\n/g, '').replace(/"/g, "'")}")`;
})();
const STATUS = `<div class="island"></div><div class="status"><span>18:59</span><span class="ic"><span class="bars"><i style="height:7px"></i><i style="height:11px"></i><i style="height:15px"></i><i style="height:20px;opacity:.35"></i></span><span class="wifi"></span><span class="bat">52</span></span></div>`;
const KB_BTNS = ['📊 Hisobot', '🕘 Oxirgi yozuvlar', '🛍 Mijozlar', '💸 Xarajatlar', '🤝 Qarzlar', '💡 Maslahat', '⏳ Tasdiq kutayotganlar', '👷 Ishchilar', '🧵 Modellar', '⚙️ Sozlamalar', '❓ Yordam'];
const SCREEN_H = 1250, COMPOSE_H = 110, KB_H = 560, HOME_PAD = 34;
const strip = s => s.replace(/<[^>]+>/g, '');

function makeTG(o) {
  const ph = h(`<div class="phone"><div class="screen">${STATUS}
    <div class="tg"><div class="tg-wall"></div><div class="tg-doodle" style="background-image:${DOODLE}"></div>
      <div class="tg-view"><div class="tg-list"></div></div>
      <div class="tg-head"><div class="tg-back glass">‹ <b>8</b></div><div class="tg-title glass"><div class="n">Moliyachi Bot</div><div class="s">бот</div></div><div class="tg-ava"><img src="${ICON}"></div></div>
      <div class="tg-compose"><div class="ilova"><span class="sq"></span>📊 Ilova<span class="ripple"></span></div><div class="circ glass">📎</div><div class="field glass"><span class="ph">Ovoz, matn…</span><span class="kbic"></span></div><div class="circ glass mic">🎙</div></div>
      <div class="rec glass"><span class="dot"></span><span class="tm">0:00,0</span><span class="hint">‹ Bekor qilish</span></div>
      <div class="bigmic"><span class="pulse"></span>🎙</div>
      <div class="tg-kb">${KB_BTNS.map((b, i) => `<div class="kbtn${i === 10 ? ' full' : ''}">${b}<span class="ripple"></span></div>`).join('')}</div>
    </div><div class="finger"></div></div></div>`);
  const screen = $('.screen', ph), view = $('.tg-view', ph), list = $('.tg-list', ph), compose = $('.tg-compose', ph), kbEl = $('.tg-kb', ph);
  const field = $('.field', ph), recEl = $('.rec', ph), bigmic = $('.bigmic', ph), finger = $('.finger', ph);
  const taps = [];
  o.msgs.forEach((m, mi) => {
    const w = h(`<div class="msg ${m.me ? 'me' : 'bot'}"></div>`);
    const meta = `<span class="meta">18:59${m.me ? ' ✓✓' : ''}</span>`;
    if (m.voice) {
      const r = rng(31 + mi);
      const bars = Array.from({ length: 34 }, (_, i) => `<i style="height:${(8 + 40 * Math.pow(Math.abs(Math.sin(i * .5 + 1)) * .55 + r() * .45, 1.3)).toFixed(0)}px"></i>`).join('');
      w.appendChild(h(`<div class="bub voice"><div class="play">▶</div><div><div class="wave">${bars}</div><div class="dur">0:05 ·  ✓✓</div></div></div>`));
    } else if (m.photo) {
      w.appendChild(h(`<div class="bub photo"><div class="pic">${m.photo}</div></div>`));
    } else w.appendChild(h(`<div class="bub">${m.html}${meta}</div>`));
    if (m.btns) {
      const kb = h(`<div class="ikb"></div>`);
      m.btns.forEach(row => {
        const r = h(`<div class="row" style="grid-template-columns:repeat(${row.length},1fr)"></div>`);
        row.forEach(b => {
          const B = typeof b === 'string' ? { txt: b } : b;
          const be = h(`<div class="ib">${B.txt}<span class="ripple"></span></div>`);
          r.appendChild(be);
          if (B.tap !== undefined) taps.push({ t: B.tap, el: be });
          if (B.hl) be._hl = B.hl;
        });
        kb.appendChild(r);
      });
      w.appendChild(kb);
    }
    m.el = w; list.appendChild(w);
  });
  (o.kbTaps || []).forEach(k => taps.push({ t: k.t, el: $$('.kbtn', ph).find(b => b.textContent.includes(k.label)) }));
  if (o.ilovaTap) taps.push({ t: o.ilovaTap, el: $('.ilova', ph) });
  if (o.micTap) taps.push({ t: o.micTap, el: $('.mic', ph), hold: true });
  let toast = null;
  if (o.toast) { toast = h(`<div class="toast"><img src="${ICON}"><div style="flex:1"><div class="tt"><span>Moliyachi Bot</span><span>hozir</span></div>${o.toast.html}</div></div>`); screen.appendChild(toast); }
  let mini = null;
  if (o.mini) { mini = h(miniHTML()); screen.insertBefore(mini, finger); }
  const vis = (m, t) => t >= m.t && (m.hide === undefined || t < m.hide);
  const kbKeys = [[0, o.kb ? o.kb[0][1] : 0]];
  (o.kb || []).slice(1).forEach(([t, v]) => kbKeys.push([t, kbKeys[kbKeys.length - 1][1]], [t + .38, v, 'out']));
  function measure() {
    o.msgs.forEach(m => { m.el.style.display = 'flex'; m.h = m.el.offsetHeight + 14; });
    o.msgs.forEach(m => { m.el.style.display = 'none'; });
    if (mini) mini._measure && mini._measure();
  }
  let listTY = 0, kbTY = 0;
  function pos(el) {
    let x = 0, y = 0, e = el;
    while (e && e !== screen) {
      x += e.offsetLeft; y += e.offsetTop;
      if (e === list) y += listTY;
      if (e === kbEl) y += kbTY;
      e = e.offsetParent;
    }
    return [x + el.offsetWidth / 2, y + el.offsetHeight / 2];
  }
  function update(t) {
    const kbo = kf(t, kbKeys);
    kbTY = (1 - kbo) * (KB_H + 20);
    kbEl.style.transform = `translateY(${kbTY.toFixed(1)}px)`;
    const composeTop = SCREEN_H - (HOME_PAD + (KB_H - HOME_PAD + 6) * kbo) - COMPOSE_H;
    compose.style.top = composeTop + 'px'; recEl.style.top = composeTop + 'px';
    view.style.height = composeTop + 'px';
    const viewH = composeTop;
    let off = 0;
    o.msgs.forEach((m, i) => {
      const on = vis(m, t);
      m.el.style.display = on ? 'flex' : 'none';
      if (!on) return;
      const p = clamp((t - m.t) / .4);
      if (m.t > -50 && p < 1) {
        const base = m.replaces !== undefined ? m.h - o.msgs[m.replaces].h : m.h;
        off += base * (1 - E.out(p));
      }
      if (m.read) {
        const shift = Math.max(0, m.h + 210 - viewH);
        off += shift * (1 - kf(t, [[m.read[0], 0], [m.read[1], 1, 'io']]));
      }
      if (m.replaces !== undefined) A(m.el, t, { o: [[m.t, 0], [m.t + .3, 1]] });
      else if (m.t > -50) A(m.el, t, { o: [[m.t, 0], [m.t + .2, 1, 'out']], s: [[m.t, .86], [m.t + .45, 1, 'back']] });
      if (m.voice) {
        const vp = kf(t, [[m.t + .3, 0], [m.t + .3 + m.voice, 1, 'lin']]);
        const bars = $$('.wave i', m.el); bars.forEach((b, k) => b.classList.toggle('on', k / bars.length < vp));
        $('.play', m.el).textContent = vp > 0 && vp < 1 ? '❚❚' : '▶';
      }
    });
    listTY = off;
    list.style.transform = `translateY(${off.toFixed(2)}px)`;
    // highlights inside bubbles
    $$('[data-a]', ph).forEach(s => {
      const a = +s.dataset.a, b = +s.dataset.b, p = Math.min(kf(t, [[a, 0], [a + .25, 1]]), kf(t, [[b, 1], [b + .4, 0]]));
      s.style.background = `rgba(255,205,40,${(.5 * p).toFixed(3)})`; s.style.boxShadow = `0 0 0 ${(5 * p).toFixed(1)}px rgba(255,205,40,${(.5 * p).toFixed(3)})`;
    });
    $$('.ib', ph).forEach(b => { if (!b._hl) return; const [a, c] = b._hl; const p = Math.min(kf(t, [[a, 0], [a + .2, 1]]), kf(t, [[c, 1], [c + .3, 0]])); b.style.boxShadow = `0 0 0 ${(4 * p).toFixed(1)}px rgba(255,214,60,${p.toFixed(2)})`; b.style.background = `rgba(${Math.round(74 + 120 * p)},${Math.round(110 + 80 * p)},52,${(.42 + .3 * p).toFixed(2)})`; });
    $$('.sw', ph).forEach(sw => { const at = +sw.dataset.at, p = kf(t, [[at, 0], [at + .3, 1]]); sw.children[0].style.opacity = 1 - p; sw.children[1].style.opacity = p; });
    // typing in composer
    const typing = o.msgs.find(m => m.typed !== undefined && t >= m.typed && t < m.t);
    const ph1 = $('.ph', field);
    if (typing) {
      const txt = strip(typing.html), n = Math.floor(clamp((t - typing.typed) / Math.max(.2, typing.t - typing.typed - .2)) * txt.length);
      ph1.innerHTML = `<span class="typed">${txt.slice(0, n)}</span><span class="caret"></span>`;
    } else ph1.textContent = 'Ovoz, matn…';
    // recording
    if (o.rec) {
      const [r0, r1] = o.rec, p = Math.min(kf(t, [[r0, 0], [r0 + .2, 1]]), kf(t, [[r1, 1], [r1 + .2, 0]]));
      recEl.style.opacity = p; compose.style.opacity = 1 - p;
      const [mx, my] = pos($('.mic', ph));
      bigmic.style.left = (mx - 95) + 'px'; bigmic.style.top = (my - 95) + 'px';
      A(bigmic, t, { o: p, s: .4 + .6 * E.back(clamp((t - r0) / .35)) * (t < r1 ? 1 : 1 - clamp((t - r1) / .2)) });
      $('.pulse', bigmic).style.transform = `scale(${(1 + .18 * Math.abs(Math.sin(t * 6))).toFixed(3)})`;
      const el = Math.max(0, t - r0);
      $('.tm', recEl).textContent = `0:${String(Math.floor(el)).padStart(2, '0')},${Math.floor(el * 10) % 10}`;
      $('.dot', recEl).style.opacity = .4 + .6 * Math.abs(Math.cos(t * 4));
    }
    // taps
    let fo = 0;
    taps.forEach(tp => {
      const rp = $('.ripple', tp.el);
      if (rp) A(rp, t, { o: [[tp.t, 0], [tp.t + .05, .9], [tp.t + .6, 0, 'out']], s: [[tp.t, 0], [tp.t + .6, 1.6, 'out']] });
      if (!tp.hold) A(tp.el, t, { s: [[tp.t - .05, 1], [tp.t + .07, .93, 'out'], [tp.t + .32, 1, 'out']] });
      const until = tp.hold ? o.rec[1] : tp.t + .2;
      if (t > tp.t - .6 && t < until + .35) {
        fo = Math.max(fo, kf(t, [[tp.t - .6, 0], [tp.t - .35, 1], [until, 1], [until + .3, 0]]));
        const [x, y] = pos(tp.el);
        finger.style.left = x + 'px'; finger.style.top = y + 'px';
        finger.style.transform = `scale(${kf(t, [[tp.t - .6, 1.3], [tp.t - .35, 1, 'out'], [tp.t, .8, 'out'], [tp.t + .2, tp.hold ? .8 : 1]]).toFixed(3)})`;
      }
    });
    finger.style.opacity = fo.toFixed(3);
    if (toast) A(toast, t, { o: [[o.toast.t, 0], [o.toast.t + .25, 1], [o.toast.hide, 1], [o.toast.hide + .3, 0]], y: [[o.toast.t, -170], [o.toast.t + .55, 0, 'back'], [o.toast.hide, 0], [o.toast.hide + .35, -170, 'in']] });
    if (mini) mini._update(t);
  }
  // sounds
  o.msgs.forEach(m => {
    if (m.t < -50 || m.silent) return;
    if (m.typed !== undefined) cue(m.typed, 'type', .55, { d: +(m.t - m.typed - .2).toFixed(2) });
    cue(m.t, m.me ? 'send' : 'recv', m.me ? .75 : .7);
  });
  taps.forEach(tp => cue(tp.t, 'tap', 1));
  if (o.kb) o.kb.slice(1).forEach(([t, v]) => cue(t, v ? 'kbup' : 'kbdown', .45));
  if (o.toast) cue(o.toast.t, 'notify', .8);
  if (o.rec) { cue(o.rec[0], 'recstart', .8); cue(o.rec[1], 'recstop', .8); }
  return { el: ph, measure, update, screen };
}

// ---------- Mini App screen ----------
const NAV_SVG = {
  home: '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1v-8.5Z"/>',
  bag: '<path d="M6 7h12l-1 13H7L6 7Z"/><path d="M9 9V6a3 3 0 0 1 6 0v3"/>',
  wallet: '<path d="M4 7.5A2.5 2.5 0 0 1 6.5 5H18v3M4 7.5V17a2 2 0 0 0 2 2h13a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H6.5A2.5 2.5 0 0 1 4 7.5Z"/><path d="M16 13.5h.01"/>',
  team: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3 2.8-4.8 5.5-4.8s4.9 1.8 5.5 4.8M16 5.2a3 3 0 0 1 0 5.6M18 14.6c1.4.7 2.3 2.2 2.6 4.4"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
};
const DAYS = [18, 24, 31, 22, 28, 35, 26, 30, 33, 28];
const MODELS = [['A-12', 90, '#16307f'], ['B-5', 80, '#3d63e0'], ['C-7', 105, '#ffb02e']];
const RANK = [['V', 'Vali', 560000, 100, '🥇'], ['D', 'Dilnoza', 470000, 95, '🥈'], ['A', 'Alisher', 370000, 80, '🥉']];
function miniHTML() {
  const C = 2 * Math.PI * 84; let acc = 0;
  const arcs = MODELS.map(([, v, c]) => { const len = v / 275 * C; const s = `<circle cx="120" cy="120" r="84" fill="none" stroke="${c}" stroke-width="34" stroke-dasharray="0 ${C}" data-len="${len}" data-off="${-acc}" stroke-dashoffset="${-acc}"/>`; acc += len; return s; }).join('');
  return `<div class="mini"><div class="mini-head"><span>Yopish</span><b style="color:#0f1733;font-size:26px">Moliyachi</b><span>⋯</span></div><div class="mini-in">
    <div class="m-top"><div class="m-brand"><div class="m-logo"><img src="${ICON}"></div><div><div class="m-bn">MOLIYACHI</div><div class="m-hello">Salom, Abdurahmon!</div></div></div><div class="m-acts"><div class="m-ib cam">📷</div><div class="m-ib">⚙️</div></div></div>
    <div class="m-seg"><span>Bugun</span><span>Hafta</span><span class="on">Oy</span><span>Yil</span></div>
    <div class="m-hero"><div class="c1"></div><div class="c2"></div><div class="m-hl">Pullarim holati · qoldiq</div><div class="m-ha">+4 250 000<small>so'm</small></div>
      <div class="m-row two"><div class="m-st"><span>9 800 000</span><small>💰 daromad</small></div><div class="m-st"><span>5 550 000</span><small>💸 xarajat</small></div></div>
      <div class="m-rg">1-oktabr — 31-oktabr 2026</div><div class="m-tip">💡 Maslahat: xarajatga va jamg'armaga qancha ajratish ›</div></div>
    <div class="m-sec">🧵 Ishxona</div>
    <div class="m-hero" id="mWork"><div class="c1"></div><div class="c2"></div><div class="m-hl">Ish haqi fondi</div><div class="m-ha"><span class="mwv">1 400 000</span><small>so'm</small></div>
      <div class="m-row"><div class="m-st"><span>275</span><small>dona</small></div><div class="m-st"><span>10</span><small>ish kuni</small></div><div class="m-st"><span>28</span><small>o'rtacha / kun</small></div></div>
      <div class="m-rg">1-oktabr — 31-oktabr 2026</div></div>
    <div class="m-card"><div class="m-ch"><h3>Dinamika</h3><div class="m-mseg"><span class="on">Dona</span><span>Summa</span></div></div>
      <div class="m-bars">${DAYS.map(d => `<i style="height:${(d / 35 * 100).toFixed(0)}%"></i>`).join('')}</div><div class="m-days">${DAYS.map((_, i) => `<span>${i + 1}</span>`).join('')}</div></div>
    <div class="m-card" id="mDonut"><div class="m-ch"><h3>Modellar bo'yicha</h3><span style="color:#6b7591;font-weight:600;font-size:20px">3 ta model</span></div>
      <div class="m-donut"><div class="m-dbox"><svg viewBox="0 0 240 240"><circle cx="120" cy="120" r="84" fill="none" stroke="#eef2fb" stroke-width="34"/>${arcs}</svg><div class="m-dc"><b>275</b><small>dona</small></div></div>
      <ul class="m-leg">${MODELS.map(([n, v, c]) => `<li><span class="d" style="background:${c}"></span>${n}<span class="p">${Math.round(v / 275 * 100)}%</span></li>`).join('')}</ul></div></div>
    <div class="m-card" id="mRank"><div class="m-ch"><h3>Ishchilar reytingi</h3><span style="color:#6b7591;font-weight:600;font-size:20px">3 ishchi</span></div>
      <ol class="m-rank">${RANK.map(([a, n, s, q, md]) => `<li><div class="m-av">${a}<span class="md">${md}</span></div><div><div class="nm">${n}</div><div class="m-bar"><i style="width:${(s / 560000 * 100).toFixed(0)}%"></i></div></div><div class="vl">${fmt(s)}<small>${q} dona</small></div></li>`).join('')}</ol></div>
  </div>
  <div class="m-recbar"><span class="dot"></span><span class="rt">0:00</span><span style="color:#6b7591">Gapiring… qo'yib yuborsangiz — yuboriladi</span></div>
  <div class="m-nav"><div class="it on"><svg viewBox="0 0 24 24">${NAV_SVG.home}</svg>Asosiy</div><div class="it"><svg viewBox="0 0 24 24">${NAV_SVG.bag}</svg>Mijozlar</div><div style="position:relative;height:100%"><div class="m-fab"><svg viewBox="0 0 24 24">${NAV_SVG.mic}</svg></div></div><div class="it"><svg viewBox="0 0 24 24">${NAV_SVG.wallet}</svg>Hamyon</div><div class="it"><svg viewBox="0 0 24 24">${NAV_SVG.team}</svg>Jamoa</div></div>
  </div>`;
}
function bindMini(tg, o) {
  const mini = $('.mini', tg.el), inner = $('.mini-in', mini);
  const M = o.mini;
  const tops = {};
  mini._measure = () => { ['mWork', 'mDonut', 'mRank'].forEach(id => { tops[id] = $('#' + id, mini).offsetTop; }); };
  mini._update = t => {
    A(mini, t, { o: [[M.t, 0], [M.t + .08, 1]], y: [[M.t, SCREEN_H], [M.t + .6, 0, 'out5']] });
    mini.style.display = t >= M.t ? 'block' : 'none';
    const maxY = Math.max(0, inner.offsetHeight - SCREEN_H);
    const keys = [[0, 0]]; M.scroll.forEach(([tt, id, d]) => { const y = id === 0 ? 0 : Math.min(maxY, tops[id] - 120); keys.push([tt, keys[keys.length - 1][1]], [tt + (d || .9), y, 'io']); });
    inner.style.transform = `translateY(${-kf(t, keys).toFixed(2)}px)`;
    // animate charts when they come into view
    $$('.m-bars i', mini).forEach((b, i) => A(b, t, { sy: [[M.t + .5 + i * .05, 0], [M.t + 1.1 + i * .05, 1, 'back']] }));
    $$('circle[data-len]', mini).forEach((c, i) => { const len = +c.dataset.len, C = 2 * Math.PI * 84, p = kf(t, [[M.donut + i * .2, 0], [M.donut + .7 + i * .2, 1, 'out']]); c.setAttribute('stroke-dasharray', `${(len * p).toFixed(2)} ${C}`); });
    $$('.m-bar i', mini).forEach((b, i) => A(b, t, { sx: [[M.rank + i * .15, 0], [M.rank + .8 + i * .15, 1, 'out']] }));
    $('.mwv', mini).textContent = fmt(Math.round(kf(t, [[M.fund, 0], [M.fund + 1.1, 1400000, 'out5']]) / 1000) * 1000);
    A($('#mWork', mini), t, { s: [[M.fund - .1, 1], [M.fund + .25, 1.03, 'out'], [M.fund + .7, 1, 'out']] });
    const fab = $('.m-fab', mini), rb = $('.m-recbar', mini), [r0, r1] = M.rec;
    const rp = Math.min(kf(t, [[r0, 0], [r0 + .25, 1]]), kf(t, [[r1, 1], [r1 + .25, 0]]));
    fab.style.transform = `scale(${(1 + .3 * rp + .05 * rp * Math.sin(t * 8)).toFixed(3)})`;
    fab.style.background = rp > .5 ? 'radial-gradient(circle at 30% 25%,#ff7486,#e0475b 70%)' : '';
    A(rb, t, { o: rp, y: (1 - rp) * 30 });
    $('.rt', rb).textContent = `0:0${Math.max(0, Math.floor(t - r0))}`;
  };
  cue(M.t, 'sheet', .8); cue(M.donut, 'shimmer', .4); cue(M.rank, 'tick3', .5); cue(M.fund, 'roll', .5, { d: 1.1 });
  cue(M.rec[0], 'recstart', .7); cue(M.rec[1], 'recstop', .6);
}

// ---------- shared pieces ----------
function headHTML(n, title, sub) {
  return `<div class="hb"><div class="kicker">${n ? `<span class="pill">${n}</span><span class="line"></span>` : ''}</div>
    <div class="h1t">${title}</div>${sub ? `<div class="subt">${sub}</div>` : ''}</div>`;
}
function headAnim(root, t, t0) {
  const k = $('.kicker', root), ti = $('.h1t', root), su = $('.subt', root);
  A(k, t, IN(t0, { from: { x: -40, blur: 8 } }));
  if (!ti._w) ti._w = splitWords(ti);
  ti._w.forEach((w, i) => A(w, t, IN(t0 + .12 + i * .07, { d: .5, from: { y: 40, blur: 14 } })));
  if (su) A(su, t, IN(t0 + .45, { from: { y: 26, blur: 10 } }));
}
function chipAnim(el, t, t0, out, dir = 1) { A(el, t, IN(t0, { d: .55, e: 'back', from: { s: .7, x: 60 * dir, blur: 10 }, out, to: { s: .9, blur: 10 } })); }
function phoneMotion(el, t, t0, opt = {}) {
  const s = opt.s ?? 1.05, x = opt.x || 0;
  A(el, t, { o: [[t0, 0], [t0 + .3, 1]], s, x, y: [[t0, 640], [t0 + 1.0, 0, 'out5']], rx: [[t0, 24], [t0 + 1.1, 2.5, 'out']], ry: [[t0, opt.ry0 ?? -14], [t0 + 1.2, (opt.ry ?? -2) + 1.6 * Math.sin(t * .8), 'out']] });
}
const PHONE_WRAP = (id, top = 640, left = 230) => `<div class="persp" id="${id}" style="left:${left}px;top:${top}px;width:620px;height:1280px"></div>`;
const HL = (a, b, txt) => `<span data-a="${a.toFixed(2)}" data-b="${b.toFixed(2)}" style="border-radius:6px">${txt}</span>`;
const DRAFT = (said, extra = '') => `📝 <b>Ish topshirish</b>\n<i>🗣 «${said}»</i>\n📅 Sana: 14.10.2026 (bugun)\n👷 Ishchi: <b>Alisher</b>\n• A-12 — 50 ta × 5 000 so'm\n• B-5 — 30 ta × 4 000 so'm\n💰 Jami: <b>370 000 so'm</b>\n\n${extra || "<b>To'g'rimi?</b>"}`;
function notebookHTML(id, style) {
  const rows = ['Alisher  A-12 — 50', 'Vali  B-5 — 30', 'Dilnoza  C-7 — 45', '50 × 5000 = ?', 'Vali  A-12 — 40 ??'];
  return `<div class="note" id="${id}" style="${style}"><div class="lines"></div><div class="margin"></div><div class="rings"></div>
    <div class="t" style="top:30px;color:#c0392b">14-oktabr</div>${rows.map((r, i) => `<div class="t r" style="top:${110 + i * 128}px">${r}</div>`).join('')}
    <div class="cross" style="left:120px;top:${110 + 3 * 128 + 34}px;width:330px;transform:rotate(-3deg)"></div></div>`;
}

// =====================================================================
// boundaries (video time)
const B = [0, PARA(1) - .3, PARA(2) - .3, PARA(3) - .3, PARA(4) - .3, PARA(5) - .3, PARA(6) - .3, PARA(7) - .3, PARA(8) - .3, PARA(9) - .3, DURATION];
const W = (i, a = .2, b = .25) => [Math.max(0, B[i] - a), i === 9 ? DURATION : B[i + 1] + b];
