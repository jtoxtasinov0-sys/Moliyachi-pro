#!/usr/bin/env python3
"""Text-to-audio alignment without ASR: DP over pause-separated speech segments.
Output plan/words.json: [{w, t0, t1, para}]"""
import json, re
sil = [tuple(map(float, l.split())) for l in open('cache/sil.txt')]
END = 131.08
segs, prev = [], 0.0
for a, b in sil:
    if a - prev > 0.05: segs.append((prev, a))
    prev = b
if END - prev > 0.05: segs.append((prev, END))
paras = [p.strip() for p in open('plan/vo_text.txt').read().split('\n\n') if p.strip()]
words = []
for pi, p in enumerate(paras):
    for w in p.split():
        words.append({'w': w, 'para': pi})
def syl(w):
    s = re.sub(r"[^a-zA-Z'ʻ]", '', w.lower()).replace("o'", 'o').replace("g'", 'g')
    return max(1, len(re.findall(r'[aeiou]', s)))
for x in words:
    x['s'] = syl(x['w']) + 0.35  # small per-word overhead
    x['punct'] = bool(re.search(r'[.,?!:;—»]$', x['w'])) or x['w'] == '—'
    x['pend'] = bool(re.search(r'[.?!]»?$', x['w']))
W, S = len(words), len(segs)
cum = [0.0]
for x in words: cum.append(cum[-1] + x['s'])
tot_speech = sum(b - a for a, b in segs)
rate = tot_speech / cum[-1]
INF = 1e18
dp = [[INF] * (W + 1) for _ in range(S + 1)]; bk = [[0] * (W + 1) for _ in range(S + 1)]
dp[0][0] = 0
for i in range(1, S + 1):
    d = segs[i - 1][1] - segs[i - 1][0]
    for j in range(1, W + 1):
        best, arg = INF, 0
        for k in range(max(0, j - 60), j):
            if dp[i - 1][k] >= INF: continue
            pred = (cum[j] - cum[k]) * rate
            c = (d - pred) ** 2 / max(d, .3)
            if j < W and not words[j - 1]['punct']: c += 0.8
            if j < W and words[j - 1]['para'] != words[j]['para']: c -= 0.4  # paragraph boundary at a pause is good
            v = dp[i - 1][k] + c
            if v < best: best, arg = v, k
        dp[i][j], bk[i][j] = best, arg
j, bounds = W, []
for i in range(S, 0, -1):
    k = bk[i][j]; bounds.append((i - 1, k, j)); j = k
bounds.reverse()
for si, k, j in bounds:
    a, b = segs[si]; tot = cum[j] - cum[k]
    for n in range(k, j):
        words[n]['t0'] = round(a + (b - a) * (cum[n] - cum[k]) / tot, 3)
        words[n]['t1'] = round(a + (b - a) * (cum[n + 1] - cum[k]) / tot, 3)
json.dump([{k: x[k] for k in ('w', 't0', 't1', 'para')} for x in words], open('plan/words.json', 'w'), ensure_ascii=False, indent=0)
for si, k, j in bounds:
    print(f"{segs[si][0]:6.2f}-{segs[si][1]:6.2f} | " + ' '.join(x['w'] for x in words[k:j]))
