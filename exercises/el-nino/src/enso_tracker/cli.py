"""Command-line interface -- the systematic re-run entry point.

    enso-tracker run          Steps A-F, write payload + state
    enso-tracker report       Step D only, from the last payload
    enso-tracker export       Build the standalone HTML dashboard
    enso-tracker all          run + report + export (what cron calls)
    enso-tracker sources      Show every source and whether it is live
    enso-tracker validate     Config validation only, for CI
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import config_dir, credentials_present, get_config, output_dir


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run

    result = run(offline=args.offline, out=Path(args.out) if args.out else None)
    state = result.state
    print(
        f"\n  Peak median {state.peak_median:.1f} degC "
        f"[{state.peak_p10:.1f}-{state.peak_p90:.1f}]  |  "
        f"ONI {state.current_oni:+.1f} ({state.current_oni_season})  |  "
        f"{state.days_to_peak} days to peak"
    )
    print(
        f"  sources: {result.metrics.sources_live} live, "
        f"{result.metrics.sources_seeded} seeded, "
        f"{result.metrics.sources_dormant} dormant, "
        f"{result.metrics.sources_failed} failed"
    )
    marks = {"incident": "!", "standing": "\u25b2", "configuration": "\u25cb"}
    for alert in result.metrics.alerts:
        print(f"  {marks.get(alert.severity, '-')} [{alert.severity}] {alert.title}")
    if not result.metrics.alerts:
        print("  no alerts")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .pipeline import load_previous_state
    from .report.sitrep import write_report

    out = Path(args.out) if args.out else output_dir()
    payload_path = out / "dashboard_payload.json"
    if not payload_path.exists():
        print("No payload found. Run `enso-tracker run` first.", file=sys.stderr)
        return 1

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    written = write_report(
        payload, out.parent / "docs" / "sitreps" if args.in_docs else out,
        previous=load_previous_state(out), pdf=not args.no_pdf,
    )
    for kind, path in written.items():
        print(f"  {kind}: {path}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .dashboard.static_export import export_dashboard

    out = Path(args.out) if args.out else output_dir()
    payload_path = out / "dashboard_payload.json"
    if not payload_path.exists():
        print("No payload found. Run `enso-tracker run` first.", file=sys.stderr)
        return 1

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    target = export_dashboard(payload, out / "dashboard.html")
    print(f"  dashboard: {target}")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    for step in (cmd_run, cmd_report, cmd_export):
        code = step(args)
        if code:
            return code
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    from .connectors import get_connector, registered  # noqa: F401

    cfg = get_config()
    rows = []
    for name, spec in sorted(cfg.source_blocks().items()):
        connector = get_connector(spec["connector"])
        if connector is None:
            status = "NO CONNECTOR"
        elif spec.get("auth") == "key" and not credentials_present(spec):
            status = "dormant (needs " + ",".join(spec.get("env", [])) + ")"
        else:
            status = "ready"
        rows.append((spec["_domain"], name, spec["connector"], status))

    width = max(len(r[1]) for r in rows) + 2
    domain = None
    for dom, name, conn, status in rows:
        if dom != domain:
            print(f"\n{dom.upper()}")
            domain = dom
        print(f"  {name:<{width}} {conn:<22} {status}")
    ready = sum(1 for r in rows if r[3] == "ready")
    print(f"\n  {ready}/{len(rows)} sources ready without additional credentials\n")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = get_config()
    print(
        f"  OK: {len(cfg.country_list)} countries, "
        f"{len(cfg.commodity_index)} commodities, "
        f"{len(cfg.source_blocks())} sources, config dir {config_dir()}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="enso-tracker", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--out", help="output directory (default ./output)")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Steps A-F")
    run_p.add_argument("--offline", action="store_true",
                       help="skip network entirely; use seed + last-good only")
    run_p.set_defaults(func=cmd_run)

    rep_p = sub.add_parser("report", help="Step D: Situation Report")
    rep_p.add_argument("--no-pdf", action="store_true")
    rep_p.add_argument("--in-docs", action="store_true",
                       help="write into docs/sitreps/ instead of output/")
    rep_p.set_defaults(func=cmd_report)

    exp_p = sub.add_parser("export", help="Build the standalone HTML dashboard")
    exp_p.set_defaults(func=cmd_export)

    all_p = sub.add_parser("all", help="run + report + export")
    all_p.add_argument("--offline", action="store_true")
    all_p.add_argument("--no-pdf", action="store_true")
    all_p.add_argument("--in-docs", action="store_true")
    all_p.set_defaults(func=cmd_all)

    src_p = sub.add_parser("sources", help="List sources and readiness")
    src_p.set_defaults(func=cmd_sources)

    val_p = sub.add_parser("validate", help="Validate configuration")
    val_p.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("enso_tracker").exception("run failed")
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
