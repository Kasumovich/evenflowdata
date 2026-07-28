"""Tests for the pipeline, model and config.
 
The regression tests at the bottom pin the two modelling bugs that the first
build actually shipped and that QC caught. Those are the ones worth keeping.
"""
 
from __future__ import annotations
 
import json
import math
 
import pandas as pd
import pytest
 
from enso_tracker.config import get_config
from enso_tracker.connectors import registered
from enso_tracker.connectors import seed as seedmod
from enso_tracker.model.anomaly import build_event_state, probability_above
from enso_tracker.model.impact import (
    build_commodity_table,
    build_country_layers,
    intensity_factor,
    rank_risk_escalations,
)
from enso_tracker.pipeline import run
from enso_tracker.qc import QCResult, check_ensemble, check_ranges, run_standard_suite
 
 
@pytest.fixture(scope="module")
def cfg():
    return get_config()
 
 
# --------------------------------------------------------------------- config
 
def test_config_loads_and_validates(cfg):
    assert len(cfg.country_list) > 30
    assert len(cfg.commodity_index) > 10
    # Was >25 when the registry carried eleven stub connectors pointed at
    # human-readable web pages. Those were removed rather than left to fail
    # forever; the bound tracks sources we actually intend to poll.
    assert len(cfg.source_blocks()) >= 20
 
 
def test_every_source_has_a_registered_connector(cfg):
    available = registered()
    missing = [
        name for name, spec in cfg.source_blocks().items()
        if spec["connector"] not in available
    ]
    assert not missing, f"sources with no connector: {missing}"
 
 
def test_crop_exposures_sum_to_one(cfg):
    for country in cfg.country_list:
        crops = country.get("crops") or {}
        if not crops:
            continue
        total = sum(c["exposure"] for c in crops.values())
        assert abs(total - 1.0) < 0.02, f"{country['iso3']} exposures sum to {total}"
 
 
def test_pinned_regions_exist(cfg):
    index = cfg.country_index
    for iso3 in cfg.pinned:
        assert iso3 in index
 
 
def test_enso_categories_are_contiguous(cfg):
    bands = list(cfg.thresholds["enso_categories"].values())
    for (_, hi), (lo, _) in zip(bands, bands[1:]):
        assert hi == lo, "category thresholds must not leave gaps"
 
 
# ---------------------------------------------------------------------- seed
 
def test_seed_snapshot_is_valid_json_and_tagged():
    seed = seedmod.load_seed()
    for key in ("oni_observed", "weekly_nino", "ensemble_forecast", "ensemble_plume"):
        assert "provenance" in seed[key], f"{key} must declare provenance"
 
 
def test_seed_loaders_produce_frames():
    for name, loader in seedmod.SEED_LOADERS.items():
        result = loader()
        assert not result.empty, f"{name} produced an empty frame"
        assert result.provenance.startswith("seed:")
 
 
def test_unverified_assertions_are_carried_not_dropped():
    """Brief figures that failed attribution must stay auditable."""
    unverified = seedmod.unverified_assertions()
    tracked = {k: v for k, v in unverified.items() if not k.startswith("_")}
    assert tracked, "unattributed brief figures must be recorded, not silently dropped"
    assert all("status" in v for v in tracked.values())
 
 
# --------------------------------------------------------------------- model
 
def test_intensity_factor_is_sublinear(cfg):
    """Doubling intensity must not double the response -- teleconnections saturate."""
    ref = cfg.thresholds["impact_model"]["reference_intensity_degC"]
    assert intensity_factor(cfg, ref) == pytest.approx(1.0, abs=1e-9)
    assert 1.0 < intensity_factor(cfg, 3.6) < 3.6 / ref
 
 
def test_probability_above_is_monotonic():
    p_low = probability_above(2.0, 3.6, 2.8, 4.4)
    p_high = probability_above(4.0, 3.6, 2.8, 4.4)
    assert 0.0 <= p_high < p_low <= 1.0
 
 
def test_country_layers_have_expected_shape(cfg):
    df = build_country_layers(cfg, 3.6)
    assert len(df) == len(cfg.country_list)
    for column in ("precip_djf", "temp_djf", "yield_index", "fire_drought",
                   "price_pressure", "confidence"):
        assert column in df.columns
 
 
