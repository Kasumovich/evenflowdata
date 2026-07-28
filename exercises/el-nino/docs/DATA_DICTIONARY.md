# Data dictionary & update cadence

## 1. Provenance tiers

Every value carries one. The tier is printed in the dashboard hover cards and
in the `provenance` column of every export.

| Tier | Meaning | Where it appears |
|---|---|---|
| `verified` | Read from the cited primary source on the retrieval date. | Observed ONI, weekly Niño indices, CPC alert status, ensemble peak statistics |
| `cached` | A primary-source value held from an earlier read; refreshed verbatim on the next successful live run. | Historical analogue series |
| `reconstructed` | Derived from published *summary* statistics because the underlying series is not publicly distributed. Indicative; never treated as observation. | Monthly ensemble plume, 90th-percentile peak |
| `composite` | Published teleconnection composites scaled to forecast intensity by this system. | All map layers |
| `modelled` | Output of this system's impact/price model. | Commodity table, price pressure, severity ranking |
| `seed:*` | Served from the bundled offline snapshot rather than a live poll. Always flagged stale. | Any dataset when the network or credentials are unavailable |

## 2. Update cadence

Cron expressions are UTC and live in `config/sources.yaml`. "Live today" means
the connector runs with **zero credentials**.

| Source | Connector | Cadence | Auth | Live today | Feeds |
|---|---|---|---|---|---|
| NOAA CPC ONI v5 | `cpc_oni` | `0 15 * * 4` (monthly release window) | none | ✅ | Observed ONI, historical analogues |
| CPC weekly SST indices | `cpc_weekly` | `0 18 * * 1` | none | ✅ | Weekly Niño 1+2/3/3.4/4, alert status |
| NOAA PSL ERSSTv5 Niño 3.4 | `cpc_ersst` | `0 06 5 * *` | none | ✅ | Monthly anomaly, ONI cross-check |
| IRI/CPC plume | `iri_plume` | `0 16 19 * *` | none | ✅ * | Model plume |
| NMME | `nmme_thredds` | `0 00,12 * * *` | optional | ⚠️ needs `xarray` | Per-member Niño 3.4 |
| Copernicus C3S (7 centres) | `cds_seasonal` | `0 00,12 * * *` | key | ✗ | Per-member SST, precip, T2m |
| ECMWF SEAS5 | `cds_seasonal` | `0 00 6 * *` | key | ✗ | Seasonal SST |
| JAMSTEC SINTEX-F | `generic_http_grib` | `0 03 15 * *` | key | ✗ | Niño 3.4 |
| BoM ACCESS-S2 | `generic_http_json` | `0 02 * * 2` | key | ✗ | Niño 3.4 |
| JMA ENSO indices | `generic_http_json` | `0 04 12 * *` | none | ✅ * | Niño 3 |
| CHIRPS v2 monthly | `chirps` | `0 07 8 * *` | none † | ⚠️ needs geo extra | Precipitation layer |
| ERA5 monthly means | `cds_reanalysis` | `0 08 7 * *` | key | ✗ | 1991–2020 baseline |
| SPEIbase | `generic_netcdf` | `0 09 10 * *` | none | ⚠️ needs geo extra | Drought index |
| MODIS NDVI / VHI | `generic_http_json` | `0 10 * * 3` | key | ✗ | Vegetation health |
| CAMS GFAS fire | `cds_atmosphere` | `0 11 * * *` | key | ✗ | Fire radiative power |
| FAO GIEWS | `generic_http_json` | `0 05 * * 1` | none | ✅ * | Country alerts |
| AMIS Market Monitor | `generic_http_json` | `0 05 * * 1` | none | ✅ * | Balances, stock-to-use |
| USDA WASDE | `usda_wasde` | `0 17 12 * *` | none | ✅ * | Production, yield, stocks |
| USDA NASS QuickStats | `generic_http_json` | `0 17 * * 2` | key | ✗ | Yield, area |
| GEOGLAM Crop Monitor | `generic_http_json` | `0 12 5 * *` | none | ✅ * | Crop conditions |
| SENAMHI Peru (ENFEN/ICEN) | `generic_http_json` | `0 13 * * 1` | none | ✅ * | Coastal El Niño index |
| BMKG Indonesia | `generic_http_json` | `0 13 * * 1` | none | ✅ * | Rainfall outlook, hotspots |
| IMARPE anchoveta | `generic_http_json` | `0 14 * * 1` | none | ✅ * | Biomass, quota |
| World Bank Pink Sheet | `worldbank_pinksheet` | `0 12 2 * *` | none | ✅ | Monthly prices, rice/fishmeal/urea |
| Yahoo continuous futures | `yahoo_futures` | `*/30 13-21 * * 1-5` | none | ✅ | Front-month settlements |
| Twelve Data | `generic_http_json` | `*/15 * * * 1-5` | key | ✗ | Intraday backup |
| CME settlements | `generic_http_json` | `0 22 * * 1-5` | key | ✗ | Official settles, forward curve |
| ICE settlements | `generic_http_json` | `0 22 * * 1-5` | key | ✗ | Softs settles |
| Bursa Malaysia FCPO | `generic_http_json` | `0 11 * * 1-5` | key | ✗ | Palm oil |
| IPC / CH | `generic_http_json` | `0 06 3 * *` | none | ✅ * | Food-insecurity phases |
| FEWS NET | `generic_http_json` | `0 06 * * 4` | none | ✅ * | Outlook projections |

