#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { mkdirSync, rmSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
const a = process.argv.slice(2);
const opt = (k, d) => { const i = a.indexOf('--' + k); return i >= 0 ? a[i + 1] : d; };
const from = parseFloat(a[0]), to = parseFloat(a[1]);
const fps = +opt('fps', 8), scale = +opt('scale', 0.25), cols = +opt('cols', 6);
const tag = `${from}-${to}`, frames = join('cache', 'preview', tag), outDir = join('renders', 'previews');
rmSync(frames, { recursive: true, force: true }); mkdirSync(frames, { recursive: true }); mkdirSync(outDir, { recursive: true });
const run = (c, args, q) => spawnSync(c, args, { stdio: q ? 'pipe' : 'inherit' });
let r = run('node', ['src/render.js', '--from', from, '--to', to, '--fps', fps, '--scale', scale, '--out', frames, '--jpg'].map(String));
if (r.status !== 0) process.exit(1);
const files = readdirSync(frames).filter(f => /^\d{5}\.jpg$/.test(f)).sort();
const lab = files.flatMap((f, i) => ['-label', (from + i / fps).toFixed(2) + 's', join(frames, f)]);
const sheet = join(outDir, `sheet_${tag}.jpg`);
r = run('montage', ['-tile', `${cols}x`, '-geometry', '+4+4', '-background', '#202020', '-fill', 'white', '-pointsize', '14', ...lab, sheet], true);
console.log(r.status === 0 ? `sheet: ${sheet} (${files.length} frames)` : 'montage failed ' + r.stderr);
