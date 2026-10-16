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


@dataclass
class MatchResult:
    exact_id: int | None = None
    # (id, ko'rsatiladigan nom, ball) — eng o'xshashlari, takrorlanmasdan
    options: list[tuple[int, str, float]] = field(default_factory=list)


def match(
    query: str,
    items: list[tuple[int, str]],
    aliases: list[tuple[int, str]] | None = None,
    limit: int = 4,
) -> MatchResult:
    """items: (id, nom). aliases: (id, taxallus) — avval tanlangan to'g'rilashlar."""
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

    if q in choices:
        return MatchResult(exact_id=choices[q])
    # "a12" == "A-12", "alisher aka" != ... — bo'shliqsiz solishtirish
    compact = {text.replace(" ", ""): item_id for text, item_id in choices.items()}
    if q.replace(" ", "") in compact:
        return MatchResult(exact_id=compact[q.replace(" ", "")])

    scored = process.extract(q, list(choices.keys()), scorer=fuzz.WRatio, limit=limit * 3)
    best: dict[int, float] = {}
    for text, score, _ in scored:
        item_id = choices[text]
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
