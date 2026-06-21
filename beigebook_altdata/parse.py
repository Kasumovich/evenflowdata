"""Segment raw Beige Book HTML into {district: {section: text}}.

The modern book (roughly 2017+) is cleanly structured: a national summary, then one
block per district, each with bold section headers (Labor Markets, Prices, ...).
Older issues are messier; the parser therefore works off heading TEXT (district names
and known section names) rather than brittle CSS selectors, and falls back to
paragraph heuristics when headers are absent.
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup
from .config import DISTRICTS, SECTIONS

_norm = lambda s: re.sub(r"[^a-z ]", "", s.lower()).strip()
_DISTRICT_KEYS = {_norm(d): d for d in DISTRICTS}
_SECTION_KEYS = {_norm(s): s for s in SECTIONS}
# Common header variants seen across decades.
_SECTION_ALIASES = {
    "employment and wages": "Labor Markets",
    "labor market": "Labor Markets",
    "wages and prices": "Prices",
    "prices and wages": "Prices",
    "real estate": "Real Estate and Construction",
    "construction and real estate": "Real Estate and Construction",
    "retail": "Consumer Spending",
}


def _clean(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup.decode()


def _match_district(text: str) -> str | None:
    n = _norm(text)
    for key, name in _DISTRICT_KEYS.items():
        if key in n:                       # 'federal reserve bank of boston' -> Boston
            return name
    return None


def _match_section(text: str) -> str | None:
    n = _norm(text)
    if n in _SECTION_KEYS:
        return _SECTION_KEYS[n]
    if n in _SECTION_ALIASES:
        return _SECTION_ALIASES[n]
    for alias, canon in _SECTION_ALIASES.items():
        if n.startswith(alias):
            return canon
    return None


def parse_book(html: str) -> dict[str, dict[str, str]]:
    """Return {district_or_'National': {section: text}}.

    Walks headings in document order, tracking the current district and section and
    accumulating the prose in between.
    """
    soup = BeautifulSoup(_clean(html), "lxml")
    out: dict[str, dict[str, str]] = {}
    cur_district, cur_section = "National", "Overall Economic Activity"
    buf: list[str] = []

    def flush():
        if buf:
            out.setdefault(cur_district, {}).setdefault(cur_section, "")
            out[cur_district][cur_section] += " " + " ".join(buf)
            buf.clear()

    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong", "b", "p"]):
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "strong", "b") and len(txt) < 80:
            d = _match_district(txt)
            s = _match_section(txt)
            if d:
                flush(); cur_district, cur_section = d, "Overall Economic Activity"; continue
            if s:
                flush(); cur_section = s; continue
        if el.name == "p":
            buf.append(txt)
    flush()
    # Tidy whitespace.
    for d in out:
        for s in out[d]:
            out[d][s] = re.sub(r"\s+", " ", out[d][s]).strip()
    return out


def section_text(book: dict[str, dict[str, str]], section: str) -> dict[str, str]:
    """Pull one section across all districts -> {district: text}."""
    return {d: secs[section] for d, secs in book.items() if section in secs and secs[section]}
