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

// ---------- background: navy mesh, dot grid, rings, gold coins ----------
const bg = $('#bg'), bx = bg.getContext('2d');
const COINS = (() => { const r = rng(42); return Array.from({ length: 11 }, (_, i) => ({ x: r() * 1080, y: r() * 2400, z: .25 + r() * .75, a: r() * 6.28, sp: (.4 + r() * .9) * (r() < .5 ? -1 : 1), vy: 6 + r() * 14, tilt: (r() - .5) * .5 })); })();
let CAM = [[0, 0]];
function drawCoin(x, y, R, a, alpha, blur) {
  const c = Math.cos(a), s = Math.sin(a), rx = Math.max(.06, Math.abs(c)) * R, th = R * .16;
  bx.save(); bx.globalAlpha = alpha; if (blur > .3) bx.filter = `blur(${blur.toFixed(1)}px)`;
  const n = 8;
  for (let k = n; k >= 1; k--) { bx.beginPath(); bx.ellipse(x + (k / n) * th * s * (c < 0 ? -1 : 1), y, rx, R, 0, 0, 6.2832); bx.fillStyle = k % 2 ? '#b8741d' : '#9c5f15'; bx.fill(); }
  const g = bx.createRadialGradient(x - rx * .35, y - R * .4, R * .05, x, y, R);
  g.addColorStop(0, '#fff3c4'); g.addColorStop(.35, '#ffd062'); g.addColorStop(.8, '#e6a12f'); g.addColorStop(1, '#b8741d');
  bx.beginPath(); bx.ellipse(x, y, rx, R, 0, 0, 6.2832); bx.fillStyle = g; bx.fill();
  bx.beginPath(); bx.ellipse(x, y, rx * .76, R * .76, 0, 0, 6.2832); bx.strokeStyle = 'rgba(150,90,15,.55)'; bx.lineWidth = R * .05; bx.stroke();
  bx.beginPath(); bx.ellipse(x - rx * .25, y - R * .35, rx * .35, R * .16, -.5, 0, 6.2832); bx.fillStyle = 'rgba(255,255,255,.35)'; bx.fill();
  bx.restore();
}
function drawBG(t) {
  const cam = kf(t, CAM);
  const g0 = bx.createLinearGradient(0, 0, 0, 1920);
  g0.addColorStop(0, '#0b1a52'); g0.addColorStop(.55, '#081240'); g0.addColorStop(1, '#040a26');
  bx.globalAlpha = 1; bx.filter = 'none'; bx.fillStyle = g0; bx.fillRect(0, 0, 1080, 1920);
  const blob = (x, y, r, c) => { const g = bx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, c); g.addColorStop(1, 'rgba(0,0,0,0)'); bx.fillStyle = g; bx.fillRect(0, 0, 1080, 1920); };
  blob(220 + 120 * Math.sin(t * .21), 380 + 80 * Math.cos(t * .17) - cam * .15, 900, 'rgba(61,99,224,.42)');
  blob(940 + 90 * Math.cos(t * .19), 1350 + 120 * Math.sin(t * .13) - cam * .1, 950, 'rgba(43,79,201,.35)');
  blob(860 + 60 * Math.sin(t * .3), 240 - cam * .2, 620, 'rgba(255,190,80,.10)');
  // dot grid
  bx.fillStyle = 'rgba(190,210,255,.09)';
  const oy = ((-t * 8 - cam * .35) % 56 + 56) % 56;
  for (let y = -56 + oy; y < 1920; y += 56) for (let x = 28; x < 1080; x += 56) { bx.beginPath(); bx.arc(x, y, 2.2, 0, 6.2832); bx.fill(); }
  // hero-circle rings (echo of the app card)
  bx.save(); bx.strokeStyle = 'rgba(255,255,255,.075)'; bx.lineWidth = 2;
  bx.beginPath(); bx.arc(1000, 120 - cam * .25, 520, 0, 6.2832); bx.stroke();
  bx.setLineDash([14, 16]); bx.lineDashOffset = -t * 12; bx.beginPath(); bx.arc(1000, 120 - cam * .25, 780, 0, 6.2832); bx.stroke();
  bx.setLineDash([]); bx.beginPath(); bx.arc(60, 1880 - cam * .2, 430, 0, 6.2832); bx.stroke(); bx.restore();
  // coins (far → near)
  const cs = COINS.map(c => {
    const y = ((c.y - t * c.vy - cam * c.z * .8) % 2400 + 2400) % 2400 - 240;
    return { ...c, yy: y, aa: c.a + t * c.sp };
  }).sort((a, b) => a.z - b.z);
  for (const c of cs) drawCoin(c.x + 30 * Math.sin(t * .3 + c.a), c.yy, 22 + 52 * c.z, c.aa, .22 + .5 * c.z, (1 - c.z) * 10);
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
  if (opt.noCue !== true && !el._cued) { el._cued = 1; cue(t0 + .05, 'phonein', .7); }
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
  ${PHONE_WRAP('pp', 640)}
  <div class="chip navy" id="k1" style="left:40px;top:930px">🎙 Siz gapirasiz</div>
  <div class="chip" id="k2" style="right:40px;top:1180px">✍️ U yozadi</div>
  <div class="chip gold" id="k3" style="left:40px;top:1430px">🧮 Hisoblaydi</div>
