#!/usr/bin/env python3
"""Generate site/data.js — the file the dashboard reads.

  python build_data.py --demo                 # synthetic series + real statsmodels probs
  python build_data.py --start 1970 --end 2026   # real pipeline (needs network + keys)

Writes:  window.DASHBOARD_DATA = { dates, lens, laborTone, withhold, hmVals, topics,
                                   feed, topline:{regime,pRec,pInf}, as_of }
The dashboard is presentation-only; everything it shows comes from this object.
"""
from __future__ import annotations
import argparse, datetime as dt, json, math, os

import numpy as np
import pandas as pd

try:
    from beigebook_altdata.config import RULE_VERSION
except Exception:
    RULE_VERSION = "v1"   # fallback when the package isn't importable (e.g. demo-only runs)

NBER_F = [(1973+10/12, 1975+2/12), (1980, 1980+6/12), (1981+6/12, 1982+10/12),
          (1990+6/12, 1991+2/12), (2001+2/12, 2001+10/12), (2007+11/12, 2009+5/12),
          (2020+1/12, 2020+3/12)]


def _g(f, c, w):
    return math.exp(-((f - c) / w) ** 2)


def synthetic():
    """Same shapes the dashboard ships with, so the demo looks coherent.
    The series runs up to today, so the latest point is genuinely current."""
    start = dt.date(1970, 1, 15)
    step = round(365.25 / 8)
    N = (dt.date.today() - start).days // step + 1
    dates = [start + dt.timedelta(days=i * step) for i in range(N)]
    fr = lambda d: d.year + (d.month - 1) / 12
    L = {"growth": [], "inflation": [], "bottlenecks": [], "risks": []}
    laborTone, withhold = [], []
    for i, d in enumerate(dates):
        f = fr(d)
        rp = min(sum(_g(f, (a + b) / 2, (b - a) / 1.5 + 0.15) for a, b in NBER_F), 1.3)
        g = round(0.45 + 0.25 * math.sin((f - 1970) / 3.4) - 1.3 * rp, 4)
        inf = round(0.2 + 0.9 * _g(f, 1979, 4) + 0.8 * _g(f, 2022, 1.6) + 0.15 * math.sin((f - 1970) / 2.7), 4)
        bo = round(-0.2 + 0.7 * _g(f, 1974, 1.2) + 1.0 * _g(f, 2021.5, 1.0) - 0.4 * rp, 4)
        ri = round(0.1 + 1.2 * rp + 0.15 * math.sin((f - 1970) / 2.1), 4)
        L["growth"].append(g); L["inflation"].append(inf); L["bottlenecks"].append(bo); L["risks"].append(ri)
        laborTone.append(round(0.6 * g - 0.25 * ri, 4))
        if f < 1998:
            withhold.append(None)
        else:
            base = 4.6 + 0.5 * math.sin((f - 1998) / 3.0) + 0.2 * (f - 2010) / 16
            shock = -7 * _g(f, 2009.0, 0.5) - 9 * _g(f, 2020.2, 0.18)
            withhold.append(round(base + shock + 0.3 * math.sin(i * 1.3), 2))
    return dates, L, laborTone, withhold


def _cpi_stub(f):
    """Synthetic CPI YoY, matching the dashboard fallback (used when real CPI is absent)."""
    return 1.5 + 6*_g(f, 1979, 4) + 5*_g(f, 2022, 1.3) - 1.3*_g(f, 2009, 1.0) + 0.6*math.sin((f-1970)/3.0)


