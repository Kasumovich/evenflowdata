"""Real Beige Book ingestion + scoring (2011-present).

Runs in an environment WITH network access to federalreserve.gov (the build sandbox
blocks it, so the first run happens in the user's GitHub Action). Built against the
CONFIRMED modern structure:

  landing : /monetarypolicy/beigebook{YYYYMM}.htm      -> links to 12 per-district pages
  district: /monetarypolicy/beigebook{YYYYMM}-{slug}.htm
            each has #### section headers: "Summary of Economic Activity",
            "Labor Markets", "Prices", then district-specific sections.
  archive : /monetarypolicy/beige-book-archive.htm     -> per-year release links

Pipeline: release_index() -> per release, fetch 12 district pages -> parse sections ->
score each with the frozen adjective ladder -> aggregate to per-book lens scores
(growth/inflation/labor/bottlenecks) + risks (uncertainty density) + breadth/dispersion.

Every fetched page is cached to disk and never overwritten: the Beige Book is never
revised, so the cache IS the point-in-time vintage record.
"""
from __future__ import annotations
import os, re, time, datetime as dt
from dataclasses import dataclass, field

try:
    import requests
except ImportError:
    requests = None

from bs4 import BeautifulSoup

from .config import DISTRICTS, LENS_SECTIONS
from . import lexicon

FED = "https://www.federalreserve.gov/monetarypolicy"
CACHE = os.environ.get("BB_CACHE", os.path.expanduser("~/.bb_cache"))
HEADERS = {"User-Agent": "beigebook-altdata/1.0 (research; contact via evenflowdata.com)"}
PAUSE = float(os.environ.get("BB_PAUSE", "0.7"))   # politeness delay between fetches

_norm = lambda s: re.sub(r"[^a-z ]", "", s.lower()).strip()
_DISTRICT_KEYS = {_norm(d): d for d in DISTRICTS}

# Canonical section headers we rely on (consistent across districts).
_CANON = {
    "summary of economic activity": "Summary of Economic Activity",
    "overall economic activity": "Summary of Economic Activity",
    "labor markets": "Labor Markets",
    "labor market": "Labor Markets",
    "employment and wages": "Labor Markets",
    "prices": "Prices",
    "wages and prices": "Prices",
}
# keyword -> lens fallback for district-specific section names (bottlenecks etc.)
_MANU_KEYS = ("manufactur", "supply", "freight", "transportation", "shipping")


# ---------------------------------------------------------------------------- fetch
def _get(url: str) -> str | None:
    if requests is None:
        raise RuntimeError("requests not installed")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        time.sleep(PAUSE)
        return r.text
    except Exception as e:
        print(f"  fetch error {url}: {e}")
        return None


def _cached(name: str, url: str) -> str | None:
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, name)
    if os.path.exists(cp):
        return open(cp, encoding="utf-8").read()
    html = _get(url)
    if html is not None:
        open(cp, "w", encoding="utf-8").write(html)
    return html


# ------------------------------------------------------------------- release index
@dataclass
class Release:
    date: dt.date
    ym: str                       # YYYYMM slug
    landing_url: str
    district_urls: dict = field(default_factory=dict)   # {district: url}


def release_index(start_year: int = 2011, end_year: int | None = None) -> list[Release]:
    """Crawl the Board archive for release landing pages >= start_year.

    The archive index links to per-YEAR pages (beigebook{YYYY}.htm); each year page
    links to the ~8 per-MONTH releases (beigebook{YYYYMM}.htm). We follow the year
    pages, then harvest the monthly release links. No filename guessing.
    """
    end_year = end_year or dt.date.today().year
    # 1) year pages: build directly (stable scheme) AND discover from the index.
    year_pages = {f"{FED}/beigebook{y}.htm" for y in range(start_year, end_year + 1)}
    idx = _get(f"{FED}/beige-book-archive.htm")
    if idx:
        for a in BeautifulSoup(idx, "lxml").find_all("a", href=True):
            m = re.search(r"beigebook(\d{4})\.htm$", a["href"])
            if m and start_year <= int(m.group(1)) <= end_year:
                year_pages.add(f"{FED}/beigebook{m.group(1)}.htm")

    # 2) each year page -> monthly release slugs (YYYYMM)
    found: dict[str, str] = {}
    for yp in sorted(year_pages):
        html = _get(yp)
        if not html:
            continue
        for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
            href = a["href"]
            # month links appear as beigebook{YYYYMM}-summary.htm (and sometimes bare)
            m = re.search(r"beigebook(\d{6})(?:-summary)?\.htm", href)
            if m:
                ym = m.group(1)
                if start_year <= int(ym[:4]) <= end_year:
                    full = href if href.startswith("http") else \
                        "https://www.federalreserve.gov" + (href if href.startswith("/")
                                                            else "/monetarypolicy/" + href)
                    found[ym] = full            # the summary page (carries the district nav)

    releases = [Release(date=_ym_to_date(ym), ym=ym, landing_url=u)
                for ym, u in sorted(found.items())]
    print(f"release_index: {len(releases)} releases {start_year}-{end_year} "
          f"(from {len(year_pages)} year pages)")
    return releases