def test_yield_index_can_be_positive(cfg):
    """A model that only ever produces losses is a narrative, not a risk model.
 
    Argentina and the US southern Plains are genuine El Nino beneficiaries.
    """
    df = build_country_layers(cfg, 3.6)
    assert (df["yield_index"] > 0).any()
    assert float(df.loc[df["iso3"] == "ARG", "yield_index"].iloc[0]) > 0
 
 
def test_confidence_penalised_above_extrapolation_threshold(cfg):
    warn_at = cfg.thresholds["impact_model"]["extrapolation_warning_degC"]
    below = build_country_layers(cfg, warn_at - 0.5)
    above = build_country_layers(cfg, warn_at + 0.5)
    assert above["confidence"].mean() < below["confidence"].mean()
 
 
def test_escalation_ranking_weights_by_confidence(cfg):
    df = build_country_layers(cfg, 3.6)
    top = rank_risk_escalations(df, top_n=5)
    assert len(top) == 5
    assert top["severity"].is_monotonic_decreasing
 
 
# ------------------------------------------------------------------ regressions
 
def test_regression_palm_response_stays_within_published_band(cfg):
    """Regression: the first build modelled +242% against a published +20-40%.
 
    Cause was compounding elasticity x unbounded stock term x concentration.
    The bounded stock modifier is the fix; this test is the fence around it.
    """
    table = build_commodity_table(cfg, 3.6, scenario="base")
    palm = table[table["commodity"] == "palm_oil"].iloc[0]
    anchor = seedmod.literature_anchors()["palm_oil_price_response"]["value_pct"]
    lo, hi = min(anchor), max(anchor)
    assert lo / 2 <= palm["price_response_pct"] <= hi * 2, (
        f"palm response {palm['price_response_pct']}% outside 2x of "
        f"published {lo}-{hi}%"
    )
 
 
def test_regression_commodity_shock_uses_global_production_share(cfg):
    """Regression: the first build weighted by share of the *country's own*
    agriculture, treating Indonesia and Malaysia as if they were the world,
    and produced a 27% global palm shortfall -- larger than any in the crop's
    history. Global shocks must stay well inside plausible bounds.
    """
    table = build_commodity_table(cfg, 3.6, scenario="base")
    worst = table["production_shock_pct"].min()
    assert worst > -30.0, f"implausible global production shock {worst}%"
    for _, row in table.iterrows():
        assert 0.0 <= row["coverage"] <= 1.0
 
 
def test_regression_fire_index_does_not_saturate(cfg):
    """Regression: clipping the fire index at 100 tied six countries at the
    ceiling and destroyed the ordering the layer exists to communicate."""
    df = build_country_layers(cfg, 3.6)
    at_ceiling = (df["fire_drought"] >= 99.9).sum()
    assert at_ceiling == 1, f"{at_ceiling} countries at the ceiling; index is saturating"
 
 
def test_regression_payload_contains_no_nan_tokens(tmp_path):
    """Regression: pandas NaN serialised to a bare NaN token, which is invalid
    JSON and which the dashboard rendered literally as 'NaN%'."""
    result = run(offline=True, out=tmp_path)
    raw = (tmp_path / "dashboard_payload.json").read_text()
    assert "NaN" not in raw
    json.loads(raw)  # must parse as strict JSON
 
 
# ------------------------------------------------------------------------ qc
 
def test_qc_rejects_out_of_range(cfg):
    df = pd.DataFrame({"oni": [0.5, 42.0]})
    result = QCResult(dataset="test")
    check_ranges(df, cfg, "test", result)
    assert not result.passed
 
 
def test_qc_rejects_percentile_inversion(cfg):
    df = pd.DataFrame({"p10": [3.0], "median": [2.0], "p90": [4.0], "n_members": [667]})
    result = QCResult(dataset="test")
    check_ensemble(df, cfg, result)
    assert not result.passed
 
 
def test_qc_flags_thin_ensemble(cfg):
    df = pd.DataFrame({"p10": [1.0], "median": [2.0], "p90": [3.0], "n_members": [5]})
    result = QCResult(dataset="test")
    check_ensemble(df, cfg, result)
    assert not result.passed
 
 
def test_qc_suite_handles_empty_frame(cfg):
    result = run_standard_suite(pd.DataFrame(), cfg, "empty")
    assert not result.passed
 
 
# ------------------------------------------------------------------ pipeline
 
