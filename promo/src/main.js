// Moliyachi Pro — motion guide. Deterministic: every frame depends only on t (renderAt(t)).
'use strict';
const ICON = '../assets/images/icon.png';
const DURATION = 86.5;

// ---------- easing + keyframes ----------
const E = {
  lin: x => x,
  out: x => 1 - Math.pow(1 - x, 3),
  out5: x => 1 - Math.pow(1 - x, 5),
  in: x => x * x * x,
  io: x => (x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  sine: x => -(Math.cos(Math.PI * x) - 1) / 2,
  back: x => { const c1 = 1.3, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
};
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
function kf(t, keys) {
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [t1, v1, e] = keys[i];
    if (t <= t1) {
      const [t0, v0] = keys[i - 1];
      const p = clamp((t - t0) / ((t1 - t0) || 1e-9));
      return v0 + (v1 - v0) * E[e || 'io'](p);
    }
  }
  return keys[keys.length - 1][1];
}
const val = (t, x) => (Array.isArray(x) ? kf(t, x) : x);

// Apply a pose: {o,x,y,z,s,sx,sy,r,rx,ry,blur}
function A(el, t, p) {
  const g = k => (p[k] === undefined ? undefined : val(t, p[k]));
  const o = g('o');
  if (o !== undefined) el.style.opacity = o.toFixed(4);
  const x = g('x') || 0, y = g('y') || 0, z = g('z') || 0, s = g('s'), r = g('r') || 0, rx = g('rx') || 0, ry = g('ry') || 0;
  const sx = g('sx'), sy = g('sy');
  let tr = `translate3d(${x.toFixed(2)}px,${y.toFixed(2)}px,${z.toFixed(2)}px)`;
  if (rx) tr += ` rotateX(${rx.toFixed(3)}deg)`;
  if (ry) tr += ` rotateY(${ry.toFixed(3)}deg)`;
  if (r) tr += ` rotate(${r.toFixed(3)}deg)`;
  if (s !== undefined) tr += ` scale(${s.toFixed(4)})`;
  if (sx !== undefined || sy !== undefined) tr += ` scale(${(sx ?? 1).toFixed(4)},${(sy ?? 1).toFixed(4)})`;
  el.style.transform = tr;
  const b = g('blur');
  if (b !== undefined) el.style.filter = b > .05 ? `blur(${b.toFixed(2)}px)` : 'none';
}
// Entrance (and optional exit) pose spec
function IN(t0, opt = {}) {
  const d = opt.d ?? .55, e = opt.e ?? 'out', from = opt.from ?? { y: 46, blur: 12 };
  const p = { o: [[t0, 0], [t0 + d * .8, 1, 'out']] };
  for (const k in from) {
    const rest = k === 's' || k === 'sx' || k === 'sy' ? 1 : 0;
    p[k] = [[t0, from[k]], [t0 + d, rest, e]];
  }
  if (opt.out !== undefined) {
    const t1 = opt.out, d1 = opt.d1 ?? .35, to = opt.to ?? { y: -30, blur: 10 };
    p.o.push([t1, 1], [t1 + d1, 0, 'in']);
    for (const k in to) {
      const rest = k === 's' || k === 'sx' || k === 'sy' ? 1 : 0;
      if (!p[k]) p[k] = [[t1, rest]];
      p[k].push([t1, rest], [t1 + d1, to[k], 'in']);
    }
  }
  return p;
}

// ---------- seeded random ----------
function rng(seed) {
  let a = seed >>> 0;
  return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}

// ---------- DOM helpers ----------
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(html) { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstElementChild; }
function splitWords(el) {
  const html = el.innerHTML;
  // keep tags intact: only split plain text segments of top-level text nodes
  const out = [];
  el.childNodes.forEach(n => {
    if (n.nodeType === 3) n.textContent.split(/(\s+)/).forEach(w => out.push(/^\s+$/.test(w) ? w : w ? `<span class="word">${w}</span>` : ''));
    else { n.classList.add('word'); out.push(n.outerHTML); }
  });
  el.innerHTML = out.join('');
  return $$('.word', el);
}
function words(t, ws, t0, stagger = .07, opt = {}) {
  ws.forEach((w, i) => A(w, t, IN(t0 + i * stagger, { d: .5, from: { y: 40, blur: 14 }, ...opt })));
}
const fmt = n => Math.round(n).toLocaleString('en-US').replace(/,/g, ' ');

// ---------- cues (sound design) ----------
const CUES = [];
const cue = (t, type, g = 1) => CUES.push({ t: +t.toFixed(3), type, g });

// ---------- scenes ----------
const SCENES = [];
function scene(start, end, html, setup, opt = {}) {
  const root = h(`<div class="scene">${html}</div>`);
  $('#scenes').appendChild(root);
  const S = { start, end, root, opt, update: null };
  SCENES.push(S);
  S.setup = () => { S.update = setup(root, start); };
  if (!opt.noIn) cue(start + .02, 'whoosh', .55);
  return S;
}

// ---------- background: neon ribbon + glows ----------
const bg = document.getElementById('bg');
const bx = bg.getContext('2d');
const RPTS = [[-1.9, 1.15], [-1.05, .62], [-.2, .12], [.5, -.48], [1.12, -.3], [1.02, .42], [.36, .6], [-.18, .18], [.02, -.58], [.68, -1.12], [1.9, -1.4]];
// ribbon layout per time: [cx, cy, scale, rotDeg, alpha]
const RIB = [];
function ribAt(t0, t1, P) { RIB.push([t0, t1, P]); }
function ribParams(t) {
  // hold P inside [t0,t1], blend between neighbours outside
  let P = RIB[0][2];
  for (let i = 0; i < RIB.length; i++) {
    const [a, b, Q] = RIB[i];
    if (t >= a && t <= b) return Q;
    const nx = RIB[i + 1];
    if (nx && t > b && t < nx[0]) {
      const p = E.io((t - b) / (nx[0] - b));
      return Q.map((v, k) => v + (nx[2][k] - v) * p);
    }
    if (t > b) P = Q;
  }
  return P;
}
function catmull(pts, seg = 22) {
  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    for (let j = 0; j < seg; j++) {
      const s = j / seg, s2 = s * s, s3 = s2 * s;
      out.push([0, 1].map(k => .5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * s + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * s2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * s3)));
    }
  }
  out.push(pts[pts.length - 1]);
  return out;
}
function drawRibbon(t) {
  const [cx, cy, sc, rot, alpha] = ribParams(t);
  const R = 540 * sc, a = rot * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
  const pts = RPTS.map(([x, y], i) => {
    x += .07 * Math.sin(t * .55 + i * 1.7); y += .07 * Math.cos(t * .47 + i * 1.3);
    return [cx + R * (x * ca - y * sa), cy + R * (x * sa + y * ca)];
  });
  const P = catmull(pts);
  const prog = kf(t, [[0, 0], [1.9, 1, 'io']]);
  const n = Math.max(2, Math.floor(P.length * prog));
  const W = 128 * sc;
  const path = (ox = 0, oy = 0) => { bx.beginPath(); bx.moveTo(P[0][0] + ox, P[0][1] + oy); for (let i = 1; i < n; i++) bx.lineTo(P[i][0] + ox, P[i][1] + oy); };
  bx.save();
  bx.globalAlpha = alpha;
  bx.lineCap = 'round'; bx.lineJoin = 'round';
  // glow
  bx.shadowColor = 'rgba(120,80,255,.85)'; bx.shadowBlur = 90;
  path(); bx.strokeStyle = '#25106e'; bx.lineWidth = W * 1.05; bx.stroke();
  bx.shadowBlur = 0;
  const layers = [[.9, '#3c1fc0', 0], [.72, '#5a3be8', -.04], [.52, '#7a5cff', -.09], [.32, '#a08aff', -.15], [.16, '#d4c8ff', -.2], [.05, 'rgba(255,255,255,.9)', -.24]];
  for (const [w, c, off] of layers) { path(off * W * .7, off * W * .8); bx.strokeStyle = c; bx.lineWidth = W * w; bx.stroke(); }
  bx.restore();
}
function drawProtect(z, k) {
  const [cx, cy, rx, ry, a] = z;
  bx.save(); bx.translate(cx, cy); bx.scale(rx / ry, 1);
  const g = bx.createRadialGradient(0, 0, 0, 0, 0, ry);
  g.addColorStop(0, `rgba(7,6,26,${(a * k).toFixed(3)})`); g.addColorStop(.6, `rgba(7,6,26,${(a * k * .75).toFixed(3)})`); g.addColorStop(1, 'rgba(7,6,26,0)');
  bx.fillStyle = g; bx.fillRect(-ry, -ry, 2 * ry, 2 * ry); bx.restore();
}
function drawBG(t) {
  bx.globalAlpha = 1;
  bx.fillStyle = '#07061a'; bx.fillRect(0, 0, 1080, 1920);
  const g1 = bx.createRadialGradient(820 + 80 * Math.sin(t * .2), 360, 0, 820, 360, 1000);
  g1.addColorStop(0, 'rgba(110,75,245,.38)'); g1.addColorStop(1, 'rgba(110,75,245,0)');
  bx.fillStyle = g1; bx.fillRect(0, 0, 1080, 1920);
  const g2 = bx.createRadialGradient(180, 1600 + 60 * Math.cos(t * .25), 0, 180, 1600, 1000);
  g2.addColorStop(0, 'rgba(61,99,224,.30)'); g2.addColorStop(1, 'rgba(61,99,224,0)');
  bx.fillStyle = g2; bx.fillRect(0, 0, 1080, 1920);
  drawRibbon(t);
}
// grain
const gc = document.getElementById('grain'), gx = gc.getContext('2d'), gimg = gx.createImageData(540, 960);
function drawGrain(t) {
  const r = rng(Math.floor(t * 30) + 7), d = gimg.data;
  for (let i = 0; i < d.length; i += 4) { const v = r() * 255; d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255; }
  gx.putImageData(gimg, 0, 0);
  gc.style.width = '1080px'; gc.style.height = '1920px';
}

