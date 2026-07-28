"""The systematic execution protocol, Steps A-F.
 
    A  Ingest & validate
    B  Recompute anomaly and impact layers
    C  Update map/tables/gauges payload
    D  Generate the Situation Report
    E  Publish, archive, log metrics
    F  Alert on critical source failure or ensemble median move > threshold
 
Every run reads the previous run's state as its baseline and writes a new
immutable version, so the audit history is complete by construction and
re-running is always incremental rather than destructive.
"""
 
from __future__ import annotations
 
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
 
import pandas as pd
 
from .config import Config, credentials_present, get_config, output_dir
from .connectors import SourceUnavailable, get_connector
from .connectors import seed as seedmod
from .model.anomaly import EventState, build_event_state, median_shift
from .model.impact import (
    LAYERS,
    build_commodity_table,
    build_country_layers,
    rank_risk_escalations,
)
from .qc import QCResult, run_standard_suite, sanity_vs_literature
from .store import ParquetStore, make_run_id
 
log = logging.getLogger(__name__)
 
STATE_FILE = "last_state.json"
SOURCE_HISTORY_FILE = "source_history.json"
 
#: Alert severities, most to least urgent. The tiers exist because an earlier
#: build rendered every alert identically as "Critical", so a permanent
#: property of the forecast looked the same as a missing API key. On a fresh
#: deployment that meant two meaningless reds on day one -- and an operator who
#: learns to ignore the strip will miss the median-shift alert when it fires.
SEVERITY_ORDER = ["incident", "standing", "configuration"]
 
SEVERITY_META = {
    # Something changed and needs a human. These are the only ones that should
    # ever page anyone.
    "incident": {
        "label": "Incident",
        "description": "Changed since the last run, or a source that was working has stopped.",
    },
    # A persistent property of the event or the model. True every run, will not
    # clear, and is not actionable -- but must stay visible because it bounds
    # how much weight the outputs can carry.
    "standing": {
        "label": "Standing",
        "description": "A persistent condition of this event. Will not clear; bounds interpretation.",
    },
    # A capability that is switched off. Fixable with credentials or config,
    # not a malfunction.
    "configuration": {
        "label": "Config",
        "description": "A capability is not enabled. Fixable with credentials or config.",
    },
}
 
 
@dataclass
class Alert:
    severity: str
    code: str
    title: str
    detail: str
 
    @property
    def text(self) -> str:
        return f"[{self.severity.upper()}] {self.title} -- {self.detail}"
 
    def __str__(self) -> str:  # keeps log lines and webhooks readable
        return self.text
 
 
@dataclass
class RunMetrics:
    run_id: str
    started_utc: str
    finished_utc: str | None = None
    sources_attempted: int = 0
    sources_live: int = 0
    sources_seeded: int = 0
    sources_dormant: int = 0
    sources_failed: int = 0
    qc_failures: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    #: Hours since we last talked to the source. Detects a dead pipeline.
    data_latency_h: dict[str, float] = field(default_factory=dict)
    #: Hours since the underlying measurement was made. Detects a dead *feed* --
    #: a source that answers on time with months-old values. None = unknown or
    #: a reference series, never silently treated as fresh.
    observation_age_h: dict[str, float | None] = field(default_factory=dict)
    #: Which tier each dataset was actually resolved from this run. Age alone
    #: cannot answer "did we reach the primary source?" -- CPC's own ONI table
    #: can run months behind while the connector is working perfectly, and a
    #: frozen file can be six years old while answering instantly. The publish
    #: gate needs both facts and they are not derivable from one another.
    dataset_provenance: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: Sources that are switched off for want of credentials -- never ran here.
    dormant_sources: list[str] = field(default_factory=list)
    #: Sources that attempted a fetch and failed this run.
    failed_sources: list[str] = field(default_factory=list)
    #: Of those, the ones that have succeeded at some point in the past. This
    #: is what separates a genuine incident from a never-configured source.
    regressed_sources: list[str] = field(default_factory=list)
    #: Sources not attempted because the run was invoked with --offline. These
    #: are NOT dormant -- most are keyless and would work with network access.
    #: Conflating the two produced an alert claiming the CPC feeds were
    #: "not connected" when they simply had not been tried.
    skipped_offline: list[str] = field(default_factory=list)
    #: Sources that answered but returned data older than what we already had.
    #: A feed can be perfectly reachable and still be dead.
    stale_live_sources: list[str] = field(default_factory=list)
 
 