def fetch_cpi_yoy(dates):
    """Real headline CPI-U (all items, NSA) YoY %, via FRED series CPIAUCNS — the BLS
    series behind the reported 12-month figure (e.g. 4.2% for May 2026).

    Aligned point-in-time to each Beige Book release with a ~35-day publication lag, so
    each release only 'sees' CPI that was actually published by then (no look-ahead).
    Returns a list aligned to `dates`, or None if FRED_API_KEY is missing / fetch fails —
    in which case the dashboard falls back to the synthetic curve + hardcoded live anchor.
    """
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("FRED_API_KEY not set — real CPI skipped (using synthetic anchor).")
        return None
    try:
        import requests
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": "CPIAUCNS", "api_key": key,
                                 "file_type": "json", "observation_start": "1968-01-01"},
                         timeout=30)
        r.raise_for_status()
        obs = sorted((dt.date.fromisoformat(o["date"]), float(o["value"]))
                     for o in r.json()["observations"] if o["value"] not in (".", ""))
    except Exception as e:
        print(f"CPI fetch failed ({e}) — using synthetic anchor.")
        return None
    by_ym = {(d.year, d.month): v for d, v in obs}
    yoy = [(d, (v / by_ym[(d.year-1, d.month)] - 1) * 100)
           for d, v in obs if (d.year-1, d.month) in by_ym]
    out = []
    for rel in dates:
        cutoff = rel - dt.timedelta(days=35)          # respect the ~monthly release lag
        avail = [y for dd, y in yoy if dd <= cutoff]
        out.append(round(avail[-1], 2) if avail else None)
    first = next((x for x in out if x is not None), 2.0)
    return [first if x is None else x for x in out]    # backfill the earliest gap


def _fit_logit(X, y, iters=4000, lr=0.3, l2=0.02):
    """Standardized logistic regression by gradient descent (matches the dashboard fit).
    Robust to tiny/degenerate samples (e.g. debug runs): returns a flat base-rate model
    when there aren't enough rows to fit."""
    X = np.atleast_2d(np.asarray(X, float)); y = np.asarray(y, float).ravel()
    n, k = X.shape
    if n < 8 or len(np.unique(y)) < 2:          # too few rows or single class -> base rate
        base = float(y.mean()) if n else 0.5
        return np.zeros(k), math.log(base / (1 - base)) if 0 < base < 1 else 0.0, \
               X.mean(0) if n else np.zeros(k), np.ones(k)
    mean = X.mean(0); sd = X.std(0); sd = np.where(sd == 0, 1.0, sd)
    Z = (X - mean) / sd
    w = np.zeros(k); b = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        e = p - y
        w -= lr * (Z.T @ e / n + l2 * w)
        b -= lr * e.mean()
    return w, b, mean, sd


def _predict(model, x):
    w, b, mean, sd = model
    z = b + float(np.sum(w * ((np.asarray(x, float) - mean) / sd)))
    return 1 / (1 + math.exp(-z))


def probabilities(dates, L, laborTone, cpi_yoy=None):
    """Authoritative 12m logits (this becomes DATA.topline, which the dashboard displays).

    recession : growth level, growth momentum, risks, labor          vs NBER dates
    inflation : actual CPI YoY (anchor), inflation tone, momentum,    vs CPI YoY > 2% in 12m
                labor, bottlenecks
    When cpi_yoy is real, BOTH the anchor feature and the >2% label use the real series,
    so history is a genuine forecast rather than a synthetic illustration.
    """
    H = 8
    N = len(dates)
    fr = lambda d: d.year + (d.month - 1) / 12
    cpi = cpi_yoy if cpi_yoy else [_cpi_stub(fr(d)) for d in dates]

    Xr, yr, Xi, yi = [], [], [], []
    for i in range(1, N - H):
        f = fr(dates[i])
        rec = 1 if any(not ((f + 1.0) < a or f > b) for a, b in NBER_F) else 0
        Xr.append([L["growth"][i], L["growth"][i] - L["growth"][i-1], L["risks"][i], laborTone[i]])
        yr.append(rec)
        fut = cpi[i + H] if i + H < N else cpi[-1]
        Xi.append([cpi[i], L["inflation"][i], L["inflation"][i] - L["inflation"][i-1],
                   laborTone[i], L["bottlenecks"][i]])
        yi.append(1 if fut > 2 else 0)

    li = N - 1
    cpi_now = cpi[li]
    g, gprev = L["growth"][-1], L["growth"][-2] if N > 1 else L["growth"][-1]
    mom = g - gprev
    regime = ("Rising expansion" if mom >= 0 else "Slowing expansion") if g >= 0 \
             else ("Recovery" if mom >= 0 else "Recession")
    if len(Xr) < 8 or len(Xi) < 8:      # debug/short runs: not enough history to fit
        return {"regime": regime, "pRec": None, "pInf": None,
                "cpi_now": round(cpi_now, 2), "note": "insufficient history to fit probabilities"}

    mr = _fit_logit(Xr, yr)
    mi = _fit_logit(Xi, yi)
    pRec = _predict(mr, [L["growth"][li], L["growth"][li] - L["growth"][li-1],
                         L["risks"][li], laborTone[li]])
    pInf = _predict(mi, [cpi_now, L["inflation"][li], L["inflation"][li] - L["inflation"][li-1],
                         laborTone[li], L["bottlenecks"][li]])
    return {"regime": regime, "pRec": round(pRec * 100, 1), "pInf": round(pInf * 100, 1),
            "cpi_now": round(cpi_now, 2)}