// ---------- phone ----------
const STATUS = `<div class="island"></div><div class="status"><span>9:41</span><span class="ic"><span class="bars"><i style="height:8px"></i><i style="height:12px"></i><i style="height:16px"></i><i style="height:21px"></i></span><span style="font-size:24px">📶</span><span class="bat"></span></span></div>`;
function makePhone(o) {
  const ph = h(`<div class="phone"><div class="screen">${STATUS}
    <div class="chat-head"><span class="back">‹</span><img src="${ICON}"><div><div class="nm">Moliyachi</div><div class="st">bot</div></div></div>
    <div class="chat-view"><div class="chat-list"></div></div>
    <div class="chat-input"><div class="field">Ovoz, matn yoki rasm…</div><div class="mic">🎙</div></div>
    <div class="finger"></div></div></div>`);
  const list = $('.chat-list', ph), view = $('.chat-view', ph), finger = $('.finger', ph), screen = $('.screen', ph);
  const taps = [];
  o.msgs.forEach((m, mi) => {
    const w = h(`<div class="msg ${m.me ? 'me' : 'bot'}"></div>`);
    if (m.voice) {
      const r = rng(11 + mi);
      const bars = Array.from({ length: 30 }, (_, i) => `<i style="height:${(10 + 46 * Math.pow(Math.abs(Math.sin(i * .55 + 1)) * .6 + r() * .4, 1.2)).toFixed(0)}px"></i>`).join('');
      w.appendChild(h(`<div class="bubble voice"><div class="play">▶</div><div class="wave">${bars}</div><div class="dur">0:05</div></div>`));
    } else {
      w.appendChild(h(`<div class="bubble">${m.html}</div>`));
    }
    if (m.btns) {
      const kb = h(`<div class="kb"></div>`);
      m.btns.forEach(row => {
        const r = h(`<div class="row" style="grid-template-columns:repeat(${row.length},1fr)"></div>`);
        row.forEach(b => {
          const B = typeof b === 'string' ? { txt: b } : b;
          const be = h(`<div class="btn${B.primary ? ' primary' : ''}">${B.txt}<span class="ripple"></span></div>`);
          r.appendChild(be);
          if (B.tap !== undefined) taps.push({ t: B.tap, el: be, m });
        });
        kb.appendChild(r);
      });
      w.appendChild(kb);
    }
    m.el = w;
    list.appendChild(w);
  });
  let toast = null;
  if (o.toast) {
    toast = h(`<div class="toast"><img src="${ICON}"><div><b>Moliyachi · hozir</b>${o.toast.html}</div></div>`);
    screen.appendChild(toast);
  }
  const vis = (m, t) => t >= m.t && (m.hide === undefined || t < m.hide);
  let scrollKeys = [[0, 0]];
  function measure() {
    const evs = [...new Set(o.msgs.flatMap(m => (m.hide !== undefined ? [m.t, m.hide] : [m.t])))].sort((a, b) => a - b);
    const vh = view.clientHeight;
    let prev = 0, last = -1;
    scrollKeys = [[0, 0]];
    for (const te of evs) {
      o.msgs.forEach(m => { m.el.style.display = vis(m, te + 1e-6) ? 'flex' : 'none'; });
      const target = Math.max(0, list.offsetHeight - vh);
      const st = Math.max(te, last);
      const nm = o.msgs.find(m => m.t === te && m.slow);
      if (nm) {
        const top = Math.min(target, Math.max(0, nm.el.offsetTop - 24));
        scrollKeys.push([st, prev], [st + .45, top, 'out'], [st + .45 + nm.slow, target, 'io']);
        last = st + .45 + nm.slow;
      } else {
        scrollKeys.push([st, prev], [st + .5, target, 'out']);
        last = st + .5;
      }
      prev = target;
    }
    // tap positions (relative to chat-list), measured with their message visible
    taps.forEach(tp => {
      o.msgs.forEach(m => { m.el.style.display = vis(m, tp.t) ? 'flex' : 'none'; });
      let x = 0, y = 0, e = tp.el;
      while (e && e !== list) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; }
      tp.x = x + tp.el.offsetWidth / 2; tp.y = y + tp.el.offsetHeight / 2;
    });
  }
  function update(t) {
    o.msgs.forEach(m => {
      const on = vis(m, t);
      m.el.style.display = on ? 'flex' : 'none';
      if (!on) return;
      if (m.fade) A(m.el, t, { o: [[m.t, 0], [m.t + .35, 1]] });
      else A(m.el, t, { o: [[m.t, 0], [m.t + .22, 1, 'out']], s: [[m.t, .82], [m.t + .45, 1, 'back']], y: [[m.t, 30], [m.t + .45, 0, 'out']] });
      if (m.voice) {
        const p = kf(t, [[m.t + .35, 0], [m.t + .35 + (m.voice || 2), 1, 'lin']]);
        const bars = $$('.wave i', m.el);
        bars.forEach((b, i) => b.classList.toggle('on', i / bars.length < p));
        $('.play', m.el).textContent = p > 0 && p < 1 ? '❚❚' : '▶';
        $('.dur', m.el).textContent = `0:0${Math.min(5, Math.floor(p * 5 + (p > 0 ? 1 : 0)) || 5)}`;
      }
    });
    $$('.sw', ph).forEach(sw => {
      const at = +sw.dataset.at, p = kf(t, [[at, 0], [at + .3, 1]]);
      sw.children[0].style.opacity = 1 - p; sw.children[1].style.opacity = p;
    });
    const sc = kf(t, scrollKeys);
    list.style.transform = `translateY(${-sc.toFixed(2)}px)`;
    let fo = 0;
    taps.forEach(tp => {
      const rp = $('.ripple', tp.el);
      A(rp, t, { o: [[tp.t, 0], [tp.t + .05, .9], [tp.t + .6, 0, 'out']], s: [[tp.t, 0], [tp.t + .6, 1.4, 'out']] });
      A(tp.el, t, { s: [[tp.t - .05, 1], [tp.t + .06, .94, 'out'], [tp.t + .3, 1, 'out']] });
      const near = t > tp.t - .55 && t < tp.t + .5;
      if (near) {
        fo = kf(t, [[tp.t - .55, 0], [tp.t - .3, 1], [tp.t + .2, 1], [tp.t + .5, 0]]);
        const fs = kf(t, [[tp.t - .55, 1.3], [tp.t - .3, 1, 'out'], [tp.t, .82, 'out'], [tp.t + .2, 1]]);
        finger.style.left = tp.x + 'px';
        finger.style.top = (196 + tp.y - sc) + 'px';
        finger.style.transform = `scale(${fs})`;
      }
    });
    finger.style.opacity = fo;
    if (toast) A(toast, t, { o: [[o.toast.t, 0], [o.toast.t + .3, 1], [o.toast.hide, 1], [o.toast.hide + .3, 0]], y: [[o.toast.t, -160], [o.toast.t + .5, 0, 'back'], [o.toast.hide, 0], [o.toast.hide + .35, -160, 'in']] });
  }
  // sound cues for messages + taps
  o.msgs.forEach(m => { if (!m.silent) cue(m.t, m.me ? 'send' : 'pop', m.me ? .7 : .8); });
  taps.forEach(tp => cue(tp.t, 'click', 1));
  if (o.toast) cue(o.toast.t, 'ding', .9);
  return { el: ph, measure, update };
}

