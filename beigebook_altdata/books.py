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
    old_base: str = ""            # non-empty => pre-2011 /fomc/BeigeBook/{YYYY}/{YYYYMMDD}/ scheme


def release_index(start_year: int = 2011, end_year: int | None = None) -> list[Release]:
    """Crawl the Board archive for release landing pages >= start_year.

    The archive index links to per-YEAR pages (beigebook{YYYY}.htm); each year page
    links to the ~8 per-MONTH releases (beigebook{YYYYMM}.htm). We follow the year
    pages, then harvest the monthly release links. No filename guessing.
    """
    end_year = end_year or dt.date.today().year
    found: dict[str, str] = {}          # ym -> modern landing url  (2011+)
    old: dict[str, str] = {}            # ym -> old dated base url   (<=2010)

    # ---- modern era (>=2011): Board archive -----------------------------------
    # year pages -> per-MONTH landing links (beigebook{YYYYMM}(-summary)?.htm).
    # Two year-page schemes across the 2017 redesign:
    #   2017+     : /monetarypolicy/beigebook{YYYY}.htm
    #   2011-2016 : /monetarypolicy/beigebook/beigebook{YYYY}.htm  (extra /beigebook/)
    modern_lo = max(start_year, 2011)
    if modern_lo <= end_year:
        year_pages = set()
        for y in range(modern_lo, end_year + 1):
            year_pages.add(f"{FED}/beigebook{y}.htm")
            year_pages.add(f"{FED}/beigebook/beigebook{y}.htm")
        idx = _get(f"{FED}/beige-book-archive.htm")
        if idx:
            for a in BeautifulSoup(idx, "lxml").find_all("a", href=True):
                m = re.search(r"beigebook(\d{4})\.htm$", a["href"])
                if m and modern_lo <= int(m.group(1)) <= end_year:
                    year_pages.add(_abs(a["href"]))
        for yp in sorted(year_pages):
            html = _get(yp)
            if not html:
                continue
            for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
                m = re.search(r"beigebook(\d{6})(?:-summary)?\.htm", a["href"])
                if m:
                    ym = m.group(1)
                    # fence to >=2011: pre-2011 'Related pages' links appear as static
                    # beigebook{YYYYMM}.htm shells but are JS-gated -> must NOT land here.
                    if 2011 <= int(ym[:4]) <= end_year and int(ym[:4]) >= modern_lo:
                        found[ym] = _abs(a["href"])   # summary page (carries district nav)

    # ---- pre-2011 era (<=2010): static FOMC per-year calendars ------------------
    # The /monetarypolicy/ year tables hide release links behind javascript:void(0),
    # but every old-era page links a static "{YYYY} calendar" at /fomc/beigebook/{YYYY}/
    # that lists the year's /{YYYYMMDD}/ release folders (numbered district pages 1..12).
    old_hi = min(end_year, 2010)
    for y in range(start_year, old_hi + 1):
        cal = _get(f"https://www.federalreserve.gov/fomc/beigebook/{y}/")  # trailing / required
        if not cal:
            print(f"  release_index: no FOMC calendar for {y}")
            continue
        n0 = len(old)
        for a in BeautifulSoup(cal, "lxml").find_all("a", href=True):
            mo = re.search(r"/fomc/beigebook/(\d{4})/(\d{8})/", a["href"], re.I)
            if mo and int(mo.group(1)) == y:
                ymd = mo.group(2); ym = ymd[:6]
                old.setdefault(ym, f"https://www.federalreserve.gov/fomc/beigebook/{y}/{ymd}/")
        if len(old) == n0:
            print(f"  release_index: {y} calendar fetched but no release folders parsed")

    # ---- assemble --------------------------------------------------------------
    releases = []
    for ym in sorted(set(found) | set(old)):
        if int(ym[:4]) <= 2010 and ym in old:    # old-era wins for <=2010
            base = old[ym]
            durls = {d: f"{base}{i}.htm" for i, d in enumerate(DISTRICTS, start=1)}
            releases.append(Release(date=_ymd_from_base(base), ym=ym,
                                    landing_url=base + "default.htm",
                                    district_urls=durls, old_base=base))
        elif ym in found:
            releases.append(Release(date=_ym_to_date(ym), ym=ym, landing_url=found[ym]))
    releases.sort(key=lambda r: r.ym)
    print(f"release_index: {len(releases)} releases {start_year}-{end_year} "
          f"({len(found)} modern >=2011, {len(old)} pre-2011 FOMC-calendar)")
    return releases


