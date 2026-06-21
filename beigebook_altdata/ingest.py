"""Ingestion layer.

Runs in an environment WITH network access to federalreserve.gov / fraser.stlouisfed.org.
(The build sandbox blocks those domains, so these functions are written and unit-shaped
but executed by the user.) Every fetched document is cached to disk as a point-in-time
artifact and never overwritten — the Beige Book is never revised, so the cache IS the
vintage record.
"""
from __future__ import annotations
import os, time, datetime as dt
from dataclasses import dataclass

try:
    import requests
except ImportError:  # keep import-safe in minimal envs
    requests = None

from .config import FED_BASE, FRASER_BASE

CACHE = os.environ.get("BB_CACHE", os.path.expanduser("~/.bb_cache"))
HEADERS = {"User-Agent": "beigebook-altdata/0.1 (research)"}


@dataclass
class Book:
    date: dt.date          # release date (the vintage stamp)
    year: int
    raw_html: str          # full document as released
    source: str            # 'fed' | 'fraser'


def _cache_path(date: dt.date) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, f"beigebook_{date:%Y%m%d}.html")


def _get(url: str) -> str:
    if requests is None:
        raise RuntimeError("requests not installed in this environment")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def fed_url(date: dt.date) -> str:
    """Board-of-Governors URL. Scheme drifts by era; these cover 2011-present.
    Older Board-era issues (1996-2010) use /newsevents/ paths resolved via the
    archive index (see release_index)."""
    ym = f"{date:%Y%m}"
    # Modern full-book pages: beigebook{YYYYMM}.htm ; some issues use -summary suffix.
    return f"{FED_BASE}/beigebook{ym}.htm"


def fetch_fed(date: dt.date) -> Book:
    html = _get(fed_url(date))
    return Book(date=date, year=date.year, raw_html=html, source="fed")


def fetch_fraser(fraser_item_url: str, date: dt.date) -> Book:
    """Pre-1996 issues. FRASER serves scanned PDFs/OCR text per issue; the caller
    supplies the resolved item URL from the FRASER Beige Book collection map."""
    html = _get(fraser_item_url)
    return Book(date=date, year=date.year, raw_html=html, source="fraser")


def get_book(date: dt.date, fraser_map: dict[str, str] | None = None) -> Book:
    """Cache-first fetch. Routes to Fed (>=1996) or FRASER (<1996)."""
    cp = _cache_path(date)
    if os.path.exists(cp):
        return Book(date=date, year=date.year, raw_html=open(cp, encoding="utf-8").read(),
                    source="cache")
    if date.year >= 1996:
        book = fetch_fed(date)
    else:
        if not fraser_map or date.isoformat() not in fraser_map:
            raise KeyError(f"Need a FRASER item URL for pre-1996 issue {date}")
        book = fetch_fraser(fraser_map[date.isoformat()], date)
    open(cp, "w", encoding="utf-8").write(book.raw_html)
    time.sleep(1.0)  # be polite to the host
    return book


def release_index(start_year: int, end_year: int) -> list[dt.date]:
    """Resolve the ~8 issue dates per year.

    Production: scrape the Board's Beige Book archive index
    (/monetarypolicy/beige-book-archive.htm) for >=1996 and the FRASER collection
    for earlier years, returning exact release dates. Guessing dates is wrong because
    the schedule shifts with the FOMC calendar — so this returns [] until wired to
    the live index, forcing the caller to use real dates rather than approximations.
    """
    return []