// ---------- shared step header ----------
function headHTML(n, title, sub, extra = '') {
  return `<div class="abs hb" style="left:84px;right:100px;top:222px;display:flex;flex-direction:column;gap:22px">
    <div class="kicker" style="position:static">${n ? `<span class="pill">${n}</span>` : ''}</div>
    <div class="disp h1t" style="font-size:88px;font-weight:700;line-height:1.03;letter-spacing:-.03em">${title}</div>
    ${sub ? `<div class="subt" style="font-size:38px;line-height:1.3;color:var(--muted)">${sub}</div>` : ''}${extra}</div>`;
}
function headAnim(root, t, t0) {
  const k = $('.kicker', root), ti = $('.h1t', root), su = $('.subt', root);
  A(k, t, IN(t0, { from: { x: -40, blur: 8 } }));
  if (!ti._w) ti._w = splitWords(ti);
  words(t, ti._w, t0 + .12, .07);
  if (su) A(su, t, IN(t0 + .45, { from: { y: 26, blur: 10 } }));
}
function chip(html, cls, style) { return `<div class="chip ${cls}" style="${style}">${html}</div>`; }
function chipAnim(el, t, t0, out, dir = 1) {
  A(el, t, IN(t0, { d: .55, e: 'back', from: { s: .7, x: 60 * dir, blur: 10 }, out, to: { s: .9, blur: 10 } }));
}
function placePhone(ph, wrapStyle) { const w = h(`<div class="persp" style="${wrapStyle}"></div>`); w.appendChild(ph.el); return w; }

// =====================================================================
// SCENE 0 — HOOK
scene(0, 3.9, `
  <div class="abs center disp" id="l1" style="top:600px;font-size:128px;font-weight:300;letter-spacing:-.02em"><span style="position:relative;display:inline-block">Daftar.<i class="strike" style="left:-14px;right:-14px;top:54%"></i></span></div>
  <div class="abs center disp" id="l2" style="top:770px;font-size:128px;font-weight:300;letter-spacing:-.02em"><span style="position:relative;display:inline-block">Kalkulyator.<i class="strike" style="left:-14px;right:-14px;top:54%"></i></span></div>
  <div class="abs center disp" id="l3" style="top:1010px;font-size:104px;font-weight:600;letter-spacing:-.03em">Endi faqat</div>
  <div class="abs center disp grad-v glow-v" id="l4" style="top:1115px;font-size:190px;font-weight:800;letter-spacing:-.045em;line-height:1.1">gapiring.</div>
  <div class="abs" id="wv" style="left:340px;width:400px;top:1400px;height:110px;display:flex;align-items:center;gap:9px"></div>
`, (root, S) => {
  const wv = $('#wv', root);
  for (let i = 0; i < 24; i++) wv.appendChild(h(`<i style="display:block;flex:1;border-radius:6px;background:linear-gradient(180deg,#d4c8ff,#6e4bf5);height:20px"></i>`));
  const [l1, l2, l3, l4] = ['#l1', '#l2', '#l3', '#l4'].map(s => $(s, root));
  const [s1, s2] = $$('.strike', root);
  cue(.15, 'pop', .8); cue(.72, 'scratch', 1); cue(.95, 'pop', .8); cue(1.52, 'scratch', 1);
  cue(1.25, 'riser', .8); cue(2.1, 'impact', 1);
  return t => {
    A(l1, t, { ...IN(.15, { from: { y: 50, blur: 16 } }), o: [[.15, 0], [.5, 1], [1.7, 1], [2.0, .28]] });
    A(s1, t, { sx: [[.72, 0], [.98, 1, 'out']] });
    A(l2, t, { ...IN(.95, { from: { y: 50, blur: 16 } }), o: [[.95, 0], [1.3, 1], [1.8, 1], [2.05, .28]] });
    A(s2, t, { sx: [[1.52, 0], [1.78, 1, 'out']] });
    A(l3, t, IN(1.95, { from: { y: 40, blur: 14 } }));
    A(l4, t, IN(2.1, { d: .6, e: 'back', from: { s: 1.35, blur: 24 } }));
    $$('i', wv).forEach((b, i) => {
      const amp = kf(t, [[2.25, 0], [2.6, 1, 'out']]);
      const hgt = 14 + amp * 80 * Math.abs(Math.sin(t * 9 + i * .7) * Math.cos(t * 3.1 + i * .33));
      b.style.height = hgt.toFixed(1) + 'px';
    });
    A(wv, t, { o: [[2.25, 0], [2.5, 1]] });
  };
}, { noIn: true, protect: [[540, 1060, 600, 560, .85]] });

