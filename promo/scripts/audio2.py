#!/usr/bin/env python3
"""v2 sound design: professional UI / motion SFX synthesised in code (no samples, no music) + VO mix.
Reads plan/cues_v2.json and assets/vo/vo_clean.wav, writes assets/music/mix_v2_raw.wav (48 kHz stereo)."""
import json, wave
import numpy as np

SR = 48000
R = np.random.default_rng(11)
cfg = json.load(open('plan/cues_v2.json'))
DUR = cfg['duration'] + .3
N = int(DUR * SR)
tt = lambda d: np.arange(int(d * SR)) / SR


def lp(x, fc):
    fc = np.broadcast_to(np.asarray(fc, float), x.shape)
    a = np.exp(-2 * np.pi * fc / SR); y = np.empty_like(x); z = 0.0
    for i in range(len(x)): z = (1 - a[i]) * x[i] + a[i] * z; y[i] = z
    return y


def hp(x, fc): return x - lp(x, fc)


def bp(x, fc, q=1.0):
    fc = np.broadcast_to(np.asarray(fc, float), x.shape)
    lo = b = 0.0; y = np.empty_like(x)
    for i in range(len(x)):
        f = 2 * np.sin(np.pi * min(fc[i], SR / 6) / SR)
        hi = x[i] - lo - b / q; b += f * hi; lo += f * b; y[i] = b
    return y


def env(n, a, d, curve=4.0):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(a, 1e-4)) * np.exp(-np.maximum(0, t - a) * curve / max(d, 1e-4))


_IR = {}
def verb(x, size=1.2, mix=.25, seed=5):
    key = (size, seed)
    if key not in _IR:
        r = np.random.default_rng(seed); n = int(size * SR)
        ir = r.standard_normal((n, 2)) * np.exp(-np.arange(n) / SR * 6 / size)[:, None]
        ir[:, 0] = lp(ir[:, 0], 6500); ir[:, 1] = lp(ir[:, 1], 6500)
        ir /= np.sqrt((ir ** 2).sum(0))
        _IR[key] = ir
    ir = _IR[key]
    if x.ndim == 1: x = np.stack([x, x], 1)
    L = len(x) + len(ir); nf = 1 << (L - 1).bit_length()
    wet = np.stack([np.fft.irfft(np.fft.rfft(x[:, c], nf) * np.fft.rfft(ir[:, c], nf), nf)[:L] for c in range(2)], 1)
    out = np.zeros((L, 2)); out[:len(x)] = x
    return out * (1 - mix) + wet * mix * 1.4


def st(x, pan=0.0):
    if x.ndim == 2: return x
    l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    return np.stack([x * l, x * r], 1) * 1.414


def panmove(x, a, b):
    n = len(x); p = np.linspace(a, b, n)
    return np.stack([x * np.cos((p + 1) * np.pi / 4), x * np.sin((p + 1) * np.pi / 4)], 1) * 1.414


def sine(f, d, ph=0):
    t = tt(d); f = np.broadcast_to(np.asarray(f, float), t.shape)
    return np.sin(2 * np.pi * np.cumsum(f) / SR + ph)


# ----------------------------------------------------------- sounds
def s_whoosh(d=.6, lo=250, hi=5200, q=.8, pan=(-.7, .7), sub=0.0):
    t = tt(d); p = t / d
    shape = np.sin(np.pi * p) ** 1.6 * (1 + .8 * p)
    shape /= shape.max()
    n = R.standard_normal(len(t))
    fc = lo + (hi - lo) * np.sin(np.pi * np.clip(p * 1.05, 0, 1)) ** 2
    s = bp(n, fc, q) * shape * .9 + bp(n, fc * .5, .6) * shape * .4
    if sub: s += sine(60 + 30 * (1 - p), d) * np.exp(-((p - .55) / .18) ** 2) * sub
    return verb(panmove(s, *pan), .8, .18)


