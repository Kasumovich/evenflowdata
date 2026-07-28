"""Prefect orchestration for the systematic execution protocol.

Deploy with::

    python flows/prefect_flows.py            # one-off run
    python flows/prefect_flows.py --serve    # register the 6-hourly schedule

Each step is a task so the Prefect UI shows exactly which one failed and
retries only that step. The pipeline itself is failure-tolerant by design, so
retries here are for infrastructure faults, not for missing data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prefect import flow, get_run_logger, task  # noqa: E402
from prefect.schedules import Cron  # noqa: E402

from enso_tracker.config import output_dir  # noqa: E402
from enso_tracker.dashboard.static_export import export_dashboard  # noqa: E402
from enso_tracker.pipeline import load_previous_state, run  # noqa: E402
from enso_tracker.report.sitrep import write_report  # noqa: E402

REFRESH_HOURS = int(os.environ.get("ENSO_REFRESH_HOURS", "6"))


@task(retries=2, retry_delay_seconds=120, name="Steps A-C+F: ingest, recompute, payload")
def ingest_and_compute(offline: bool = False):
    return run(offline=offline)


@task(retries=1, name="Step D: Situation Report")
def situation_report(payload: dict) -> dict:
    out = output_dir()
    docs = Path(__file__).resolve().parents[1] / "docs" / "sitreps"
    return {k: str(v) for k, v in
            write_report(payload, docs, previous=load_previous_state(out)).items()}


@task(retries=1, name="Step E: publish static export")
def publish(payload: dict) -> str:
    return str(export_dashboard(payload, output_dir() / "dashboard.html"))


@task(name="Step F: dispatch alerts")
def dispatch_alerts(alerts: list) -> int:
    """Push incidents; log everything else.

    Standing conditions are true every single run. Pushing them turns the
    channel into noise and guarantees the one alert that matters gets missed.
    """
    from dataclasses import asdict

    logger = get_run_logger()
    webhook = os.environ.get("ENSO_ALERT_WEBHOOK")
    incidents = [a for a in alerts if getattr(a, "severity", None) == "incident"]

    for alert in alerts:
        emit = logger.warning if alert.severity == "incident" else logger.info
        emit("[%s] %s -- %s", alert.severity, alert.title, alert.detail)

    if incidents and webhook:
        import requests
        try:
            requests.post(
                webhook, json={"alerts": [asdict(a) for a in incidents]}, timeout=20
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("alert webhook failed: %s", exc)
    return len(incidents)


@flow(name="elnino-tracker-refresh", log_prints=True)
def refresh(offline: bool = False) -> dict:
    """Steps A-F end to end."""
    logger = get_run_logger()
    result = ingest_and_compute(offline=offline)

    reports = situation_report(result.payload)
    dashboard = publish(result.payload)
    n_alerts = dispatch_alerts(result.metrics.alerts)

    logger.info(
        "run %s | peak median %.2f degC | %d live / %d seeded / %d dormant / %d failed | %d alerts",
        result.metrics.run_id, result.state.peak_median,
        result.metrics.sources_live, result.metrics.sources_seeded,
        result.metrics.sources_dormant, result.metrics.sources_failed, n_alerts,
    )

    # Step F sensitivity re-run: if the ensemble median moved past the
    # configured threshold, recompute the severe and benign scenarios
    # immediately rather than waiting for the next scheduled cycle.
    if any(a.code == "ensemble_median_shift" for a in result.metrics.alerts):
        logger.warning("median shift breached threshold -- triggering sensitivity re-run")
        ingest_and_compute(offline=offline)

    return {
        "run_id": result.metrics.run_id,
        "peak_median": result.state.peak_median,
        "alerts": [a.text for a in result.metrics.alerts],
        "dashboard": dashboard,
        "reports": reports,
    }


if __name__ == "__main__":
    if "--serve" in sys.argv:
        refresh.serve(
            name="elnino-6h",
            schedule=Cron(f"0 */{REFRESH_HOURS} * * *", timezone="UTC"),
            tags=["enso", "production"],
        )
    else:
        print(refresh(offline="--offline" in sys.argv))
