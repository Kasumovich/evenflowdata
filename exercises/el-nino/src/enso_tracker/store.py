"""Versioned Parquet store + DuckDB analytics layer.

Design
------
Every write is immutable and lands under::

    <store>/<dataset>/run_date=<YYYY-MM-DD>/run_id=<ts>-<hash>/part.parquet

alongside a sidecar ``_manifest.json`` capturing source, retrieval time,
row count, QC verdict and a content hash. Nothing is ever overwritten, so
the full audit history the brief asks for falls out of the layout itself:
any past state of the dashboard can be rebuilt by reading the store as of
a given run.

``latest()`` resolves the newest *passing* version of a dataset, which is
what makes the "last-good + stale flag" fallback work without special
cases in the connectors.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .config import store_dir

log = logging.getLogger(__name__)

MANIFEST = "_manifest.json"


@dataclass
class Manifest:
    dataset: str
    run_id: str
    source: str
    source_url: str | None
    retrieved_utc: str
    rows: int
    columns: list[str]
    content_sha256: str
    qc_passed: bool
    qc_flags: list[str]
    stale: bool
    provenance: str
    valid_time: str | None = None
    reference_series: bool = False
    schema_version: int = 4

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_run_id(seed: str = "") -> str:
    """Timestamp plus a genuinely unique suffix.

    The suffix used to be a hash of the timestamp and seed, which meant two
    runs inside the same second produced the *same* id -- so the second run
    overwrote the first's archived snapshot and the audit history quietly lost
    an entry. uuid4 removes the collision entirely; the timestamp prefix keeps
    ids sortable, which is what it was there for.
    """
    ts = _now().strftime("%Y%m%dT%H%M%SZ")
    unique = uuid.uuid4().hex[:8]
    if seed:
        unique = hashlib.sha256(f"{seed}{unique}".encode()).hexdigest()[:8]
    return f"{ts}-{unique}"


def _frame_hash(df: pd.DataFrame) -> str:
    payload = df.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ParquetStore:
    """Immutable, partitioned, manifest-backed dataset store."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else store_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- write -----------------------------------------------------------

    def write(
        self,
        dataset: str,
        df: pd.DataFrame,
        *,
        source: str,
        source_url: str | None = None,
        provenance: str = "live",
        qc_passed: bool = True,
        qc_flags: Iterable[str] = (),
        stale: bool = False,
        valid_time: str | None = None,
        reference_series: bool = False,
        run_id: str | None = None,
    ) -> Path:
        run_id = run_id or make_run_id(dataset)
        run_date = _now().strftime("%Y-%m-%d")
        part = self.root / dataset / f"run_date={run_date}" / f"run_id={run_id}"
        part.mkdir(parents=True, exist_ok=True)

        target = part / "part.parquet"
        try:
            df.to_parquet(target, index=False)
        except Exception as exc:  # pyarrow absent in a minimal install
            log.warning("Parquet write failed (%s); falling back to CSV", exc)
            target = part / "part.csv"
            df.to_csv(target, index=False)

        manifest = Manifest(
            dataset=dataset,
            run_id=run_id,
            source=source,
            source_url=source_url,
            retrieved_utc=_now().isoformat(),
            rows=int(len(df)),
            columns=[str(c) for c in df.columns],
            content_sha256=_frame_hash(df),
            qc_passed=bool(qc_passed),
            qc_flags=list(qc_flags),
            stale=bool(stale),
            provenance=provenance,
            valid_time=valid_time,
            reference_series=bool(reference_series),
        )
        (part / MANIFEST).write_text(manifest.to_json(), encoding="utf-8")
        log.info("wrote %s rows=%d run_id=%s", dataset, len(df), run_id)
        return target

    # -- read ------------------------------------------------------------

    def versions(self, dataset: str) -> list[tuple[Path, Manifest]]:
        base = self.root / dataset
        if not base.exists():
            return []
        out: list[tuple[Path, Manifest]] = []
        for mpath in sorted(base.glob("run_date=*/run_id=*/" + MANIFEST)):
            try:
                out.append((mpath.parent, Manifest(**json.loads(mpath.read_text()))))
            except Exception as exc:
                log.warning("unreadable manifest %s: %s", mpath, exc)
        return sorted(out, key=lambda t: t[1].retrieved_utc)

    def latest(
        self, dataset: str, *, require_pass: bool = True
    ) -> tuple[pd.DataFrame, Manifest] | None:
        for part, manifest in reversed(self.versions(dataset)):
            if require_pass and not manifest.qc_passed:
                continue
            pq, csv = part / "part.parquet", part / "part.csv"
            if pq.exists():
                return pd.read_parquet(pq), manifest
            if csv.exists():
                return pd.read_csv(csv), manifest
        return None

    def as_of(self, dataset: str, when: datetime) -> tuple[pd.DataFrame, Manifest] | None:
        """Rebuild any historical state -- the audit-history guarantee."""
        cutoff = when.isoformat()
        for part, manifest in reversed(self.versions(dataset)):
            if manifest.retrieved_utc <= cutoff and manifest.qc_passed:
                pq, csv = part / "part.parquet", part / "part.csv"
                if pq.exists():
                    return pd.read_parquet(pq), manifest
                if csv.exists():
                    return pd.read_csv(csv), manifest
        return None

    def staleness_hours(self, dataset: str) -> float | None:
        """Hours since we last *talked to the source*.

        Useful for spotting a dead pipeline, useless for spotting a dead feed:
        a source that answers promptly with three-month-old data scores zero
        here. Use :meth:`observation_age_hours` for that.
        """
        latest = self.latest(dataset)
        if latest is None:
            return None
        retrieved = datetime.fromisoformat(latest[1].retrieved_utc)
        return (_now() - retrieved).total_seconds() / 3600.0

    def observation_age_hours(self, dataset: str) -> float | None:
        """Hours since the underlying measurement was *made*.

        This is the number that matters. Returns None for reference series and
        for datasets whose connector does not declare a valid_time -- a missing
        valid_time is reported as unknown rather than quietly treated as fresh,
        because "unknown age" and "zero age" are very different claims.
        """
        latest = self.latest(dataset)
        if latest is None:
            return None
        manifest = latest[1]
        if manifest.reference_series or not manifest.valid_time:
            return None
        try:
            valid = datetime.fromisoformat(manifest.valid_time)
        except ValueError:
            return None
        if valid.tzinfo is None:
            valid = valid.replace(tzinfo=timezone.utc)
        return max((_now() - valid).total_seconds() / 3600.0, 0.0)


# ---------------------------------------------------------------------------
# DuckDB analytics layer
# ---------------------------------------------------------------------------

def duckdb_view(store: ParquetStore, datasets: Iterable[str]) -> Any:
    """Register every dataset in the store as a DuckDB view.

    Optional dependency: if duckdb is not installed the caller falls back to
    pandas, which is fine at this data volume. DuckDB earns its place once the
    gridded layers (CHIRPS, ERA5) are switched on and the store grows past
    memory.
    """
    try:
        import duckdb
    except ImportError:
        log.info("duckdb not installed; analytics layer falls back to pandas")
        return None

    con = duckdb.connect(database=":memory:")
    for dataset in datasets:
        latest = store.latest(dataset)
        if latest is None:
            continue
        df, _ = latest
        con.register(dataset, df)
    return con
