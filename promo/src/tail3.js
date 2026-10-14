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