// SCENE 1 — LOGO
scene(3.6, 7.6, `
  <div class="ring" id="r1" style="left:390px;top:540px;width:300px;height:300px"></div>
  <div class="ring" id="r2" style="left:390px;top:540px;width:300px;height:300px;border-color:#ffc24a"></div>
  <div class="ring" id="r3" style="left:390px;top:540px;width:300px;height:300px;border-color:#3d63e0"></div>
  <div class="logo-tile" id="lt" style="left:390px;top:540px"><img src="${ICON}"></div>
  <div class="abs center disp" id="nm" style="top:930px;font-size:132px;font-weight:800;letter-spacing:.04em">MOLIYACHI</div>
  <div class="abs center disp grad-g glow-g" id="pro" style="top:1090px;font-size:70px;font-weight:700;letter-spacing:.5em;padding-left:.5em">PRO</div>
  <div class="abs center" id="tg" style="top:1250px;font-size:40px;line-height:1.35;color:var(--muted)">Tikuv sexi hisobi, ish haqi va shaxsiy moliya —<br><b style="color:#fff;font-weight:600">bitta Telegram botda</b></div>
`, (root, S) => {
  const lt = $('#lt', root), nm = $('#nm', root), pro = $('#pro', root), tg = $('#tg', root), rings = ['#r1', '#r2', '#r3'].map(s => $(s, root));
  cue(S + .35, 'impact', .8); cue(S + .55, 'shimmer', .8); cue(S + 1.45, 'pop', .6);
  return t => {
    const u = t - S;
    A(lt, u, { o: [[.1, 0], [.3, 1]], s: [[.1, .2], [.75, 1, 'back']], r: [[.1, -18], [.8, 0, 'out']], y: [[.75, 0], [4, -14, 'sine']] });
    rings.forEach((r, i) => A(r, u, { o: [[.35 + i * .12, 0], [.4 + i * .12, .9], [1.3 + i * .12, 0, 'out']], s: [[.35 + i * .12, 1], [1.3 + i * .12, 2.3 + i * .35, 'out']] }));
    nm.style.letterSpacing = kf(u, [[.5, .7], [1.4, .04, 'out5']]).toFixed(3) + 'em';
    A(nm, u, { o: [[.5, 0], [.9, 1]], blur: [[.5, 14], [1.2, 0, 'out']] });
    A(pro, u, IN(1.0, { from: { y: 30, blur: 12 } }));
    A(tg, u, IN(1.45, { from: { y: 30, blur: 10 } }));
  };
}, { protect: [[540, 1050, 620, 420, .8]] });

// SCENE 2 — PROBLEM & SOLUTION
function appHomeHTML() {
  return `<div class="phone"><div class="screen" style="background:#eef0fb">${STATUS}
    <div style="position:absolute;left:36px;right:36px;top:110px">
      <div style="display:flex;align-items:center;gap:18px;margin-bottom:30px"><img src="${ICON}" style="width:76px;height:76px;border-radius:50%">
        <div><div style="font-size:20px;font-weight:800;letter-spacing:.18em;color:#5a3be0">MOLIYACHI</div><div style="font-size:30px;font-weight:800">Assalomu alaykum!</div></div></div>
      <div style="border-radius:40px;padding:30px 32px;color:#fff;background:linear-gradient(150deg,#7b57ff,#4a2bd0);box-shadow:0 20px 40px rgba(80,40,220,.35)">
        <div style="font-size:23px;opacity:.85">Shu oy · ish haqi fondi</div>
        <div style="font-family:'Inter Display';font-size:62px;font-weight:700;letter-spacing:-.02em;margin:6px 0 16px">1 400 000 <span style="font-size:32px">so'm</span></div>
        <div style="display:flex;gap:10px"><span style="background:rgba(255,255,255,.2);border-radius:20px;padding:10px 18px;font-size:22px">275 ta ish</span><span style="background:#ffc24a;color:#2a1700;border-radius:20px;padding:10px 18px;font-size:22px;font-weight:700">3 ishchi</span></div></div>
      <div style="font-size:28px;font-weight:800;margin:34px 0 18px">Bo'limlar</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        ${[['👷', 'Ishchilar'], ['🧵', 'Modellar'], ['📊', 'Hisobot'], ['🛍', 'Mijozlar']].map(([e, n]) => `<div style="background:#fff;border-radius:30px;padding:26px 22px;height:170px;box-shadow:0 8px 20px rgba(30,20,90,.06)"><div style="font-size:52px">${e}</div><div style="font-size:26px;font-weight:700;margin-top:14px">${n}</div></div>`).join('')}
      </div></div>
    <div style="position:absolute;left:24px;right:24px;bottom:30px;height:110px;border-radius:40px;background:#16133a;display:flex;justify-content:space-around;align-items:center;color:#fff;font-size:40px">
      <span>🏠</span><span>🧾</span><span style="width:80px;height:80px;border-radius:50%;background:#6e4bf5;display:grid;place-items:center">🎙</span><span>👛</span><span>👷</span></div>
  </div></div>`;
}
scene(7.3, 16.2, `
  <div class="abs" id="kk" style="left:84px;top:212px;font-size:30px;color:var(--muted)">Keling, ko'rib chiqamiz</div>
  <div class="abs disp" id="tt" style="left:80px;top:256px;font-size:112px;font-weight:500;letter-spacing:-.035em;line-height:1">Muammo va yechim</div>
  <div class="persp" id="pa" style="left:110px;top:70px;width:620px;height:1280px"></div>
  <div class="persp" id="pb" style="left:500px;top:170px;width:620px;height:1280px"></div>
  <div class="pcard violet" id="cp" style="left:50px;top:960px"><h3>Muammo</h3><p>Kim necha dona tikdi, kimga qancha ish haqi — hammasi daftarda. Hisob chalkash, xato ko'p, oy oxirida soatlab vaqt ketadi.</p></div>
  <div class="pcard light" id="cs" style="left:390px;top:1395px"><h3>Yechim</h3><p>Moliyachi ovoz, matn yoki rasmdan yozuvni o'zi tushunadi, ish haqini o'zi hisoblaydi va bir bosishda hisobot beradi.</p></div>
`, (root, S) => {
  const pa = $('#pa', root), pb = $('#pb', root);
  pa.appendChild(h(appHomeHTML()));
  const chat = makePhone({ msgs: [
    { t: -9, me: true, voice: 0.001, silent: true },
    { t: -9, html: `📝 <b>Ish topshirish</b>\n📅 Sana: 14.10.2026 (bugun)\n👷 Ishchi: <b>Alisher</b>\n• A-12 — 50 ta × 5 000 so'm\n• B-5 — 30 ta × 4 000 so'm\n💰 Jami: <b>370 000 so'm</b>\n\n<b>To'g'rimi?</b>`, btns: [[{ txt: '✅ Saqlash', primary: true }], ['✏️ Tuzatish', '❌ Bekor']], silent: true },
  ] });
  pb.appendChild(chat.el);

  const phA = $('.phone', pa), phB = chat.el;
  const kk = $('#kk', root), tt = $('#tt', root), cp = $('#cp', root), cs = $('#cs', root);
  cue(S + 1.3, 'whoosh', .7); cue(S + 1.45, 'pop', .7); cue(S + 3.2, 'whoosh', .7); cue(S + 3.35, 'pop', .7);
  root._measure = () => chat.measure();
  return t => {
    const u = t - S;
    A(kk, u, IN(.1, { from: { x: -30, blur: 8 } }));
    if (!tt._w) tt._w = splitWords(tt);
    words(u, tt._w, .2, .1);
    const fl = Math.sin(u * 1.1);
    phA.style.transformOrigin = '50% 50%';
    A(phA, u, { o: [[.35, 0], [.7, 1]], s: .5, y: [[.35, 420], [1.3, 0, 'out']], rx: 10, ry: [[.35, 45], [1.4, 26, 'out']], r: -12 + fl * 1.2 });
    A(phB, u, { o: [[.6, 0], [.95, 1]], s: .47, y: [[.6, 460], [1.6, 0, 'out']], rx: 6, ry: [[.6, -50], [1.7, -26, 'out']], r: [[.6, 14], [1.7, 9 - fl * 1.2, 'out']] });
    chat.update(u);
    A(cp, u, { ...IN(1.3, { d: .7, from: { x: -260, blur: 16, s: .9 } }), r: [[1.3, -4], [2.0, 0, 'out']] });
    A(cs, u, { ...IN(3.2, { d: .7, from: { x: 260, blur: 16, s: .9 } }), r: [[3.2, 4], [3.9, 0, 'out']] });
  };
}, { protect: [[460, 300, 640, 190, .8]] });