`, (root, S) => {
  const tE = T(0, 'endi'), tM = T(0, 'moliyachi'), tTg = T(0, "telegram'dagi"), tS = T(0, 'siz'), tG = T(0, 'gapirasiz'), tY = T(0, 'yozib'), tH = T(0, 'hisoblab');
  const tg = makeTG({ msgs: [
    { t: tG + .1, me: true, voice: 1.4, silent: true },
    { t: tY, html: DRAFT('Alisher A-12 modeldan 50 ta, B-5 dan 30 ta topshirdi').replace("370 000 so'm", HL(tH, tH + 1.4, "370 000 so'm")) },
  ] });
  $('#pp', root).appendChild(tg.el); root._measure = () => tg.measure();
  const q1 = $('#q1', root), nb = $('#nb', root), q2 = $('#q2', root), lg = $('#lg', root), nm = $('#nm', root), tgp = $('#tgp', root);
  const rows = $$('.note .r', root), cross = $('.note .cross', root), rings = ['#r1', '#r2'].map(s => $(s, root)), ks = ['#k1', '#k2', '#k3'].map(s => $(s, root));
  cue(.3, 'paper', .7); cue(tE - .05, 'crumple', .9); cue(tE + .1, 'whoosh', .7); cue(T(0, 'hojat') , 'impact', .55);
  cue(tM - .25, 'riser', .5); cue(tM, 'impact', .9); cue(tTg, 'swish', .6); cue(tG, 'send', .7); [tG, tY, tH].forEach(x => cue(x, 'pop', .6)); cue(tH + .1, 'shimmer', .5);
  return t => {
    A(q1, t, { ...IN(.2, { from: { y: 40, blur: 16 } }), o: [[.2, 0], [.6, 1], [tE - .1, 1], [tE + .2, 0]] });
    A(nb, t, { o: [[.1, 0], [.4, 1], [tE + .3, 1], [tE + .5, 0]], y: [[.1, 300], [.9, 0, 'out5'], [tE, 0], [tE + .5, 520, 'in']], r: [[.1, -14], [.9, -5, 'out'], [tE, -5], [tE + .5, 38, 'in']], s: [[tE, 1], [tE + .5, .45, 'in']], blur: [[tE + .2, 0], [tE + .5, 12]] });
    rows.forEach((r, i) => { const a = .7 + i * .38; r.style.clipPath = `inset(0 ${(100 - 100 * kf(t, [[a, 0], [a + .4, 1, 'out']])).toFixed(1)}% 0 0)`; });
    A(cross, t, { sx: [[2.5, 0], [2.75, 1, 'out']] });
    A(q2, t, { ...IN(tE + .15, { d: .5, e: 'back', from: { s: 1.3, blur: 20 } }), o: [[tE + .15, 0], [tE + .4, 1], [tM - .25, 1], [tM, 0]] });
    A(lg, t, { o: [[tM, 0], [tM + .2, 1], [tS - .1, 1], [tS + .25, 0]], s: [[tM, .2], [tM + .6, 1, 'back']], r: [[tM, -16], [tM + .7, 0, 'out']] });
    rings.forEach((r, i) => A(r, t, { o: [[tM + .1 + i * .12, 0], [tM + .15 + i * .12, .9], [tM + 1 + i * .12, 0, 'out']], s: [[tM + .1 + i * .12, 1], [tM + 1 + i * .12, 2.4 + i * .4, 'out']] }));
    A(nm, t, { ...IN(tM + .25, { from: { y: 40, blur: 16 } }), o: [[tM + .25, 0], [tM + .55, 1], [tS - .1, 1], [tS + .25, 0]] });
    A(tgp, t, { ...IN(tTg, { e: 'back', from: { s: .7, blur: 12 } }), o: [[tTg, 0], [tTg + .3, 1], [tS - .1, 1], [tS + .25, 0]] });
    if (t > tS - .2) phoneMotion(tg.el, t, tS); else tg.el.style.opacity = 0;
    tg.update(t);
    ks.forEach((k, i) => chipAnim(k, t, [tG, tY, tH][i], undefined, i === 1 ? -1 : 1));
  };
});

// SCENE 1 — models
scene(...W(1), headHTML('1-QADAM', 'Modellar va narx', '') + PHONE_WRAP('pp', 420) + `
  <div class="chip gold" id="c1" style="left:36px;top:1730px">① Model nomi<small>masalan: A-12</small></div>
  <div class="chip navy" id="c2" style="right:36px;top:1730px">② 1 dona uchun<br>ish haqi</div>`,
(root, S) => {
  const p = 1, tMod = T(p, 'modellarni'), tAdd = T(p, 'model'), tNom = T(p, 'nomini'), tHaq = T(p, 'haqini'), tA = T(p, "a-o'n"), tBesh = T(p, 'besh'), tEnd = T(p, "so'm", 1, true);
  const tg = makeTG({
    kb: [[0, 1], [tMod + .35, 0]], kbTaps: [{ label: 'Modellar', t: tMod }],
    msgs: [
      { t: -99, html: `Assalomu alaykum, <b>Abdurahmon</b>!` },
      { t: tMod + .2, me: true, html: '🧵 Modellar' },
      { t: tMod + .75, html: `🧵 <b>Modellar</b> (narx — bir dona uchun ish haqi)\n\n• <b>B-5</b> — 4 000 so'm\n• <b>C-7</b> — 6 000 so'm`, btns: [['💰 B-5', '⛔ O‘chirish'], ['💰 C-7', '⛔ O‘chirish'], [{ txt: "➕ Model qo'shish", tap: tAdd }]] },
      { t: tAdd + .5, html: 'Model nomini yozing (masalan: <i>A-12</i>):' },
      { t: tA + .75, typed: tA - .1, me: true, html: 'A-12' },
      { t: tA + 1.2, html: `🧵 <b>A-12</b>\n\nBir dona uchun ish haqini yozing.\nMasalan: <i>5000 so'm</i>.`, btns: [['Narxsiz qoldirish']] },
      { t: tEnd + .1, typed: tBesh - .1, me: true, html: '5000' },
      { t: tEnd + .5, html: `✅ <b>A-12</b> — 5 000 so'm saqlandi` },
    ] });
  $('#pp', root).appendChild(tg.el); root._measure = () => tg.measure();
  const c1 = $('#c1', root), c2 = $('#c2', root);
  cue(tNom, 'pop', .6); cue(tHaq, 'pop', .6);
  return t => {
    headAnim(root, t, S + .25);
    phoneMotion(tg.el, t, S + .35);
    tg.update(t);
    chipAnim(c1, t, tNom, tHaq + .9); chipAnim(c2, t, tHaq, undefined, -1);
  };
});

