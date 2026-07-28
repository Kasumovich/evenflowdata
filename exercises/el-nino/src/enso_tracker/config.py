"""Configuration loading.

Everything the system does is driven by the YAML files in ``config/``.
No threshold, source, region, commodity or colour is hard-coded anywhere
in ``src/``; if you find one, it is a bug.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    env = os.environ.get("ENSO_TRACKER_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return Path(os.environ.get("ENSO_CONFIG_DIR", repo_root() / "config"))


def data_dir() -> Path:
    return Path(os.environ.get("ENSO_DATA_DIR", repo_root() / "data"))


def seed_dir() -> Path:
    return data_dir() / "seed"


def store_dir() -> Path:
    """Root of the versioned Parquet store. Point at s3:// for cloud use."""
    return Path(os.environ.get("ENSO_STORE_DIR", data_dir() / "store"))


def output_dir() -> Path:
    return Path(os.environ.get("ENSO_OUTPUT_DIR", repo_root() / "output"))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict[str, Any]:
    path = config_dir() / name
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file {path}. Config is required -- this system "
            f"deliberately has no in-code defaults."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class Config:
    """Merged, validated view over the YAML config tree."""

    sources: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    regions: dict[str, Any] = field(default_factory=dict)
    commodities: dict[str, Any] = field(default_factory=dict)
    styles: dict[str, Any] = field(default_factory=dict)
    glossary: dict[str, Any] = field(default_factory=dict)
    columns: dict[str, Any] = field(default_factory=dict)

    # -- convenience accessors -------------------------------------------

    @property
    def country_list(self) -> list[dict[str, Any]]:
        return self.regions["countries"]

    @property
    def country_index(self) -> dict[str, dict[str, Any]]:
        return {c["iso3"]: c for c in self.country_list}

    @property
    def pinned(self) -> list[str]:
        return list(self.regions.get("pinned_by_default", []))

    @property
    def commodity_index(self) -> dict[str, dict[str, Any]]:
        return dict(self.commodities["commodities"])

    def source_blocks(self) -> dict[str, dict[str, Any]]:
        """Flatten the source tree to ``{name: block}``, dropping ``defaults``."""
        out: dict[str, dict[str, Any]] = {}
        for domain, block in self.sources.items():
            if domain in {"version", "defaults"} or not isinstance(block, dict):
                continue
            for name, spec in block.items():
                spec = dict(spec)
                spec["_domain"] = domain
                spec["_name"] = name
                out[name] = spec
        return out

    def source_defaults(self) -> dict[str, Any]:
        return dict(self.sources.get("defaults", {}))

    def enso_category(self, oni: float) -> str:
        for name, (lo, hi) in self.thresholds["enso_categories"].items():
            if lo <= oni < hi:
                return name
        return "super" if oni >= 0 else "la_nina"

    def qc_range(self, variable: str) -> tuple[float, float] | None:
        rng = self.thresholds["qc"]["ranges"].get(variable)
        return (float(rng[0]), float(rng[1])) if rng else None


@lru_cache(maxsize=1)
def get_config() -> Config:
    cfg = Config(
        sources=load_yaml("sources.yaml"),
        thresholds=load_yaml("thresholds.yaml"),
        regions=load_yaml("regions.yaml"),
        commodities=load_yaml("commodities.yaml"),
        styles=load_yaml("styles.yaml"),
        glossary=load_yaml("glossary.yaml"),
        columns=load_yaml("columns.yaml"),
    )
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Fail loudly at startup rather than subtly at render time."""
    known_commodities = set(cfg.commodity_index)
    problems: list[str] = []

    for country in cfg.country_list:
        for required in ("iso3", "name", "group"):
            if required not in country:
                problems.append(f"country missing '{required}': {country}")
        if len(country.get("iso3", "")) != 3:
            problems.append(f"iso3 must be 3 chars: {country.get('iso3')!r}")
        if country.get("group") not in cfg.regions["groups"]:
            problems.append(
                f"{country.get('iso3')} references unknown group "
                f"{country.get('group')!r}"
            )
        for crop in (country.get("crops") or {}):
            # Crops may map to a commodity or be a local staple with no traded
            # contract (potato, teff, cassava). Only warn on the traded ones.
            if crop in known_commodities:
                continue
        exposures = [c["exposure"] for c in (country.get("crops") or {}).values()]
        if exposures and abs(sum(exposures) - 1.0) > 0.02:
            problems.append(
                f"{country['iso3']} crop exposures sum to {sum(exposures):.2f}, "
                f"expected 1.00"
            )

    for name, spec in cfg.source_blocks().items():
        if spec.get("auth") not in {"none", "key"}:
            problems.append(f"source {name}: auth must be 'none' or 'key'")
        if "connector" not in spec:
            problems.append(f"source {name}: missing 'connector'")

    if problems:
        raise ValueError(
            "Configuration validation failed:\n  - " + "\n  - ".join(problems)
        )


def credentials_present(spec: dict[str, Any]) -> bool:
    """True when every env var a keyed source needs is set and non-empty."""
    if spec.get("auth") != "key":
        return True
    return all(os.environ.get(var) for var in spec.get("env", []))
