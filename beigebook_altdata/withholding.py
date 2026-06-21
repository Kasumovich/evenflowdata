"""Daily Treasury Statement withholding -> filtered YoY growth.

The raw series is extremely volatile (day-of-week, paydays, holidays, refund season,
tax-law changes). This module turns it into a usable hard-data overlay aligned to
Beige Book release dates. Runs where the Treasury API is reachable; the sandbox stubs it.

Filtering recipe:
  1. keep 'withheld income and employment taxes' deposits
  2. cumulate over a trailing window and compare to the same window one year prior,
     aligned by business-day-of-month (not calendar date) so payday timing matches
  3. report YoY % growth only when enough business days in the window are populated
"""
from __future__ import annotations
import datetime as dt

try:
    import requests, pandas as pd
except ImportError:
    requests, pd = None, None

from .config import DTS_API

WINDOW_BDAYS = 30        # trailing window for cumulation
MIN_COVERAGE = 0.5       # need >=50% of window's business days


def fetch_withholding(start: dt.date, end: dt.date):
    """Return a daily DataFrame [date, withheld] from Fiscal Data. Network required."""
    if requests is None:
        raise RuntimeError("requests/pandas not installed")
    rows, page = [], 1
    while True:
        params = {
            "filter": (f"record_date:gte:{start},record_date:lte:{end},"
                       "transaction_type:eq:Deposits"),
            "fields": "record_date,transaction_catg,transaction_today_amt",
            "page[size]": 10000, "page[number]": page,
        }
        r = requests.get(DTS_API, params=params, timeout=60); r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        rows += data; page += 1
    df = pd.DataFrame(rows)
    df = df[df["transaction_catg"].str.contains("ithheld", na=False)]
    df["date"] = pd.to_datetime(df["record_date"])
    df["withheld"] = pd.to_numeric(df["transaction_today_amt"], errors="coerce")
    return df.groupby("date", as_index=False)["withheld"].sum().sort_values("date")


def filtered_yoy(daily) -> "pd.DataFrame":
    """Calendar-aligned trailing-window YoY growth, NaN where coverage is thin."""
    s = daily.set_index("date")["withheld"].asfreq("B")
    roll = s.rolling(f"{WINDOW_BDAYS}D").sum()
    coverage = s.notna().rolling(f"{WINDOW_BDAYS}D").mean()
    yoy = roll / roll.shift(252) - 1.0          # ~252 business days = 1 year
    yoy[coverage < MIN_COVERAGE] = float("nan")
    return (yoy * 100).round(2).rename("withholding_yoy_pct").reset_index()


def align_to_releases(yoy, release_dates: list[dt.date]):
    """Sample the filtered series at each Beige Book release (most recent valid point)."""
    out = []
    ser = yoy.set_index("date")["withholding_yoy_pct"].dropna()
    for d in release_dates:
        prior = ser[ser.index <= pd.Timestamp(d)] if pd is not None else None
        out.append({"release": d,
                    "withholding_yoy_pct": (float(prior.iloc[-1]) if prior is not None
                                            and len(prior) else None)})
    return pd.DataFrame(out) if pd is not None else out


def stub_yoy(release_dates: list[dt.date]):
    """Deterministic placeholder shaped like the real filtered series (sandbox use)."""
    base = 5.0
    return [{"release": d,
             "withholding_yoy_pct": round(base - 0.4 * ((i // 3) % 4) + 0.2 * (i % 2), 2)}
            for i, d in enumerate(release_dates)]