def test_offline_run_produces_complete_payload(tmp_path):
    result = run(offline=True, out=tmp_path)
    payload = result.payload
 
    assert payload["state"]["peak_median"] > 0
    assert len(payload["countries"]) > 30
    assert "base" in payload["commodities"]
    assert (tmp_path / "dashboard_payload.json").exists()
    assert (tmp_path / "last_state.json").exists()
    assert list((tmp_path / "archive").glob("payload_*.json"))
 
 
def test_offline_run_never_reports_seeded_data_as_live(tmp_path):
    result = run(offline=True, out=tmp_path)
    assert result.metrics.sources_live == 0
    assert result.metrics.sources_seeded > 0
 
 
def test_extrapolation_alert_is_raised(tmp_path):
    result = run(offline=True, out=tmp_path)
    assert any(a.code == "extrapolation_regime" for a in result.metrics.alerts)
 
 
# ------------------------------------------------------- alert severity tiers
 
def test_regression_dormant_source_is_not_an_incident(tmp_path):
    """Regression: every alert rendered identically as "Critical", so a source
    that had simply never been credentialed looked the same as one that had
    gone down. Two meaningless reds on day one train an operator to ignore the
    strip -- and then miss the median-shift alert that the system exists for.
    """
    result = run(offline=True, out=tmp_path)
 
    # Source-availability incidents specifically: a feed that has never been
    # configured here is not an incident, no matter how loudly it is missing.
    # (Stale *observations* are a separate, legitimate incident class -- the
    # seeded snapshot really is months old.)
    source_codes = {"source_regression", "critical_source_unavailable"}
    offenders = [
        a for a in result.metrics.alerts
        if a.severity == "incident" and a.code in source_codes
    ]
    assert not offenders, (
        f"never-configured sources must not raise incidents, got "
        f"{[a.code for a in offenders]}"
    )
    assert any(a.severity == "standing" for a in result.metrics.alerts)
    assert any(a.severity == "configuration" for a in result.metrics.alerts)
    # And the dormant ensemble feeds must land in the configuration tier.
    dormant = [a for a in result.metrics.alerts if a.code == "critical_sources_dormant"]
    assert dormant and dormant[0].severity == "configuration"
 
 
def test_offline_skip_is_not_reported_as_uncredentialed(tmp_path):
    """Keyless sources skipped by --offline must not be described as needing
    credentials -- they would work fine with network access."""
    result = run(offline=True, out=tmp_path)
    dormant = next(
        (a for a in result.metrics.alerts if a.code == "critical_sources_dormant"), None
    )
    assert dormant is not None
    assert "cpc_oni" not in dormant.title, "keyless CPC feeds are not credential-gated"
    assert result.metrics.skipped_offline, "offline skips must be tracked separately"
 
 
def test_source_regression_is_an_incident(tmp_path):
    """A source that worked before and now fails IS an incident."""
    from enso_tracker.pipeline import (
        Alert, RunMetrics, load_source_history, save_source_history, step_f_alerts,
    )
    from enso_tracker.model.anomaly import build_event_state
 
    run(offline=True, out=tmp_path)
    history = load_source_history(tmp_path)
    history["cpc_oni"] = "2026-07-01T00:00:00+00:00"
    save_source_history(tmp_path, history)
 
    cfg = get_config()
    metrics = RunMetrics(run_id="test", started_utc="now")
    metrics.regressed_sources = ["cpc_oni"]
    metrics.sources_live = 1
    state = run(offline=True, out=tmp_path).state
    alerts = step_f_alerts(cfg, state, metrics, None, history)
 
    regression = [a for a in alerts if a.code == "source_regression"]
    assert regression and regression[0].severity == "incident"
 
 
def test_alerts_are_sorted_most_urgent_first(tmp_path):
    from enso_tracker.pipeline import SEVERITY_ORDER
 
    result = run(offline=True, out=tmp_path)
    ranks = [SEVERITY_ORDER.index(a.severity) for a in result.metrics.alerts]
    assert ranks == sorted(ranks)
 
 
def test_payload_alerts_are_json_serialisable_dicts(tmp_path):
    result = run(offline=True, out=tmp_path)
    for alert in result.payload["alerts"]:
        assert set(alert) == {"severity", "code", "title", "detail"}
    assert result.payload["severity_order"]
    counts = result.payload["meta"]["alert_counts"]
    assert set(counts) == {"incident", "standing", "configuration"}
    assert counts["standing"] >= 1
 
 
