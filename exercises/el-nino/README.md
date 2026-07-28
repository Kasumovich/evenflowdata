# El Niño 2026–27 Tracker

A COVID-dashboard-style tracking system for the forecast record-strength
2026–27 El Niño and its agricultural and commodity exposure.

Ingests climate, agriculture, market and food-security sources on fixed
cadences; recomputes anomaly and impact layers with a fixed, documented
methodology; publishes an interactive dashboard, sortable data tables, and an
automated Situation Report; and keeps a complete audit history so any past
state can be rebuilt.

```bash
cp .env.example .env
docker compose up -d --build      # → http://localhost:8501
```

No credentials required. Everything below runs on free, keyless sources.

---

## The forecast this tracks

Verified against primary sources on 2026-07-26 before any code was written:

| | |
|---|---|
| Ensemble peak Niño 3.4 (median) | **3.6 °C** |
| 80% interval | 2.8 – 4.4 °C (upper bound reconstructed) |
| Models / members | 14 / 667 — 6 NMME, 7 C3S, SINTEX-F |
| P(exceeding the 2.75 °C record) | ~91% |
| Peak window | Nov 2026 – Jan 2027 |
| Observed ONI, MAM 2026 | +0.5 °C |
| CPC status | El Niño Advisory; 63% chance of a very strong event in NDJ |
| Records to beat | 2.2 °C (1982–83) · 2.4 °C (1997–98) · 2.8 °C (2015–16) |

