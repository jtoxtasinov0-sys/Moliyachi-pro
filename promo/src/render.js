#!/usr/bin/env node
// Deterministic frame renderer.
// node src/render.js --from 0 --to 5 --fps 30 --scale 1 --out <dir> [--jpg]   -> frames 00000.jpg …
// node src/render.js --from 0 --to 5 --fps 30 --video out.mp4                -> encoded segment (no audio)
// node src/render.js --cues plan/cues.json                                     -> dump sound cues
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const a = process.argv.slice(2);
const opt = (k, d) => { const i = a.indexOf('--' + k); return i >= 0 ? a[i + 1] : d; };
const from = +opt('from', 0), fps = +opt('fps', 30), scale = +opt('scale', 1);
const out = opt('out'), video = opt('video'), cues = opt('cues');

(async () => {
  const browser = await chromium.launch({ args: ['--disable-gpu-vsync', '--force-color-profile=srgb', '--font-render-hinting=none'] });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: scale });
  page.on('pageerror', e => { console.error('PAGE ERROR', e.message); process.exitCode = 1; });
  await page.goto('file://' + path.resolve(__dirname, 'index.html'));
  await page.waitForFunction(() => window.READY === true, null, { timeout: 20000 });
  const dur = await page.evaluate(() => window.DURATION);
  if (cues) {
    fs.writeFileSync(cues, JSON.stringify({ duration: dur, cues: await page.evaluate(() => window.CUES) }, null, 1));
    console.log('cues ->', cues);
    await browser.close();
    return;
  }
  const to = Math.min(+opt('to', dur), dur);
  const n = Math.round((to - from) * fps);
  let ff;
  if (video) {
    ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-',
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p', '-r', String(fps), video], { stdio: ['pipe', 'inherit', 'inherit'] });
  } else fs.mkdirSync(out, { recursive: true });
  const t0 = Date.now();
  for (let f = 0; f < n; f++) {
    const t = from + f / fps;
    await page.evaluate(t => window.renderAt(t), t);
    const buf = await page.screenshot({ type: 'jpeg', quality: video ? 95 : 80 });
    if (video) { if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r)); }
    else fs.writeFileSync(path.join(out, String(f).padStart(5, '0') + '.jpg'), buf);
    if (video && f % 150 === 0) console.log(`${path.basename(video)} ${f}/${n} ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  if (video) { ff.stdin.end(); await new Promise(r => ff.on('close', r)); }
  await browser.close();
  console.log(`rendered ${n} frames in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
})();