def snapshot():
    """Latest-book district x lens, topic router, and paraphrased feed.
    In production these come from the latest parsed book; here they are placeholders."""
    hmVals = [[.3, .5, -.2, .4], [.4, .6, -.1, .5], [.2, .5, -.3, .3], [.1, .4, 0, .2],
              [.3, .6, -.2, .4], [.5, .5, -.4, .6], [.2, .5, -.1, .3], [.3, .4, -.2, .2],
              [.1, .3, .1, .1], [.4, .6, -.3, .5], [.6, .7, -.5, .7], [-.1, .4, .2, .5]]
    topics = [{"n": "Tariffs / trade", "w": 74, "up": True},
              {"n": "Labor availability", "w": 58, "up": False},
              {"n": "Supply chain", "w": 41, "up": True},
              {"n": "Credit conditions", "w": 33, "up": False},
              {"n": "AI / automation", "w": 29, "up": True}]
    feed = [{"d": "Dallas", "l": "Bottleneck", "t": "Contacts described ongoing difficulty filling skilled trade roles."},
            {"d": "Atlanta", "l": "Inflation", "t": "Firms said input costs eased but they kept passing earlier increases to customers."},
            {"d": "San Francisco", "l": "Risk", "t": "Several contacts flagged trade-policy uncertainty as a reason to delay capital plans."}]
    return hmVals, topics, feed


def build_real(start, end):
    """Wire-up for the live pipeline (runs where Fed/Treasury/FRED are reachable)."""
    from beigebook_altdata import ingest, parse, panel, withholding
    dates = ingest.release_index(start, end)
    if not dates:
        raise SystemExit("ingest.release_index() returned no dates — wire it to the live "
                         "Beige Book archive / FRASER map first.")
    cells = pd.concat([panel.score_book(parse.parse_book(ingest.get_book(d).raw_html), d)
                       for d in dates], ignore_index=True)
    lens_df = panel.build_lens(cells)
    piv = lens_df.pivot_table(index="release", columns="lens", values="composite").sort_index()
    L = {k: [round(float(x), 4) for x in piv[k]] for k in ["growth", "inflation", "bottlenecks", "risks"]}
    daily = withholding.fetch_withholding(dt.date(start - 1, 1, 1), dt.date(end, 12, 31))
    wh = withholding.align_to_releases(withholding.filtered_yoy(daily), list(piv.index))
    withhold = [None if pd.isna(v) else round(float(v), 2) for v in wh["withholding_yoy_pct"]]
    laborTone = L["growth"]  # placeholder until labor-section composite is wired
    return [d.date() for d in piv.index], L, laborTone, withhold


