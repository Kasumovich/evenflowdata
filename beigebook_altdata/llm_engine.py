"""LLM scorer (nuance pass).

Returns the same metric shape as the lexicon engine so the two can be diffed cell by
cell. Uses a strict JSON-only rubric. Requires ANTHROPIC_API_KEY in the environment;
the build sandbox does not call it. Paraphrased evidence only — never store verbatim
Beige Book sentences longer than a short clause (they're public domain, but the panel
is a derived dataset, not a reproduction).
"""
from __future__ import annotations
import json, os

MODEL = os.environ.get("BB_LLM_MODEL", "claude-sonnet-4-6")

RUBRIC = """You score one Federal Reserve Beige Book section for one district.
Return ONLY a JSON object, no prose, with keys:
  diffusion   float in [-2,2]  (-2 sharp decline, 0 flat, +2 robust growth)
  polarity    float in [-1,1]  (overall tone)
  uncertainty float in [0,1]   (density of hedging / uncertainty language)
  pass_through one of "easing","stable","rising","na"  (only for Prices; else "na")
  evidence    array of <=3 short PARAPHRASES (no verbatim quotes) supporting the scores
Score only what the text says; do not infer beyond it."""


def score_block(text: str, section: str, client=None) -> dict:
    if client is None:
        from anthropic import Anthropic
        client = Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=400,
        system=RUBRIC,
        messages=[{"role": "user",
                   "content": f"Section: {section}\n\n{text[:6000]}"}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"diffusion": None, "polarity": None, "uncertainty": None,
                "pass_through": "na", "evidence": [], "_parse_error": True}
