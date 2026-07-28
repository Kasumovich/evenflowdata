"""Step D: automated Situation Report (Markdown, optionally rendered to PDF).

The report is deliberately written to be readable by someone who has not seen
the dashboard: it leads with what changed, then the top-5 escalations, then
the confidence and data-quality picture. Anything the pipeline could not
verify appears in an explicit caveats section rather than being quietly
omitted.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)


def _severity_of(alert: Any) -> str:
    """Alerts arrive as dicts from the payload, or dataclasses in-process."""
    if isinstance(alert, dict):
        return str(alert.get("severity", "incident"))
    return str(getattr(alert, "severity", "incident"))


def _alert_parts(alert: Any) -> tuple[str, str]:
    if isinstance(alert, dict):
        return str(alert.get("title", "")), str(alert.get("detail", ""))
    return str(getattr(alert, "title", alert)), str(getattr(alert, "detail", ""))


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:+.{digits}f}%"


def build_markdown(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> str:
    state = payload["state"]
    meta = payload["meta"]
    ensemble = payload["ensemble"]
    escalations = payload["escalations"]
    alerts = payload.get("alerts", [])
    base_commodities = payload["commodities"].get("base", [])

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines += [
        f"# El Nino 2026-27 Situation Report",
        "",
        f"**Issued** {generated} &nbsp;|&nbsp; **Run** `{meta['run_id']}` "
        f"&nbsp;|&nbsp; **Valid as of** {state['as_of']}",
        "",
        "---",
        "",
        "## 1. Headline",
        "",
    ]

    delta = ""
    if previous and previous.get("peak_median") is not None:
        shift = state["peak_median"] - float(previous["peak_median"])
        delta = f" ({shift:+.2f} degC vs previous run)"

    lines += [
        f"Ensemble peak Nino 3.4 median **{state['peak_median']:.1f} degC**{delta}, "
        f"80% interval **{state['peak_p10']:.1f} to {state['peak_p90']:.1f} degC**, "
        f"across {ensemble['n_models']} models / {ensemble['n_members']} members "
        f"initialised {ensemble['initialization']}.",
        "",
        f"Observed state: ONI **{state['current_oni']:+.1f} degC** "
        f"({state['current_oni_season']}), category **{state['category']}**, "
        f"CPC status **{state['alert_status']}**. "
        f"Peak centred **{state['peak_centre']}**, "
        f"**{state['days_to_peak']} days** out.",
        "",
        f"Probability of exceeding the {state['record_to_beat_monthly']} degC "
        f"monthly record: **{state['prob_exceed_record']:.0%}**. "
        f"Probability of a 'super' event (>= 2.5 degC): "
        f"**{state['prob_super']:.0%}**.",
        "",
    ]

    lines += ["## 2. Alerts", ""]
    if alerts:
        order = payload.get("severity_order", ["incident", "standing", "configuration"])
        sev_meta = payload.get("severity_meta", {})
        marks = {"incident": "**!**", "standing": "**\u25b2**", "configuration": "\u25cb"}
        emitted = False
        for severity in order:
            group = [a for a in alerts if _severity_of(a) == severity]
            if not group:
                continue
            emitted = True
            label = sev_meta.get(severity, {}).get("label", severity.title())
            note = sev_meta.get(severity, {}).get("description", "")
            lines.append(f"**{label}** \u2014 *{note}*")
            lines.append("")
            for alert in group:
                title, detail = _alert_parts(alert)
                lines.append(f"- {marks.get(severity, '-')} {title}. {detail}")
            lines.append("")
        if not emitted:
            lines += ["No alert thresholds breached this run.", ""]
        incidents = sum(1 for a in alerts if _severity_of(a) == "incident")
        lines += [
            f"> {incidents} incident(s) this run. Only incidents represent change; "
            f"standing and configuration entries are true every run by design and "
            f"are listed so they stay visible, not because anything happened.",
            "",
        ]
    else:
        lines += ["No alerts this run.", ""]

    lines += ["## 3. Top 5 risk escalations", "",
              "| # | Region | Severity | Yield index | Fire/drought | "
              "Price pressure | Confidence | Dominant hazards |",
              "|---|--------|----------|-------------|--------------|"
              "----------------|------------|------------------|"]
    for i, row in enumerate(escalations, start=1):
        hazards = ", ".join((row.get("hazards") or [])[:3]).replace("_", " ")
        lines.append(
            f"| {i} | **{row['name']}** | {row['severity']:.1f} | "
            f"{_fmt_pct(row['yield_index'])} | {row['fire_drought']:.0f} | "
            f"{row['price_pressure']:.0f} | {row['confidence']:.2f} | {hazards} |"
        )
    lines.append("")

    top_commodities = sorted(
        base_commodities, key=lambda r: r.get("price_response_pct", 0), reverse=True
    )[:6]
    lines += ["## 4. Commodity price pressure (base scenario)", "",
              "| Commodity | Production shock | Modelled price response | "
              "Lag | Stock-to-use | Note |",
              "|-----------|------------------|-------------------------|"
              "-----|--------------|------|"]
    for row in top_commodities:
        stu = row.get("stock_to_use")
        note = "thin market" if row.get("thin_market") else ""
        lines.append(
            f"| {row['label']} | {_fmt_pct(row['production_shock_pct'])} | "
            f"{_fmt_pct(row['price_response_pct'])} | {row['lag_months']} mo | "
            f"{'n/a' if stu is None else f'{float(stu):.1%}'} | {note} |"
        )
    lines.append("")
    lines += [
        "> These are **scenario outputs**, not forecasts. They are produced by "
        "scaling published teleconnection composites by forecast intensity and "
        "applying configured price elasticities. The one directly citable "
        "El Nino price response we could attribute is palm oil at +20-40% on a "
        "six-month lag; modelled values are cross-checked against it and a "
        "QC flag is raised on a departure beyond 2x.",
        "",
    ]

    lines += ["## 5. Data quality and confidence", ""]
    lines += [
        f"- Sources live: **{meta['sources_live']}**, "
        f"seeded: **{meta['sources_seeded']}**, "
        f"dormant (no credentials): **{meta['sources_dormant']}**, "
        f"failed: **{meta['sources_failed']}**.",
    ]
    if meta.get("data_latency_h"):
        worst = max(meta["data_latency_h"].items(), key=lambda kv: kv[1])
        lines.append(f"- Worst data latency: **{worst[0]}** at {worst[1]:.0f} h.")
    if state.get("extrapolation_warning"):
        lines.append(
            f"- **Extrapolation regime.** The forecast median exceeds the "
            f"amplitude at which teleconnection composites are observationally "
            f"constrained. Every impact layer carries a confidence penalty and "
            f"is labelled `composite` in the interface."
        )
    for flag in payload.get("qc_flags", [])[:8]:
        lines.append(f"- QC: {flag}")
    lines.append("")

    unverified = payload.get("unverified_assertions", {})
    tracked = {k: v for k, v in unverified.items() if not k.startswith("_")}
    if tracked:
        lines += ["## 6. Caveats and unattributed inputs", "",
                  "Figures supplied in the commissioning brief that this run "
                  "could not trace to a primary source. They are excluded from "
                  "all headline numbers and appear only inside labelled "
                  "scenario bands. Attribution is re-attempted every run.", ""]
        for key, entry in tracked.items():
            status = entry.get("status", "unknown")
            value = entry.get("value")
            line = f"- `{key}` = {value} -- *{status}*"
            if entry.get("conflict"):
                line += f". {entry['conflict']}"
            if entry.get("note"):
                line += f". {entry['note']}"
            lines.append(line)
        lines.append("")

    lines += [
        "## 7. Method",
        "",
        f"ONI computed as a {3}-month running mean of Nino 3.4 against CPC's "
        f"era-specific climatology, matching the convention of the source "
        f"forecast so peak intensities are comparable with the 1982-83, "
        f"1997-98 and 2015-16 record. Impact layers use a fixed 1991-2020 "
        f"base. Composites are scaled by "
        f"`(intensity / 2.4) ** 0.85` and by scenario multiplier. "
        f"Full detail in `docs/DATA_DICTIONARY.md`.",
        "",
        "---",
        "",
        f"*Generated automatically by enso-tracker. Every figure in the "
        f"dashboard carries a hover citation and retrieval timestamp.*",
        "",
    ]

    return "\n".join(lines)


def write_report(
    payload: dict[str, Any],
    out_dir: Path,
    previous: dict[str, Any] | None = None,
    *,
    pdf: bool = True,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = payload["state"]["as_of"]
    md_path = out_dir / f"sitrep_{stamp}.md"
    md_path.write_text(build_markdown(payload, previous), encoding="utf-8")

    written = {"markdown": md_path}
    if pdf:
        pdf_path = md_path.with_suffix(".pdf")
        if _render_pdf(md_path, pdf_path):
            written["pdf"] = pdf_path
    return written


def _render_pdf(md_path: Path, pdf_path: Path) -> bool:
    """Best-effort PDF. Absence of a renderer is a warning, never a failure."""
    for cmd in (
        ["pandoc", str(md_path), "-o", str(pdf_path)],
        ["weasyprint", str(md_path), str(pdf_path)],
    ):
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    log.warning("no PDF renderer available (tried pandoc, weasyprint); Markdown only")
    return False