// SCENE 3 — 5 STEPS
const STEPS = [['Modellar va narx', 'bir dona uchun ish haqi'], ['Ishchilarni ulash', 'bir martalik taklif havolasi'], ['Ishni kiritish', 'ovoz, matn yoki rasm'], ['Tasdiqlash', 'faqat tasdiqlangani hisoblanadi'], ['Ish haqi va hisobot', 'kunlik · haftalik · oylik']];
scene(15.9, 19.4, headHTML("QO'LLANMA", '5 qadamda hammasi', '') +
  STEPS.map(([a, b], i) => `<div class="steprow" style="top:${560 + i * 176}px"><div class="n">${i + 1}</div><div class="tx">${a}<small>${b}</small></div></div>`).join(''),
(root, S) => {
  const rows = $$('.steprow', root);
  rows.forEach((_, i) => cue(S + .55 + i * .16, 'tick', .8));
  return t => {
    const u = t - S;
    headAnim(root, u, .05);
    rows.forEach((r, i) => A(r, u, IN(.55 + i * .16, { d: .6, from: { x: -90, blur: 14, s: .96 } })));
  };
}, { protect: [[460, 340, 640, 200, .75]] });

// generic step scene with a chat phone
function stepScene(start, end, n, title, sub, msgs, chips, opt = {}) {
  const phoneTop = opt.phoneTop ?? 700;
  return scene(start, end, headHTML(n, title, sub, opt.extraHead || '') +
    `<div class="persp" id="pp" style="left:230px;top:${phoneTop}px;width:620px;height:1280px"></div>` +
    chips.map((c, i) => chip(c.html, c.cls || '', c.style).replace('class="chip', `data-i="${i}" class="chip`)).join(''),
  (root, S) => {
    const ph = makePhone({ msgs: msgs.map(m => ({ ...m, t: S + m.t, hide: m.hide !== undefined ? S + m.hide : undefined, btns: m.btns && m.btns.map(r => r.map(b => (typeof b === 'object' && b.tap !== undefined ? { ...b, tap: S + b.tap } : b))) })), toast: opt.toast && { ...opt.toast, t: S + opt.toast.t, hide: S + opt.toast.hide } });
    $('#pp', root).appendChild(ph.el);
    root._measure = () => ph.measure();
    const chipEls = $$('.chip', root);
    chips.forEach(c => cue(S + c.t, 'pop', .6));
    cue(S + .35, 'whoosh', .5);
    const extra = opt.anim ? opt.anim(root, S) : null;
    return t => {
      const u = t - S;
      headAnim(root, u, .05);
      ph.el.style.transformOrigin = '50% 0%';
      A(ph.el, u, { o: [[.3, 0], [.6, 1]], s: .92, y: [[.3, 560], [1.25, 0, 'out']], rx: [[.3, 22], [1.3, 3, 'out']], ry: [[.3, -16], [1.4, -3 + 2 * Math.sin(u * .9), 'out']] });
      ph.update(t);
      chips.forEach((c, i) => chipAnim(chipEls[i], u, c.t, c.out, c.dir || 1));
      if (extra) extra(u, t);
    };
  }, { protect: [[480, 400, 680, 300, .8]] });
}

// SCENE 4 — STEP 1: models
stepScene(19.1, 27.0, '1-QADAM', 'Modellar va narx', 'Har bir modelga bir dona uchun ish haqini yozing', [
  { t: 1.1, me: true, html: '🧵 Modellar' },
  { t: 1.5, html: `🧵 <b>Modellar</b> (narx — bir dona uchun ish haqi)\n\n• <b>A-12</b> — 5 000 so'm\n• <b>B-5</b> — 4 000 so'm\n• <b>C-7</b> — 6 000 so'm`, btns: [['💰 A-12', '💰 B-5', '💰 C-7'], [{ txt: "➕ Model qo'shish", tap: 3.1 }]] },
  { t: 3.45, html: 'Model nomini yozing (masalan: <i>A-12</i>):' },
  { t: 4.0, me: true, html: 'D-3' },
  { t: 4.5, html: `🧵 <b>D-3</b>\n\nBir dona uchun ish haqini yozing.\nMasalan: <i>5000 so'm</i>.` },
  { t: 5.1, me: true, html: '7000' },
  { t: 5.6, html: `✅ <b>D-3</b> — 7 000 so'm saqlandi` },
], [
  { t: 2.2, out: 4.6, cls: 'gold', html: "💰 Narx = 1 dona uchun<br>ish haqi", style: 'left:40px;top:1690px' },
  { t: 6.1, cls: '', dir: -1, html: 'Yangi model ovozdan<br>ham qo\'shiladi<small>«➕ Yangi: D-3» tugmasi</small>', style: 'right:36px;top:1690px' },
]);

// SCENE 5 — STEP 2: workers
stepScene(26.7, 34.6, '2-QADAM', 'Ishchilarni ulang', 'Ishchi qo\'shing va unga bir martalik havola yuboring', [
  { t: 1.1, me: true, html: '👷 Ishchilar' },
  { t: 1.5, html: `👷 <b>Ishchilar</b>\n\n• <b>Vali</b> — ✅ ulangan\n• <b>Dilnoza</b> — ✅ ulangan`, btns: [[{ txt: "➕ Ishchi qo'shish", tap: 2.4 }]] },
  { t: 2.75, html: 'Yangi ishchining ismini yozing (masalan: <i>Alisher</i>):' },
  { t: 3.3, me: true, html: 'Alisher' },
  { t: 3.8, html: `✅ <b>Alisher</b> qo'shildi.\n\nUshbu havolani ishchiga yuboring — u bosganda bot unga ulanadi:\n<span style="color:#5a3be0;text-decoration:underline">t.me/moliyachi_bot?start=inv_7Kq2xR</span>\n\nHavola bir martalik.` },
], [
  { t: 4.5, out: 6.3, cls: 'gold', html: '🔗 Havolani ishchiga<br>Telegram\'da yuboring', style: 'left:40px;top:1690px' },
  { t: 6.4, cls: '', dir: -1, html: 'Ishchi o\'z ishini<br>o\'zi kiritadi<small>va faqat o\'z hisobini ko\'radi</small>', style: 'right:36px;top:1690px' },
], { toast: { t: 5.5, hide: 7.3, html: '👷 <b style="display:inline;color:#14112b;font-size:26px">Alisher</b> botga ulandi.' } });

