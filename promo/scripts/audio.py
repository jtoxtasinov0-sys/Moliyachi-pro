#!/usr/bin/env python3
"""Synthesise the music bed + all SFX from plan/cues.json -> assets/music/mix_raw.wav (48 kHz stereo).
Everything is generated in code (no third-party samples, no licences needed). Seeded -> deterministic."""
import json, wave, sys
import numpy as np

SR = 48000
rs = np.random.default_rng(7)
cues = json.load(open('plan/cues.json'))
DUR = cues['duration'] + 0.5
N = int(DUR * SR)
music = np.zeros((N, 2))
sfx = np.zeros((N, 2))
tt = lambda d: np.arange(int(d * SR)) / SR


def onepole_lp(x, fc):
    a = np.exp(-2 * np.pi * np.asarray(fc) / SR)
    y = np.empty_like(x); z = 0.0
    a = np.broadcast_to(a, x.shape)
    for i in range(len(x)):
        z = (1 - a[i]) * x[i] + a[i] * z; y[i] = z
    return y


def svf_bp(x, fc, q=1.2):
    fc = np.broadcast_to(np.asarray(fc, float), x.shape)
    lo = bp = 0.0; y = np.empty_like(x)
    for i in range(len(x)):
        f = 2 * np.sin(np.pi * min(fc[i], SR / 6) / SR)
        hi = x[i] - lo - bp / q
        bp += f * hi; lo += f * bp; y[i] = bp
    return y


def conv_reverb(x, dur=1.2, mix=.25, seed=3):
    r = np.random.default_rng(seed)
    n = int(dur * SR)
    ir = r.standard_normal(n) * np.exp(-np.arange(n) / SR * 5.5 / dur)
    ir = onepole_lp(ir, 5000); ir /= np.sqrt((ir ** 2).sum())
    L = len(x) + n
    nf = 1 << (L - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, nf) * np.fft.rfft(ir, nf), nf)[:L]
    out = np.zeros(L); out[:len(x)] = x
    return out * (1 - mix) + wet * mix * 1.6


def put(buf, sig, t, g=1.0, pan=0.0):
    i = int(t * SR)
    if i >= len(buf): return
    sig = sig[:len(buf) - i]
    l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    buf[i:i + len(sig), 0] += sig * g * l * 1.414
    buf[i:i + len(sig), 1] += sig * g * r * 1.414


# ---------------- SFX ----------------
def whoosh(d=.55):
    t = tt(d); n = rs.standard_normal(len(t))
    env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2 * np.exp(-t * 1.5)
    fc = 300 + 3500 * np.sin(np.pi * t / d) ** 1.5
    return svf_bp(n, fc, .9) * env * .55


def pop():
    t = tt(.16); f = 520 * np.exp(-t * 18) + 240
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 32)
    s[:200] += rs.standard_normal(200) * np.linspace(.3, 0, 200)
    return s * .6


def send():
    t = tt(.14); f = 700 + 900 * (1 - np.exp(-t * 40))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 30) * .45


def click():
    t = tt(.06)
    s = np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 120) * .5
    s[:120] += rs.standard_normal(120) * np.linspace(.6, 0, 120)
    return onepole_lp(s, 9000) * .8


def tick():
    t = tt(.05)
    return np.sin(2 * np.pi * 1900 * t) * np.exp(-t * 110) * .4


def ding():
    t = tt(1.6)
    s = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * k) for f, a, k in [(1318.5, .5, 3), (1975.5, .3, 4), (2637, .18, 6), (3951, .07, 9)])
    s *= np.minimum(1, t / .004)
    return conv_reverb(s, 1.0, .3) * .45


def impact():
    t = tt(1.8)
    f = 70 * np.exp(-t * 3) + 38
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.6)
    nz = onepole_lp(rs.standard_normal(len(t)), 900) * np.exp(-t * 14) * 1.4
    s = (sub + nz) * np.minimum(1, t / .003)
    return conv_reverb(s, 1.6, .35) * .8


def sub():
    t = tt(2.6)
    return np.sin(2 * np.pi * (45 * np.exp(-t * .4)) * t) * np.exp(-t * 1.6) * np.minimum(1, t / .01) * .7


def riser(d=.9):
    t = tt(d); p = t / d
    n = svf_bp(rs.standard_normal(len(t)), 400 + 6000 * p ** 2, 1.5) * .5
    tone = np.sin(2 * np.pi * np.cumsum(220 + 900 * p ** 2) / SR) * .15
    return (n + tone) * p ** 2.2


def shimmer():
    out = np.zeros(int(1.8 * SR))
    for i, f in enumerate([1760, 2217, 2637, 3520, 2960]):
        t = tt(1.2); s = np.sin(2 * np.pi * f * t) * np.exp(-t * 4) * np.minimum(1, t / .003) * .12
        o = int(i * .055 * SR); out[o:o + len(s)] += s
    return conv_reverb(out, 1.4, .45)


def scratch():
    t = tt(.28)
    n = svf_bp(rs.standard_normal(len(t)), 1200 + 3000 * t / .28, 2.5)
    return n * np.sin(np.pi * t / .28) * .45


def roll(d=1.4):
    out = np.zeros(int((d + .1) * SR)); x = 0.0; k = tick()
    while x < d:
        o = int(x * SR); out[o:o + len(k)] += k * .55
        p = x / d; x += .035 + .11 * p ** 2.4
    return out


