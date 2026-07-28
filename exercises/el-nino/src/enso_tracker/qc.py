"""Automated quality control.

Three classes of check, all config-driven:

1. **Range** -- physically implausible values (an ONI of 40 degC means the
   parser drifted onto the wrong column, which is the single most common
   failure mode for fixed-width NOAA text products).
2. **Completeness** -- missing-value fraction and, for ensembles, member count.
3. **Consistency** -- percentile ordering, monotonic time axes, and agreement
   between overlapping sources (weekly Nino 3.4 vs monthly ERSST).

A failing check does not crash the run. It marks the dataset, the store keeps
the last passing version, and the dashboard renders the stale badge. Silent
substitution of bad data is the thing this module exists to prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class QCResult:
    dataset: str
    passed: bool = True
    flags: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        self.passed = False
        self.flags.append(msg)
        log.warning("QC FAIL [%s] %s", self.dataset, msg)

    def warn(self, msg: str) -> None:
        self.flags.append(f"WARN: {msg}")
        log.info("QC warn [%s] %s", self.dataset, msg)


def check_ranges(df: pd.DataFrame, cfg: Config, dataset: str, result: QCResult) -> None:
    reject = cfg.thresholds["qc"].get("reject_on_range_violation", True)
    for column in df.columns:
        rng = cfg.qc_range(str(column))
        if rng is None:
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        if series.empty:
            continue
        lo, hi = rng
        bad = series[(series < lo) | (series > hi)]
        if len(bad):
            msg = (
                f"{column}: {len(bad)} value(s) outside [{lo}, {hi}] "
                f"(min={series.min():.3f}, max={series.max():.3f})"
            )
            result.fail(msg) if reject else result.warn(msg)


def check_completeness(df: pd.DataFrame, cfg: Config, result: QCResult) -> None:
    max_missing = cfg.thresholds["qc"]["max_missing_fraction"]
    if df.empty:
        result.fail("empty frame")
        return
    for column in df.columns:
        frac = float(df[column].isna().mean())
        if frac > max_missing:
            result.fail(f"{column}: {frac:.0%} missing (limit {max_missing:.0%})")
    result.stats["rows"] = int(len(df))


def check_ensemble(df: pd.DataFrame, cfg: Config, result: QCResult) -> None:
    """Percentile ordering and member count for forecast plumes."""
    min_members = cfg.thresholds["qc"]["ensemble_min_members"]
    if "n_members" in df.columns:
        n = int(pd.to_numeric(df["n_members"], errors="coerce").max())
        result.stats["n_members"] = n
        if n < min_members:
            result.fail(f"ensemble has {n} members, minimum {min_members}")

    cols = {"p10", "median", "p90"}
    if cols.issubset(set(df.columns)):
        bad = df[(df["p10"] > df["median"]) | (df["median"] > df["p90"])]
        if len(bad):
            result.fail(f"percentile ordering violated on {len(bad)} row(s)")

        blowout = cfg.thresholds["alerts"]["spread_blowout_p90_minus_p10"]
        spread = (df["p90"] - df["p10"]).max()
        result.stats["max_spread"] = float(spread)
        if spread > blowout:
            result.warn(
                f"ensemble spread {spread:.2f} degC exceeds {blowout} degC -- "
                f"treat the central estimate with corresponding caution"
            )


def check_monotonic_time(df: pd.DataFrame, column: str, result: QCResult) -> None:
    if column not in df.columns:
        return
    series = pd.to_datetime(df[column], errors="coerce")
    if series.isna().any():
        result.fail(f"{column}: unparseable timestamps")
        return
    if not series.is_monotonic_increasing:
        result.warn(f"{column}: not monotonically increasing; sorting")


def cross_check_nino34(
    weekly: float | None, monthly: float | None, result: QCResult, tol: float = 0.6
) -> None:
    """Weekly and monthly Nino 3.4 should not disagree wildly.

    They legitimately differ -- different averaging windows and climatologies --
    but a gap beyond ``tol`` usually means one feed has changed format.
    """
    if weekly is None or monthly is None:
        return
    gap = abs(weekly - monthly)
    result.stats["nino34_weekly_monthly_gap"] = float(gap)
    if gap > tol:
        result.warn(
            f"weekly ({weekly:.2f}) and monthly ({monthly:.2f}) Nino 3.4 differ "
            f"by {gap:.2f} degC -- check for a feed format change"
        )


def sanity_vs_literature(
    modelled_pct: float, anchor: list[float], label: str, result: QCResult
) -> None:
    """Guard against the impact model running away from published magnitudes."""
    lo, hi = min(anchor), max(anchor)
    if modelled_pct > hi * 2 or (modelled_pct < lo / 2 and modelled_pct > 0):
        result.warn(
            f"{label}: modelled {modelled_pct:.1f}% vs published range "
            f"{lo}-{hi}% -- outside 2x tolerance, review the elasticity"
        )


def run_standard_suite(
    df: pd.DataFrame,
    cfg: Config,
    dataset: str,
    *,
    time_column: str | None = None,
    ensemble: bool = False,
) -> QCResult:
    result = QCResult(dataset=dataset)
    check_completeness(df, cfg, result)
    if df.empty:
        return result
    check_ranges(df, cfg, dataset, result)
    if time_column:
        check_monotonic_time(df, time_column, result)
    if ensemble:
        check_ensemble(df, cfg, result)
    return result