def _ym_to_date(ym: str) -> dt.date:
    return dt.date(int(ym[:4]), int(ym[4:6]), 1)   # refined from landing text in get_book


# -------------------------------------------------------------------- district fetch
def _district_links(landing_html: str, ym: str) -> dict[str, str]:
    """From a landing page, harvest the per-district page URLs."""
    soup = BeautifulSoup(landing_html, "lxml")
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        m = re.search(rf"beigebook{ym}-([a-z\-]+)\.htm", a["href"])
        if not m:
            continue
        label = _match_district(a.get_text(" ", strip=True)) or _slug_to_district(m.group(1))
        if label:
            href = a["href"]
            full = href if href.startswith("http") else \
                   "https://www.federalreserve.gov" + (href if href.startswith("/")
                                                       else "/monetarypolicy/" + href)
            out[label] = full
    return out


def _slug_to_district(slug: str) -> str | None:
    n = slug.replace("-", " ")
    for key, name in _DISTRICT_KEYS.items():
        if key == n or key in n:
            return name
    return None


def _match_district(text: str) -> str | None:
    n = _norm(text)
    for key, name in _DISTRICT_KEYS.items():
        if key in n:
            return name
    return None


def _refine_date(landing_html: str, ym: str) -> dt.date:
    """Pull the real release date from the landing page (e.g. 'June 04, 2025')."""
    txt = BeautifulSoup(landing_html, "lxml").get_text(" ", strip=True)
    m = re.search(r"(January|February|March|April|May|June|July|August|September|"
                  r"October|November|December)\s+(\d{1,2}),\s+(\d{4})", txt)
    if m:
        try:
            return dt.datetime.strptime(m.group(0), "%B %d, %Y").date()
        except ValueError:
            pass
    return _ym_to_date(ym)


# ------------------------------------------------------------------- district parse
def parse_district_page(html: str) -> dict[str, str]:
    """Return {section_name: text} for one district page, using #### headers.

    Bounds content between the district body and the 'Back to Top'/'For more
    information' footer; treats short heading-like lines as section breaks.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    # collect headings (h3-h5, strong) and paragraphs in document order
    sections: dict[str, list[str]] = {}
    cur = "Summary of Economic Activity"
    for el in soup.find_all(["h2", "h3", "h4", "h5", "h6", "strong", "b", "p"]):
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        low = txt.lower()
        if low.startswith(("for more information", "back to top", "note:")):
            break
        if el.name in ("h2", "h3", "h4", "h5", "h6", "strong", "b") and len(txt) < 70:
            canon = _CANON.get(_norm(txt))
            sections.setdefault(canon or txt.strip(), [])
            cur = canon or txt.strip()
            continue
        if el.name == "p":
            sections.setdefault(cur, []).append(txt)
    return {k: re.sub(r"\s+", " ", " ".join(v)).strip() for k, v in sections.items() if v}


# ------------------------------------------------------------------------ scoring
def _route_section(name: str) -> set:
    """Which lenses a section header feeds, by keyword — robust across eras.
    2024+ per-district pages use 'Summary of Economic Activity / Labor Markets / Prices';
    pre-2024 single-page books use topic headers 'Manufacturing / Consumer Spending /
    Employment and Wages / Prices / Nonfinancial Services'. Keywords catch both."""
    n = name.lower()
    lenses = set()
    if "price" in n:
        lenses.add("inflation")
    if any(k in n for k in ("labor", "employ", "wage")) and "price" not in n:
        lenses.add("labor")
    if any(k in n for k in ("summary", "overall", "activity", "manufactur", "consumer",
                            "spending", "service", "retail", "tourism", "economic")):
        lenses.add("growth")
    if any(k in n for k in _MANU_KEYS):
        lenses.add("bottlenecks")
    return lenses


