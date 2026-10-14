// Silky flowing gradient background (reference: soft satin folds), recoloured to the app's navy/royal blue.
const bg = $('#bg'), bx = bg.getContext('2d');
const SW = 180, SH = 320;
const silk = document.createElement('canvas'); silk.width = SW; silk.height = SH;
const sx = silk.getContext('2d'), simg = sx.createImageData(SW, SH);
const STOPS = [[0, [3, 5, 24]], [.28, [7, 14, 56]], [.52, [16, 34, 118]], [.74, [36, 66, 196]], [.9, [78, 104, 246]], [1, [150, 165, 255]]];
function pal(v, out) {
  v = v < 0 ? 0 : v > 1 ? 1 : v;
  for (let i = 1; i < STOPS.length; i++) {
    if (v <= STOPS[i][0]) {
      const [a, ca] = STOPS[i - 1], [b, cb] = STOPS[i], p = (v - a) / (b - a), q = p * p * (3 - 2 * p);
      out[0] = ca[0] + (cb[0] - ca[0]) * q; out[1] = ca[1] + (cb[1] - ca[1]) * q; out[2] = ca[2] + (cb[2] - ca[2]) * q; return;
    }
  }
}
const _c = [0, 0, 0];
function drawBG(t) {
  const d = simg.data, T = t * .2;
  let k = 0;
  for (let y = 0; y < SH; y++) {
    const w = y / SH;
    for (let x = 0; x < SW; x++) {
      const u = x / SW;
      const a = u * 1.15 + w * 1.5 + .34 * Math.sin(w * 2.7 + T * 1.3) + .24 * Math.sin(u * 3.2 - T * 1.05 + w * 1.4);
      const f = Math.sin(a * 2.9 - T * 1.7);
      const g = Math.sin(a * 1.35 + T * .8 + .9);
      let v = .44 + .3 * f + .14 * g;
      const diag = u - w * .62 + .12 * Math.sin(T * 1.2 + w * 2.2) - .02;
      v -= Math.exp(-(diag * diag) / .018) * .32;
      v -= w * .12 + (1 - u) * .05;
      pal(v, _c);
      const sh = Math.pow(Math.max(0, Math.sin(a * 2.9 - T * 1.7 + 1.05)), 18) * .55 + Math.pow(Math.max(0, Math.sin(a * 5.8 - T * 2.2 + .3)), 30) * .18;
      d[k] = _c[0] + sh * 150; d[k + 1] = _c[1] + sh * 160; d[k + 2] = _c[2] + sh * 210; d[k + 3] = 255;
      k += 4;
    }
  }
  sx.putImageData(simg, 0, 0);
  bx.globalAlpha = 1; bx.filter = 'none';
  bx.imageSmoothingEnabled = true; bx.imageSmoothingQuality = 'high';
  bx.drawImage(silk, 0, 0, 1080, 1920);
}