\* Keyless and implemented, but the agency publishes bulletins as HTML/PDF
rather than a documented JSON API. The connector reads a JSON endpoint where
one exists and otherwise fails cleanly — the corresponding panel shows
"awaiting source" rather than a fabricated number. Point `url` at a real
endpoint to activate.

† Keyless but requires `rasterio` + `geopandas`; install with
`pip install -r requirements-geo.txt`.

`enso-tracker sources` prints the live/dormant state for your environment.

## 3. Field definitions

### Event state (`payload.state`)

| Field | Unit | Definition |
|---|---|---|
| `current_oni` | °C | Latest CPC ONI: 3-month running mean of Niño 3.4 vs the era-specific 30-year climatology |
| `current_weekly_nino34` | °C | Latest weekly Niño 3.4 anomaly (different averaging window; a >0.6 °C gap raises a QC warning) |
| `category` | — | CPC band: neutral / weak / moderate / strong / very_strong / super |
| `peak_median`, `peak_p10`, `peak_p90` | °C | Ensemble peak statistics |
| `prob_exceed_record` | 0–1 | P(peak > 2.75 °C), the ERSSTv5 monthly record |
| `prob_super` | 0–1 | P(peak ≥ 2.5 °C), fitted normal on the 10th/90th percentiles |
| `days_to_peak` | days | To `peak_centre_estimate` |
| `confidence_multiplier` | 0–1 | 0.65 in the extrapolation regime, 1.0 below it |

### Country layers (`payload.countries`)

| Field | Unit | Definition |
|---|---|---|
| `precip_djf`, `precip_mam` | % | Departure from the 1991–2020 seasonal total, national mean |
| `temp_djf` | °C | Departure from 1991–2020 |
| `yield_index` | % | Exposure-weighted crop yield deviation. **Signed** — positive means a yield gain |
| `fire_drought` | 0–100 | Fire/drought load, normalised across the modelled set (not clipped) |
| `price_pressure` | 0–100 | How hard this country's exposure pushes world prices; elasticity- and stock-weighted |
| `fisheries` | % | Biomass anomaly. Peru and Chile only |
| `confidence` | 0–1 | Teleconnection reliability prior × extrapolation penalty |

> **National means hide sub-national extremes.** Peru is the sharpest case: the
> national DJF figure is a fraction of what coastal Piura and Lambayeque
> experience, where 1997–98 delivered order-of-magnitude rainfall departures.
> Read the hazard list, not just the average.

### Commodities (`payload.commodities[scenario]`)