def test_second_run_is_incremental_and_preserves_history(tmp_path):
    first = run(offline=True, out=tmp_path)
    second = run(offline=True, out=tmp_path)
    archives = list((tmp_path / "archive").glob("payload_*.json"))
    assert len(archives) == 2, "each run must archive its own immutable snapshot"
    assert first.metrics.run_id != second.metrics.run_id
 
 
def test_static_export_is_self_contained(tmp_path):
    from enso_tracker.dashboard.static_export import export_dashboard
 
    result = run(offline=True, out=tmp_path)
    target = export_dashboard(result.payload, tmp_path / "dashboard.html")
    html = target.read_text(encoding="utf-8")
    assert "<html" in html and "PAYLOAD" in html
    assert "__PAYLOAD__" not in html, "payload placeholder was not substituted"
    assert len(html) > 50_000
 
 
def test_sitrep_renders_and_names_unverified_figures(tmp_path):
    from enso_tracker.report.sitrep import build_markdown
 
    result = run(offline=True, out=tmp_path)
    md = build_markdown(result.payload)
    assert "# El Nino 2026-27 Situation Report" in md
    assert "Top 5 risk escalations" in md
    assert "Caveats and unattributed inputs" in md
 
 
def test_regression_analogues_are_calendar_aligned(tmp_path):
    """Regression: analogue step i was plotted at forecast month i, which put
    the 1982-83 / 1997-98 / 2015-16 peaks in spring 2027 and made the forecast
    look like it peaked months earlier than every historical event.
 
    El Nino is seasonally phase-locked. Every one of these events peaks in the
    Nov-Jan window, and after calendar alignment the chart must show that.
    """
    result = run(offline=True, out=tmp_path)
    payload = result.payload
 
    forecast_peak = max(payload["plume"], key=lambda r: r["median"])["month"]
    assert forecast_peak.endswith(("-11", "-12", "-01"))
 
    analogs = pd.DataFrame(payload["analogs"])
    assert "month" in analogs.columns, "analogues must carry a calendar month"
    for event, rows in analogs.groupby("event"):
        peak_month = rows.loc[rows["oni"].idxmax(), "month"]
        assert peak_month.endswith(("-11", "-12", "-01")), (
            f"{event} peaks at {peak_month}; ENSO events are phase-locked to Nov-Jan"
        )
 
 
def test_oni_seasons_map_to_their_centre_month(tmp_path):
    from enso_tracker.pipeline import SEASON_CENTRE
 
    assert SEASON_CENTRE["DJF"] == 1 and SEASON_CENTRE["NDJ"] == 12
    result = run(offline=True, out=tmp_path)
    calendar = result.payload["oni_calendar"]
    assert calendar, "observed ONI must be published on a calendar axis"
    latest = calendar[-1]
    assert latest["month"] == "2026-04" and latest["season"].startswith("MAM")
 
 
# ------------------------------------------------- observation vs retrieval age
 
def test_regression_staleness_measures_observation_not_write(tmp_path):
    """Regression: staleness was computed from the store write time, so a value
    served from the seed snapshot reported 0 h age no matter how old the
    underlying measurement was. The stale-data alarm was structurally incapable
    of firing -- every run reset the clock it was supposed to be watching.
    """
    result = run(offline=True, out=tmp_path)
    ages = result.metrics.observation_age_h
 
    assert ages, "observation age must be recorded"
    assert ages["oni_observed"] is not None
    assert ages["oni_observed"] > 24, (
        "the seeded ONI observation is months old; reporting it as fresh is the bug"
    )
    # Retrieval age is legitimately ~0 on a fresh run. The point is that the two
    # clocks must not be the same number.
    assert result.metrics.data_latency_h["oni_observed"] < 1.0
    assert ages["oni_observed"] > result.metrics.data_latency_h["oni_observed"]
 
 
def test_reference_series_never_reports_an_age(tmp_path):
    """Closed historical events do not go stale and must not be alerted on."""
    result = run(offline=True, out=tmp_path)
    assert result.metrics.observation_age_h["historical_analogs"] is None
    codes = [a.code for a in result.metrics.alerts]
    stale = [
        a for a in result.metrics.alerts
        if a.code.startswith("observation") and "historical_analogs" in a.title
    ]
    assert not stale, "a closed historical series was flagged as stale"
 
 