def _lens_from_sections(sections: dict[str, str]) -> dict[str, float | None]:
    """Score a page's sections into the four ladder lenses via keyword routing."""
    buckets: dict[str, list[str]] = {"growth": [], "inflation": [], "labor": [], "bottlenecks": []}
    for name, text in sections.items():
        for lens in _route_section(name):
            buckets[lens].append(text)
    return {lens: (lexicon.diffusion(" ".join(txts)) if txts else None)
            for lens, txts in buckets.items()}


def _risk_uncertainty(sections: dict[str, str]) -> float | None:
    """Risk lens = density of uncertainty/risk words across all of a district's text.
    (uncertain, uncertainty, risk, cautious, concern, volatile, tariff, disruption, ...)"""
    alltext = " ".join(sections.values())
    if not alltext:
        return None
    return lexicon.risk_density(alltext)


def score_release(rel: Release) -> dict | None:
    """Fetch + parse + score one release -> per-book lens composites + breadth/dispersion."""
    landing = _cached(f"bb_{rel.ym}.htm", rel.landing_url)
    if not landing:
        print(f"  {rel.ym}: no landing page")
        return None
    rel.date = _refine_date(landing, rel.ym)
    links = _district_links(landing, rel.ym)

    per_district: dict[str, dict[str, float | None]] = {}
    if len(links) >= 6:
        # modern era: one page per district
        for d, url in links.items():
            html = _cached(f"bb_{rel.ym}_{_norm(d).replace(' ', '-')}.htm", url)
            if not html:
                continue
            secs = parse_district_page(html)
            vals = _lens_from_sections(secs)
            vals["risks"] = _risk_uncertainty(secs)
            per_district[d] = vals
    else:
        # pre-2024 era: the whole book is on the landing page (topic-organized).
        # Score it as a single national observation.
        secs = parse_district_page(landing)
        if len(secs) < 2:
            print(f"  {rel.ym}: single-page parse found {len(secs)} sections — skipped")
            return None
        vals = _lens_from_sections(secs)
        vals["risks"] = _risk_uncertainty(secs)
        per_district["National"] = vals

    def agg(lens):
        xs = [v[lens] for v in per_district.values() if v.get(lens) is not None]
        if not xs:
            return None, None, None
        mean = sum(xs) / len(xs)
        breadth = sum(1 for x in xs if x > 0) / len(xs)
        disp = (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5
        return round(mean, 4), round(breadth, 4), round(disp, 4)

    rec = {"date": rel.date.isoformat(), "ndist": len(per_district)}
    for lens in ("growth", "inflation", "labor", "bottlenecks", "risks"):
        m, b, s = agg(lens)
        rec[lens] = m
        rec[f"{lens}_breadth"] = b
        rec[f"{lens}_disp"] = s
    return rec


# --------------------------------------------------------------------------- driver
def build_books(start_year: int = 2011, debug: bool = False) -> list[dict]:
    """Full 2011-present real ingestion. debug=True does only the latest 2 releases
    and prints what parsed, so you can validate before a full backfill."""
    rels = release_index(start_year)
    if debug:
        rels = rels[-2:]
        print(f"DEBUG: scoring last {len(rels)} releases only")
    books = []
    for rel in rels:
        rec = score_release(rel)
        if rec:
            books.append(rec)
            if debug:
                print(f"  {rec['date']} districts={rec['ndist']} "
                      f"growth={rec['growth']} inflation={rec['inflation']} "
                      f"labor={rec['labor']} risks={rec['risks']}")
    books.sort(key=lambda r: r["date"])
    print(f"build_books: scored {len(books)} real books")
    return books
