# Architecture

```mermaid
flowchart TB

subgraph CFG["config/ — the only place numbers live"]
  C1["sources.yaml<br/>31 sources, cadence, auth"]
  C2["thresholds.yaml<br/>ONI bands, QC ranges,<br/>elasticities, alert triggers"]
  C3["regions.yaml<br/>41 countries,<br/>teleconnection composites"]
  C4["commodities.yaml<br/>13 commodities,<br/>global production shares"]
  C5["styles.yaml<br/>validated palettes"]
end

subgraph A["STEP A — Ingest & validate"]
  direction TB
  KL["Keyless · live today<br/>CPC ONI · CPC weekly · ERSSTv5<br/>IRI plume · Pink Sheet · Yahoo futures<br/>USDA · FAO/AMIS · IPC"]
  KY["Keyed · dormant until credentialed<br/>Copernicus C3S · ECMWF SEAS5 · ERA5<br/>NMME THREDDS · GFAS · MODIS VHI<br/>CME · ICE · Bursa · Twelve Data"]
  SEED["Seed snapshot<br/>data/seed/observations.json<br/>provenance-tagged, offline floor"]
  QC{"QC suite<br/>range · completeness<br/>percentile order · member count<br/>cross-source agreement"}
  KL --> QC
  KY --> QC
  QC -->|pass| STORE
  QC -->|fail| LASTGOOD["last-good + stale=true"]
  LASTGOOD --> STORE
  SEED -.->|no live, no cache| STORE
end

STORE[("Versioned Parquet store<br/>dataset / run_date / run_id<br/>immutable + _manifest.json<br/>· full audit history ·")]
DUCK["DuckDB analytics layer"]
STORE --> DUCK

subgraph B["STEP B — Recompute"]
  AN["anomaly.py<br/>ONI 3-mo running mean,<br/>era-specific climatology<br/>peak stats, P(record), P(super)"]
  IM["impact.py<br/>composite × (I/2.4)^0.85<br/>yield index · price pressure<br/>fire/drought · fisheries"]
  LIT{{"Literature cross-check<br/>vs published response ranges<br/>→ QC flag beyond 2×"}}
  AN --> IM --> LIT
end

DUCK --> B
CFG -.drives.-> A
CFG -.drives.-> B

subgraph C["STEP C — Payload"]
  PL["dashboard_payload.json<br/>state · plume · analogues<br/>countries · commodities<br/>provenance · QC flags"]
end
B --> C

subgraph D["STEP D — Situation Report"]
  MD["sitrep_YYYY-MM-DD.md<br/>+ PDF via pandoc"]
end

subgraph E["STEP E — Publish & archive"]
  ST["dashboard.html<br/>single file, embedded payload"]
  SL["Streamlit app<br/>live, auto-refresh"]
  AR["output/archive/<br/>timestamped snapshots"]
end

C --> D
C --> E

subgraph F["STEP F — Alerts"]
  AL{"median moved ≥ 0.30 °C?<br/>critical source down?<br/>data stale?<br/>above extrapolation limit?"}
  AL -->|yes| WH["log · webhook ·<br/>sensitivity re-run"]
end
C --> F

subgraph ORCH["Orchestration"]
  PF["Prefect flow<br/>cron 0 */6 * * *"]
  SC["scheduler.py<br/>dependency-free loop"]
  SH["flows/rerun.sh<br/>cron / webhook, flock-guarded"]
end
ORCH ==> A

STATE["output/last_state.json<br/>previous run = baseline<br/>for the Step F comparison"]
C --> STATE
STATE -.baseline.-> F
```

## Why it is shaped this way

**Config is the product.** Every threshold, region, commodity, colour and
cadence lives in `config/`. `src/` contains no magic numbers by policy, and
`config.py` fails loudly at startup if the tree is inconsistent. Adding a
country or a commodity is a YAML append; nothing in the core changes.

**Failure is a state, not an exception.** A dead source produces a stale badge
and an alert, never a crashed run and never a silently substituted number. The
four states — live, cached, seeded, dormant — are all rendered honestly in the
interface, which is why the "sources live" tile is on the front page rather
than buried.

**The store is the audit trail.** Writes are immutable and partitioned by run.
`ParquetStore.as_of()` rebuilds any past state of the dashboard, so the brief's
"treat the previous state as the baseline, preserve full audit history"
requirement is a property of the layout rather than a process anyone has to
follow.

**QC runs against the literature, not just against itself.** Range and
completeness checks catch parser drift. The cross-check against published
response magnitudes catches something more dangerous: a model that is
internally consistent and wrong. It fired on the first build (a +242% palm-oil
response against a published +20–40%) and that is the reason the price chain
looks the way it does now.