SFX = {'whoosh': whoosh(), 'pop': pop(), 'send': send(), 'click': click(), 'tick': tick(), 'ding': ding(),
       'impact': impact(), 'sub': sub(), 'riser': riser(), 'shimmer': shimmer(), 'scratch': scratch(), 'roll': roll()}
OFFS = {'whoosh': -.22, 'riser': -.0}  # whoosh peak lands on the cut
last = {}
for c in cues['cues']:
    ty, t, g = c['type'], c['t'], c['g']
    if ty in last and t - last[ty] < .06: continue  # avoid stacked duplicates
    last[ty] = t
    pan = {'send': .25, 'pop': -.1, 'click': .1}.get(ty, 0) + (rs.random() - .5) * .2
    put(sfx, SFX[ty], max(0, t + OFFS.get(ty, 0)), g, pan)

# ---------------- MUSIC (100 BPM, F – G – Am – C) ----------------
BPM = 100; B = 60 / BPM
m2f = lambda m: 440 * 2 ** ((m - 69) / 12)
CH = [[53, 57, 60, 64], [55, 59, 62, 67], [57, 60, 64, 67], [48, 52, 55, 60]]  # Fmaj7, G, Am7, C
ROOT = [41, 43, 45, 36]
CTA = 79.9; END = cues['duration']


def adsr(n, a, r):
    e = np.ones(n); na, nr = int(a * SR), int(r * SR)
    e[:na] = np.linspace(0, 1, na); e[-nr:] *= np.linspace(1, 0, nr)
    return e


bar = 0; t0 = 0.0
pad = np.zeros(N); bass = np.zeros(N); arp = np.zeros(N); kick = np.zeros(N); hat = np.zeros(N)
while t0 < DUR:
    ci = (bar // 2) % 4; dur = 4 * B * 2
    n = int(dur * SR); t = np.arange(n) / SR
    chord = np.zeros(n)
    for m in CH[ci]:
        for det in (-.0018, .0018):
            f = m2f(m) * (1 + det)
            chord += sum(np.sin(2 * np.pi * f * h * t + h) / h ** 1.6 for h in range(1, 6))
    chord *= adsr(n, 1.2, 1.0) * .05
    i0 = int(t0 * SR); pad[i0:i0 + n] += chord[:max(0, N - i0)]
    for beat in range(8):
        bt = t0 + beat * B
        if bt >= DUR: break
        # bass on 1 and 3
        if beat % 2 == 0 and 3.6 < bt < CTA:
            tb = tt(B * 1.8); f = m2f(ROOT[ci])
            s = (np.sin(2 * np.pi * f * tb) + .3 * np.sin(4 * np.pi * f * tb)) * np.exp(-tb * 2.2) * np.minimum(1, tb / .01) * .32
            put_i = int(bt * SR); bass[put_i:put_i + len(s)] += s[:max(0, N - put_i)]
        # soft kick each beat
        if 7.3 < bt < CTA:
            tk = tt(.35); f = 110 * np.exp(-tk * 30) + 45
            s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tk * 11) * .55
            k_i = int(bt * SR); kick[k_i:k_i + len(s)] += s[:max(0, N - k_i)]
        # pluck arpeggio (8ths)
        for half in range(2):
            at = bt + half * B / 2
            if 7.3 < at < CTA + 2:
                idx = (beat * 2 + half) % 8
                m = CH[ci][[0, 2, 1, 3, 2, 1, 3, 2][idx]] + 12
                ta = tt(.6); f = m2f(m)
                s = (np.sin(2 * np.pi * f * ta) + .25 * np.sin(6 * np.pi * f * ta)) * np.exp(-ta * 7) * np.minimum(1, ta / .004) * .09
                a_i = int(at * SR); arp[a_i:a_i + len(s)] += s[:max(0, N - a_i)]
            # hats on offbeats
            if half == 1 and 15.9 < at < CTA:
                th = tt(.08); s = rs.standard_normal(len(th)) * np.exp(-th * 60) * .05
                s = s - onepole_lp(s, 7000)
                h_i = int(at * SR); hat[h_i:h_i + len(s)] += s[:max(0, N - h_i)]
    bar += 2; t0 += dur

# sidechain-style pump from the kick, intro fade, CTA drop, outro fade
tg = np.arange(N) / SR
pump = np.ones(N)
for bt in np.arange(0, DUR, B):
    if 7.3 < bt < CTA:
        i = int(bt * SR); L = int(.3 * SR)
        pump[i:i + L] = np.minimum(pump[i:i + L], 1 - .35 * np.exp(-np.arange(min(L, N - i)) / SR * 12))
pad = onepole_lp(pad, 2400)
arp_rev = conv_reverb(arp, 1.6, .4)[:N]
bed = (pad * 1.0 + bass + arp_rev) * pump + kick + hat
fade = np.clip(tg / 2.0, 0, 1) * np.clip((END - tg) / 1.6, 0, 1)
bed *= fade
music[:, 0] = bed; music[:, 1] = bed
# stereo width for pad/arp
music[:, 0] += np.roll(arp_rev, 240) * .25 * fade; music[:, 1] += np.roll(arp_rev, -240) * .25 * fade

mix = music * .55 + sfx * .9
mix /= max(1e-9, np.abs(mix).max()) / .89
out = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
with wave.open('assets/music/mix_raw.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(out.tobytes())
print('wrote assets/music/mix_raw.wav', round(N / SR, 2), 's,', len(cues['cues']), 'cues')