def test_stale_observation_raises_an_incident(tmp_path):
    """The alarm must actually fire on old data -- it previously could not."""
    result = run(offline=True, out=tmp_path)
    stale = [a for a in result.metrics.alerts if a.code == "observation_critically_stale"]
    assert stale, "months-old seeded observations must raise a stale incident"
    assert all(a.severity == "incident" for a in stale)
 
 
def test_missing_valid_time_is_unknown_not_fresh(tmp_path):
    """A dataset with no declared valid_time reports None, never zero.
 
    'Unknown age' and 'zero age' are very different claims and the second one
    is the dangerous one.
    """
    from enso_tracker.store import ParquetStore
 
    store = ParquetStore(tmp_path / "store")
    store.write(
        "mystery", pd.DataFrame({"x": [1]}), source="test", valid_time=None,
    )
    assert store.observation_age_hours("mystery") is None
    assert store.staleness_hours("mystery") is not None
 
 
def test_observation_age_limits_are_per_dataset(cfg):
    """A weekly product and a monthly product cannot share one threshold."""
    limits = cfg.thresholds["alerts"]["observation_age_h"]
    assert limits["weekly_nino"]["critical"] < limits["oni_observed"]["critical"]
    assert limits["historical_analogs"] is None
 
 
# ------------------------------------------------------- fisheries transmission
 
def test_fisheries_response_reproduces_the_observation_at_reference(cfg):
    """At the reference intensity the chain must return the observed 1997-98
    collapse exactly: 5.8 Mt -> 1.2 Mt is a 79% loss, not an estimate."""
    from enso_tracker.model.impact import fisheries_response
 
    chain = fisheries_response(cfg, -79.0, 1.0)
    assert chain["biomass_pct"] == pytest.approx(-79.0, abs=0.1)
    assert chain["survival_fraction"] == pytest.approx(0.21, abs=0.005)
 
 
def test_regression_fisheries_depletion_cannot_exceed_total_collapse(cfg):
    """Regression: fishmeal borrowed the crop pipeline and scaled a biomass
    anomaly linearly, which runs past -100% at high intensity. Depletion
    compounds; it does not extrapolate through zero.
    """
    from enso_tracker.model.impact import fisheries_response
 
    for factor in (1.0, 1.5, 3.0, 10.0):
        chain = fisheries_response(cfg, -79.0, factor)
        assert -100.0 < chain["biomass_pct"] <= 0.0, (
            f"biomass {chain['biomass_pct']} at factor {factor} is unphysical"
        )
    # Monotonic and saturating: worse with intensity, never through the floor.
    mild = fisheries_response(cfg, -79.0, 1.0)["biomass_pct"]
    severe = fisheries_response(cfg, -79.0, 2.0)["biomass_pct"]
    assert severe < mild
 
 
def test_regression_coverage_counts_only_modelled_supply(cfg):
    """Regression: coverage summed every country listed in production_shares,
    including ones with no modelled response. Fishmeal reported 57% coverage
    when the estimate rested on ~32% of world supply -- reassuring and false.
    """
    table = build_commodity_table(cfg, 3.6, scenario="base")
    fish = table[table["commodity"] == "fishmeal"].iloc[0]
 
    assert fish["coverage"] < fish["listed_share"], (
        "fishmeal has listed supply it cannot model; coverage must be the smaller number"
    )
    assert fish["coverage"] == pytest.approx(0.32, abs=0.01), (
        "Peru + Chile + Ecuador is the modelled share, matching the published "
        "'nearly a third of world fishmeal'"
    )
    for _, row in table.iterrows():
        assert row["coverage"] <= row["listed_share"] + 1e-9
 
 
def test_fishmeal_uses_the_fisheries_route_and_its_faster_lag(cfg):
    table = build_commodity_table(cfg, 3.6, scenario="base")
    fish = table[table["commodity"] == "fishmeal"].iloc[0]
    assert fish["transmission"] == "fisheries"
    assert fish["lag_months"] == cfg.thresholds["fisheries"]["lag_months"]
    assert fish["lag_months"] < cfg.thresholds["price_pressure"]["transmission_lag_months"]
 
 