// SCENE 2 — workers
scene(...W(2), headHTML('2-QADAM', 'Ishchilarni ulang', '') + PHONE_WRAP('pp', 420) + `
  <div class="card" id="ct" style="right:28px;top:1180px;width:330px;padding:26px;text-align:center;z-index:46">
    <div style="width:110px;height:110px;margin:0 auto 14px;border-radius:50%;background:linear-gradient(140deg,#16307f,#3d63e0);color:#fff;display:grid;place-items:center;font-size:52px;font-weight:800">A</div>
    <div style="font-size:34px;font-weight:800">Alisher</div><div style="font-size:24px;color:#6b7591;font-weight:600;margin-top:4px">ishchi</div>
    <div class="sw" id="cst" style="display:inline-grid;margin-top:16px;font-size:24px;font-weight:800"><span style="grid-area:1/1;color:#e8972a">🔗 havola kutilmoqda</span><span style="grid-area:1/1;color:#12a26b;opacity:0">✅ ulandi</span></div></div>
  <div class="chip gold" id="lk" style="left:300px;top:1300px;font-size:26px;padding:16px 24px">🔗 t.me/…?start=inv_7Kq2</div>`,
(root, S) => {
  const p = 2, tIsh = T(p, 'ishchilar', 2), tAdd = T(p, "qo'shishni"), tIsm = T(p, 'ismini'), tBot = T(p, 'bot'), tHav = T(p, 'havola'), tYub = T(p, 'yuboring'), tUl = T(p, 'ulanadi');
  const tg = makeTG({
    kb: [[0, 1], [tIsh + .35, 0]], kbTaps: [{ label: 'Ishchilar', t: tIsh }],
    msgs: [
      { t: -99, html: `✅ <b>A-12</b> — 5 000 so'm saqlandi` },
      { t: tIsh + .2, me: true, html: '👷 Ishchilar' },
      { t: tIsh + .7, html: `👷 <b>Ishchilar</b>\n\n• <b>Vali</b> — ✅ ulangan\n• <b>Dilnoza</b> — ✅ ulangan`, btns: [[{ txt: "➕ Ishchi qo'shish", tap: tAdd }]] },
      { t: tAdd + .45, html: 'Yangi ishchining ismini yozing (masalan: <i>Alisher</i>):' },
      { t: tIsm + .9, typed: tIsm, me: true, html: 'Alisher' },
      { t: tBot + .2, html: `✅ <b>Alisher</b> qo'shildi.\n\nUshbu havolani ishchiga yuboring — u bosganda bot unga ulanadi:\n${HL(tHav, tYub + 1.2, '<span style="color:#2563eb;text-decoration:underline">t.me/moliyachi_bot?start=inv_7Kq2</span>')}\n\nHavola bir martalik.` },
    ],
    toast: { t: tUl - .1, hide: tUl + 2.2, html: '👷 Alisher botga ulandi.' } });
  $('#pp', root).appendChild(tg.el); root._measure = () => tg.measure();
  const ct = $('#ct', root), lk = $('#lk', root);
  $('#cst', root).dataset.at = (tUl).toFixed(2);
  cue(tYub, 'whoosh', .55); cue(tYub + .75, 'pop', .6); cue(tUl + .05, 'ding', .5);
  return t => {
    headAnim(root, t, S + .25);
    phoneMotion(tg.el, t, S + .35);
    tg.update(t);
    const p2 = E.io(clamp((t - tYub) / .8));
    A(lk, t, { o: Math.min(kf(t, [[tYub, 0], [tYub + .1, 1]]), kf(t, [[tYub + .7, 1], [tYub + .85, 0]])), x: 380 * p2, y: -120 * Math.sin(Math.PI * p2), s: 1 - .4 * p2 });
    $$('.sw', root).forEach(sw => { const at = +sw.dataset.at, q = kf(t, [[at, 0], [at + .3, 1]]); sw.children[0].style.opacity = 1 - q; sw.children[1].style.opacity = q; });
    A(ct, t, { o: [[tYub - .3, 0], [tYub, 1]], x: [[tYub - .3, 120], [tYub + .3, 0, 'back']], blur: [[tYub - .3, 12], [tYub + .2, 0]], s: [[tYub - .3, .8], [tYub + .3, 1, 'back'], [tUl, 1], [tUl + .15, 1.06, 'out'], [tUl + .45, 1, 'out']] });
  };
});