// SCENE 6 — STEP 3: entering work
stepScene(34.3, 46.2, '3-QADAM', 'Ishni kiriting', 'Bosib turing va gapiring — bot o\'zi tushunadi', [
  { t: 1.3, me: true, voice: 2.0 },
  { t: 3.7, slow: 1.4, html: `📝 <b>Ish topshirish</b>\n<i>🗣 «Alisher A-12 modeldan 50 ta, B-5 dan 30 ta topshirdi»</i>\n📅 Sana: 14.10.2026 (bugun)\n👷 Ishchi: <b>Alisher</b>\n• A-12 — 50 ta × 5 000 so'm\n• B-5 — 30 ta × 4 000 so'm\n💰 Jami: <b>370 000 so'm</b>\n\n<b>To'g'rimi?</b>`, btns: [[{ txt: '✅ Saqlash', primary: true, tap: 8.1 }], ['✏️ Tuzatish', '❌ Bekor'], ['📅 Bugun', '📅 Kecha']] },
  { t: 8.55, html: `✅ <b>Saqlandi</b>\n\n<b>#127</b> · A-12 — <b>50 ta</b> · 250 000 so'm\n<b>#128</b> · B-5 — <b>30 ta</b> · 120 000 so'm` },
], [
  { t: 1.7, out: 3.7, cls: 'light', html: '🎙 «Alisher A-12 modeldan 50 ta,<br>B-5 dan 30 ta topshirdi»', style: 'left:60px;top:1150px;font-size:34px' },
  { t: 5.3, out: 7.7, cls: 'gold', html: '✏️ Xato bo\'lsa — ayting:<small>«soni 45 ta» yoki «kecha edi»</small>', style: 'left:36px;top:1690px' },
  { t: 9.3, cls: '', dir: -1, html: '📷 Daftar yoki chek rasmini<br>yuborsangiz ham bo\'ladi', style: 'right:36px;top:600px' },
], {
  phoneTop: 780,
  extraHead: `<div class="meth" style="display:flex;gap:16px;margin-top:4px">${[['🎙', 'Ovoz', 1], ['⌨️', 'Matn', 0], ['📷', 'Rasm', 0]].map(([e, n, on]) => `<span style="border-radius:999px;padding:14px 28px;font-size:32px;font-weight:600;${on ? 'background:#6e4bf5;box-shadow:0 0 30px rgba(110,75,245,.6)' : 'background:rgba(255,255,255,.08);border:1.5px solid rgba(185,168,255,.3)'}">${e} ${n}</span>`).join('')}</div>`,
  anim: (root, S) => {
    const ms = $$('.meth span', root);
    ms.forEach((_, i) => cue(S + .7 + i * .12, 'tick', .6));
    return u => ms.forEach((m, i) => A(m, u, IN(.7 + i * .12, { e: 'back', from: { s: .6, blur: 8 } })));
  },
});
// shift the step-3 subtitle block: extra head row needs room
// SCENE 7 — STEP 4: approval (two phones)
scene(45.9, 54.8, headHTML('4-QADAM', 'Ishchi yozadi — siz tasdiqlaysiz', 'Sozlamalarda tasdiqlash yoqilgan bo\'lsa') + `
  <div class="pathlbl" id="lw" style="left:44px;width:434px;top:700px">Ishchi · Vali</div>
  <div class="pathlbl" id="lo" style="left:602px;width:434px;top:700px">Ega · Siz</div>
  <div class="persp" id="pw" style="left:44px;top:760px;width:434px;height:896px"></div>
  <div class="persp" id="po" style="left:602px;top:760px;width:434px;height:896px"></div>
  <div class="dot" id="d1"></div><div class="dot" id="d2"></div>
  <div class="abs center disp" id="cap" style="top:1690px;font-size:54px;font-weight:700;letter-spacing:-.02em;line-height:1.15">Faqat <span class="grad-g">tasdiqlangan</span> ish<br>hisobotga kiradi</div>
`, (root, S) => {
  const pw = makePhone({ msgs: [
    { t: S + 1.0, me: true, html: 'A-12 dan 40 ta topshirdim' },
    { t: S + 1.6, html: `✅ <b>Saqlandi</b>\n⏳ Egasining tasdig'ini kutmoqda.` },
    { t: S + 5.0, html: `Yozuvingiz ✅ tasdiqlandi:\n\n<b>#129</b> · 14.10.2026\n👷 Vali\n🧵 A-12 — <b>40 ta</b>\n💰 200 000 so'm` },
  ] });
  const po = makePhone({ msgs: [
    { t: S + 2.75, html: `🔔 <b>Yangi yozuv tasdiq kutmoqda</b>\n\n<b>#129</b> · 14.10.2026 (bugun)\n👷 Vali\n🧵 A-12 — <b>40 ta</b>\n💰 200 000 so'm\n<span class="sw" data-at="${S + 4.45}" style="display:inline-grid"><span style="grid-area:1/1">⏳ tasdiq kutmoqda</span><span style="grid-area:1/1;color:#12a26b;font-weight:700">✅ tasdiqlangan</span></span>`, btns: [[{ txt: '✅ Tasdiqlash', primary: true, tap: S + 4.2 }, '🚫 Rad etish'], ['✏️ Tahrirlash', "🗑 O'chirish"]] },
  ] });
  $('#pw', root).appendChild(pw.el); $('#po', root).appendChild(po.el);
  root._measure = () => { pw.measure(); po.measure(); };
  const d1 = $('#d1', root), d2 = $('#d2', root), cap = $('#cap', root), lw = $('#lw', root), lo = $('#lo', root);
  cue(S + 2.05, 'whoosh', .6); cue(S + 4.55, 'whoosh', .5); cue(S + 4.5, 'ding', .7); cue(S + 5.6, 'impact', .55);
  const arc = (u, t0, t1, a, b, lift) => { const p = E.io(clamp((u - t0) / (t1 - t0))); return [a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p - Math.sin(Math.PI * p) * lift]; };
  return t => {
    const u = t - S;
    headAnim(root, u, .05);
    A(lw, u, IN(.6)); A(lo, u, IN(.75));
    pw.el.style.transformOrigin = '0 0'; po.el.style.transformOrigin = '0 0';
    A(pw.el, u, { o: [[.3, 0], [.6, 1]], s: .7, y: [[.3, 600], [1.25, 0, 'out']], ry: [[.3, 30], [1.3, 12, 'out']] });
    A(po.el, u, { o: [[.45, 0], [.75, 1]], s: .7, y: [[.45, 600], [1.4, 0, 'out']], ry: [[.45, -30], [1.45, -12, 'out']] });
    pw.update(t); po.update(t);
    let [x, y] = arc(u, 2.05, 2.7, [300, 1200], [820, 1100], 260);
    d1.style.left = x + 'px'; d1.style.top = y + 'px';
    A(d1, u, { o: [[2.0, 0], [2.1, 1], [2.65, 1], [2.75, 0]], s: [[2.0, .5], [2.3, 2.2], [2.75, 1]] });
    [x, y] = arc(u, 4.55, 5.1, [820, 1100], [300, 1220], 220);
    d2.style.left = x + 'px'; d2.style.top = y + 'px';
    d2.style.background = 'radial-gradient(circle,#fff 0%,#ffd36e 40%,rgba(255,160,30,0) 72%)';
    A(d2, u, { o: [[4.5, 0], [4.6, 1], [5.05, 1], [5.15, 0]], s: [[4.5, .5], [4.8, 2.2], [5.15, 1]] });
    A(cap, u, IN(5.6, { d: .6, e: 'back', from: { s: .85, blur: 14 } }));
  };
}, { protect: [[480, 400, 680, 300, .8], [540, 1660, 600, 200, .7]] });