def test_fishmeal_price_response_matches_its_anchor(cfg):
    """The elasticity is anchored, not assumed: a ~40% production fall
    coincided with an ~80% price rise."""
    table = build_commodity_table(cfg, 3.6, scenario="base")
    fish = table[table["commodity"] == "fishmeal"].iloc[0]
    anchor = seedmod.literature_anchors()["fishmeal_price_response"]
    lo, hi = min(anchor["value_pct"]), max(anchor["value_pct"])
    assert lo / 2 <= fish["price_response_pct"] <= hi * 2
    assert fish["elasticity"] == pytest.approx(anchor["implied_elasticity"], abs=0.01)
 
 
def test_countries_without_fisheries_report_no_value(cfg):
    """A landlocked country must have no fisheries value at all.
 
    Note pandas coerces None to NaN inside a frame; the payload serialiser
    converts it back to a real JSON null (see the NaN-token regression above),
    so both representations are checked in their own layer.
    """
    df = build_country_layers(cfg, 3.6)
    landlocked = df[df["iso3"] == "ZWE"].iloc[0]
    assert pd.isna(landlocked["fisheries"])
    assert pd.isna(landlocked["fisheries_landings"])
 
 
def test_fisheries_countries_survive_json_serialisation(tmp_path):
    result = run(offline=True, out=tmp_path)
    peru = next(c for c in result.payload["countries"] if c["iso3"] == "PER")
    zwe = next(c for c in result.payload["countries"] if c["iso3"] == "ZWE")
    assert peru["fisheries"] is not None and peru["fisheries_landings"] is not None
    assert peru["fisheries_species"] == "anchoveta"
    assert zwe["fisheries"] is None
 
 
# ---------------------------------------------------------------------------
# Regressions from the first live GitHub Actions run
#
# That run went green and published a worse dashboard than the bundled
# snapshot: a live CPC feed pointed at a file frozen in 2020, its six-year-old
# values displaced a 47-day-old seed, the CPC advisory read "Unknown", and the
# Pink Sheet mapped none of its columns. Four separate faults, one green tick.
# ---------------------------------------------------------------------------
 
def test_cpc_urls_are_the_current_ones():
    """Both CPC URLs were wrong in different ways, and both were invisible.
 
    The ONI hostname was a guess that did not resolve. The weekly file was the
    1981-2010 base-period file, which CPC froze rather than retired -- so it
    answered 200 OK forever with 2020 data.
    """
    cfg = get_config()
    climate = cfg.sources["climate"]
    assert "origin.cpc" not in climate["cpc_oni"]["url"]
    assert climate["cpc_oni"]["url"].startswith("https://www.cpc.ncep.noaa.gov/")
    assert "wksst8110" not in climate["cpc_weekly_nino"]["url"]
    assert "wksst9120" in climate["cpc_weekly_nino"]["url"]
 
 
def test_advisory_survives_a_live_weekly_feed_that_has_no_advisory_column(cfg):
    """CPC issues the advisory monthly, separately from the weekly SST file.
 
    So a live weekly feed carries temperatures and no advisory. Before this
    fix, promoting that feed over the seed degraded the header from
    'El Nino Advisory' to 'Unknown' -- real data making the dashboard say less.
    """
    oni = seedmod.oni_observed().frame
    ensemble = seedmod.ensemble_summary()
    live_weekly = pd.DataFrame([{
        "week_ending": pd.Timestamp("2026-07-22"),
        "nino12": 2.4, "nino3": 2.2, "nino34": 2.1, "nino4": 1.3,
    }])
 
    state = build_event_state(
        cfg, oni_df=oni, weekly_df=live_weekly, ensemble=ensemble,
        alert_status_fallback="El Nino Advisory",
    )
    assert state.alert_status == "El Nino Advisory"
    assert state.provenance["advisory"] == "carried_forward"
 
 
def test_a_real_advisory_in_the_feed_beats_the_fallback(cfg):
    oni = seedmod.oni_observed().frame
    weekly = pd.DataFrame([{
        "week_ending": pd.Timestamp("2026-07-22"), "nino34": 2.1,
        "alert_status": "Final El Nino Advisory",
    }])
    state = build_event_state(
        cfg, oni_df=oni, weekly_df=weekly, ensemble=seedmod.ensemble_summary(),
        alert_status_fallback="El Nino Advisory",
    )
    assert state.alert_status == "Final El Nino Advisory"
    assert state.provenance["advisory"] == "weekly_feed"
 
 