def s_trans():
    w = s_whoosh(.75, 180, 6000, .7, (-.8, .8))
    t = tt(.5); thump = sine(90 * np.exp(-t * 9) + 45, .5) * env(len(t), .003, .18) * .55
    out = np.zeros((len(w) + 100, 2)); out[:len(w)] += w
    i = int(.36 * SR); th = st(thump); out[i:i + len(th)] += th[:len(out) - i]
    return out


def s_phonein():
    w = s_whoosh(.95, 120, 3200, .6, (.2, -.1), sub=.35)
    t = tt(.6); land = sine(70 * np.exp(-t * 6) + 38, .6) * env(len(t), .004, .25) * .6
    land += lp(R.standard_normal(len(t)), 1200) * env(len(t), .001, .03) * .25
    out = np.zeros((len(w) + int(.3 * SR), 2)); out[:len(w)] += w
    i = int(.78 * SR); L = st(land); out[i:i + len(L)] += L[:len(out) - i]
    return out


def s_tap():
    t = tt(.09)
    click = hp(R.standard_normal(len(t)), 2500) * env(len(t), .0004, .004) * .5
    body = sine(1150, .09) * env(len(t), .001, .018) * .35 + sine(170, .09) * env(len(t), .002, .03) * .45
    return verb(st(click + body, .05), .4, .08)


def s_send():
    t = tt(.16); f = 520 + 600 * (1 - np.exp(-t * 45))
    s = (sine(f, .16) + .25 * sine(f * 2, .16)) * env(len(t), .004, .06) * .45
    s += hp(R.standard_normal(len(t)), 3000) * env(len(t), .001, .02) * .12
    return verb(st(s, .3), .6, .2)


def s_recv():
    t = tt(.35)
    s = (sine(988, .35) * .6 + sine(1480, .35) * .25) * env(len(t), .003, .12)
    s += sine(659, .35) * env(len(t), .002, .07) * .35
    return verb(st(s * .45, -.25), .7, .22)


def s_type(d):
    out = np.zeros(int((d + .2) * SR)); x = 0.0
    while x < d:
        t = tt(.05)
        k = bp(R.standard_normal(len(t)), 2400 + R.random() * 1800, 1.4) * env(len(t), .0005, .008) * (.5 + .4 * R.random())
        k += sine(260 + 80 * R.random(), .05) * env(len(t), .001, .012) * .35
        i = int(x * SR); out[i:i + len(k)] += k[:len(out) - i]
        x += .065 + R.random() * .07
    return verb(st(out * .8, .35), .3, .1)


def s_kb(up):
    d = .28; t = tt(d); p = t / d
    s = bp(R.standard_normal(len(t)), (900 + 2200 * p) if up else (3100 - 2200 * p), .9) * np.sin(np.pi * p) ** 2 * .45
    return st(s)


def s_notify():
    out = np.zeros(int(1.6 * SR))
    for i, f in enumerate([1318.5, 1760, 2093]):
        t = tt(1.1); s = (sine(f, 1.1) + .3 * sine(f * 4, 1.1) * np.exp(-t * 30)) * env(len(t), .002, .35) * .32
        o = int(i * .075 * SR); out[o:o + len(s)] += s
    return verb(st(out), 1.0, .3)


def s_rec(up):
    out = np.zeros(int(.3 * SR))
    for i, f in enumerate([880, 1320] if up else [1320, 880]):
        t = tt(.09); s = sine(f, .09) * env(len(t), .003, .04) * .35
        o = int(i * .085 * SR); out[o:o + len(s)] += s
    return verb(st(out), .4, .15)


def s_sheet():
    w = s_whoosh(.55, 300, 3800, .9, (0, 0))
    out = np.zeros((len(w) + 100, 2)); out[:len(w)] += w * .8
    c = s_tap(); i = int(.45 * SR); out[i:i + len(c)] += c[:len(out) - i] * .7
    return out


def s_shimmer():
    out = np.zeros(int(1.6 * SR))
    for i, f in enumerate([2093, 2637, 3136, 3951, 4699]):
        t = tt(1.1); s = sine(f, 1.1) * env(len(t), .002, .5) * .1
        o = int(i * .045 * SR); out[o:o + len(s)] += s
    return verb(panmove(out, -.5, .5), 1.4, .45)


