# Deployment

## One-command deploy

```bash
cp .env.example .env          # every variable is optional; see below
docker compose up -d --build
```

- Dashboard: <http://localhost:8501>
- The `scheduler` container runs Steps A–F every `ENSO_REFRESH_HOURS` (default 6).
- Static snapshot behind nginx: `docker compose --profile publish up -d` → <http://localhost:8080/dashboard.html>
- Prefect UI with run history and retries: `docker compose --profile orchestration up -d` → <http://localhost:4200>

## Without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[app,orchestration,dev]"

enso-tracker validate     # config sanity
enso-tracker sources      # what is live in your environment
enso-tracker all          # Steps A-F + Situation Report + static export
streamlit run src/enso_tracker/dashboard/app.py
```

Open `output/dashboard.html` directly in a browser for the static snapshot.

## Credentials

**None are required.** With an empty `.env`, the system runs on CPC ONI, the
weekly Niño indices, ERSSTv5, the World Bank Pink Sheet and Yahoo continuous
futures, and produces a complete dashboard.

Adding credentials activates dormant sources with no code change. The order of
value:

1. `CDSAPI_KEY` — Copernicus C3S and ERA5. Free registration. Biggest single
   upgrade: the precipitation and temperature layers switch from
   composite-derived to model-derived and lose the `composite` badge.
2. `ADSAPI_KEY` — CAMS GFAS. Turns the fire layer into observed radiative power.
3. `EARTHDATA_TOKEN` — MODIS NDVI/VHI for the vegetation-stress layer.
4. Exchange keys (`CME_API_KEY`, `ICE_API_KEY`, `BURSA_API_KEY`) — official
   settlements and the forward curve, replacing Yahoo's front-month proxy.

After adding any of them:

```bash
enso-tracker sources      # confirm it flipped to "ready"
enso-tracker all
```

## Scheduling

Three interchangeable options — pick one, do not run two.

**Prefect** (recommended when you want run history and retries):
```bash
python flows/prefect_flows.py --serve
```

**Built-in scheduler** (what `docker compose` uses; no server to stand up):
```bash
ENSO_REFRESH_HOURS=6 python -m enso_tracker.scheduler
```

**Plain cron / webhook:**
```
0 */6 * * *  /opt/enso-tracker/flows/rerun.sh >> /var/log/enso-tracker.log 2>&1
```
`rerun.sh` is `flock`-guarded, so a slow ingest cannot race the next tick.

## Alerting

Alerts carry one of three severities. The tier is the important part: a strip
where a permanent condition looks identical to a missing API key is a strip
people stop reading.

| Tier | Meaning | Reaches the webhook |
|---|---|---|
| `incident` | Something **changed** — median shift ≥ 0.30 °C, a source that used to work has stopped, an observation past its critical age, or the pipeline itself not refreshing. | Yes |
| `standing` | A persistent property of the event that will not clear — currently the extrapolation regime. True every run. | No |
| `configuration` | A capability is switched off — dormant credentials, offline mode, zero live sources. Fixable, not a fault. | No |

### Two clocks

Staleness is measured twice, and the distinction matters:

- **Retrieval age** — how long since the pipeline last spoke to the source.
  Detects a dead scheduler. Governed by `stale_warn_h` / `stale_critical_h`.
- **Observation age** — how old the underlying measurement is, from each
  dataset's declared `valid_time`. Detects a source that answers on time with
  months-old values. Governed by `alerts.observation_age_h`, **per dataset**,
  because a weekly product and a monthly one age at completely different rates.

Only the second one tells you whether what you are reading is current. A
dataset with no declared `valid_time` reports *unknown*, never *fresh* —
"unknown age" and "zero age" are very different claims and the second is the
dangerous one. Set a dataset's limit to `null` to mark it a reference series
that does not age (closed historical events).

A source failure is only an incident if that source has succeeded here before.
`output/source_history.json` records the last good fetch per source and is what
makes that distinction possible — delete it and every failure looks like a
first-time configuration gap.

Set `ENSO_ALERT_WEBHOOK` to any URL accepting
`POST {"alerts": [{severity, code, title, detail}, ...]}`. **Only incidents are
pushed.** Standing and configuration alerts live in the dashboard and the
Situation Report, where they are read deliberately rather than pushed every six
hours until muted.

Thresholds are all in `config/thresholds.yaml`.

## Extending without touching core code

**A new country.** Append a block to `config/regions.yaml` with `iso3`, `name`,
`group`, the four composite fields, `confidence`, `hazards` and a `crops` map
whose exposures sum to 1.0. It appears on the map, in the tables, in the
regional cards and in the commodity roll-up on the next run. `enso-tracker
validate` will tell you if the exposures do not sum.

**A new commodity.** Append to `config/commodities.yaml`, add a
`production_shares` entry, and reference it from at least one country's `crops`.
Optionally set `elasticity` in `config/thresholds.yaml` — otherwise it inherits
its group's.

**A new data source.** Add a block to `config/sources.yaml` and a class:

```python
from enso_tracker.connectors.base import Connector, FetchResult, register

@register("my_source")
class MySourceConnector(Connector):
    dataset = "my_dataset"

    def fetch(self) -> FetchResult:
        payload = self.http_json(self.url)
        return FetchResult(
            dataset=self.dataset,
            frame=pd.json_normalize(payload["data"]),
            source="My Agency",
            source_url=self.url,
        )
```

The orchestrator discovers it by name. Retry, backoff, QC, versioned storage,
staleness tracking and the last-good fallback are inherited.

**A new map layer.** Append a `LayerSpec` to `LAYERS` in
`src/enso_tracker/model/impact.py` and add the field to `build_country_layers`.
The selector, colour scale, legend, popup card and table view pick it up
automatically.

## Operational notes

- **Restricted egress.** If the host cannot reach the data providers, every
  connector degrades to last-good and then to the bundled seed snapshot. The
  run succeeds, the dashboard renders, and the "sources live" tile plus the
  stale badges make the situation obvious. Verify with
  `enso-tracker run --offline`.
- **Disk.** The store is immutable and grows with every run. Roughly 2 MB/run
  on the keyless source set; the gridded layers change that by orders of
  magnitude, at which point point `ENSO_STORE_DIR` at object storage.
- **Rebuilding a past state.** `ParquetStore.as_of(dataset, when)` returns the
  data exactly as it stood, and `output/archive/payload_<run_id>.json` holds
  the rendered payload for every run.
- **Fully offline export.** `export_dashboard(payload, path, vendor=True)`
  inlines plotly.js (~4.8 MB). The choropleth still fetches country topology
  from the Plotly CDN unless you also pass `topojson_url` pointing at a local
  copy.