def test_pinksheet_reconciles_the_workbooks_real_column_labels():
    """The workbook says 'Crude oil, WTI'; commodities.yaml says 'CRUDE_WTI'.
 
    Exact-match alone mapped nothing and reported '100% missing', which is true
    and useless. Resolution now goes exact -> alias -> token-subset, and
    refuses ambiguity rather than guessing.
    """
    from enso_tracker.connectors.markets import PinkSheetConnector as PS
 
    cfg = get_config()
    conn = PS("worldbank_pinksheet", cfg.sources["markets"]["worldbank_pinksheet"], cfg)
    wanted = conn._lookup()
 
    assert conn._resolve("Maize", wanted) == "maize"              # exact
    assert conn._resolve("Crude oil, WTI", wanted) == "wti_reference"  # token subset
    assert conn._resolve("Urea, E. Europe, bulk", wanted) == "urea_reference"  # alias
    assert conn._resolve("Rice, Thai 5%", wanted) == "rice"        # alias
    assert conn._resolve("Bananas, Europe", wanted) is None        # not modelled
 
 
def test_pinksheet_header_is_found_rather_than_assumed(tmp_path):
    """A fixed header=[0,1] met a title banner and produced 'Unnamed: 3_level_0'.
 
    The header is now located by finding the first YYYYMmm row, which works
    whether the banner is there or not.
    """
    from enso_tracker.connectors.markets import PinkSheetConnector as PS
    from openpyxl import Workbook
 
    wb = Workbook()
    ws = wb.active
    ws.title = PS.SHEET
    ws.append(["World Bank Commodity Price Data (The Pink Sheet)"])
    ws.append([])
    ws.append([None, "Maize", "Crude oil, WTI", "Bananas, Europe"])
    ws.append([None, "($/mt)", "($/bbl)", "($/kg)"])
    ws.append(["2026M05", 210.0, 71.2, 1.1])
    ws.append(["2026M06", 214.5, 69.8, 1.2])
    path = tmp_path / "pink.xlsx"
    wb.save(path)
 
    cfg = get_config()
    conn = PS("worldbank_pinksheet", cfg.sources["markets"]["worldbank_pinksheet"], cfg)
    long, seen = conn._parse(path.read_bytes())
 
    assert seen == ["Bananas, Europe", "Crude oil, WTI", "Maize"]
    assert not any("Unnamed" in s for s in seen)
    assert len(long) == 6
 
 
def test_pinksheet_failure_names_the_columns_it_actually_saw(tmp_path):
    """This connector cannot be reached from the sandbox, so its error message
    is the only debugging channel it has. It must carry the evidence."""
    from enso_tracker.connectors.base import SourceUnavailable
    from enso_tracker.connectors.markets import PinkSheetConnector as PS
    from openpyxl import Workbook
 
    wb = Workbook()
    ws = wb.active
    ws.title = PS.SHEET
    ws.append([None, "Bananas, Europe", "Shrimp, Mexican"])
    ws.append(["2026M06", 1.2, 14.0])
    path = tmp_path / "pink.xlsx"
    wb.save(path)
 
    cfg = get_config()
    conn = PS("worldbank_pinksheet", cfg.sources["markets"]["worldbank_pinksheet"], cfg)
    long, seen = conn._parse(path.read_bytes())
    wanted = conn._lookup()
    assert all(conn._resolve(s, wanted) is None for s in seen)
 
    # and the message the operator would receive
    with pytest.raises(SourceUnavailable) as err:
        raise SourceUnavailable(
            f"parsed {len(seen)} price columns but none matched.\n"
            f"  workbook labels: {', '.join(seen)}"
        )
    assert "Bananas, Europe" in str(err.value)
 
 