def _abs(href: str) -> str:
    """Absolutize a Board-site href (root-relative, protocol, or /monetarypolicy-relative)."""
    if href.startswith("http"):
        return href
    return "https://www.federalreserve.gov" + (href if href.startswith("/")
                                               else "/monetarypolicy/" + href)


def _ymd_from_base(base: str) -> dt.date:
    m = re.search(r"/(\d{8})/$", base)
    if m:
        s = m.group(1)
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return dt.date(1996, 1, 1)


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
        if low.startswith(("for more information", "back to top", "return to top", "note:")):
            break
        # pre-2011 table-layout footer chrome (no semantic <footer> to decompose).
        # All of these sit AFTER the district body, so breaking here is safe; the
        # top-of-page nav ("Skip to content", district links) is <a> text the tag
        # filter already ignores, so it is deliberately NOT listed.
        if low in ("previous summary", "home", "monetary policy",
                   "accessibility") or re.fullmatch(r"\d{4} calendar", low):
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


_PRICE_KW = ("price", "prices", "pricing", "cost", "costs", "inflation", "inflationary")
_LABOR_KW = ("wage", "wages", "employ", "employment", "hiring", "hire", "labor",
             "jobs", "payroll", "workers", "staffing", "hires")


def _sentences_with(text: str, keywords) -> str:
    """Return the sentences of `text` that mention any keyword (theme extraction)."""
    hits = [s for s in re.split(r"(?<=[.!?])\s+", text) if any(k in s.lower() for k in keywords)]
    return " ".join(hits)


def _lens_from_sections(sections: dict[str, str]) -> dict[str, float | None]:
    """Score a page/district into the four ladder lenses.

    growth      : sections about activity (Summary/Manufacturing/Consumer/Services)
    inflation   : price sentences across ALL text (works when 'Prices' isn't its own section)
    labor       : wage/employment sentences across ALL text (ditto for old-era books)
    bottlenecks : manufacturing/supply sections
    This sentence-level routing handles both the modern (header-organized) and pre-2011
    (theme-embedded) layouts uniformly.
    """
    fulltext = " ".join(sections.values())
    growth_txt, bott_txt = [], []
    for name, text in sections.items():
        r = _route_section(name)
        if "growth" in r:
            growth_txt.append(text)
        if "bottlenecks" in r:
            bott_txt.append(text)
    g_src = " ".join(growth_txt) or fulltext
    infl_src = _sentences_with(fulltext, _PRICE_KW)
    labor_src = _sentences_with(fulltext, _LABOR_KW)
    return {
        "growth":      lexicon.diffusion(g_src) if g_src else None,
        "inflation":   lexicon.diffusion(infl_src) if infl_src else None,
        "labor":       lexicon.diffusion(labor_src) if labor_src else None,
        "bottlenecks": lexicon.diffusion(" ".join(bott_txt)) if bott_txt else None,
    }


def _risk_uncertainty(sections: dict[str, str]) -> float | None:
    """Risk lens = density of uncertainty/risk words across all of a district's text.
    (uncertain, uncertainty, risk, cautious, concern, volatile, tariff, disruption, ...)"""
    alltext = " ".join(sections.values())
    if not alltext:
        return None
    return lexicon.risk_density(alltext)


def score_release(rel: Release) -> dict | None:
    """Fetch + parse + score one release -> per-book lens composites + breadth/dispersion."""
    per_district: dict[str, dict[str, float | None]] = {}

    if rel.old_base:
        # pre-2011 era: numbered per-district pages 1.htm..12.htm (full 12-district scoring)
        for d, url in rel.district_urls.items():
            html = _cached(f"bb_{rel.ym}_{_norm(d).replace(' ', '-')}.htm", url)
            if not html:
                continue
            secs = parse_district_page(html)
            if not secs:
                continue
            vals = _lens_from_sections(secs)
            vals["risks"] = _risk_uncertainty(secs)
            per_district[d] = vals
        if len(per_district) < 6:
            print(f"  {rel.ym}: pre-2011 got {len(per_district)} districts — skipped")
            return None
    else:
        landing = _cached(f"bb_{rel.ym}.htm", rel.landing_url)
        if not landing:
            print(f"  {rel.ym}: no landing page")
            return None
        rel.date = _refine_date(landing, rel.ym)
        links = _district_links(landing, rel.ym)
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
            # 2011-2023: whole book on the landing page (topic-organized) -> national obs
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
