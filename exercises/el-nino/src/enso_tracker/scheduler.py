"""Dependency-free scheduler for the container that has no Prefect server.

Runs Steps A-F on the configured cadence. Prefect is the better answer when
you want run history, retries and a UI; this exists so ``docker compose up``
gives a working auto-refreshing deployment with nothing else to stand up.

    ENSO_REFRESH_HOURS   cadence, default 6 (the brief asks for 6-12)
    ENSO_ALERT_WEBHOOK   optional POST target for Step F alerts
"""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, timezone

from .config import output_dir
from .dashboard.static_export import export_dashboard
from .pipeline import load_previous_state, run
from .report.sitrep import write_report

log = logging.getLogger("enso_tracker.scheduler")

_stop = False


def _handle_signal(signum, _frame):  # pragma: no cover - signal path
    global _stop
    log.info("received signal %s; finishing current cycle then exiting", signum)
    _stop = True


def _post_alerts(alerts: list) -> None:
    """Post incidents only.

    A webhook that fires every six hours for a standing condition is a webhook
    people mute. Standing and configuration alerts live in the dashboard and
    the Situation Report, where they are read deliberately rather than pushed.
    """
    from dataclasses import asdict

    webhook = os.environ.get("ENSO_ALERT_WEBHOOK")
    incidents = [a for a in alerts if getattr(a, "severity", None) == "incident"]
    if not incidents or not webhook:
        return
    try:
        import requests
        requests.post(webhook, json={"alerts": [asdict(a) for a in incidents]}, timeout=20)
        log.info("posted %d incident(s) to webhook", len(incidents))
    except Exception as exc:  # noqa: BLE001
        log.error("alert webhook failed: %s", exc)


def cycle() -> None:
    out = output_dir()
    previous = load_previous_state(out)
    result = run(out=out)

    try:
        write_report(result.payload, out, previous=previous)
    except Exception as exc:  # noqa: BLE001
        log.error("Situation Report failed (continuing): %s", exc)

    export_dashboard(result.payload, out / "dashboard.html")

    for alert in result.metrics.alerts:
        # Only incidents are worth waking anyone for; the rest are context.
        emit = log.warning if alert.severity == "incident" else log.info
        emit("[%s] %s -- %s", alert.severity, alert.title, alert.detail)
    _post_alerts(result.metrics.alerts)

    log.info(
        "run %s complete | peak median %.2f degC | %d live / %d seeded / %d dormant",
        result.metrics.run_id, result.state.peak_median,
        result.metrics.sources_live, result.metrics.sources_seeded,
        result.metrics.sources_dormant,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    hours = float(os.environ.get("ENSO_REFRESH_HOURS", "6"))
    interval = max(hours, 1.0) * 3600.0
    log.info("scheduler starting; cadence %.1f h", hours)

    while not _stop:
        started = time.monotonic()
        try:
            cycle()
        except Exception:  # noqa: BLE001
            log.exception("cycle failed; will retry on the next tick")

        elapsed = time.monotonic() - started
        wait = max(interval - elapsed, 60.0)
        next_at = datetime.now(timezone.utc).timestamp() + wait
        log.info(
            "next run at %s",
            datetime.fromtimestamp(next_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )
        # Sleep in slices so SIGTERM is honoured promptly.
        while wait > 0 and not _stop:
            step = min(wait, 15.0)
            time.sleep(step)
            wait -= step

    log.info("scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