def s_tick(f=1900, g=.35):
    t = tt(.05); return st(sine(f, .05) * env(len(t), .0008, .012) * g + hp(R.standard_normal(len(t)), 4000) * env(len(t), .0003, .003) * .12)


def s_tick3():
    out = np.zeros((int(.6 * SR), 2))
    for i in range(3):
        k = s_tick(1700 + 250 * i); o = int(i * .15 * SR); out[o:o + len(k)] += k
    return out


def s_roll(d):
    out = np.zeros((int((d + .1) * SR), 2)); x = 0.0
    while x < d:
        p = x / d; k = s_tick(1500 + 900 * p, .3)
        o = int(x * SR); out[o:o + len(k)] += k[:len(out) - o]
        x += .04 + .1 * p ** 2.5
    return out


def s_pop():
    t = tt(.12); f = 340 + 520 * (1 - np.exp(-t * 60))
    s = sine(f, .12) * env(len(t), .002, .035) * .5
    s += bp(R.standard_normal(len(t)), 1800, 1.5) * env(len(t), .0005, .006) * .25
    return verb(st(s, (R.random() - .5) * .4), .5, .15)


def s_swish():
    return s_whoosh(.3, 1200, 7000, 1.1, (-.3, .5)) * .7


def s_paper():
    d = .7; t = tt(d); n = R.standard_normal(len(t))
    flutter = .5 + .5 * np.abs(np.sin(2 * np.pi * (9 + 5 * R.random()) * t + R.random()))
    s = bp(n, 2600, .7) * np.sin(np.pi * t / d) ** 1.5 * flutter * .45
    return verb(st(s, -.2), .5, .15)


def s_crumple():
    d = .55; out = np.zeros(int(d * SR)); x = 0.0
    while x < d:
        t = tt(.03); c = bp(R.standard_normal(len(t)), 1500 + R.random() * 4500, 2) * env(len(t), .0005, .006) * (.4 + .6 * R.random())
        i = int(x * SR); out[i:i + len(c)] += c[:len(out) - i]
        x += .008 + R.random() * .03
    out *= np.sin(np.pi * np.arange(len(out)) / len(out)) ** .6
    return verb(st(out * .8), .5, .15)


def s_impact():
    t = tt(2.2)
    sub = sine(62 * np.exp(-t * 2.5) + 34, 2.2) * env(len(t), .003, 1.1) * .9
    punch = sine(140 * np.exp(-t * 18) + 80, 2.2) * env(len(t), .001, .09) * .5
    nz = lp(R.standard_normal(len(t)), 2500) * env(len(t), .0005, .05) * .5
    air = hp(R.standard_normal(len(t)), 5000) * env(len(t), .001, .5) * .05
    return verb(st(sub + punch + nz + air), 2.2, .3)


def s_riser(d=.9):
    t = tt(d); p = t / d
    n = bp(R.standard_normal(len(t)), 300 + 7000 * p ** 2, 1.4) * .4
    tone = (sine(200 + 700 * p ** 2, d) + .5 * sine(300 + 1050 * p ** 2, d)) * .12
    return verb(panmove((n + tone) * p ** 2.4, -.4, .4), 1.0, .3)


def s_ding():
    t = tt(1.8)
    s = sum(a * sine(f, 1.8) * env(len(t), .002, k) for f, a, k in [(1567.98, .45, .7), (2349.3, .25, .45), (3135.96, .12, .3), (4698.6, .06, .2)])
    return verb(st(s * .55), 1.3, .35)


def s_shutter():
    out = np.zeros(int(.2 * SR))
    for o in (0, .055):
        t = tt(.04); c = bp(R.standard_normal(len(t)), 3200, 1.2) * env(len(t), .0004, .01) * .6 + sine(400, .04) * env(len(t), .001, .01) * .3
        i = int(o * SR); out[i:i + len(c)] += c
    return verb(st(out), .4, .12)


def s_flip():
    w = s_whoosh(.22, 1500, 6000, 1.2, (.3, -.3)) * .6
    out = np.zeros((len(w) + int(.1 * SR), 2)); out[:len(w)] += w
    k = s_tick(2400, .3); i = int(.18 * SR); out[i:i + len(k)] += k
    return out


