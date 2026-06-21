#!/usr/bin/env python3
"""Generate site/data.js — the file the dashboard reads.

  python build_data.py --demo                 # synthetic series + real statsmodels probs
  python build_data.py --start 1970 --end 2026   # real pipeline (needs network + keys)

Writes:  window.DASHBOARD_DATA = { dates, lens, laborTone, withhold, hmVals, topics,
                                   feed, topline:{regime,pRec,pInf}, as_of }
The dashboard is presentation-only; everything it shows comes from this object.
"""
from __future__ import annotations
import argparse, datetime as dt, json, math

import numpy as np
import pandas as pd

from beigebook_altdata import forecast as F
from beigebook_altdata.config import RULE_VERSION

NBER_F = [(1973+10/12, 1975+2/12), (1980, 1980+6/12), (1981+6/12, 1982+10/12),
          (1990+6/12, 1991+2/12), (2001+2/12, 2001+10/12), (2007+11/12, 2009+5/12),
          (2020+1/12, 2020+3/12)]


def _g(f, c, w):
    return math.exp(-((f - c) / w) ** 2)


def synthetic():
    """Same shapes the dashboard ships with, so the demo looks coherent."""
    N = 440
    start = dt.date(1970, 1, 15)
    dates = [start + dt.timedelta(days=int(i * round(365.25 / 8))) for i in range(N)]
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


def probabilities(dates, L):
    """Fit the real statsmodels logits and return current probs + regime."""
    rows = []
    for i, d in enumerate(dates):
        for lens in ("growth", "inflation", "bottlenecks", "risks"):
            rows.append({"release": pd.Timestamp(d), "lens": lens, "composite": L[lens][i]})
    feat = F.build_features(pd.DataFrame(rows))
    recL = F.recession_label(feat.index)
    cpi = F.stub_cpi_yoy(feat.index)            # swap for fetch_cpi_yoy() in production
    infL = F.inflation_label(feat.index, cpi)
    today = pd.Timestamp(dates[-1])
    pRec, _ = F.fit_and_current(feat, recL, F.REC_FEATURES, today=today)
    pInf, _ = F.fit_and_current(feat, infL, F.INF_FEATURES, today=today)
    g, gprev = L["growth"][-1], L["growth"][-2]
    mom = g - gprev
    regime = ("Rising expansion" if mom >= 0 else "Slowing expansion") if g >= 0 \
             else ("Recovery" if mom >= 0 else "Recession")
    return {"regime": regime, "pRec": round(pRec * 100, 1), "pInf": round(pInf * 100, 1)}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--start", type=int, default=1970)
    ap.add_argument("--end", type=int, default=dt.date.today().year)
    ap.add_argument("--out", default="site/data.js")
    args = ap.parse_args()

    if args.demo:
        dates, L, laborTone, withhold = synthetic()
    else:
        dates, L, laborTone, withhold = build_real(args.start, args.end)

    hmVals, topics, feed = snapshot()
    payload = {
        "as_of": dates[-1].isoformat(),
        "rule_version": RULE_VERSION,
        "dates": [d.isoformat() for d in dates],
        "lens": L, "laborTone": laborTone, "withhold": withhold,
        "hmVals": hmVals, "topics": topics, "feed": feed,
        "topline": probabilities(dates, L),
    }
    with open(args.out, "w") as fh:
        fh.write("window.DASHBOARD_DATA = " + json.dumps(payload) + ";\n")
    print(f"wrote {args.out}: {len(dates)} books, as_of {payload['as_of']}, "
          f"topline {payload['topline']}")


if __name__ == "__main__":
    main()
