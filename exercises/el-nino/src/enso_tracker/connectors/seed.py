"""Seed snapshot loader -- the offline floor under the whole system.

The bundled ``data/seed/observations.json`` holds primary-source values read
on the snapshot date, each tagged with a provenance level. This module turns
them into the same frames the live connectors emit, so downstream code cannot
tell the difference except through the ``provenance`` field it is required to
render.

Why this exists rather than a mock: the pipeline must produce a *defensible*
dashboard in a restricted-egress environment (CI, air-gapped deployment,
first run before credentials land) without ever inventing an observation.
Values that could not be verified are carried in the seed file's
``unverified_prompt_assertions`` block and never enter a data frame.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

import pandas as pd

from ..config import seed_dir
from .base import FetchResult, SourceUnavailable

log = logging.getLogger(__name__)

SEASONS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
           "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]


@lru_cache(maxsize=1)
def load_seed() -> dict[str, Any]:
    path = seed_dir() / "observations.json"
    if not path.exists():
        raise SourceUnavailable(f"seed snapshot missing at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _result(dataset: str, df: pd.DataFrame, block: dict[str, Any]) -> FetchResult:
    return FetchResult(
        dataset=dataset,
        frame=df,
        source=block.get("source", "bundled seed snapshot"),
        source_url=block.get("url"),
        provenance=f"seed:{block.get('provenance', 'unknown')}",
        stale=True,
        # The seed's valid_time is the age of the *observation*, which keeps
        # ageing whether or not the pipeline runs. Without it, re-running every
        # six hours would keep resetting the clock and a three-month-old value
        # would read as brand new.
        valid_time=block.get("valid_time"),
        reference_series=bool(block.get("reference_series", False)),
        notes=[block.get("note")] if block.get("note") else [],
    )


def oni_observed() -> FetchResult:
    block = load_seed()["oni_observed"]
    df = pd.DataFrame(block["series"])
    df["oni"] = df["value"]
    df["season_index"] = df["season"].map({s: i for i, s in enumerate(SEASONS)})
    df = df.sort_values(["year", "season_index"]).reset_index(drop=True)
    return _result("oni_observed", df, block)


def weekly_nino() -> FetchResult:
    block = load_seed()["weekly_nino"]
    df = pd.DataFrame([{
        "week_ending": pd.to_datetime(block["issued"]),
        **block["values"],
        "alert_status": block["alert_status"],
    }])
    return _result("weekly_nino", df, block)


def ensemble_plume() -> FetchResult:
    block = load_seed()["ensemble_plume"]
    summary = load_seed()["ensemble_forecast"]
    df = pd.DataFrame(block["series"])
    df["date"] = pd.to_datetime(df["month"] + "-01")
    df["n_members"] = summary["n_members"]
    df["n_models"] = summary["n_models"]
    return _result("ensemble_plume", df, block)


def ensemble_summary() -> dict[str, Any]:
    return dict(load_seed()["ensemble_forecast"])


def historical_analogs() -> FetchResult:
    block = load_seed()["historical_analogs"]
    labels = block["series_index_labels"]
    rows = []
    for event, payload in block["events"].items():
        for i, value in enumerate(payload["series"]):
            rows.append({
                "event": event,
                "step": i,
                "label": labels[i] if i < len(labels) else f"step{i}",
                "oni": value,
                "peak": payload["peak"],
            })
    return _result("historical_analogs", pd.DataFrame(rows), block)


def market_baseline() -> FetchResult:
    block = load_seed()["market_baseline"]
    rows = []
    for commodity, payload in block["prices"].items():
        rows.append({
            "commodity": commodity,
            "price": payload.get("value"),
            "unit": payload.get("unit"),
            "provenance": payload.get("provenance"),
            "source": payload.get("source"),
            "source_url": payload.get("url"),
            "as_of": block["as_of"],
        })
    return _result("market_baseline", pd.DataFrame(rows), block)


def literature_anchors() -> dict[str, Any]:
    return dict(load_seed()["literature_anchors"])


def unverified_assertions() -> dict[str, Any]:
    """Brief-supplied figures that failed attribution. Audit trail, not data."""
    return dict(load_seed()["unverified_prompt_assertions"])


#: Dataset name -> seed loader. The orchestrator consults this when a live
#: connector raises, which is what implements "last-good + stale flag".
SEED_LOADERS = {
    "oni_observed": oni_observed,
    "weekly_nino": weekly_nino,
    "ensemble_plume": ensemble_plume,
    "historical_analogs": historical_analogs,
    "market_baseline": market_baseline,
}