def _synth_withhold(dates):
    """Synthetic withholding overlay aligned to arbitrary release dates (1998+ only).
    Real DTS withholding is a separate ingestion; this keeps the labor panel populated
    until that series is wired."""
    out = []
    for i, d in enumerate(dates):
        if d.year < 1998:
            out.append(None)
        else:
            base = 4.0 + 2.5 * math.sin((d.year + d.month/12 - 1998) / 3.0)
            out.append(round(base + 0.3 * math.sin(i * 1.3), 2))
    return out


def build_from_books(start_year, debug=False):
    """REAL Beige Book lens scores (2011-present) via books.py ingestion.
    Returns the same (dates, L, laborTone, withhold) shape as synthetic()/build_real()."""
    from beigebook_altdata import books as BB
    recs = BB.build_books(start_year=start_year, debug=debug)
    if not recs:
        raise SystemExit("no real books scored — check network / parsing (try --debug-books)")
    dates = [dt.date.fromisoformat(r["date"]) for r in recs]

    def series(key):
        # forward-fill the occasional missing lens so the line stays continuous
        vals, last = [], 0.0
        for r in recs:
            v = r.get(key)
            last = v if v is not None else last
            vals.append(round(last, 4))
        return vals

    L = {"growth": series("growth"), "inflation": series("inflation"),
         "bottlenecks": series("bottlenecks"), "risks": series("risks")}
    laborTone = series("labor")                 # labor is now its own real lens
    withhold = _synth_withhold(dates)           # real DTS withholding still to be wired
    return dates, L, laborTone, withhold


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="synthetic lenses (layout/testing)")
    ap.add_argument("--real-books", action="store_true",
                    help="ingest & score the real Beige Book (2011-present)")
    ap.add_argument("--debug-books", action="store_true",
                    help="score only the latest 2 real books and print what parsed")
    ap.add_argument("--start", type=int, default=1970)
    ap.add_argument("--books-start", type=int, default=2011,
                    help="first year for real book ingestion")
    ap.add_argument("--end", type=int, default=dt.date.today().year)
    ap.add_argument("--out", default="site/data.js")
    args = ap.parse_args()

    if args.real_books or args.debug_books:
        dates, L, laborTone, withhold = build_from_books(args.books_start, debug=args.debug_books)
    elif args.demo:
        dates, L, laborTone, withhold = synthetic()
    else:
        dates, L, laborTone, withhold = build_real(args.start, args.end)

    hmVals, topics, feed = snapshot()
    cpi_yoy = fetch_cpi_yoy(dates)          # real BLS CPI-U YoY (None if no FRED key)
    last = dates[-1]
    next_release = last + dt.timedelta(days=round(365.25 / 8))  # ~next scheduled book
    payload = {
        "as_of": last.isoformat(),
        "as_of_label": last.strftime("%B %Y"),
        "next_release": next_release.isoformat(),
        "rule_version": RULE_VERSION,
        "dates": [d.isoformat() for d in dates],
        "lens": L, "laborTone": laborTone, "withhold": withhold,
        "hmVals": hmVals, "topics": topics, "feed": feed,
        "topline": probabilities(dates, L, laborTone, cpi_yoy),
    }
    if cpi_yoy:
        payload["cpiYoY"] = cpi_yoy         # real anchor for the dashboard's in-browser model
    src = "real-books" if (args.real_books or args.debug_books) else ("synthetic" if args.demo else "pipeline")
    payload["lens_source"] = src
    with open(args.out, "w") as fh:
        fh.write("window.DASHBOARD_DATA = " + json.dumps(payload) + ";\n")
    print(f"wrote {args.out}: {len(dates)} books ({src}), as_of {payload['as_of']}, "
          f"cpi {'real' if cpi_yoy else 'synthetic'}, topline {payload['topline']}")


if __name__ == "__main__":
    import sys, traceback
    print("build_data starting", flush=True)
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        print("BUILD FAILED — traceback follows:", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        sys.exit(1)