@dataclass
class RunResult:
    state: EventState
    countries: pd.DataFrame
    commodities: dict[str, pd.DataFrame]
    plume: pd.DataFrame
    analogs: pd.DataFrame
    escalations: pd.DataFrame
    metrics: RunMetrics
    payload: dict[str, Any]
 
 
# ---------------------------------------------------------------------------
# Step A
# ---------------------------------------------------------------------------
 
def load_source_history(out: Path) -> dict[str, str]:
    """When each source last returned usable data, by source name.
 
    This is what lets Step F tell an *incident* (a feed that used to work and
    has stopped) apart from a *configuration* gap (a feed that has never been
    switched on here). Without it the two are indistinguishable and every
    dormant source screams on day one.
    """
    path = out / SOURCE_HISTORY_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
 
 
def save_source_history(out: Path, history: dict[str, str]) -> None:
    (out / SOURCE_HISTORY_FILE).write_text(
        json.dumps(history, indent=2, sort_keys=True), encoding="utf-8"
    )
 
 
def step_a_ingest(
    cfg: Config,
    store: ParquetStore,
    metrics: RunMetrics,
    *,
    offline: bool = False,
    history: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Poll every configured source; fall back to last-good, then to seed.
 
    Failure is never fatal. Each dataset ends up in one of four states, all of
    which the dashboard renders honestly: live, cached (last-good, stale),
    seeded (bundled snapshot, stale), or absent.
    """
    collected: dict[str, Any] = {}
    blocks = cfg.source_blocks()
    history = {} if history is None else history
 
    def record_failure(name: str, note: str) -> None:
        """A failure is only an *incident* if the source has worked before."""
        metrics.sources_failed += 1
        metrics.failed_sources.append(name)
        metrics.notes.append(note)
        if name in history:
            metrics.regressed_sources.append(name)
 
    for name, spec in blocks.items():
        metrics.sources_attempted += 1
        connector_cls = get_connector(spec["connector"])
        if connector_cls is None:
            record_failure(name, f"{name}: no connector '{spec['connector']}' registered")
            continue
 
        if spec.get("auth") == "key" and not credentials_present(spec):
            metrics.sources_dormant += 1
            metrics.dormant_sources.append(name)
            metrics.notes.append(f"{name}: dormant (credentials absent)")
            continue
 
        if offline:
            metrics.sources_dormant += 1
            metrics.skipped_offline.append(name)
            continue
 
        connector = connector_cls(name, spec, cfg)
        try:
            result = connector.fetch()
        except SourceUnavailable as exc:
            record_failure(name, f"{name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            record_failure(name, f"{name}: unexpected error {exc!r}")
            log.exception("connector %s raised", name)
            continue
 
        qc = run_standard_suite(
            result.frame, cfg, result.dataset,
            time_column="date" if "date" in result.frame.columns else None,
            ensemble="p10" in result.frame.columns,
        )
        if not qc.passed:
            metrics.qc_failures.extend(f"{name}: {f}" for f in qc.flags)
 
        store.write(
            result.dataset, result.frame,
            source=result.source, source_url=result.source_url,
            provenance=result.provenance, qc_passed=qc.passed,
            qc_flags=qc.flags, stale=result.stale,
            valid_time=result.valid_time, reference_series=result.reference_series,
        )
        if qc.passed:
            metrics.sources_live += 1
            history[name] = datetime.now(timezone.utc).isoformat()
            collected[result.dataset] = result
        else:
            record_failure(name, f"{name}: failed QC ({'; '.join(qc.flags[:2])})")
 
    # Resolve every dataset the model needs, degrading gracefully.
    #
    # Freshness decides, not provenance. An earlier build preferred live data
    # unconditionally, and the first real run showed why that is wrong: the CPC
    # weekly connector pointed at a file CPC froze in 2020, answered promptly,
    # and its six-year-old values displaced a 47-day-old seed. "Live" is a
    # statement about where a number came from, not about whether it is current.
    def _age_hours(valid_time: str | None) -> float | None:
        if not valid_time:
            return None
        try:
            when = datetime.fromisoformat(str(valid_time))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - when).total_seconds() / 3600.0, 0.0)
 
    resolved: dict[str, Any] = {}
    for dataset, loader in seedmod.SEED_LOADERS.items():
        candidates: list[dict[str, Any]] = []
 
        live = collected.get(dataset)
        if live is not None:
            candidates.append({
                "frame": live.frame, "provenance": "live",
                "age": _age_hours(live.valid_time), "label": "live",
            })
 
        cached = store.latest(dataset)
        if cached is not None and cached[1].provenance == "live":
            candidates.append({
                "frame": cached[0], "provenance": "cached",
                "age": _age_hours(cached[1].valid_time), "label": "last-good",
            })
 
        seeded = loader()
        candidates.append({
            "frame": seeded.frame, "provenance": seeded.provenance,
            "age": _age_hours(seeded.valid_time), "label": "seed",
            "_seed": seeded,
        })
 
        # Unknown age sorts last: a candidate that will not say how old it is
        # must not win against one that will.
        ranked = sorted(candidates, key=lambda c: (c["age"] is None, c["age"] or 0.0))
        best = ranked[0]
 
        if best["label"] != "live" and live is not None:
            live_age = next(c["age"] for c in candidates if c["label"] == "live")
            metrics.notes.append(
                f"{dataset}: live data rejected as staler than {best['label']} "
                f"({(live_age or 0)/24:.0f} d vs {(best['age'] or 0)/24:.0f} d)"
            )
            metrics.stale_live_sources.append(dataset)
 
        if best["label"] == "seed":
            seeded = best["_seed"]
            store.write(
                seeded.dataset, seeded.frame, source=seeded.source,
                source_url=seeded.source_url, provenance=seeded.provenance,
                qc_passed=True, stale=True,
                valid_time=seeded.valid_time, reference_series=seeded.reference_series,
            )
            metrics.sources_seeded += 1
        elif best["label"] == "last-good":
            metrics.notes.append(f"{dataset}: serving last-good (stale)")
 
        resolved[dataset] = {"frame": best["frame"], "provenance": best["provenance"]}
        metrics.dataset_provenance[dataset] = str(best["provenance"])
 
    for dataset in resolved:
        hours = store.staleness_hours(dataset)
        if hours is not None:
            metrics.data_latency_h[dataset] = round(hours, 2)
        age = store.observation_age_hours(dataset)
        metrics.observation_age_h[dataset] = None if age is None else round(age, 1)
 
    return resolved
 
 
# ---------------------------------------------------------------------------
# Step B
# ---------------------------------------------------------------------------
 
def step_b_recompute(
    cfg: Config, resolved: dict[str, Any], metrics: RunMetrics
) -> tuple[EventState, pd.DataFrame, dict[str, pd.DataFrame]]:
    ensemble = seedmod.ensemble_summary()
 
    oni = resolved["oni_observed"]["frame"].copy()
    if "oni" not in oni.columns and "value" in oni.columns:
        oni["oni"] = oni["value"]
 
    weekly = resolved.get("weekly_nino", {}).get("frame")
 
    # Last known CPC advisory, from the bundled snapshot. Used only when the
    # resolved weekly frame carries no advisory of its own -- which is the
    # normal case once the live SST feed wins, since CPC publishes the advisory
    # separately from the weekly file.
    try:
        advisory_fallback = seedmod.weekly_nino().frame.iloc[-1].get("alert_status")
    except Exception:  # noqa: BLE001 -- a missing snapshot must not stop a run
        advisory_fallback = None
 
    state = build_event_state(
        cfg, oni_df=oni, weekly_df=weekly, ensemble=ensemble,
        alert_status_fallback=advisory_fallback,
        provenance={
            "oni": resolved["oni_observed"]["provenance"],
            "weekly": resolved.get("weekly_nino", {}).get("provenance", "absent"),
            "ensemble": ensemble.get("provenance", "verified"),
        },
    )
 
    countries = build_country_layers(cfg, state.peak_median, scenario="base")
 
    commodities = {
        scenario: build_commodity_table(cfg, state.peak_median, scenario=scenario)
        for scenario in cfg.commodities.get("scenarios", {"base": {}})
    }
 
    # Cross-check the modelled palm response against the one published,
    # citable El Nino price-response range we could attribute.
    anchors = seedmod.literature_anchors()
    base = commodities.get("base")
    if base is not None:
        qc = QCResult(dataset="impact_model")
        checks = [
            ("palm_oil", "palm_oil_price_response"),
            ("fishmeal", "fishmeal_price_response"),
        ]
        for commodity, anchor_key in checks:
            anchor = anchors.get(anchor_key)
            row = base.loc[base["commodity"] == commodity]
            if anchor is None or row.empty:
                continue
            sanity_vs_literature(
                float(row["price_response_pct"].iloc[0]), anchor["value_pct"],
                f"{commodity} price response", qc,
            )
 
        # The fisheries chain has a second anchor the crop pipeline does not:
        # the biomass response itself is an observation, not a composite. At
        # the reference intensity the model must reproduce it exactly.
        peru = countries.loc[countries["iso3"] == "PER"]
        biomass_anchor = anchors.get("anchoveta_biomass_1997_98")
        if biomass_anchor is not None and not peru.empty:
            modelled_biomass = float(peru["fisheries"].iloc[0])
            observed = float(biomass_anchor["value_pct"])
            if modelled_biomass > observed:
                qc.warn(
                    f"Peru biomass response {modelled_biomass:.1f}% is milder than the "
                    f"observed {observed:.0f}% at a LOWER intensity -- the depletion "
                    f"curve is inverted"
                )
        metrics.qc_failures.extend(qc.flags)
 
    return state, countries, commodities
 
 
# ---------------------------------------------------------------------------
# Step C
# ---------------------------------------------------------------------------
 
#: ONI 3-month season -> the calendar month it is centred on.
SEASON_CENTRE = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}
 
#: The event year the 2026-27 analogue comparison is anchored on.
ANCHOR_YEAR = 2026
 
 
def _calendar_align_analogs(analogs: pd.DataFrame) -> pd.DataFrame:
    """Place each analogue step on the calendar month its season is centred on.
 
    Step 0 is the DJF of the event year, centred on January, so step i maps to
    month ``i % 12 + 1`` of ``ANCHOR_YEAR + i // 12``. Under this mapping every
    historical peak lands in Nov-Dec of the anchor year, which is where the
    forecast peak sits -- so the chart compares like with like.
    """
    out = analogs.copy()
    steps = out["step"].astype(int)
    years = ANCHOR_YEAR + steps // 12
    months = steps % 12 + 1
    out["month"] = [f"{y}-{m:02d}" for y, m in zip(years, months)]
    return out
 
 
def _oni_to_calendar(oni: pd.DataFrame) -> list[dict[str, Any]]:
    """Observed ONI as (calendar month, value), for plotting against forecast.
 
    This is the *verified* observed series. It replaces the reconstructed
    monthly 'observed' point that used to ride along with the plume, so the
    chart has exactly one definition of observed and it is the citable one.
    """
    frame = oni.copy()
    if "oni" not in frame.columns and "value" in frame.columns:
        frame["oni"] = frame["value"]
    rows = []
    for _, row in frame.dropna(subset=["oni"]).iterrows():
        month = SEASON_CENTRE.get(str(row["season"]).upper())
        if month is None:
            continue
        rows.append({
            "month": f"{int(row['year'])}-{month:02d}",
            "oni": float(row["oni"]),
            "season": f"{row['season']} {int(row['year'])}",
        })
    return sorted(rows, key=lambda r: r["month"])
 
def step_c_payload(
    cfg: Config,
    state: EventState,
    countries: pd.DataFrame,
    commodities: dict[str, pd.DataFrame],
    resolved: dict[str, Any],
    metrics: RunMetrics,
) -> dict[str, Any]:
    plume = resolved["ensemble_plume"]["frame"].copy()
    analogs = resolved["historical_analogs"]["frame"].copy()
    market = resolved.get("market_baseline", {}).get("frame")
 
    for frame in (plume, analogs):
        for col in frame.columns:
            if pd.api.types.is_datetime64_any_dtype(frame[col]):
                frame[col] = frame[col].dt.strftime("%Y-%m-%d")
 
    ensemble = seedmod.ensemble_summary()
    escalations = rank_risk_escalations(countries, top_n=5)
 
    # --- calendar alignment -------------------------------------------------
    # ONI seasons are 3-month running means centred on their middle month:
    # DJF -> January, JFM -> February, ... NDJ -> December. El Nino is
    # seasonally phase-locked, so analogues must be placed on the calendar
    # month their season is centred on, NOT on their index within the event.
    #
    # An earlier build mapped analogue step i straight onto forecast month i,
    # which put the 1982-83/1997-98/2015-16 peaks in spring 2027 instead of
    # Nov-Dec, and made the forecast look like it peaked months earlier than
    # every historical event. It does not: they all peak in the same window.
    analogs = _calendar_align_analogs(analogs)
    oni_calendar = _oni_to_calendar(resolved["oni_observed"]["frame"])
 
    def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        """Serialise with real nulls.
 
        pandas NaN does not survive ``json.dumps(default=str)`` as ``null`` --
        it emits a bare ``NaN`` token, which is invalid JSON and which the
        dashboard rendered as the string "NaN%". Coercing to object first and
        substituting None is the only reliable fix at frame level.
        """
        return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
 
    return {
        "meta": {
            "run_id": metrics.run_id,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "as_of": state.as_of,
            "schema_version": 3,
            "sources_live": metrics.sources_live,
            "sources_seeded": metrics.sources_seeded,
            "sources_dormant": metrics.sources_dormant,
            "sources_failed": metrics.sources_failed,
            "data_latency_h": metrics.data_latency_h,
            "observation_age_h": metrics.observation_age_h,
            "dataset_provenance": metrics.dataset_provenance,
        },
        "state": state.to_dict(),
        "ensemble": ensemble,
        "plume": records(plume),
        "analogs": records(analogs),
        "oni_calendar": oni_calendar,
        "anchor_year": ANCHOR_YEAR,
        "countries": records(countries),
        "commodities": {k: records(v) for k, v in commodities.items()},
        "market_baseline": [] if market is None else records(market),
        "escalations": records(escalations),
        "layers": [
            {"key": spec.key, "label": spec.label, "unit": spec.unit, "scale": spec.scale}
            for spec in LAYERS
        ],
        "styles": cfg.styles,
        "glossary": cfg.glossary,
        "column_notes": cfg.columns,
        "groups": cfg.regions["groups"],
        "literature_anchors": seedmod.literature_anchors(),
        "unverified_assertions": seedmod.unverified_assertions(),
        "qc_flags": metrics.qc_failures,
        "notes": metrics.notes,
    }
 
 
# ---------------------------------------------------------------------------
# Step F
# ---------------------------------------------------------------------------
 
def step_f_alerts(
    cfg: Config,
    state: EventState,
    metrics: RunMetrics,
    previous: dict[str, Any] | None,
    history: dict[str, str] | None = None,
) -> list[Alert]:
    """Emit tiered alerts.
 
    The tier matters more than the message. An operator who sees two permanent
    reds every morning stops reading the strip, and then misses the one alert
    the system exists to raise -- a 0.3 degC move in the ensemble median. So:
    only things that *changed* are incidents.
    """
    alerts: list[Alert] = []
    limits = cfg.thresholds["alerts"]
    history = history or {}
    critical = set(limits.get("source_failure_critical", []))
 
    # -- incidents: something changed --------------------------------------
    shift = median_shift(previous, state)
    if shift is not None and abs(shift) >= float(limits["ensemble_median_shift_degC"]):
        alerts.append(Alert(
            severity="incident", code="ensemble_median_shift",
            title=f"Ensemble median moved {shift:+.2f} degC",
            detail=(
                f"Exceeds the {limits['ensemble_median_shift_degC']} degC trigger since the "
                f"previous run. A sensitivity re-run is required and every impact layer "
                f"downstream has moved with it."
            ),
        ))
 
    for name in sorted(set(metrics.regressed_sources)):
        last_seen = history.get(name, "unknown")
        alerts.append(Alert(
            severity="incident", code="source_regression",
            title=f"Source stopped working: {name}",
            detail=(
                f"This feed returned data before (last good {last_seen[:19]}) and failed "
                f"this run. "
                + ("It is on the critical list, so the outputs it feeds are now stale."
                   if name in critical else
                   "Serving last-good data in its place.")
            ),
        ))
 
    # Observation age, per dataset. This is the check that catches a feed
    # publishing months-old values on schedule -- the failure the retrieval
    # clock is blind to by construction.
    age_limits = limits.get("observation_age_h", {})
    default_limit = age_limits.get("default", {"warn": 720, "critical": 2160})
    for dataset, age in sorted(metrics.observation_age_h.items()):
        if age is None:
            continue
        limit = age_limits.get(dataset, default_limit)
        if limit is None:          # explicitly marked as a reference series
            continue
        days = age / 24.0
        if age >= float(limit["critical"]):
            alerts.append(Alert(
                severity="incident", code="observation_critically_stale",
                title=f"Observation critically stale: {dataset}",
                detail=(
                    f"The newest measurement is {days:.0f} days old, past the "
                    f"{float(limit['critical'])/24:.0f}-day limit. The feed may be "
                    f"reachable and still not publishing."
                ),
            ))
        elif age >= float(limit["warn"]):
            alerts.append(Alert(
                severity="configuration", code="observation_ageing",
                title=f"Observation ageing: {dataset}",
                detail=(
                    f"Newest measurement is {days:.0f} days old "
                    f"({float(limit['warn'])/24:.0f}-day soft limit). Normal for a "
                    f"product with a long publication lag; worth checking otherwise."
                ),
            ))
 
    # Retrieval age -- a dead pipeline rather than a dead feed.
    crit_h = float(limits["stale_critical_h"])
    for dataset, hours in sorted(metrics.data_latency_h.items()):
        if hours >= crit_h:
            alerts.append(Alert(
                severity="incident", code="pipeline_not_running",
                title=f"Pipeline has not refreshed {dataset}",
                detail=(
                    f"{hours:.0f} h since the last write, past the {crit_h:.0f} h limit. "
                    f"This is about the scheduler, not the source."
                ),
            ))
 
    # -- standing: true every run, will not clear --------------------------
    if state.extrapolation_warning:
        im = cfg.thresholds["impact_model"]
        alerts.append(Alert(
            severity="standing", code="extrapolation_regime",
            title=f"Extrapolation regime ({state.peak_median:.2f} degC)",
            detail=(
                f"The forecast median exceeds {im['extrapolation_warning_degC']} degC, above "
                f"which teleconnection composites have no observational grounding -- no event "
                f"this large has been recorded. Every impact layer already carries a "
                f"{im['confidence_penalty_above_warning']:.0%} confidence penalty. This is a "
                f"property of the event, not a fault: it will not clear."
            ),
        ))
 
    # -- configuration: a capability is switched off -----------------------
    if metrics.sources_live == 0:
        alerts.append(Alert(
            severity="configuration", code="no_live_sources",
            title="No live sources -- every figure is from the bundled snapshot",
            detail=(
                f"{metrics.sources_seeded} dataset(s) served from the provenance-tagged seed "
                f"snapshot. Of {metrics.sources_attempted} configured sources: "
                f"{len(metrics.dormant_sources)} lack credentials, "
                f"{len(metrics.skipped_offline)} were skipped by --offline, "
                f"{metrics.sources_failed} failed. Nothing on screen was retrieved this run. "
                f"Run `enso-tracker sources` to see what would go live here."
            ),
        ))
 
    if metrics.skipped_offline:
        alerts.append(Alert(
            severity="configuration", code="offline_run",
            title=f"Offline run -- {len(metrics.skipped_offline)} source(s) not attempted",
            detail=(
                "Invoked with --offline, so no source was contacted. Outputs come from the "
                "seed snapshot and last-good store. This is a deliberate mode, not a fault."
            ),
        ))
 
    dormant_critical = sorted(set(metrics.dormant_sources) & critical)
    if dormant_critical:
        alerts.append(Alert(
            severity="configuration", code="critical_sources_dormant",
            title=f"Ensemble sources not credentialed: {', '.join(dormant_critical)}",
            detail=(
                "These are the per-member forecast feeds. Until they are credentialed the "
                "monthly plume is reconstructed from published summary statistics rather than "
                "read from model output. Not a failure -- they have never run here."
            ),
        ))
 
    alerts.sort(key=lambda a: SEVERITY_ORDER.index(a.severity))
    metrics.alerts = alerts
    return alerts
 
 
# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
 
def load_previous_state(out: Path) -> dict[str, Any] | None:
    path = out / STATE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
 
 
def run(offline: bool = False, out: Path | None = None) -> RunResult:
    cfg = get_config()
    out = out or output_dir()
    out.mkdir(parents=True, exist_ok=True)
    store = ParquetStore()
 
    metrics = RunMetrics(
        run_id=make_run_id("pipeline"),
        started_utc=datetime.now(timezone.utc).isoformat(),
    )
    previous = load_previous_state(out)
    history = load_source_history(out)
 
    resolved = step_a_ingest(cfg, store, metrics, offline=offline, history=history)
    state, countries, commodities = step_b_recompute(cfg, resolved, metrics)
    payload = step_c_payload(cfg, state, countries, commodities, resolved, metrics)
    alerts = step_f_alerts(cfg, state, metrics, previous, history)
 
    save_source_history(out, history)
 
    payload["alerts"] = [asdict(a) for a in alerts]
    payload["severity_meta"] = SEVERITY_META
    payload["severity_order"] = SEVERITY_ORDER
    payload["meta"]["alert_counts"] = {
        s: sum(1 for a in alerts if a.severity == s) for s in SEVERITY_ORDER
    }
 
    metrics.finished_utc = datetime.now(timezone.utc).isoformat()
    payload["meta"]["metrics"] = asdict(metrics)
 
    (out / STATE_FILE).write_text(
        json.dumps(state.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    (out / "dashboard_payload.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
 
    archive = out / "archive"
    archive.mkdir(exist_ok=True)
    (archive / f"payload_{metrics.run_id}.json").write_text(
        json.dumps(payload, default=str), encoding="utf-8"
    )
 
    return RunResult(
        state=state,
        countries=countries,
        commodities=commodities,
        plume=resolved["ensemble_plume"]["frame"],
        analogs=resolved["historical_analogs"]["frame"],
        escalations=rank_risk_escalations(countries, top_n=5),
        metrics=metrics,
        payload=payload,
    )