Sources: [CPC ENSO Diagnostic Discussion](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.pdf),
[CPC ONI v5 table](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/ensostuff/ONI_v5.php),
[Hausfather, *The Climate Brink*](https://www.theclimatebrink.com/p/the-strongest-el-nino-ever),
[Yale Climate Connections](https://yaleclimateconnections.org/2026/07/this-could-be-the-strongest-el-nino-on-record/).

**No El Niño of this amplitude has ever been observed.** Model skill above
~3 °C in Niño 3.4 is unverified by construction, and every impact estimate at
that amplitude is extrapolation from composites calibrated on 2.4 °C events.
The system says so, loudly and in the interface — above the configured
threshold every impact layer carries a 35% confidence penalty and the header
raises an EXTRAPOLATION REGIME alert.

---

## What it does

**Header** — live intensity gauge (observed ONI, the record marker, the
forecast, with printed band labels so colour is redundant), days to peak,
P(record), P(super event), and an honest source-health tile.

**Overview** — ensemble plume with its 80% interval against the three
strongest events in the record, aligned by event phase, on one axis.

**Global map** — seven selectable layers over 41 countries: precipitation
anomaly (DJF and MAM), temperature anomaly, agricultural yield impact index,
commodity price pressure, fire/drought risk, fisheries biomass anomaly. Peru
and Indonesia pinned by default; click any country to pin it.

**Regional deep-dive** — per-country cards with metrics, hazard tags,
confidence, and a written note on the transmission mechanism where one is
worth stating.

**Commodities** — global-production-weighted production shock and modelled
price response across 13 commodities, under base / severe / benign scenarios.

**Data table** — every modelled region, sortable and filterable, exportable to
CSV and JSON.

**Method & provenance** — provenance tiers, the extrapolation problem, the
attributable anchors the model is calibrated against, and an explicit table of
figures the system *could not* verify.

Plus: dark and light mode, mobile-responsive, shareable date-stamped snapshot
URLs, a table view behind every chart, and an automated Situation Report in
Markdown and PDF.

---

## Honest accounting

This section exists because a dashboard that hides its own limits is worse
than no dashboard.

**Figures in the commissioning brief that could not be attributed.** Carried in
`data/seed/observations.json` under `unverified_prompt_assertions`, surfaced in
the Method tab and in every Situation Report, and excluded from all headline
numbers:

| Assertion | Status |
|---|---|
| $14 tn global food system | unattributed |
| ~3.5% multi-year maize/wheat/soy yield loss, ~$20 bn/yr | unattributed |
| 10–50% general 2027 price shocks | unattributed |
| **50–100%+ for rice, palm oil, coffee** | unattributed, **and in conflict** with the only sourced El Niño price response found (palm oil, +20–40% at a six-month lag) by roughly 2–3× |
| 2027 at ~1.8 °C above pre-industrial | partially corroborated — "warmest on record by a sizable margin" is sourced; the specific figure is not |

Attribution is re-attempted on every run and anything that resolves is
promoted automatically.

**A finding that cuts against the narrative.** The model's own output says
soybeans and soybean oil face *downward* price pressure, because Argentina and
southern Brazil are reliable El Niño beneficiaries in row crops. That is not a
bug and it has not been suppressed. A model that only produces losses is a
narrative, not a risk model.

**Seven bugs this build shipped and then fixed.** Each was caught by the
system's own QC, by looking at the rendered output, or by a reader asking why
something looked the way it did. All seven now have regression tests:

1. The commodity roll-up weighted each country's response by that crop's share
   of *the country's own* agriculture, which treated Indonesia and Malaysia as
   if they were the world — producing a 27% global palm shortfall, larger than
   any in the crop's history. Fixed with explicit global production shares.
2. The price chain multiplied elasticity by an unbounded stock term by a
   concentration term, compounding to a +242% palm response against a published
   +20–40%. The literature cross-check caught it; the stock modifier is now
   bounded.
3. Run IDs were a hash of the timestamp, so two runs inside the same second
   collided and the second silently overwrote the first's archived snapshot —
   losing audit history, which is the one thing the store exists to guarantee.
4. The plume chart plotted analogue step *i* at forecast month *i*, putting the
   1982–83 / 1997–98 / 2015–16 peaks in spring 2027 and making the forecast
   appear to peak months earlier than every historical event. ENSO is
   seasonally phase-locked — they all peak in Nov–Jan. The axis is now
   calendar-aligned (DJF→Jan … NDJ→Dec), which reveals the thing that is
   genuinely unprecedented here: not the timing, the **ramp rate**.
5. Every alert rendered identically as red "Critical," and the matcher couldn't
   tell a source that had *never been configured* from one that had *gone
   down*. A fresh deployment showed two meaningless reds on day one — textbook
   alert fatigue on a system whose entire job is flagging change. Alerts now
   carry three tiers (`incident` / `standing` / `configuration`), only
   incidents reach the webhook, and a never-configured source raises none.
6. **Staleness was measured from the wrong clock.** It used the store *write*
   time, so any value served from the seed snapshot or last-good cache reported
   0 h age — and re-running every six hours kept resetting the very clock the
   alarm was watching. The stale-data alert was structurally incapable of
   firing. Every dataset now carries a `valid_time` (when the measurement was
   *made*), thresholds are per-dataset because weekly and monthly products age
   at different rates, and a missing `valid_time` reports *unknown* rather than
   *fresh*. The dashboard immediately raised two real incidents it had been
   silent about: the seeded ONI is 104 days old and the weekly Niño reading 47.
7. Fishmeal borrowed the crop pipeline. Its "yield sensitivity" was a raw
   biomass anomaly scaled *linearly* — unphysical, since depletion compounds
   and linear scaling runs past −100%. Worse, `coverage` summed every country
   listed in `production_shares` whether or not the model had a response for
   it, so a estimate resting on ~32% of world supply advertised 57% coverage.
   Fishmeal now routes through an explicit fisheries chain (biomass → quota →
   landings → meal) with survival raised to the intensity factor, and coverage
   counts only modelled supply — which corrected five other commodities too.

**Live data.** This build was assembled in an environment with no egress to the
data providers, so the bundled snapshot is what you see until the first
connected run. Every value in it is provenance-tagged and every connector is
written against the real endpoint. `enso-tracker sources` shows exactly what
will go live on your machine; `enso-tracker run` promotes seeded values to
live ones automatically.

---

## Command line

```
enso-tracker run          Steps A-F
enso-tracker run --offline  Same, no network — seed + last-good only
enso-tracker report       Step D: Situation Report (Markdown + PDF)
enso-tracker export       Standalone HTML dashboard
enso-tracker all          run + report + export — what cron calls
enso-tracker sources      Which sources are live vs dormant here
enso-tracker validate     Config validation, for CI
```

## Layout

```
config/           sources · thresholds · regions · commodities · styles
src/enso_tracker/
  config.py       loading + startup validation
  store.py        versioned Parquet store, DuckDB layer, as_of() replay
  qc.py           range · completeness · ensemble · literature cross-check
  pipeline.py     Steps A-F
  scheduler.py    dependency-free refresh loop
  connectors/     base · climate · markets · keyed · seed
  model/          anomaly.py · impact.py
  report/         sitrep.py
  dashboard/      app.py (Streamlit) · static_export.py (single-file HTML)
flows/            prefect_flows.py · rerun.sh
scripts/          calibrate_sensitivities.py
data/seed/        provenance-tagged offline snapshot
docs/             ARCHITECTURE · DATA_DICTIONARY · DEPLOYMENT · sitreps/
tests/            47 tests, including regressions for all seven bugs above
```

## Extending

Adding a country, commodity, source or map layer is a YAML edit plus, at most,
one registered class. `src/` contains no hard-coded thresholds, regions,
commodities or colours by policy. See
[DEPLOYMENT.md](docs/DEPLOYMENT.md#extending-without-touching-core-code).

## Colour

The categorical order in `config/styles.yaml` was machine-validated in both
light and dark modes for colourblind separation, chroma and contrast — not
chosen by eye. Light mode has three slots below 3:1 contrast, so the relief
rule applies: every series is directly end-labelled and every chart has a
table-view twin. Diverging layers use two opposite-temperature poles with a
neutral gray midpoint; magnitude layers use a single hue, light to dark.

## Licence

MIT.
