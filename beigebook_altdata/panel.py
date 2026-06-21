"""Assemble per-book, per-district scores into the tidy panel the dashboard reads.

Outputs three tables:
  cells      one row per (release, district, section) with the metric bundle
  lens       one row per (release, lens) composite + breadth + dispersion (sigma)
  overlay    withholding YoY aligned to releases (joined to the labor lens)

'breadth'   = share of districts with diffusion > 0 for the lens
'dispersion'= cross-district standard deviation of diffusion (the tile's "district spread (sigma)")
"""
from __future__ import annotations
import datetime as dt
import pandas as pd

from .config import LENS_SECTIONS, DISTRICTS, RULE_VERSION
from . import lexicon


def score_book(book: dict, release: dt.date, engine="lexicon", llm_client=None) -> pd.DataFrame:
    rows = []
    for district, secs in book.items():
        for section, text in secs.items():
            if not text:
                continue
            if engine == "lexicon":
                m = lexicon.score_block(text)
            else:
                from . import llm_engine
                m = llm_engine.score_block(text, section, client=llm_client)
            rows.append({"release": release, "district": district,
                         "section": section, "rule_version": RULE_VERSION, **m})
    return pd.DataFrame(rows)


def build_lens(cells: pd.DataFrame) -> pd.DataFrame:
    out = []
    for release, g in cells.groupby("release"):
        for lens, sections in LENS_SECTIONS.items():
            sub = g[g["section"].isin(sections) & (g["district"].isin(DISTRICTS))]
            d = sub["diffusion"].dropna()
            if d.empty:
                continue
            out.append({
                "release": release, "lens": lens, "rule_version": RULE_VERSION,
                "composite": round(d.mean(), 4),
                "breadth": round((d > 0).mean(), 3),          # share of districts positive
                "dispersion": round(d.std(ddof=0), 4),        # sigma across districts
                "uncertainty": round(sub["uncertainty"].dropna().mean(), 4)
                                if "uncertainty" in sub else None,
                "n_districts": int(sub["district"].nunique()),
            })
    return pd.DataFrame(out).sort_values(["lens", "release"])


def momentum(lens_df: pd.DataFrame) -> pd.DataFrame:
    """Add change-vs-prior-release per lens (your second-derivative read)."""
    lens_df = lens_df.sort_values(["lens", "release"]).copy()
    lens_df["d_composite"] = lens_df.groupby("lens")["composite"].diff().round(4)
    return lens_df


# --- anchored (expanding-window) normalization ------------------------------
# The live, never-revised signal. Each book's z uses ONLY books on or before its
# own release (anchor = corpus start), so no future data ever touches a past row.
# Window grows forward: first emitted point rests on ~MIN_YEARS of data, the last
# point's window IS the full sample (so no separate full-sample column is needed).
MIN_YEARS = 2
RELEASES_PER_YEAR = 8
MIN_WINDOW = MIN_YEARS * RELEASES_PER_YEAR   # books required before emitting z


def anchored_z(lens_df: pd.DataFrame) -> pd.DataFrame:
    """Add raw_ladder (= composite, absolute) and z_anchored (live, append-only).

    z_anchored[t] = (x_t - mean(x_0..x_t)) / std(x_0..x_t), population std,
    NaN until the window reaches MIN_WINDOW.
    """
    df = lens_df.sort_values(["lens", "release"]).copy()
    df["raw_ladder"] = df["composite"]
    out = []
    for _lens, g in df.groupby("lens", sort=False):
        x = g["composite"].to_numpy(dtype=float)
        csum = x.cumsum()
        csq = (x * x).cumsum()
        n = pd.Series(range(1, len(x) + 1), index=g.index).to_numpy()
        mean = csum / n
        var = csq / n - mean * mean
        var[var < 0] = 0.0
        sd = var ** 0.5
        with __import__('numpy').errstate(invalid='ignore', divide='ignore'):
            z = (x - mean) / sd
        z[(n < MIN_WINDOW) | (sd < 1e-9)] = float("nan")
        s = g.copy()
        s["z_anchored"] = [round(v, 4) if v == v else None for v in z]
        out.append(s)
    return pd.concat(out).sort_values(["lens", "release"])


def integrity_hash(lens_df: pd.DataFrame) -> str:
    """SHA-256 over the frozen (release, lens, z_anchored) history."""
    import hashlib
    cols = lens_df.sort_values(["lens", "release"])[["release", "lens", "z_anchored"]]
    return hashlib.sha256(cols.to_csv(index=False).encode()).hexdigest()


def write_appendonly(lens_df: pd.DataFrame, path: str) -> None:
    """Persist z_anchored, refusing to alter any previously written value."""
    import os
    keys = ["release", "lens"]
    if os.path.exists(path):
        prev = pd.read_parquet(path)
        m = prev.merge(lens_df, on=keys, suffixes=("_old", "_new"))
        changed = m[m["z_anchored_old"].notna() &
                    (m["z_anchored_old"] != m["z_anchored_new"])]
        if len(changed):
            raise ValueError(
                f"never-revised violation: {len(changed)} historical z_anchored "
                f"values would change — refusing to write.")
    lens_df.to_parquet(path)
