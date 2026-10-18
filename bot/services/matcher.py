"""Ovozdan chiqqan ismni bazadagi ism bilan solishtirish.

Ovoz matni ba'zan kirill, ba'zan lotin yozuvida keladi, ismlar esa
biroz buzilib yoziladi ("Alishir"). Shuning uchun ikkala tomonni bir xil
ko'rinishga keltirib, so'ng noaniq (fuzzy) solishtiramiz.
"""
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}
_APOSTROPHES = re.compile(r"[ʻʼ'`‘’ʹ]")
_NON_WORD = re.compile(r"[^a-z0-9' ]+")


def normalize(text: str) -> str:
    text = text.lower()
    text = "".join(_CYR.get(ch, ch) for ch in text)
    text = _APOSTROPHES.sub("'", text)
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


# Ism yonida aytiladigan hurmat so'zlari: «Robiya opa», «Alisher aka»
_HONORIFICS = {
    "aka", "akajon", "opa", "opajon", "xola", "xolajon", "hola", "amaki", "tog'a", "uka", "ukajon", "singil",
    "ota", "ona", "buvi", "bobo", "momo", "domla", "usta",
}
# So'z oxiridagi qo'shimchalar (uzunrog'i birinchi): «Robiyaga», «Robiyadan», «Robiyajonning», «A-12dan»
_SUFFIXES = (
    "larning", "lardan", "larga", "larni", "gacha", "ning", "niki", "dagi", "lar", "dan", "jon", "ga", "ka", "qa", "ni",
    "da",
)


def _strip_suffix(word: str) -> str | None:
    for suffix in _SUFFIXES:
        stem = word[: -len(suffix)]
        if word.endswith(suffix) and (len(stem) >= 3 or (len(stem) >= 2 and any(ch.isdigit() for ch in stem))):
            return stem
    return None


def variants(text: str) -> list[str]:
    """Normallashgan matnning qo'shimcha va hurmat so'zlarisiz ko'rinishlari:
    «robiya opaga» -> ["robiya opa", "robiya"], «robiyajonga» -> ["robiyajon", "robiya"]."""
    out: list[str] = []
    words = text.split()
    for _ in range(4):
        if not words:
            break
        last = words[-1]
        if last in _HONORIFICS and len(words) > 1:
            words = words[:-1]
        else:
            stem = _strip_suffix(last)
            if stem is None:
                break
            words = [*words[:-1], stem]
        out.append(" ".join(words))
    return out


_SOUNDS = str.maketrans({"o": "a", "x": "h", "q": "k", "e": "i", "y": "i", "w": "v"})
_FINAL_G = re.compile(r"g\b")
_REPEATS = re.compile(r"(.)\1+")


def phonetic(text: str) -> str:
    """Ovozdan matnga o'tganda adashadigan harflar bir xil: o/a («Rabiya» — Robiya), x/h («Toxir» — Tohir),
    q/k, e/i, y/i, oxirgi g/k («sapok» — Sapog); takror harf — bitta. Faqat solishtirish uchun.
    Raqamli kodlar («A-12», «E-5») o'zgarmaydi: ularda har bir harf boshqa model."""
    t = normalize(text)
    if any(ch.isdigit() for ch in t):
        return t
    t = _FINAL_G.sub("k", t.replace("'", "").replace("dj", "j").translate(_SOUNDS))
    return _REPEATS.sub(r"\1", t)


@dataclass
class MatchResult:
    exact_id: int | None = None
    # (id, ko'rsatiladigan nom, ball) — eng o'xshashlari, takrorlanmasdan
    options: list[tuple[int, str, float]] = field(default_factory=list)


def _only(ids: set[int] | None) -> int | None:
    return next(iter(ids)) if ids and len(ids) == 1 else None


def match(
    query: str,
    items: list[tuple[int, str]],
    aliases: list[tuple[int, str]] | None = None,
    limit: int = 4,
) -> MatchResult:
    """items: (id, nom). aliases: (id, taxallus) — avval tanlangan to'g'rilashlar.

    Aniq mos kelishi (exact_id): yozilishi bir xil; qo'shimchasiz bir xil («Robiyaga» — Robiya); ismning
    bir qismi («Robiya» — Robiya Karimova, «Robiya» — Robiya opa, «Uz charm» — Xayrulloh Uz charm) yoki
    talaffuzi bir xil («Rabiya» — Robiya) — faqat bittasiga mos kelsa. Ikki ishchi bir xil chiqsa — so'raladi."""
    q = normalize(query)
    if not q or not items:
        return MatchResult()

    names = {item_id: name for item_id, name in items}
    # Har bir variant: normallashgan matn -> id
    choices: dict[str, int] = {}
    for item_id, name in items:
        choices.setdefault(normalize(name), item_id)
    for item_id, alias in aliases or []:
        if item_id in names:
            choices.setdefault(normalize(alias), item_id)

    queries = [q, *variants(q)]
    # "a12" == "A-12", "alisher aka" != ... — bo'shliqsiz solishtirish
    compact = {text.replace(" ", ""): item_id for text, item_id in choices.items()}
    for text in queries:
        if text in choices:
            return MatchResult(exact_id=choices[text])
        if text.replace(" ", "") in compact:
            return MatchResult(exact_id=compact[text.replace(" ", "")])

    # Nomning qismlari: «Robiya opa» -> «robiya», «Robiya Karimova» -> «robiya», «karimova»,
    # «Xayrulloh Uz charm» -> «xayrulloh uz», «uz charm»
    parts: dict[str, set[int]] = {}
    for text, item_id in choices.items():
        words = text.split()
        if len(words) < 2:
            continue
        phrases = (" ".join(words[i:j]) for i in range(len(words)) for j in range(i + 2, len(words) + (i > 0)))
        for part in {*variants(text), *(w for w in words if len(w) >= 3 and w not in _HONORIFICS), *phrases}:
            parts.setdefault(part, set()).add(item_id)
    for text in queries:
        if (found := _only(parts.get(text))) is not None:
            return MatchResult(exact_id=found)

    sounds: dict[str, set[int]] = {}
    for text, item_id in [*choices.items(), *((t, i) for t, ids in parts.items() for i in ids)]:
        sounds.setdefault(phonetic(text), set()).add(item_id)
    for text in queries:
        if (found := _only(sounds.get(phonetic(text)))) is not None:
            return MatchResult(exact_id=found)

    scored = process.extract(q, list(choices.keys()), scorer=fuzz.WRatio, limit=limit * 3)
    best: dict[int, float] = {}
    for text, score, _ in scored:
        item_id = choices[text]
        best[item_id] = max(best.get(item_id, 0), score)
    # Talaffuz bo'yicha ham: «Rabiyaxon» — Robiya
    sound_keys: dict[str, set[int]] = {}
    for text, item_id in choices.items():
        sound_keys.setdefault(phonetic(text), set()).add(item_id)
    for text, score, _ in process.extract(phonetic(q), list(sound_keys.keys()), scorer=fuzz.WRatio, limit=limit * 3):
        for item_id in sound_keys[text]:
            best[item_id] = max(best.get(item_id, 0), score)

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    if ranked:
        top_id, top_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0
        if top_score >= 92 and top_score - second >= 8:
            return MatchResult(exact_id=top_id)

    top = ranked[0][1] if ranked else 0
    options = [(i, names[i], s) for i, s in ranked if s >= 60 and s >= top - 15][:limit]
    return MatchResult(options=options)
