"""Probability models on top of the descriptive panel.

Two logistic regressions:
  * P(recession within next 12m)      ~ growth level, growth momentum, risk level
  * P(CPI YoY > 2% at the 12m horizon) ~ inflation level, inflation momentum, bottlenecks

These are FORECASTS, a different layer from the never-revised scores. Honesty rules:
  * a row is only trainable once its 12m future is observed (release <= today - 12m);
  * for a historical probability TRACK, refit with an expanding window so each date
    uses only coefficients estimable from data available then (no hindsight).

Labels need outside truth: NBER recession dates (hard-coded, public) and a CPI YoY
series (Treasury/FRED adapter + stub). Fitting uses statsmodels Logit.
"""
from __future__ import annotations
import datetime as dt
import numpy as np
import pandas as pd

try:
    import statsmodels.api as sm
except ImportError:
    sm = None

# Public NBER peak->trough ranges (monthly granularity is enough here).
NBER = [("1973-11", "1975-03"), ("1980-01", "1980-07"), ("1981-07", "1982-11"),
        ("1990-07", "1991-03"), ("2001-03", "2001-11"), ("2007-12", "2009-06"),
        ("2020-02", "2020-04")]
NBER = [(pd.Timestamp(a), pd.Timestamp(b)) for a, b in NBER]

REC_FEATURES = ["growth", "growth_mom", "risks"]
INF_FEATURES = ["inflation", "inflation_mom", "bottlenecks"]


# --- labels -----------------------------------------------------------------
def in_recession(ts: pd.Timestamp) -> bool:
    return any(a <= ts <= b for a, b in NBER)


def recession_label(dates, horizon_m: int = 12) -> pd.Series:
    """1 if any NBER recession month falls in (d, d+horizon]."""
    out = {}
    for d in dates:
        d = pd.Timestamp(d)
        end = d + pd.DateOffset(months=horizon_m)
        hit = any((a <= end and b >= d + pd.Timedelta(days=1)) for a, b in NBER)
        out[d] = int(hit)
    return pd.Series(out, name="rec12")


def inflation_label(dates, cpi_yoy: pd.Series, horizon_m: int = 12,
                    thr: float = 2.0) -> pd.Series:
    """1 if CPI YoY at ~d+horizon exceeds thr. cpi_yoy is a date-indexed % series."""
    cpi = cpi_yoy.sort_index()
    out = {}
    for d in dates:
        d = pd.Timestamp(d)
        h = d + pd.DateOffset(months=horizon_m)
        prior = cpi[cpi.index <= h]
        out[d] = int(prior.iloc[-1] > thr) if len(prior) else np.nan
    return pd.Series(out, name="inf12")


# --- features ---------------------------------------------------------------
def build_features(lens_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot lens composites to one row per release with momentum terms."""
    piv = (lens_df.pivot_table(index="release", columns="lens", values="composite")
           .sort_index())
    piv.index = pd.to_datetime(piv.index)
    piv["growth_mom"] = piv["growth"].diff()
    piv["inflation_mom"] = piv["inflation"].diff()
    return piv.dropna()


# --- fit / predict ----------------------------------------------------------
def _fit(X: pd.DataFrame, y: pd.Series):
    if sm is None:
        raise RuntimeError("statsmodels not installed")
    Xc = sm.add_constant(X, has_constant="add")
    try:
        res = sm.Logit(y, Xc).fit(disp=0)
        ok = res.mle_retvals.get("converged", True) and np.max(np.abs(res.params.values)) < 50
        if ok:
            return res
    except Exception:
        pass
    # (quasi-)separation or non-convergence -> L2-regularized fit
    return sm.Logit(y, Xc).fit_regularized(disp=0, alpha=1.0)


def _predict(model, x_row: pd.DataFrame) -> float:
    import statsmodels.api as sm
    return float(model.predict(sm.add_constant(x_row, has_constant="add")).iloc[0])


def fit_and_current(features: pd.DataFrame, labels: pd.Series, cols, today=None):
    """Train on rows whose 12m label is observed, predict the latest book.
    Returns (probability_for_latest, fitted_model)."""
    today = pd.Timestamp(today or dt.date.today())
    obs = labels.dropna()
    train_idx = [d for d in features.index
                 if d in obs.index and pd.Timestamp(d) <= today - pd.DateOffset(months=12)]
    X, y = features.loc[train_idx, cols], obs.loc[train_idx]
    model = _fit(X, y)
    latest = features.iloc[[-1]][cols]
    return _predict(model, latest), model


def expanding_pit(features: pd.DataFrame, labels: pd.Series, cols,
                  min_train: int = 40) -> pd.Series:
    """Hindsight-free historical track: at each date, fit only on earlier rows
    whose label was already observable, then predict that date."""
    obs = labels.dropna()
    out = {}
    idx = list(features.index)
    for i, d in enumerate(idx):
        cutoff = pd.Timestamp(d) - pd.DateOffset(months=12)
        train = [e for e in idx[:i] if e in obs.index and pd.Timestamp(e) <= cutoff]
        if len(train) < min_train:
            continue
        try:
            m = _fit(features.loc[train, cols], obs.loc[train])
            out[d] = _predict(m, features.loc[[d], cols])
        except Exception:
            continue
    return pd.Series(out, name="pit_prob")


# --- CPI adapter (label source) --------------------------------------------
def fetch_cpi_yoy(start: dt.date, end: dt.date) -> pd.Series:
    """CPI YoY % from FRED (CPIAUCSL). Network required; runs in your env."""
    import requests
    url = "https://api.stlouisfed.org/fred/series/observations"
    # caller supplies FRED_API_KEY via env; left as a thin sketch
    raise NotImplementedError("wire FRED_API_KEY; or load a local CPI YoY series")


def stub_cpi_yoy(dates) -> pd.Series:
    """Synthetic CPI YoY shaped like history (high 70s/80s, ~2% middle, 2021-23 spike)."""
    idx = pd.to_datetime(list(dates))
    yrs = idx.year + (idx.month - 1) / 12.0
    g = lambda f, c, w: np.exp(-((f - c) / w) ** 2)
    v = 1.5 + 6 * g(yrs, 1979, 4) + 5 * g(yrs, 2022, 1.3) - 1.3 * g(yrs, 2009, 1.0) \
        + 0.6 * np.sin((yrs - 1970) / 3.0)
    return pd.Series(np.round(v, 2), index=idx, name="cpi_yoy")