// SCENE 3 — entering work
const QUOTE = ["«Alisher", "A-o'n", 'ikki', 'modeldan', 'ellik', 'ta,', 'Be-besh', 'modeldan', "o'ttiz", 'ta', 'topshirdi».'];
scene(...W(3), headHTML('3-QADAM', 'Ishni kiriting', '') + PHONE_WRAP('pp', 590) + `
  <div class="kara" id="kr" style="top:410px">🎙 ${QUOTE.map(w => `<span class="word">${w.replace(/[«»]/g, '').replace("A-o'n", 'A-12').replace('ikki', '').replace('Be-besh', 'B-5').replace('ellik', '50').replace("o'ttiz", '30')}</span>`).join('')}</div>
  <div class="kara" id="kx" style="top:410px;font-size:36px">✏️ Xato bo'lsa — ayting: <span style="color:#ffc24a">«soni 45 ta»</span><div style="font-size:30px;color:rgba(255,255,255,.75);margin-top:10px;font-weight:600">• A-12 — <s style="opacity:.6">50 ta</s> → <b style="color:#fff">45 ta</b> ✓</div></div>
  <div class="kara" id="kp" style="top:410px;font-size:36px">📷 Daftar rasmini yuborsangiz ham bo'ladi</div>`,
(root, S) => {
  const p = 3;
  const tMic = T(p, 'mikrofonni'), tBos = T(p, 'bosib'), tQ0 = T(p, 'alisher'), tQ1 = T(p, 'topshirdi', 1, true), tKar = T(p, 'kartochka'), tTo = T(p, "to'g'rimi"), tSaq = T(p, 'saqlashni'), tX = T(p, 'xato'), tS45 = T(p, 'soni'), tD = T(p, 'daftaringiz');
  const qw = WORDS.filter(w => w.para === p && w.t0 + OFF >= tQ0 - .01 && w.t0 + OFF <= tQ1);
  const tg = makeTG({
    rec: [tBos, tQ1 + .25], micTap: tBos,
    msgs: [
      { t: -99, html: `✅ <b>Alisher</b> qo'shildi.` },
      { t: -99, html: '👷 Alisher botga ulandi.' },
      { t: tQ1 + .35, me: true, voice: 1.8 },
      { t: tKar, html: DRAFT('Alisher A-12 modeldan 50 ta, B-5 dan 30 ta topshirdi', HL(tTo, tTo + 1.3, "<b>To'g'rimi?</b>")), btns: [[{ txt: '✅ Saqlash', tap: tSaq }], ['✏️ Tuzatish', '❌ Bekor'], ['📅 Bugun', '📅 Kecha']] },
      { t: tSaq + .45, html: `✅ <b>Saqlandi</b>\n<b>#127</b> · A-12 — <b>50 ta</b> · 250 000 so'm\n<b>#128</b> · B-5 — <b>30 ta</b> · 120 000 so'm` },
      { t: tD + .5, me: true, photo: `<div style="position:absolute;inset:0;background:#fbf8ee"><div style="position:absolute;inset:0;background:repeating-linear-gradient(180deg,transparent 0 37px,rgba(60,110,200,.3) 37px 39px)"></div><div style="position:absolute;left:46px;top:0;bottom:0;width:2px;background:rgba(220,60,70,.55)"></div>${['Alisher A-12 — 50', 'Vali B-5 — 30', 'Dilnoza C-7 — 45', 'Vali A-12 — 40'].map((r, i) => `<div style="position:absolute;left:60px;top:${18 + i * 76}px;font:600 40px Caveat;color:#1f3b8a;white-space:nowrap">${r}</div>`).join('')}</div>` },
    ] });
  $('#pp', root).appendChild(tg.el); root._measure = () => tg.measure();
  const kr = $('#kr', root), kx = $('#kx', root), kp = $('#kp', root), kws = $$('#kr .word', root);
  cue(tKar - .1, 'shimmer', .4); cue(tX, 'swish', .6); cue(tS45, 'pop', .6); cue(tD, 'swish', .6); cue(tD + .5, 'shutter', .6);
  return t => {
    headAnim(root, t, S + .25);
    phoneMotion(tg.el, t, S + .35);
    tg.update(t);
    A(kr, t, IN(tQ0 - .5, { from: { y: 30, blur: 12 }, out: tX - .6 }));
    kws.forEach((w, i) => { const on = qw[i] && t >= qw[i].t0 + OFF; w.classList.toggle('on', !!on); w.classList.toggle('key', !!on && /\d/.test(w.textContent)); });
    A(kx, t, IN(tX, { from: { y: 30, blur: 12 }, out: tD - .5 }));
    A($('#kx div', root), t, IN(tS45 + .4, { from: { x: -30, blur: 8 } }));
    A(kp, t, IN(tD, { from: { y: 30, blur: 12 } }));
  };
});