def test_publish_gate_names_critical_sources_not_just_a_count():
    """The gate that passed the bad run asked 'did any source go live?'.
 
    Three had, so it passed. None of the three was the headline.
    """
    import re
    from pathlib import Path
 
    # The tracker lives at exercises/el-nino/ in the published repo while the
    # workflow lives at the repo root, and CI runs pytest from the tracker
    # directory. A relative path passed here and failed there -- which is the
    # same class of mistake as every other bug in this file: something that was
    # true about my machine, asserted as if it were true about the world.
    here = Path(__file__).resolve()
    gate_path = next(
        (p for p in here.parents if (p / ".github/workflows/publish.yml").is_file()),
        None,
    )
    if gate_path is None:
        pytest.skip("workflow not present in this checkout")
    gate = (gate_path / ".github/workflows/publish.yml").read_text()
    assert 'CRITICAL = ["oni_observed", "weekly_nino"]' in gate
    # It must ask both questions. Age alone was the second wrong answer: CPC's
    # ONI table can run months behind while the connector reads it perfectly,
    # and an age-only gate withholds the page for the publisher's lateness.
    assert "observation_age_h" in gate
    assert re.search(r'startswith\("live"\)', gate)
    assert "ABSURD_DAYS" in gate
    # Limits come from the config the dashboard alarm already uses, so the two
    # cannot disagree about what "too old" means.
    assert re.search(r'yaml\.safe_load\(open\("config/thresholds\.yaml"\)\)', gate)
    # And it must read provenance from either field, so that a partially
    # applied update behaves the same as a complete one. Three runs were lost
    # to a repo that was half old and half new.
    assert "dataset_provenance" in gate and "STATE_KEY" in gate
 
 
def test_oni_age_limit_accommodates_a_healthy_monthly_release(cfg):
    """An ONI season is centred on its middle month and released a month after
    the season closes, so a healthy feed peaks around 80 days old. A 75-day
    critical limit would have failed a working source every month."""
    critical_h = cfg.thresholds["alerts"]["observation_age_h"]["oni_observed"]["critical"]
    assert critical_h / 24 >= 85
 
 
def test_payload_states_which_tier_each_dataset_came_from(tmp_path):
    """The gate cannot work off age alone, so the payload has to say where
    each headline dataset was actually resolved from."""
    result = run(offline=True, out=tmp_path)
    prov = result.payload["meta"]["dataset_provenance"]
    assert set(prov) >= {"oni_observed", "weekly_nino"}
    # offline, everything is necessarily a snapshot -- and must say so
    assert all(str(v).startswith("seed:") for v in prov.values())
 
 
def test_oni_connector_reads_both_cpc_layouts():
    """CPC publishes the ONI twice: oni.ascii.txt in long form, ONI_v5.php in
    wide form. The parser was written for the long one and pointed at the wide
    one, so it rejected every row and reported '0 usable rows' -- which reads
    as a dead source rather than a mismatched shape."""
    from enso_tracker.connectors.climate import CPCOniConnector
 
    cfg = get_config()
    conn = CPCOniConnector("cpc_oni", cfg.sources["climate"]["cpc_oni"], cfg)
 
    long_rows, _ = conn._parse_long(
        " SEAS  YR   TOTAL   ANOM\n"
        "  DJF 1950  24.72  -1.53\n"
        "  FMA 2026  27.30   0.13\n"
    )
    assert len(long_rows) == 2
    assert long_rows[-1] == {"season": "FMA", "year": 2026, "sst": 27.30, "oni": 0.13}
 
    wide_rows, _ = conn._parse_wide(
        "<table><tr><th>Year</th><th>DJF</th><th>JFM</th><th>FMA</th></tr>"
        "<tr><td>2026</td><td>-0.37</td><td>-0.14</td><td>0.13</td></tr></table>"
    )
    assert [r["season"] for r in wide_rows] == ["DJF", "JFM", "FMA"]
    assert wide_rows[-1]["oni"] == 0.13
    # the wide layout carries no absolute SST, and must not invent one
    assert all(math.isnan(r["sst"]) for r in wide_rows)
 
 
def test_oni_connector_refuses_to_return_an_empty_frame():
    """Silence would be read as 'no El Nino'."""
    from enso_tracker.connectors.base import SourceUnavailable
    from enso_tracker.connectors.climate import CPCOniConnector
 
    cfg = get_config()
    conn = CPCOniConnector("cpc_oni", cfg.sources["climate"]["cpc_oni"], cfg)
    conn.http_get = lambda url: "<html><body>Service temporarily unavailable</body></html>"
    with pytest.raises(SourceUnavailable) as err:
        conn.fetch()
    assert "0 usable rows" in str(err.value)
    # the message must carry what it actually received, not just that it failed
    assert "Service temporarily unavailable" in str(err.value)
 
 
def test_oni_url_is_the_layout_the_parser_expects():
    cfg = get_config()
    url = cfg.sources["climate"]["cpc_oni"]["url"]
    assert url.endswith("oni.ascii.txt")
