"""Transparent lexicon scorer.

Two signals per text block:
  * tone / uncertainty  -> Loughran-McDonald financial dictionary (via pysentiment2)
  * activity diffusion  -> the Beige Book adjective ladder mapped to an ordinal scale
                           (the Balke/Fulmer/Zhang approach)

Negation is handled with a small look-back window so "not strong" / "no longer tight"
flip sign instead of scoring positive. This engine is deterministic and runs over the
full 1970-present corpus cheaply; the LLM engine (llm_engine.py) is the nuance check.
"""
from __future__ import annotations
import re

# Ordinal ladder: the standardized intensity words the Beige Book leans on.
LADDER = {
    "robust": 2, "strong": 2, "strongly": 2, "rapid": 2, "surged": 2, "booming": 2,
    "solid": 1.5, "healthy": 1.5,
    "moderate": 1, "moderately": 1, "increased": 1, "rose": 1, "grew": 1, "expanded": 1,
    "improved": 1, "gains": 1, "rising": 1,
    "modest": 0.5, "modestly": 0.5, "slight": 0.5, "slightly": 0.5, "edged": 0.5,
    "flat": 0, "unchanged": 0, "stable": 0, "steady": 0, "little changed": 0,
    "soft": -1, "softened": -1, "softer": -1, "slowed": -1, "slowing": -1,
    "declined": -1, "decreased": -1, "fell": -1, "weak": -1, "weakened": -1,
    "weaker": -1, "deteriorated": -1, "contracted": -1, "pulled back": -1,
    "sharp decline": -2, "plunged": -2, "collapsed": -2, "deteriorated sharply": -2,
}
NEGATORS = {"not", "no", "never", "without", "little", "few", "hardly", "barely"}
_WORD = re.compile(r"[a-z]+(?:\s[a-z]+)?")

try:
    from pysentiment2 import LM
    _lm = LM()
except Exception:  # pragma: no cover
    _lm = None


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]+", text.lower())


def diffusion(text: str) -> float | None:
    """Mean ladder value over matched intensity words in [-2, 2], with negation flip.
    Returns None if no ladder words appear (so empty cells don't masquerade as 0)."""
    t = text.lower()
    toks = _tokens(t)
    hits = []
    # multi-word ladder phrases first
    for phrase, val in LADDER.items():
        if " " in phrase and phrase in t:
            hits.append(val)
    for i, w in enumerate(toks):
        if w in LADDER:
            val = LADDER[w]
            window = toks[max(0, i - 3):i]
            if any(n in window for n in NEGATORS):
                val = -val
            hits.append(val)
    if not hits:
        return None
    return round(sum(hits) / len(hits), 4)


def tone(text: str) -> dict:
    """Loughran-McDonald polarity and uncertainty share."""
    if _lm is None:
        return {"polarity": None, "subjectivity": None, "uncertainty": None}
    toks = _lm.tokenize(text)
    sc = _lm.get_score(toks)            # Positive, Negative, Polarity, Subjectivity
    pos, neg = _lm.get_score(toks).get("Positive", 0), sc.get("Negative", 0)
    n = max(len(toks), 1)
    # crude uncertainty share via LM uncertainty list if available
    unc = sum(1 for w in toks if w in getattr(_lm, "_uncertainty", set())) / n
    return {"polarity": round(sc.get("Polarity", 0), 4),
            "subjectivity": round(sc.get("Subjectivity", 0), 4),
            "pos_rate": round(pos / n, 4), "neg_rate": round(neg / n, 4),
            "uncertainty": round(unc, 4)}


def score_block(text: str) -> dict:
    """Per-cell metric bundle for one district x section block."""
    t = tone(text)
    return {
        "diffusion": diffusion(text),
        "polarity": t.get("polarity"),
        "uncertainty": t.get("uncertainty"),
        "neg_rate": t.get("neg_rate"),
        "n_words": len(_tokens(text)),
    }
