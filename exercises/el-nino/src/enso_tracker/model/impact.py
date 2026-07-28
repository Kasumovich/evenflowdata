"""Step B, part 2: impact layers.

What this module is
-------------------
A transparent, auditable scaling of published teleconnection composites by
forecast event intensity. It is deliberately simple, because the honest
constraint here is that **no El Nino of the forecast amplitude has ever been
observed**, so a more elaborate statistical model would add precision without
adding accuracy.

The chain, for each country and layer:

    composite response (calibrated at ONI ~2.4)
      x (event_intensity / 2.4) ** damping        <- sub-linear, saturating
      x scenario multiplier
      -> layer value, carrying a confidence that is penalised above the
         extrapolation threshold

What this module is not
-----------------------
It is not a dynamical downscaling and it does not pretend to be. Every value
it produces is labelled ``composite`` in the UI, and switches to
``model-derived`` only when the credentialed gridded connectors are active.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import Config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intensity scaling
# ---------------------------------------------------------------------------

def intensity_factor(cfg: Config, event_intensity: float,
                     scenario_multiplier: float = 1.0) -> float:
    """Sub-linear scaling from the composite reference intensity.

    Damping < 1 encodes the physical expectation that teleconnection responses
    saturate: doubling the SST anomaly does not double Indonesian rainfall
    deficit, because you cannot dry below zero rainfall. With the configured
    damping of 0.85, a 3.6 degC event gives a factor of ~1.39 on a composite
    calibrated at 2.4 degC -- a meaningful amplification, not a runaway one.
    """
    im = cfg.thresholds["impact_model"]
    ref = float(im["reference_intensity_degC"])
    damping = float(im["intensity_damping"])
    ratio = max(event_intensity, 0.0) / ref
    return float(ratio ** damping) * float(scenario_multiplier)


def fisheries_response(cfg: Config, biomass_anom_pct: float, factor: float) -> dict[str, float]:
    """Biomass -> landings -> meal, as a saturating depletion chain.

    Why this is not the crop pipeline
    ---------------------------------
    A crop yield response scales roughly linearly with stress over the observed
    range. A fish stock does not: it *depletes*, and depletion compounds. The
    previous version multiplied a biomass anomaly by the intensity factor, which
    is unphysical -- push it hard enough and biomass falls by more than 100%.
    It also silently equated biomass with saleable meal, skipping the quota
    decision and the rendering step where the real transmission happens.

    Here, survival is raised to the intensity factor::

        survival(I) = survival_ref ** factor(I)

    At the reference intensity this reproduces the observation exactly (1997-98:
    5.8 Mt -> 1.2 Mt, survival 0.21). Above it the loss keeps growing but
    asymptotes toward total collapse instead of running through it -- which is
    both correct and considerably less alarmist than the linear form.

    Returns the whole chain, not just the endpoint, so the dashboard can show
    where the number comes from.
    """
    spec = cfg.thresholds["fisheries"]
    survival_ref = max(min(1.0 + float(biomass_anom_pct) / 100.0, 1.0), 1e-6)
    survival = survival_ref ** factor

    biomass = (survival - 1.0) * 100.0
    landings = biomass * float(spec["quota_transmission"])
    meal = landings * float(spec["meal_yield_transmission"])

    return {
        "biomass_pct": float(np.clip(biomass, -99.9, 100.0)),
        "landings_pct": float(np.clip(landings, -99.9, 100.0)),
        "meal_pct": float(np.clip(meal, -99.9, 100.0)),
        "survival_fraction": float(survival),
    }


def layer_confidence(cfg: Config, base_confidence: float,
                     event_intensity: float) -> float:
    im = cfg.thresholds["impact_model"]
    if event_intensity > float(im["extrapolation_warning_degC"]):
        return float(base_confidence * (1.0 - float(im["confidence_penalty_above_warning"])))
    return float(base_confidence)


# ---------------------------------------------------------------------------
# Country layers
# ---------------------------------------------------------------------------

@dataclass
class LayerSpec:
    key: str
    label: str
    unit: str
    scale: str
    source_field: str | None = None


LAYERS: list[LayerSpec] = [
    LayerSpec("precip_djf", "Precipitation anomaly, DJF 2026-27", "%", "precip_div", "precip_djf"),
    LayerSpec("precip_mam", "Precipitation anomaly, MAM 2027", "%", "precip_div", "precip_mam"),
    LayerSpec("temp_djf", "Temperature anomaly, DJF 2026-27", "degC", "temp_div", "temp_djf"),
    LayerSpec("yield_index", "Agricultural yield impact index", "%", "yield_div"),
    LayerSpec("price_pressure", "Commodity price pressure index", "index", "risk_seq"),
    LayerSpec("fire_drought", "Fire / drought risk", "0-100", "risk_seq"),
    LayerSpec("fisheries", "Fisheries biomass anomaly", "%", "fisheries"),
]


def build_country_layers(
    cfg: Config, event_intensity: float, scenario: str = "base"
) -> pd.DataFrame:
    """One row per country, one column per map layer."""
    scenarios = cfg.commodities.get("scenarios", {})
    multiplier = float(scenarios.get(scenario, {}).get("intensity_multiplier", 1.0))
    factor = intensity_factor(cfg, event_intensity, multiplier)

    rows: list[dict[str, Any]] = []
    for country in cfg.country_list:
        base_conf = float(country.get("confidence", 0.6))
        conf = layer_confidence(cfg, base_conf, event_intensity)

        precip_djf = float(country.get("precip_djf", 0.0)) * factor
        precip_mam = float(country.get("precip_mam", 0.0)) * factor
        temp_djf = float(country.get("temp_djf", 0.0)) * factor

        # Fire/drought: composite index amplified by intensity and by the dry
        # anomaly itself. Left on its raw scale here and normalised across the
        # whole country set below -- clipping at 100 saturated half a dozen
        # countries into a tie and destroyed the ordering the layer exists to
        # show.
        dry_bonus = max(0.0, -precip_djf) / 100.0
        fire = float(country.get("fire_risk", 0.0)) * factor * (1.0 + 0.6 * dry_bonus)

        yield_index = _country_yield_index(country, factor)

        fisheries = None
        fisheries_chain = None
        if country.get("fisheries"):
            fisheries_chain = fisheries_response(
                cfg, country["fisheries"]["biomass_anom_pct"], factor
            )
            fisheries = round(fisheries_chain["biomass_pct"], 1)

        rows.append({
            "iso3": country["iso3"],
            "name": country["name"],
            "group": country["group"],
            "group_label": cfg.regions["groups"][country["group"]]["label"],
            "precip_djf": round(precip_djf, 1),
            "precip_mam": round(precip_mam, 1),
            "temp_djf": round(temp_djf, 2),
            "fire_drought_raw": fire,
            "yield_index": round(yield_index, 2),
            "fisheries": fisheries,
            "fisheries_landings": (
                None if fisheries_chain is None
                else round(fisheries_chain["landings_pct"], 1)
            ),
            "fisheries_species": (country.get("fisheries") or {}).get("species"),
            "confidence": round(conf, 2),
            "hazards": country.get("hazards", []),
            "notes": (country.get("notes") or "").strip(),
            "pinned": country["iso3"] in cfg.pinned,
            "provenance": "composite",
        })

    df = pd.DataFrame(rows)

    # Normalise the fire/drought index onto 0-100 across the modelled set, so
    # the layer preserves rank order instead of saturating at the ceiling.
    peak = float(df["fire_drought_raw"].max())
    df["fire_drought"] = (
        (df["fire_drought_raw"] / peak * 100.0).round(1) if peak > 0 else 0.0
    )
    df = df.drop(columns=["fire_drought_raw"])

    df["price_pressure"] = _country_price_pressure(cfg, df, event_intensity, multiplier)
    return df


def _country_yield_index(country: dict[str, Any], factor: float) -> float:
    """Exposure-weighted yield deviation, in percent of the 1991-2020 baseline.

    Positive values mean a yield *gain* -- Argentina and the US southern Plains
    are genuine El Nino beneficiaries and the index must be able to say so.
    A model that only produces losses is not a risk model, it is a narrative.
    """
    crops = country.get("crops") or {}
    if not crops:
        return 0.0
    total = 0.0
    for spec in crops.values():
        exposure = float(spec.get("exposure", 0.0))
        sensitivity = float(spec.get("sensitivity", 0.0))
        total += exposure * sensitivity * factor * 100.0
    return float(np.clip(total, -80.0, 40.0))


def _country_price_pressure(
    cfg: Config, df: pd.DataFrame, event_intensity: float, multiplier: float
) -> pd.Series:
    """0-100 index: how much this country's exposure pushes on world prices.

    Weighted by production share proxy (crop exposure), the commodity's
    elasticity, and inversely by its stock-to-use buffer. A country that grows
    a lot of something the world holds no stocks of scores high.
    """
    elasticity = cfg.thresholds["price_pressure"]["elasticity"]
    use_buffer = bool(cfg.thresholds["price_pressure"].get("stock_to_use_buffer", True))
    commodities = cfg.commodity_index
    country_index = cfg.country_index

    values = []
    for iso3 in df["iso3"]:
        country = country_index[iso3]
        score = 0.0
        for crop, spec in (country.get("crops") or {}).items():
            meta = commodities.get(crop)
            if meta is None or meta.get("context_only"):
                continue
            group = meta.get("group", "grains")
            elast = float(elasticity.get(crop, elasticity.get(group, 1.5)))
            shortfall = -float(spec.get("sensitivity", 0.0)) * float(spec.get("exposure", 0.0))
            if shortfall <= 0:
                continue        # a yield gain relieves rather than adds pressure
            stu = meta.get("stock_to_use")
            buffer = 1.0
            if use_buffer and stu:
                buffer = float(np.clip(1.0 / (float(stu) + 0.15), 0.5, 3.0))
            concentration = 1.0 + float(meta.get("concentration_hhi", 0.0))
            score += shortfall * elast * buffer * concentration
        values.append(score)

    raw = pd.Series(values, index=df.index, dtype=float)
    scaled = raw * intensity_factor(cfg, event_intensity, multiplier)
    if scaled.max() > 0:
        scaled = scaled / scaled.max() * 100.0
    return scaled.round(1)


# ---------------------------------------------------------------------------
# Commodity roll-up
# ---------------------------------------------------------------------------

def _stock_modifier(cfg: Config, stock_to_use: float | None) -> float:
    """Bounded stock-cover adjustment to elasticity.

    Bounded deliberately. The predecessor of this function multiplied an
    unbounded ``1/stock_to_use`` term by a concentration term by the elasticity,
    and the three compounded into a 242% modelled palm-oil response against a
    published 20-40%. The QC literature cross-check caught it; these bounds
    are the structural fix.
    """
    spec = cfg.thresholds["price_pressure"]["stock_modifier"]
    if not stock_to_use:
        return 1.0
    raw = float(spec["numerator"]) / (float(stock_to_use) + float(spec["offset"]))
    return float(np.clip(raw, float(spec["min"]), float(spec["max"])))


def build_commodity_table(
    cfg: Config, event_intensity: float, scenario: str = "base"
) -> pd.DataFrame:
    """Per-commodity GLOBAL production shock and implied price response.

    The shock is a **global-production-weighted** sum of country yield
    responses, using the explicit shares in ``config/commodities.yaml``. The
    price response is that shock times a commodity elasticity, times a bounded
    stock-cover modifier, times a thin-market multiplier where trade is a small
    share of output.

    ``coverage`` reports how much of world production the modelled countries
    actually span, so a reader can see when a shock estimate rests on a partial
    view of supply rather than assuming it spans the whole market.
    """
    pp = cfg.thresholds["price_pressure"]
    scenarios = cfg.commodities.get("scenarios", {})
    multiplier = float(scenarios.get(scenario, {}).get("intensity_multiplier", 1.0))
    factor = intensity_factor(cfg, event_intensity, multiplier)
    elasticity = pp["elasticity"]
    lag = int(pp["transmission_lag_months"])
    thin_mult = float(pp.get("thin_market_multiplier", 1.0))
    shares_all = cfg.commodities.get("production_shares", {})
    countries = cfg.country_index

    rows: list[dict[str, Any]] = []
    for key, meta in cfg.commodity_index.items():
        if meta.get("context_only"):
            continue

        shares = shares_all.get(key, {})
        fisheries_route = meta.get("transmission") == "fisheries"
        shock = 0.0
        covered = 0.0
        listed = 0.0
        contributors: list[tuple[str, float]] = []

        for iso3, share in shares.items():
            listed += float(share)
            country = countries.get(iso3)
            if country is None:
                continue

            if fisheries_route:
                block = country.get("fisheries")
                if not block:
                    # Listed in the global denominator but with no modelled
                    # response -- e.g. Chinese fishmeal is largely rendering of
                    # imported raw material and does not answer to a Peruvian
                    # El Nino. It must NOT count toward coverage.
                    continue
                chain = fisheries_response(cfg, block["biomass_anom_pct"], factor)
                contribution = float(share) * chain["meal_pct"]
            else:
                sensitivity = _country_sensitivity(country, key)
                if sensitivity is None:
                    continue
                contribution = float(share) * sensitivity * factor * 100.0

            # Coverage counts only what the model can actually answer for.
            # Counting merely-listed countries made a 25%-modelled estimate
            # look like 57% coverage -- reassuring, and false.
            covered += float(share)
            shock += contribution
            contributors.append((iso3, contribution))

        group = meta.get("group", "grains")
        elast = float(elasticity.get(key, elasticity.get(group, 1.5)))
        stu = meta.get("stock_to_use")
        modifier = _stock_modifier(cfg, stu)
        thin = bool(meta.get("thin_market", False))

        price_response = -shock * elast * modifier * (thin_mult if thin else 1.0)

        contributors.sort(key=lambda t: t[1])
        rows.append({
            "commodity": key,
            "label": meta.get("label", key),
            "group": group,
            "exchange": meta.get("exchange"),
            "unit": meta.get("unit"),
            "stock_to_use": stu,
            "production_shock_pct": round(shock, 2),
            "price_response_pct": round(price_response, 1),
            "elasticity": elast,
            "stock_modifier": round(modifier, 2),
            "lag_months": int(meta.get(
                "lag_months",
                cfg.thresholds["fisheries"]["lag_months"] if fisheries_route else lag,
            )),
            "coverage": round(covered, 2),
            "listed_share": round(listed, 2),
            "transmission": meta.get("transmission", "crop"),
            "top_contributors": [iso3 for iso3, _ in contributors[:3]],
            "exposed_countries": sorted(shares),
            "thin_market": thin,
            "scenario": scenario,
            "provenance": "modelled",
        })

    df = pd.DataFrame(rows)
    return df.sort_values("price_response_pct", ascending=False).reset_index(drop=True)


def _country_sensitivity(country: dict[str, Any], commodity: str) -> float | None:
    """Yield sensitivity of one country for one commodity.

    Crops come from the country's ``crops`` block. Fishmeal is the exception:
    it is not a crop, so it is read off the fisheries biomass anomaly, which is
    the physically correct channel and the fastest-transmitting one in the
    whole food complex.
    """
    if commodity == "fishmeal":
        fisheries = country.get("fisheries")
        if not fisheries:
            return None
        return float(fisheries["biomass_anom_pct"]) / 100.0

    crops = country.get("crops") or {}
    alias = {
        "coffee_arabica": "coffee",
        "coffee_robusta": "coffee",
        "soybean_oil": "soybeans",
    }
    spec = crops.get(commodity) or crops.get(alias.get(commodity, ""))
    if spec is None:
        return None
    return float(spec.get("sensitivity", 0.0))


# ---------------------------------------------------------------------------
# Risk ranking
# ---------------------------------------------------------------------------

def rank_risk_escalations(
    countries: pd.DataFrame, top_n: int = 5
) -> pd.DataFrame:
    """Composite severity ranking used for the Situation Report top-5.

    Severity blends the magnitude of the yield hit, drought/fire load, price
    pressure and fisheries loss, then multiplies by confidence -- so a large
    but poorly-constrained response (West African cocoa) does not outrank a
    moderate, well-established one (Indonesian palm).
    """
    df = countries.copy()
    yield_term = (-df["yield_index"]).clip(lower=0) / 30.0
    fire_term = df["fire_drought"] / 100.0
    price_term = df["price_pressure"] / 100.0
    fish_term = (-df["fisheries"].fillna(0.0)).clip(lower=0) / 60.0

    df["severity"] = (
        0.35 * yield_term + 0.25 * fire_term + 0.25 * price_term + 0.15 * fish_term
    ) * df["confidence"]
    df["severity"] = (df["severity"] * 100).round(1)
    return df.sort_values("severity", ascending=False).head(top_n).reset_index(drop=True)