// SCENE 8 — STEP 5: how pay is calculated
scene(54.5, 64.8, headHTML('5-QADAM', 'Ish haqi qanday hisoblanadi?', '') + `
  <div class="abs center" id="f1" style="top:560px"><span class="tok a">Soni</span><span class="op">×</span><span class="tok a">1 dona narxi</span></div>
  <div class="abs center" id="f2" style="top:700px"><span class="op">=</span><span class="tok g">Ish haqi</span></div>
  <div class="glass" id="gl" style="left:60px;right:60px;top:880px;padding:40px 50px 44px">
    <div style="font-size:34px;color:var(--muted);margin-bottom:6px">👷 <b style="color:#fff">Alisher</b> · bugun</div>
    <div class="prow" id="pr1"><span class="m">A-12</span><span class="eq">50 × 5 000</span><span class="v">= <span id="v1">250 000</span></span></div>
    <div class="prow" id="pr2"><span class="m">B-5</span><span class="eq">30 × 4 000</span><span class="v">= <span id="v2">120 000</span></span></div>
    <div id="tot" style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:34px">
      <div style="font-size:34px;color:var(--muted)">Jami: <b style="color:#fff">80 ta</b><br>Ish haqi</div>
      <div class="disp grad-g glow-g" style="font-size:104px;font-weight:800;letter-spacing:-.03em;line-height:1;font-variant-numeric:tabular-nums"><span id="vt">370 000</span><span style="font-size:46px;font-weight:600"> so'm</span></div>
    </div>
  </div>
  <div class="chip light" id="nt" style="left:60px;right:60px;top:1500px;font-size:34px">🔒 Narx — yozuv saqlangan paytdagisi<small>Model narxi keyin o'zgarsa ham, eski hisob buzilmaydi</small></div>
`, (root, S) => {
  const f1 = $('#f1', root), f2 = $('#f2', root), gl = $('#gl', root), pr1 = $('#pr1', root), pr2 = $('#pr2', root), tot = $('#tot', root), nt = $('#nt', root);
  const toks = [...$$('#f1 > span', root), ...$$('#f2 > span', root)];
  const v1 = $('#v1', root), v2 = $('#v2', root), vt = $('#vt', root);
  toks.forEach((_, i) => cue(S + .7 + i * .2, 'pop', .7));
  cue(S + 2.2, 'whoosh', .5); cue(S + 2.8, 'tick', .8); cue(S + 3.5, 'tick', .8);
  cue(S + 4.4, 'roll', .9); cue(S + 5.85, 'ding', .9); cue(S + 6.5, 'pop', .6);
  return t => {
    const u = t - S;
    headAnim(root, u, .05);
    toks.forEach((tk, i) => A(tk, u, IN(.7 + i * .2, { d: .55, e: 'back', from: { s: .4, y: 30, blur: 10 } })));
    A(gl, u, IN(2.2, { d: .7, from: { y: 120, s: .94, blur: 16 } }));
    A(pr1, u, IN(2.8, { from: { x: -60, blur: 10 } }));
    A(pr2, u, IN(3.5, { from: { x: -60, blur: 10 } }));
    v1.textContent = fmt(kf(u, [[2.9, 0], [3.5, 250000, 'out']]));
    v2.textContent = fmt(kf(u, [[3.6, 0], [4.2, 120000, 'out']]));
    A(tot, u, IN(4.3, { from: { y: 30, blur: 10 } }));
    vt.textContent = fmt(Math.round(kf(u, [[4.4, 0], [5.85, 370000, 'out5']]) / 1000) * 1000);
    A(vt.parentElement, u, { s: [[5.8, 1], [5.95, 1.07, 'out'], [6.3, 1, 'out']] });
    A(nt, u, IN(6.5, { d: .6, e: 'back', from: { s: .85, y: 40, blur: 12 } }));
  };
}, { protect: [[480, 400, 680, 300, .8], [540, 1150, 640, 600, .55]] });

// SCENE 9 — REPORT
scene(64.5, 74.0, headHTML('HISOBOT', 'Tayyor hisobot — bir bosishda', '📊 Bugun · Shu hafta · Shu oy') + `
  <div class="persp" id="pp" style="left:36px;top:700px;width:620px;height:1280px"></div>
  <div class="chart" id="ch" style="left:640px;top:800px;width:400px">
    <h5>Ish haqi · shu oy</h5><div class="cap">ming so'm</div>
    <div class="cols">${[['Alisher', 370], ['Dilnoza', 470], ['Vali', 560]].map(([n, v], i) => `<div class="col"><div class="val" data-v="${v}">${v}</div><div class="bar${i === 2 ? ' top' : ''}" style="height:${(v / 560 * 190).toFixed(0)}px"></div><div class="lb">${n}</div></div>`).join('')}</div>
  </div>
  <div class="chip gold" id="fd" style="left:640px;width:400px;top:1270px;font-size:28px">💰 Ish haqi fondi<div class="disp" style="font-size:54px;font-weight:800;letter-spacing:-.02em;margin-top:6px;font-variant-numeric:tabular-nums"><span id="fv">1 400 000</span> <span style="font-size:28px">so'm</span></div></div>
  <div class="chip" id="wk" style="left:640px;width:400px;top:1490px;font-size:28px">👷 Ishchi esa faqat<br>o'z ish haqini ko'radi</div>
`, (root, S) => {
  const rep = `📊 <b>Shu oy</b> (01.10 – 31.10)\n\n👷 <b>Alisher</b> — 80 ta\n   A-12: 50, B-5: 30\n   💰 370 000 so'm\n\n👷 <b>Dilnoza</b> — 95 ta\n   B-5: 50, C-7: 45\n   💰 470 000 so'm\n\n👷 <b>Vali</b> — 100 ta\n   A-12: 40, C-7: 60\n   💰 560 000 so'm\n\n🧵 <b>Modellar bo'yicha</b>\n• A-12 — 90 ta\n• B-5 — 80 ta\n• C-7 — 105 ta\n\nJami: <b>275 ta</b>\nIsh haqi fondi: <b>1 400 000 so'm</b>`;
  const ph = makePhone({ msgs: [
    { t: S + 1.0, me: true, html: '📊 Hisobot' },
    { t: S + 1.4, hide: S + 2.45, html: '📊 Qaysi davr uchun?', btns: [['Bugun', 'Shu hafta', { txt: 'Shu oy', tap: S + 2.05 }], ['📱 Ilovani ochish']] },
    { t: S + 2.45, fade: true, silent: true, slow: 3.6, html: rep, btns: [['Bugun', 'Shu hafta', 'Shu oy'], ['📱 Ilovani ochish']] },
  ] });
  $('#pp', root).appendChild(ph.el);
  root._measure = () => ph.measure();
  const ch = $('#ch', root), fd = $('#fd', root), wk = $('#wk', root), fv = $('#fv', root);
  const bars = $$('.bar', root), vals = $$('.val', root);
  cue(S + 2.45, 'shimmer', .5); cue(S + 3.4, 'whoosh', .5); [0, 1, 2].forEach(i => cue(S + 3.7 + i * .15, 'tick', .7));
  cue(S + 5.3, 'pop', .7); cue(S + 5.4, 'roll', .7); cue(S + 6.6, 'ding', .7); cue(S + 7.0, 'pop', .6);
  return t => {
    const u = t - S;
    headAnim(root, u, .05);
    ph.el.style.transformOrigin = '50% 0%';
    A(ph.el, u, { o: [[.3, 0], [.6, 1]], s: .92, y: [[.3, 560], [1.25, 0, 'out']], rx: [[.3, 20], [1.3, 3, 'out']], ry: [[.3, 26], [1.4, 8 + 2 * Math.sin(u * .9), 'out']] });
    ph.update(t);
    A(ch, u, IN(3.4, { d: .7, e: 'back', from: { x: 120, s: .8, blur: 14 } }));
    bars.forEach((b, i) => { A(b, u, { sy: [[3.7 + i * .15, 0], [4.4 + i * .15, 1, 'back']] }); vals[i].textContent = Math.round(kf(u, [[3.7 + i * .15, 0], [4.5 + i * .15, +vals[i].dataset.v, 'out']])); });
    A(fd, u, IN(5.3, { d: .6, e: 'back', from: { x: 80, s: .85, blur: 12 } }));
    fv.textContent = fmt(Math.round(kf(u, [[5.4, 0], [6.6, 1400000, 'out5']]) / 1000) * 1000);
    A(wk, u, IN(7.0, { d: .6, e: 'back', from: { x: 80, s: .85, blur: 12 } }));
  };
}, { protect: [[480, 400, 680, 300, .8]] });