| Field | Unit | Definition |
|---|---|---|
| `production_shock_pct` | % | **Global**-production-weighted yield response, using `production_shares` |
| `price_response_pct` | % | `−shock × elasticity × stock_modifier × thin_market_multiplier` |
| `stock_modifier` | ×  | Bounded [0.70, 1.60]; deep stocks damp, thin stocks amplify |
| `coverage` | 0–1 | Share of world production the model has an **actual response** for |
| `listed_share` | 0–1 | Share it can *name*. The gap is supply it knows exists but cannot model |
| `transmission` | — | `crop` or `fisheries` — which chain the commodity routes through |
| `top_contributors` | ISO-3 | Three largest negative contributions |
| `lag_months` | months | SST peak → price peak |

## 4. Method

**ONI vs impact baseline.** ONI uses CPC's era-specific climatology so peak
intensities compare directly with the historical record. Impact layers use a
fixed 1991–2020 base so they stay comparable across regions. Mixing the two is
the most common way to manufacture a spurious 0.2–0.3 °C step between runs and
trip the Step F alert for no physical reason — hence they are kept separate.

**Intensity scaling.** `factor = (intensity / 2.4) ** 0.85`. Sub-linear because
teleconnection responses saturate: you cannot dry below zero rainfall. At
3.6 °C the factor is ≈1.41.

**Fisheries chain.** Fishmeal does not use the crop pipeline. A fish stock
*depletes* rather than yielding less, so survival is raised to the intensity
factor: `survival(I) = 0.21 ** factor(I)`. The 0.21 is an observation — 1997–98
took Peruvian anchoveta from 5.8 Mt to 1.2 Mt at ONI 2.4. Biomass then passes
through a quota decision (1:1, as observed in 1997–98: biomass −79%, catch
−78%) and a rendering yield (pass-through) before reaching price. The
elasticity of 2.0 is anchored: a ~40% fall in global fishmeal production
coincided with an ~80% price rise. Lag is 3 months, not 6 — there are no
carryover stocks to absorb the shock.

**Elasticity calibration.** Elasticities are set so the one El Niño price
response that could be attributed to a citable source — palm oil, +20–40% at a
six-month lag — falls inside the modelled band. `qc.sanity_vs_literature`
raises a flag on any commodity that departs from its published anchor by more
than 2×.

**Crop sensitivity ceilings.** Set in `scripts/calibrate_sensitivities.py` and
anchored on observed losses. Perennials (palm, coffee, cocoa, rubber, tea) are
buffered and lagged; rainfed cereals in strong-teleconnection zones carry the
genuine tail. Re-run the script after editing the ceilings.

## 4b. Data age

Every dataset declares a `valid_time` — the date its newest measurement refers
to, which is *not* when it was fetched. The dashboard's Method tab shows both
clocks side by side. Current per-dataset limits live in
`config/thresholds.yaml` under `alerts.observation_age_h`; `historical_analogs`
is `null` because closed events do not age.

## 5. Known limitations

1. **The forecast amplitude is outside the observational record.** Model skill
   above ~3 °C in Niño 3.4 is unverified by construction, and composite-based
   impact estimates at that amplitude are extrapolation. The sign of the
   responses is well established; the magnitude is not.
2. **Map layers are composite-derived, not downscaled.** They become
   model-derived when the C3S/ERA5/CHIRPS connectors are credentialed.
3. **The monthly plume is reconstructed** from published summary statistics.
   Per-member data replaces it verbatim on the first credentialed NMME/C3S run.
4. **West African cocoa carries a deliberately low confidence** (~0.47) because
   the ENSO teleconnection there barely clears noise. It is included for
   completeness, and the interface says so.
5. **Rice is policy-dominated.** Roughly 10% of production crosses borders, so
   a single Indian export restriction has historically moved world prices more
   than a full monsoon failure. The weather-driven estimate is a floor, not a
   forecast.
6. **The choropleth fetches country topology from the Plotly CDN** at render
   time. Everything else in the static export is self-contained.