// SCENE 4 — approval (two phones)
scene(...W(4), headHTML('4-QADAM', 'Ishchi yozadi —<br>siz tasdiqlaysiz', '') + `
  <div class="lbl" style="left:30px;width:484px;top:590px">Ishchi · Vali</div><div class="lbl" style="left:566px;width:484px;top:590px">Siz · ega</div>
  ${PHONE_WRAP('pw', 645, 30)}${PHONE_WRAP('po', 645, 566)}
  <div class="dot2" id="d1"></div><div class="dot2" id="d2"></div>
  <div class="abs center" id="cap" style="top:1690px;font-size:56px;font-weight:800;letter-spacing:-.025em;line-height:1.15">Faqat <span class="grad-g">tasdiqlangan</span> ish<br>hisobotga kiradi</div>`,
(root, S) => {
  const p = 4, tYoz = T(p, 'yozishi'), tMum = T(p, 'mumkin'), tYub = T(p, 'yuborgan'), tTas = T(p, 'tasdiq'), tBos = T(p, 'tasdiqlashni'), tQo = T(p, "qo'shiladi"), tEs = T(p, 'esda');
  const pw = makeTG({ msgs: [
    { t: -99, html: 'Xush kelibsiz, <b>Vali</b>!' },
    { t: tMum - .2, typed: tYoz - .5, me: true, html: 'A-12 dan 40 ta topshirdim' },
    { t: tMum + .5, html: `✅ <b>Saqlandi</b>\n⏳ Egasining tasdig'ini kutmoqda.` },
    { t: tQo + .2, html: `Yozuvingiz ✅ tasdiqlandi:\n\n<b>#129</b> · 14.10.2026\n🧵 A-12 — <b>40 ta</b>\n💰 200 000 so'm` },
  ] });
  const po = makeTG({ msgs: [
    { t: -99, html: `✅ <b>Saqlandi</b>\n#127 · A-12 — 50 ta` },
    { t: tTas - .05, html: `🔔 <b>Yangi yozuv tasdiq kutmoqda</b>\n\n<b>#129</b> · 14.10.2026 (bugun)\n👷 Vali\n🧵 A-12 — <b>40 ta</b>\n💰 200 000 so'm\n<span class="sw" data-at="${(tBos + .25).toFixed(2)}" style="display:inline-grid"><span style="grid-area:1/1">⏳ tasdiq kutmoqda</span><span style="grid-area:1/1;color:#12a26b;font-weight:700">✅ tasdiqlangan</span></span>`, btns: [[{ txt: '✅ Tasdiqlash', tap: tBos }, '🚫 Rad etish'], ['✏️ Tahrirlash', "🗑 O'chirish"]] },
  ] });
  $('#pw', root).appendChild(pw.el); $('#po', root).appendChild(po.el);
  root._measure = () => { pw.measure(); po.measure(); };
  const d1 = $('#d1', root), d2 = $('#d2', root), cap = $('#cap', root), lbls = $$('.lbl', root);
  cue(tYub, 'whoosh', .5); cue(tTas - .1, 'notify', .7); cue(tQo, 'whoosh', .4); cue(tQo + .05, 'ding', .55); cue(tEs, 'riser', .35); cue(tEs + .55, 'impact', .45);
  const arc = (t, t0, t1, a, b, lift) => { const q = E.io(clamp((t - t0) / (t1 - t0))); return [a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q - Math.sin(Math.PI * q) * lift]; };
  return t => {
    headAnim(root, t, S + .25);
    lbls.forEach((l, i) => A(l, t, IN(S + .7 + i * .12)));
    pw.el.style.transformOrigin = '0 0'; po.el.style.transformOrigin = '0 0';
    A(pw.el, t, { o: [[S + .35, 0], [S + .65, 1]], s: .78, y: [[S + .35, 700], [S + 1.35, 0, 'out5']], ry: [[S + .35, 30], [S + 1.4, 10, 'out']] });
    A(po.el, t, { o: [[S + .5, 0], [S + .8, 1]], s: .78, y: [[S + .5, 700], [S + 1.5, 0, 'out5']], ry: [[S + .5, -30], [S + 1.55, -10, 'out']] });
    if (!root._c) { root._c = 1; cue(S + .4, 'phonein', .6); }
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

// SCENE 6 — report
scene(...W(6), headHTML('HISOBOT', 'Hisobot — bir bosishda', '') + PHONE_WRAP('pp', 420) + `
  <div class="card" id="wr" style="left:560px;top:860px;width:480px;padding:30px 32px;font-family:Inter,'Noto Color Emoji';font-size:27px;line-height:1.45;z-index:46">
    <div style="font-family:Manrope;font-size:22px;font-weight:800;letter-spacing:.12em;color:#3d63e0;margin-bottom:10px">ALISHER KO'RADI</div>
    📊 <b>Shu oy</b> (01.10 – 31.10)<br><br>• A-12 — 50 ta — 250 000 so'm<br>• B-5 — 30 ta — 120 000 so'm<br><br>Jami: <b>80 ta</b><br>Ish haqi: <b style="color:#16307f">370 000 so'm</b></div>`,
(root, S) => {
  const p = 6, tH = T(p, 'hisobotni', 1), tHb = T(p, 'hisobotni', 2), tBug = T(p, 'bugun'), tHaf = T(p, 'hafta'), tOy = T(p, 'oy'), tNe = T(p, 'nechta'), tQa = T(p, 'qancha'), tUm = T(p, 'umumiy'), tKor = T(p, "ko'rsatadi", 1, true), tIsh = T(p, 'ishchi', 2);
  const rep = `📊 <b>Shu oy</b> (01.10 – 31.10)\n\n👷 ${HL(tNe, tQa, '<b>Alisher</b> — 80 ta')}\n   A-12: 50, B-5: 30\n   ${HL(tQa, tUm, "💰 370 000 so'm")}\n\n👷 ${HL(tNe, tQa, '<b>Dilnoza</b> — 95 ta')}\n   B-5: 50, C-7: 45\n   ${HL(tQa, tUm, "💰 470 000 so'm")}\n\n👷 ${HL(tNe, tQa, '<b>Vali</b> — 100 ta')}\n   A-12: 40, C-7: 60\n   ${HL(tQa, tUm, "💰 560 000 so'm")}\n\n🧵 <b>Modellar bo'yicha</b>\n• A-12 — 90 ta\n• B-5 — 80 ta\n• C-7 — 105 ta\n\nJami: <b>275 ta</b>\n${HL(tUm, tKor + .6, "Ish haqi fondi: <b>1 400 000 so'm</b>")}`;
  const tg = makeTG({
    kb: [[0, 1], [tHb + .35, 0]], kbTaps: [{ label: 'Hisobot', t: tHb }],
    msgs: [
      { t: -99, html: `✅ <b>Saqlandi</b>\n#127 · A-12 — 50 ta` },
      { t: tHb + .2, me: true, html: '📊 Hisobot' },
      { t: tHb + .7, hide: tOy + .45, html: '📊 Qaysi davr uchun?', btns: [[{ txt: 'Bugun', hl: [tBug, tHaf] }, { txt: 'Shu hafta', hl: [tHaf, tOy] }, { txt: 'Shu oy', hl: [tOy, tOy + .4], tap: tOy + .2 }], ['📱 Ilovani ochish']] },
      { t: tOy + .45, replaces: 2, read: [tNe - .2, tKor], silent: true, html: rep, btns: [['Bugun', 'Shu hafta', 'Shu oy'], ['📱 Ilovani ochish']] },
    ] });
  $('#pp', root).appendChild(tg.el); root._measure = () => tg.measure();
  const wr = $('#wr', root);
  cue(tOy + .45, 'shimmer', .5); cue(tIsh - .1, 'whoosh', .5); cue(tIsh + .2, 'pop', .6);
  return t => {
    headAnim(root, t, S + .25);
    phoneMotion(tg.el, t, S + .35, { x: kf(t, [[tIsh - .2, 0], [tIsh + .5, -190, 'io']]) });
    tg.update(t);
    A(wr, t, IN(tIsh + .1, { d: .65, e: 'back', from: { x: 140, s: .85, blur: 14 } }));
  };
});

// SCENE 7 — mini app
scene(...W(7), headHTML('ILOVA', 'Grafiklar ilovada', '') + PHONE_WRAP('pp', 420),
(root, S) => {
  const p = 7, tIl = T(p, 'ilova'), tBu = T(p, 'bu'), tFo = T(p, 'fondi'), tDi = T(p, 'diagramma'), tMo = T(p, 'modellar'), tRe = T(p, 'reytingi'), tIsh = T(p, 'ishchilar'), tMik = T(p, 'mikrofon'), tYoz = T(p, 'mumkin', 1, true);
  const tg = makeTG({
    ilovaTap: tIl,
    msgs: [{ t: -99, html: `📊 <b>Shu oy</b>\nJami: <b>275 ta</b>\nIsh haqi fondi: <b>1 400 000 so'm</b>` }],
    mini: true });
  const M = { t: tIl + .2, fund: tFo - .2, donut: tMo, rank: tIsh, rec: [tMik + .2, tYoz + .2], scroll: [[tBu - .3, 'mWork', .8], [tMo - .4, 'mDonut', .9], [tIsh - .5, 'mRank', .9], [tMik - .6, 0, 1.0]] };
  tg.el._o = M;
  $('#pp', root).appendChild(tg.el);
  bindMini(tg, { mini: M });
  root._measure = () => tg.measure();
  // finger on the fab in the mini app
  const fing = h(`<div class="finger"></div>`); tg.screen.appendChild(fing);
  cue(tMik + .2, 'tap', .9);
  return t => {
    headAnim(root, t, S + .25);
    phoneMotion(tg.el, t, S + .35);
    tg.update(t);
    const fab = $('.m-fab', tg.el);
    let x = 0, y = 0, e = fab; while (e && e !== tg.screen) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; }
    fing.style.left = (x + 53) + 'px'; fing.style.top = (y + 53) + 'px';
    A(fing, t, { o: [[tMik - .4, 0], [tMik - .1, 1], [tYoz + .2, 1], [tYoz + .5, 0]], s: [[tMik - .4, 1.3], [tMik + .2, .8, 'out']] });
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

// camera parallax: background drifts on every cut
CAM = [[0, 0]];
B.slice(1, -1).forEach((b, i) => CAM.push([b - .3, i * 260], [b + .6, (i + 1) * 260, 'io']));
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
init().catch(e => { console.error(e); window.ERR = String(e); throw e; });