// SCENE 10 — BONUS
const BONUS = [['🛍', 'Mijozlar', "Komplekt × narx, to'lov va qoldiq o'zi hisoblanadi"], ['💸', 'Xarajatlar', 'Chek yoki bank skrinshotidan o\'zi o\'qiydi'], ['🤝', 'Qarzlar', 'Kim kimga qancha qarz — aniq ro\'yxat'], ['💡', 'Maslahat', '50/30/20 reja, kunlik limit, har kuni 🔔 eslatma']];
scene(73.7, 80.2, headHTML('YANA', 'Bundan tashqari', '') +
  BONUS.map(([e, a, b], i) => `<div class="bcard${i === 3 ? ' hi' : ''}" style="left:${i % 2 ? 558 : 84}px;top:${520 + Math.floor(i / 2) * 440}px"><div class="e">${e}</div><h4>${a}</h4><p>${b}</p></div>`).join('') +
  `<div class="abs center" id="ap" style="top:1440px"><span class="ctapill">📱 Hammasi grafiklar bilan — <b>«📊 Ilova»</b>da</span></div>`,
(root, S) => {
  const cards = $$('.bcard', root), ap = $('#ap', root);
  cards.forEach((_, i) => cue(S + .5 + i * .16, 'pop', .7));
  cue(S + 2.3, 'shimmer', .6);
  return t => {
    const u = t - S;
    headAnim(root, u, .05);
    cards.forEach((c, i) => A(c, u, { o: [[.5 + i * .16, 0], [.75 + i * .16, 1]], ry: [[.5 + i * .16, i % 2 ? -70 : 70], [1.2 + i * .16, 0, 'out']], s: [[.5 + i * .16, .8], [1.2 + i * .16, 1, 'back']], y: [[1.2 + i * .16, 0], [6.5, (i % 2 ? -8 : 8), 'sine']] }));
    A(ap, u, IN(2.3, { d: .6, e: 'back', from: { s: .85, y: 30, blur: 12 } }));
  };
}, { protect: [[480, 330, 640, 200, .8], [540, 1000, 640, 640, .55]] });

// SCENE 11 — CTA
scene(79.9, DURATION, `
  <div class="logo-tile" id="lt" style="left:420px;top:560px;width:240px;height:240px;border-radius:64px"><img src="${ICON}"></div>
  <div class="abs center disp" id="c1" style="top:880px;font-size:92px;font-weight:500;letter-spacing:-.03em">Siz gapiring —</div>
  <div class="abs center disp grad-v glow-v" id="c2" style="top:985px;font-size:176px;font-weight:800;letter-spacing:-.045em;line-height:1.1">Moliyachi</div>
  <div class="abs center disp" id="c3" style="top:1185px;font-size:112px;font-weight:300;letter-spacing:-.03em">hisoblaydi.</div>
  <div class="abs center" id="c4" style="top:1420px"><span class="ctapill">Telegram → <b>Moliyachi</b> bot → /start</span></div>
`, (root, S) => {
  const lt = $('#lt', root), c = ['#c1', '#c2', '#c3', '#c4'].map(s => $(s, root));
  cue(S + .75, 'impact', 1); cue(S + 2.1, 'shimmer', .7); cue(DURATION - 1.2, 'sub', .7);
  return t => {
    const u = t - S;
    A(lt, u, { o: [[.1, 0], [.35, 1]], s: [[.1, .3], [.7, 1, 'back']], r: [[.1, 14], [.8, 0, 'out']], y: [[.8, 0], [7, -12, 'sine']] });
    A(c[0], u, IN(.4, { from: { y: 40, blur: 14 } }));
    A(c[1], u, IN(.75, { d: .65, e: 'back', from: { s: 1.3, blur: 24 } }));
    A(c[2], u, IN(1.15, { from: { y: 40, blur: 14 } }));
    A(c[3], u, IN(2.1, { d: .6, e: 'back', from: { s: .85, y: 30, blur: 12 } }));
  };
}, { noOut: true, protect: [[540, 1080, 620, 480, .85]] });

// ribbon layout per scene: [cx, cy, scale, rot, alpha]
ribAt(0, 3.3, [600, 900, 1.15, -8, 1]);
ribAt(4.2, 7.0, [560, 760, 1.35, 18, .95]);
ribAt(7.9, 15.6, [650, 700, 1.15, -28, .95]);
ribAt(16.5, 18.8, [900, 380, .85, 12, .6]);
ribAt(19.7, 26.4, [880, 560, .9, 22, .5]);
ribAt(27.3, 34.0, [200, 520, .9, -150, .5]);
ribAt(34.9, 45.6, [880, 600, .9, 30, .45]);
ribAt(46.5, 54.2, [540, 330, .8, 0, .5]);
ribAt(55.1, 64.2, [240, 1560, 1.0, -20, .65]);
ribAt(65.1, 73.4, [900, 520, .9, 20, .45]);
ribAt(74.3, 79.6, [540, 1000, 1.45, -10, .45]);
ribAt(80.6, DURATION, [560, 1020, 1.3, -6, .9]);

// transitions: light sweep at each cut
const SWEEPS = SCENES.slice(1).map(s => s.start);

// ---------- frame ----------
function renderAt(t) {
  drawBG(t);
  for (const S of SCENES) {
    if (!(t >= S.start && t < S.end) || !S.opt.protect) continue;
    const k = Math.min(kf(t, [[S.start, S.opt.noIn ? 1 : 0], [S.start + .5, 1]]), S.opt.noOut ? 1 : kf(t, [[S.end - .4, 1], [S.end, 0]]));
    S.opt.protect.forEach(z => drawProtect(z, k));
  }
  for (const S of SCENES) {
    const on = t >= S.start && t < S.end;
    S.root.style.display = on ? 'block' : 'none';
    if (!on) continue;
    const tin = S.opt.noIn ? -1 : S.start, tout = S.opt.noOut ? 1e9 : S.end;
    const o = Math.min(kf(t, [[tin, 0], [tin + .45, 1, 'out']]), kf(t, [[tout - .4, 1], [tout, 0, 'in']]));
    const s = t < S.start + .6 ? kf(t, [[tin, .93], [tin + .6, 1, 'out']]) : kf(t, [[tout - .4, 1], [tout, 1.08, 'in']]);
    const b = Math.max(kf(t, [[tin, 16], [tin + .5, 0, 'out']]), kf(t, [[tout - .4, 0], [tout, 18, 'in']]));
    S.root.style.opacity = o.toFixed(4);
    S.root.style.transform = `scale(${s.toFixed(4)})`;
    S.root.style.filter = b > .05 ? `blur(${b.toFixed(2)}px)` : 'none';
    S.update(t);
  }
  // sweep
  const sw = document.getElementById('sweep');
  let so = 0, sx = -60;
  for (const c of SWEEPS) if (t > c - .4 && t < c + .4) { const p = (t - (c - .4)) / .8; so = Math.sin(Math.PI * p) * .9; sx = -60 + p * 220; }
  sw.style.opacity = so.toFixed(3); sw.style.left = sx.toFixed(2) + '%';
  // end fade
  document.getElementById('stage').style.opacity = kf(t, [[DURATION - .8, 1], [DURATION, 0, 'in']]).toFixed(3);
  drawGrain(t);
}

async function init() {
  await document.fonts.ready;
  await Promise.all([...document.images].map(i => (i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))));
  // fonts used by text: force-load weights
  await Promise.all(['300', '400', '500', '600', '700', '800'].map(w => document.fonts.load(`${w} 40px "Inter Display"`)).concat(['400', '600', '700'].map(w => document.fonts.load(`${w} 40px "Inter"`))));
  for (const S of SCENES) {
    S.setup();
    S.root.style.display = 'block';
    if (S.root._measure) S.root._measure();
    S.root.style.display = 'none';
  }
  CUES.sort((a, b) => a.t - b.t);
  window.CUES = CUES;
  window.DURATION = DURATION;
  window.renderAt = renderAt;
  renderAt(0);
  window.READY = true;
}
init();
