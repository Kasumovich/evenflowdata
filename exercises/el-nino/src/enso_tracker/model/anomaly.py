"""Step B, part 1: anomaly and event-state computation.

Methodology is fixed and config-driven so that successive runs are
comparable:

* **ONI convention** -- 3-month running mean of Nino 3.4 SST anomaly against
  CPC's era-specific 30-year climatology (the base period shifts every five
  years). This is what the source forecast chart uses, so peak intensities are
  directly comparable to the 1982-83 / 1997-98 / 2015-16 record.
* **Impact layers** -- a fixed 1991-2020 base, so that map layers are
  internally comparable across regions and across runs.

Mixing the two conventions is the most common way to get a spurious 0.2-0.3
degC step change between updates, which would trip the Step F alert for no
physical reason. Hence they are kept explicitly separate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from ..config import Config


@dataclass
class EventState:
    """Everything the header gauge needs, plus the Step F comparison basis."""

    as_of: str
    current_oni: float
    current_oni_season: str
    current_weekly_nino34: float | None
    category: str
    alert_status: str
    peak_median: float
    peak_p10: float
    peak_p90: float
    peak_centre: str
    days_to_peak: int
    prob_super: float
    prob_exceed_record: float
    n_models: int
    n_members: int
    record_to_beat_oni: float
    record_to_beat_monthly: float
    extrapolation_warning: bool
    confidence_multiplier: float
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def running_mean_oni(monthly: pd.DataFrame, cfg: Config,
                     value_col: str = "nino34_anom") -> pd.DataFrame:
    """Convert monthly Nino 3.4 anomalies to the 3-month running mean (ONI)."""
    window = int(cfg.thresholds["climatology"]["oni_window_months"])
    out = monthly.sort_values("date").copy()
    out["oni"] = (
        out[value_col].rolling(window=window, center=True, min_periods=window).mean()
    )
    return out


def anomaly_vs_baseline(
    values: pd.Series, climatology: pd.Series, *, as_percent: bool = False
) -> pd.Series:
    """Anomaly against an explicit climatology series.

    Percent form is used for precipitation (where a mm anomaly is meaningless
    without knowing the base), absolute for temperature.
    """
    if as_percent:
        base = climatology.replace(0, np.nan)
        return (values - base) / base * 100.0
    return values - climatology


def probability_above(threshold: float, median: float, p10: float, p90: float) -> float:
    """P(peak > threshold) from a three-point summary.

    Fits a normal to the 10th/90th percentiles (z = +/-1.2816). The ensemble is
    right-skewed in reality, so this understates the upper tail slightly --
    a conservative direction for a risk product, and stated as such in the
    methodology tab.
    """
    z = 1.2815515655446004
    sigma = max((p90 - p10) / (2 * z), 1e-6)
    from math import erf, sqrt
    return float(0.5 * (1.0 - erf((threshold - median) / (sigma * sqrt(2.0)))))


def build_event_state(
    cfg: Config,
    *,
    oni_df: pd.DataFrame,
    weekly_df: pd.DataFrame | None,
    ensemble: dict[str, Any],
    today: date | None = None,
    provenance: dict[str, str] | None = None,
) -> EventState:
    today = today or datetime.now(timezone.utc).date()

    latest = oni_df.dropna(subset=["oni"]).iloc[-1]
    current_oni = float(latest["oni"])
    current_season = f"{latest['season']} {int(latest['year'])}"

    weekly_value = None
    alert_status = "unknown"
    if weekly_df is not None and not weekly_df.empty:
        row = weekly_df.iloc[-1]
        weekly_value = float(row["nino34"]) if "nino34" in row else None
        alert_status = str(row.get("alert_status", "unknown"))

    peak_median = float(ensemble["peak_median_degC"])
    peak_p10 = float(ensemble["peak_p10_degC"])
    peak_p90 = float(ensemble["peak_p90_degC"])

    centre = pd.to_datetime(ensemble["peak_centre_estimate"]).date()
    days_to_peak = (centre - today).days

    records = cfg.thresholds["records"]
    record_oni = float(records["oni_peak_2015_16"])
    record_monthly = float(records["ersst_monthly_record"])

    super_floor = float(cfg.thresholds["enso_categories"]["super"][0])
    prob_super = probability_above(super_floor, peak_median, peak_p10, peak_p90)
    prob_record = float(
        ensemble.get("prob_exceed_record_2p75")
        or probability_above(record_monthly, peak_median, peak_p10, peak_p90)
    )

    warn_at = float(cfg.thresholds["impact_model"]["extrapolation_warning_degC"])
    extrapolating = peak_median > warn_at
    penalty = float(cfg.thresholds["impact_model"]["confidence_penalty_above_warning"])
    confidence_multiplier = (1.0 - penalty) if extrapolating else 1.0

    return EventState(
        as_of=str(today),
        current_oni=current_oni,
        current_oni_season=current_season,
        current_weekly_nino34=weekly_value,
        category=cfg.enso_category(current_oni),
        alert_status=alert_status,
        peak_median=peak_median,
        peak_p10=peak_p10,
        peak_p90=peak_p90,
        peak_centre=str(centre),
        days_to_peak=days_to_peak,
        prob_super=prob_super,
        prob_exceed_record=prob_record,
        n_models=int(ensemble.get("n_models", 0)),
        n_members=int(ensemble.get("n_members", 0)),
        record_to_beat_oni=record_oni,
        record_to_beat_monthly=record_monthly,
        extrapolation_warning=extrapolating,
        confidence_multiplier=confidence_multiplier,
        provenance=provenance or {},
    )


def median_shift(previous: dict[str, Any] | None, current: EventState) -> float | None:
    """Step F trigger input: change in ensemble median since the last run."""
    if not previous:
        return None
    prior = previous.get("peak_median")
    if prior is None:
        return None
    return float(current.peak_median - float(prior))