def s_lock():
    t = tt(.3)
    s = bp(R.standard_normal(len(t)), 3400, 4) * env(len(t), .0005, .012) * .9
    s += sine(2200, .3) * env(len(t), .001, .02) * .25 + sine(180, .3) * env(len(t), .002, .05) * .45
    s2 = np.zeros_like(s); i = int(.06 * SR); s2[i:] = s[:len(s) - i] * .6
    return verb(st(s + s2), .5, .2)


def s_sub():
    t = tt(2.5); return st(sine(48 * np.exp(-t * .5), 2.5) * env(len(t), .01, 1.0) * .7)


GEN = {
    'trans': s_trans, 'phonein': s_phonein, 'tap': s_tap, 'send': s_send, 'recv': s_recv, 'kbup': lambda: s_kb(True),
    'kbdown': lambda: s_kb(False), 'notify': s_notify, 'recstart': lambda: s_rec(True), 'recstop': lambda: s_rec(False),
    'sheet': s_sheet, 'shimmer': s_shimmer, 'tick3': s_tick3, 'pop': s_pop, 'swish': s_swish, 'whoosh': lambda: s_whoosh(),
    'paper': s_paper, 'crumple': s_crumple, 'impact': s_impact, 'riser': s_riser, 'ding': s_ding, 'shutter': s_shutter,
    'tick': lambda: s_tick(), 'flip': s_flip, 'lock': s_lock, 'sub': s_sub,
}
OFFS = {'trans': -.36, 'whoosh': -.3, 'phonein': -.05, 'riser': -.65, 'swish': -.12, 'sheet': -.1, 'kbup': -.05, 'kbdown': -.05}
cache = {}
sfx = np.zeros((N, 2))
last = {}
for c in cfg['cues']:
    ty, t, g = c['type'], c['t'], c['g']
    if ty in last and t - last[ty] < .05: continue
    last[ty] = t
    if ty in ('type', 'roll'):
        s = s_type(c.get('d', .6)) if ty == 'type' else s_roll(c.get('d', 1.0))
    else:
        if ty not in cache or ty in ('pop', 'whoosh', 'swish', 'paper', 'crumple', 'tap'):  # re-roll variable ones
            cache[ty] = GEN[ty]()
        s = cache[ty]
    i = int(max(0, t + OFFS.get(ty, 0)) * SR)
    if i >= N: continue
    s = s[:N - i]; sfx[i:i + len(s)] += s * g

# --- VO: placed at OFF, mono -> stereo
with wave.open('assets/vo/vo_clean.wav') as w:
    vo = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float) / 32768
    if w.getnchannels() == 2: vo = vo.reshape(-1, 2).mean(1)
OFF = cfg.get('off', 0.4)
voa = np.zeros(N); i = int(OFF * SR); voa[i:i + len(vo)] = vo[:N - i]
# gentle ducking of SFX under speech (envelope follower)
e = lp(np.abs(voa), 8.0)
duck = 1 - .35 * np.clip(e / (e.max() * .25 + 1e-9), 0, 1)
sfx_stem = sfx * duck[:, None] * .42
sfx_stem = .4 * np.tanh(sfx_stem / .4)
def rms_db(x): return 20*np.log10(np.sqrt(np.mean(x**2))+1e-12)
print('VO rms dB', round(rms_db(voa[voa!=0]),1), ' SFX rms dB (active)', round(rms_db(sfx_stem[np.abs(sfx_stem).sum(1)>1e-4]),1), ' SFX peak', round(20*np.log10(np.abs(sfx_stem).max()),1), ' VO peak', round(20*np.log10(np.abs(voa).max()),1))
mix = np.stack([voa, voa], 1) + sfx_stem
mix /= max(1e-9, np.abs(mix).max()) / .9
with wave.open('assets/music/mix_v2_raw.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes())
print('mix_v2_raw.wav', round(N / SR, 2), 's', len(cfg['cues']), 'cues')
