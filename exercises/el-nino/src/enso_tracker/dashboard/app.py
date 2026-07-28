"""Streamlit app -- the served, auto-refreshing face of the tracker.

The static export in ``static_export.py`` is the shareable snapshot; this is
the live view. Both read the same payload, so they can never disagree.

Run:  streamlit run src/enso_tracker/dashboard/app.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from enso_tracker.config import get_config, output_dir  # noqa: E402

REFRESH_HOURS = int(os.environ.get("ENSO_REFRESH_HOURS", "6"))

st.set_page_config(
    page_title="El Nino 2026-27 Tracker",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(ttl=60 * 30)
def load_payload() -> dict | None:
    path = output_dir() / "dashboard_payload.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def trigger_rerun(offline: bool = False) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "enso_tracker.cli", "all"]
    if offline:
        cmd.append("--offline")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return proc.returncode, (proc.stdout + proc.stderr)[-4000:]


payload = load_payload()

# -- sidebar --------------------------------------------------------------
with st.sidebar:
    st.subheader("Controls")
    if st.button("Update now", use_container_width=True):
        with st.spinner("Running Steps A-F..."):
            code, log = trigger_rerun()
        load_payload.clear()
        st.success("Run complete") if code == 0 else st.error("Run failed")
        st.code(log, language="text")
        st.rerun()

    st.caption(
        f"Scheduled cadence: every {REFRESH_HOURS} h. "
        f"The scheduler container runs Steps A-F unattended; this button forces "
        f"one immediately."
    )

    if payload:
        meta = payload["meta"]
        st.divider()
        st.metric("Sources live", meta["sources_live"])
        st.caption(
            f"{meta['sources_seeded']} seeded &middot; {meta['sources_dormant']} dormant "
            f"&middot; {meta['sources_failed']} failed"
        )
        st.caption(f"Run `{meta['run_id']}`")
        html_path = output_dir() / "dashboard.html"
        if html_path.exists():
            st.download_button(
                "Download static snapshot",
                html_path.read_bytes(),
                file_name=f"elnino_snapshot_{payload['state']['as_of']}.html",
                mime="text/html",
                use_container_width=True,
            )

if payload is None:
    st.title("El Nino 2026-27 Tracker")
    st.warning(
        "No payload found. Run the pipeline first:\n\n"
        "```\nenso-tracker all\n```\n\n"
        "or press **Update now** in the sidebar."
    )
    st.stop()

state = payload["state"]
ensemble = payload["ensemble"]
cfg = get_config()

# -- header ---------------------------------------------------------------
st.title("El Nino 2026-27 Tracker")
st.caption(
    f"Peak forecast **{state['peak_median']:.1f} degC** "
    f"(80% interval {state['peak_p10']:.1f}-{state['peak_p90']:.1f}) "
    f"| {ensemble['n_models']} models, {ensemble['n_members']} members "
    f"| generated {payload['meta']['generated_utc'][:19]}Z"
)

for alert in payload.get("alerts", []):
    (st.error if "CRITICAL" in alert or "EXTRAPOLATION" in alert else st.warning)(alert)

cols = st.columns(6)
cols[0].metric("Forecast peak", f"{state['peak_median']:.1f} degC",
               f"{state['peak_median'] - state['record_to_beat_monthly']:+.2f} vs record")
cols[1].metric("Observed ONI", f"{state['current_oni']:+.1f}", state["current_oni_season"])
cols[2].metric("Days to peak", state["days_to_peak"], state["peak_centre"])
cols[3].metric(f"P(beats {state['record_to_beat_monthly']})",
               f"{state['prob_exceed_record']:.0%}")
cols[4].metric("P(super event)", f"{state['prob_super']:.0%}", "ONI >= 2.5")
cols[5].metric("Category", state["category"].replace("_", " ").title(),
               state["alert_status"])

tab_names = ["Overview", "Global map", "Regions", "Commodities", "Data", "Method"]
tabs = st.tabs(tab_names)

countries = pd.DataFrame(payload["countries"])
plume = pd.DataFrame(payload["plume"])
analogs = pd.DataFrame(payload["analogs"])

# -- overview -------------------------------------------------------------
with tabs[0]:
    import plotly.graph_objects as go

    cat = cfg.styles["categorical"]["light"]
    fig = go.Figure()
    fig.add_traces([
        go.Scatter(x=list(plume["month"]) + list(plume["month"])[::-1],
                   y=list(plume["p90"]) + list(plume["p10"])[::-1],
                   fill="toself", fillcolor="rgba(235,104,52,0.14)",
                   line=dict(width=0), hoverinfo="skip", showlegend=False),
        go.Scatter(x=plume["month"], y=plume["median"], name="Forecast median",
                   line=dict(color=cat[1], width=2)),
    ])
    observed = plume.dropna(subset=["observed"])
    if not observed.empty:
        fig.add_trace(go.Scatter(
            x=observed["month"], y=observed["observed"], name="Observed 2026",
            mode="lines+markers", line=dict(color=cat[0], width=2)))
    for i, event in enumerate(["1982-83", "1997-98", "2015-16"]):
        rows = analogs[(analogs["event"] == event) & (analogs["step"] <= 12)]
        if rows.empty:
            continue
        fig.add_trace(go.Scatter(
            x=list(plume["month"])[:len(rows)], y=rows["oni"], name=event,
            line=dict(color=cat[2 + i], width=2, dash="dot")))
    fig.add_hline(y=state["record_to_beat_monthly"], line_dash="dash",
                  line_color="#898781",
                  annotation_text=f"2015-16 record {state['record_to_beat_monthly']} degC")
    fig.update_layout(
        height=460, margin=dict(l=50, r=30, t=10, b=40),
        yaxis_title="Nino 3.4 anomaly (degC)", hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Analogue series are aligned by event phase, not calendar date. "
        "The plume is reconstructed from published ensemble summary statistics "
        "until credentialed per-member data is available -- see Method."
    )

# -- map ------------------------------------------------------------------
with tabs[1]:
    import plotly.express as px

    layers = {l["key"]: l for l in payload["layers"]}
    key = st.selectbox("Layer", list(layers), format_func=lambda k: layers[k]["label"])
    frame = countries.dropna(subset=[key])
    diverging = key in {"precip_djf", "precip_mam", "temp_djf", "yield_index", "fisheries"}
    bound = float(frame[key].abs().max())
    fig = px.choropleth(
        frame, locations="iso3", color=key, hover_name="name",
        hover_data={"confidence": True, "group_label": True, "iso3": False},
        color_continuous_scale="RdBu" if diverging else "Oranges",
        range_color=(-bound, bound) if diverging else (0, frame[key].max()),
        projection="natural earth",
    )
    fig.update_layout(height=560, margin=dict(l=0, r=0, t=0, b=0),
                      paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"{layers[key]['label']} ({layers[key]['unit']}). Values are "
        f"`composite`: published teleconnection composites scaled to forecast "
        f"intensity, not dynamically downscaled model output."
    )

# -- regions --------------------------------------------------------------
with tabs[2]:
    pinned = st.multiselect(
        "Regions", sorted(countries["name"]),
        default=[c["name"] for _, c in countries.iterrows() if c["pinned"]],
    )
    for name in pinned:
        row = countries[countries["name"] == name].iloc[0]
        with st.expander(f"{name} - {row['group_label']}", expanded=True):
            c1, c2, c3 = st.columns(3)
            c1.metric("Precip DJF", f"{row['precip_djf']:+.0f}%")
            c1.metric("Precip MAM", f"{row['precip_mam']:+.0f}%")
            c2.metric("Temp DJF", f"{row['temp_djf']:+.2f} degC")
            c2.metric("Yield index", f"{row['yield_index']:+.1f}%")
            c3.metric("Fire / drought", f"{row['fire_drought']:.0f}/100")
            c3.metric("Confidence", f"{row['confidence']:.2f}")
            if row.get("fisheries") is not None and pd.notna(row.get("fisheries")):
                st.metric("Fisheries biomass", f"{row['fisheries']:+.0f}%")
            st.write("**Hazards:** " + ", ".join(
                h.replace("_", " ") for h in (row["hazards"] or [])))
            if row.get("notes"):
                st.info(row["notes"])

# -- commodities ----------------------------------------------------------
with tabs[3]:
    scenario = st.radio("Scenario", list(payload["commodities"]), horizontal=True)
    comm = pd.DataFrame(payload["commodities"][scenario])
    st.bar_chart(comm.set_index("label")["price_response_pct"], horizontal=True)
    st.dataframe(comm, use_container_width=True, hide_index=True)
    st.caption(
        "Scenario output, not a forecast. Cross-checked against the one "
        "attributable El Nino price response found (palm oil, +20-40% at a "
        "six-month lag); a departure beyond 2x raises a QC flag."
    )

# -- data -----------------------------------------------------------------
with tabs[4]:
    st.dataframe(countries, use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    c1.download_button("Download countries CSV", countries.to_csv(index=False),
                       f"elnino_countries_{state['as_of']}.csv", "text/csv",
                       use_container_width=True)
    c2.download_button("Download full payload JSON", json.dumps(payload, indent=2),
                       f"elnino_payload_{state['as_of']}.json", "application/json",
                       use_container_width=True)

# -- method ---------------------------------------------------------------
with tabs[5]:
    st.subheader("Provenance tiers")
    st.write(
        "**verified** - read from the cited primary source on the retrieval date. "
        "**cached** - a primary-source value held from an earlier read. "
        "**composite / modelled** - derived by this system from published "
        "composites and elasticities; indicative, never an observation."
    )
    st.subheader("The extrapolation problem")
    st.write(
        f"No El Nino of the forecast amplitude has ever been observed. Composites "
        f"are calibrated near 2.4 degC and scaled by `(intensity / 2.4) ** 0.85`. "
        f"Above 2.8 degC every impact layer carries a "
        f"{(1 - state['confidence_multiplier']):.0%} confidence penalty, already "
        f"reflected in the confidence figures shown. The sign of these responses "
        f"is well established; the magnitude at this amplitude is not."
    )
    unverified = payload.get("unverified_assertions", {})
    rows = [{"assertion": k, "value": str(v.get("value")), "status": v.get("status"),
             "note": v.get("conflict") or v.get("note") or ""}
            for k, v in unverified.items() if not k.startswith("_")]
    if rows:
        st.subheader("Figures this system could not verify")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if payload.get("qc_flags"):
        st.subheader("QC flags this run")
        for flag in payload["qc_flags"]:
            st.warning(flag)

st.caption(
    f"enso-tracker | run {payload['meta']['run_id']} | "
    f"rendered {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
)
