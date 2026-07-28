"""Market connectors -- Yahoo continuous futures (keyless) and World Bank Pink Sheet.

The Pink Sheet is the reference series for anything without a liquid futures
contract (rice FOB Thailand, fishmeal, urea). Yahoo covers the exchange-traded
complex. Between them the market tables populate with zero credentials; CME,
ICE and Bursa clients in ``keyed.py`` add official settlements and the forward
curve when accounts are available.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Any

import pandas as pd

from ..config import Config
from .base import Connector, FetchResult, SourceUnavailable, register

log = logging.getLogger(__name__)


@register("yahoo_futures")
class YahooFuturesConnector(Connector):
    """Front-month continuous futures via the public chart endpoint.

    One request per symbol, symbols read from ``config/commodities.yaml``.
    Adding a commodity to that file is sufficient to add it here.
    """

    dataset = "futures_prices"

    def __init__(self, name: str, spec: dict[str, Any], cfg: Config) -> None:
        super().__init__(name, spec, cfg)
        self.symbols = {
            key: meta["symbol_yahoo"]
            for key, meta in cfg.commodity_index.items()
            if meta.get("symbol_yahoo")
        }

    def fetch(self) -> FetchResult:
        rows: list[dict[str, Any]] = []
        failures: list[str] = []

        for commodity, symbol in self.symbols.items():
            url = f"{self.url.rstrip('/')}/{symbol}"
            try:
                payload = self.http_json(
                    url, params={"range": "2y", "interval": "1d"}
                )
            except SourceUnavailable as exc:
                failures.append(f"{commodity}: {exc}")
                continue

            try:
                result = payload["chart"]["result"][0]
                stamps = result["timestamp"]
                closes = result["indicators"]["quote"][0]["close"]
                currency = result["meta"].get("currency")
            except (KeyError, IndexError, TypeError) as exc:
                failures.append(f"{commodity}: unexpected payload ({exc})")
                continue

            for ts, close in zip(stamps, closes):
                if close is None:
                    continue
                rows.append({
                    "commodity": commodity,
                    "symbol": symbol,
                    "date": pd.to_datetime(ts, unit="s").normalize(),
                    "close": float(close),
                    "currency": currency,
                })

        if not rows:
            raise SourceUnavailable(
                f"{self.name}: every symbol failed ({'; '.join(failures[:3])})"
            )

        df = pd.DataFrame(rows).sort_values(["commodity", "date"]).reset_index(drop=True)
        return FetchResult(
            dataset=self.dataset,
            frame=df,
            source="Yahoo Finance continuous futures",
            source_url=self.url,
            valid_time=df["date"].max().date().isoformat(),
            notes=failures,
        )


@register("worldbank_pinksheet")
class PinkSheetConnector(Connector):
    """World Bank monthly commodity price data ('Pink Sheet').

    Authoritative, free, and the only consistent monthly series for rice FOB,
    fishmeal and fertiliser.

    The workbook's shape is the whole difficulty. The first live run assumed a
    fixed two-row header, got a title banner and a blank row instead, produced
    columns named ``Unnamed: 3_level_0``, mapped nothing, and reported
    ``commodity: 100% missing`` -- a true statement that says nothing about
    what actually went wrong. This version locates the header by finding the
    first row whose period cell reads ``1960M01``, and when mapping fails it
    prints the labels it really saw next to the symbols it was looking for.
    A connector that cannot be reached from this sandbox has to diagnose
    itself; that is the only debugging channel it has.
    """

    dataset = "pinksheet_prices"
    SHEET = "Monthly Prices"
    PERIOD_RE = re.compile(r"^\s*\d{4}\s*M\s*\d{1,2}\s*$", re.IGNORECASE)

    # The Pink Sheet's own column labels, which do not all match the short
    # symbols in commodities.yaml. Reconciling one vendor's labels to our keys
    # is connector business rather than policy, so it lives here -- but an
    # exact `symbol_pinksheet` match always wins, and an alias for a commodity
    # that is not configured is simply inert.
    ALIASES = {
        "crude_oil_wti": "CRUDE_WTI",
        "urea_e_europe_bulk": "UREA_EE_BULK",
        "urea_ee_bulk": "UREA_EE_BULK",
        "fish_meal": "FISHMEAL",
        "rice_thai_5": "RICE_05",
        "rice_thailand_5": "RICE_05",
        "sugar_world": "SUGAR_WORLD",
        "wheat_us_hrw": "WHEAT_US_HRW",
    }

    @staticmethod
    def _norm(text: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")

    def _lookup(self) -> dict[str, str]:
        wanted = {
            self._norm(meta["symbol_pinksheet"]): key
            for key, meta in self.cfg.commodity_index.items()
            if meta.get("symbol_pinksheet")
        }
        for alias, symbol in self.ALIASES.items():
            key = wanted.get(self._norm(symbol))
            if key:
                wanted.setdefault(alias, key)
        return wanted

    def _resolve(self, label: str, wanted: dict[str, str]) -> str | None:
        norm = self._norm(label)
        if norm in wanted:
            return wanted[norm]
        # Token-subset fallback: {crude, wti} is contained in
        # {crude, oil, wti}. Ambiguity is treated as no match, never as a
        # coin flip -- a mis-mapped price series is worse than a missing one.
        tokens = set(norm.split("_"))
        hits = {
            key for sym, key in wanted.items()
            if sym and set(sym.split("_")) <= tokens
        }
        return hits.pop() if len(hits) == 1 else None

    def _parse(self, content: bytes) -> tuple[pd.DataFrame, list[str]]:
        raw = pd.read_excel(BytesIO(content), sheet_name=self.SHEET, header=None)

        first_data = next(
            (
                i for i, val in enumerate(raw.iloc[:, 0])
                if isinstance(val, str) and self.PERIOD_RE.match(val)
            ),
            None,
        )
        if not first_data:
            preview = [str(v)[:40] for v in raw.iloc[:6, 0].tolist()]
            raise SourceUnavailable(
                f"{self.name}: no data row matching YYYYMmm in column A of "
                f"sheet '{self.SHEET}'. First column A values: {preview}"
            )

        header = raw.iloc[:first_data]
        labels: list[str] = []
        units: list[str] = []
        for col in range(raw.shape[1]):
            cells = [
                str(v).strip() for v in header.iloc[:, col]
                if pd.notna(v) and str(v).strip()
            ]
            labels.append(cells[0] if cells else "")
            unit_like = [c for c in cells[1:] if "/" in c or "$" in c or "(" in c]
            units.append(unit_like[0] if unit_like else (cells[-1] if len(cells) > 1 else ""))

        body = raw.iloc[first_data:].reset_index(drop=True)
        dates = pd.to_datetime(
            body.iloc[:, 0].astype(str).str.replace(r"\s+", "", regex=True),
            format="%YM%m", errors="coerce",
        )

        blocks = []
        for col in range(1, raw.shape[1]):
            if not labels[col]:
                continue
            blocks.append(pd.DataFrame({
                "date": dates,
                "commodity_raw": labels[col],
                "unit": units[col],
                "price": pd.to_numeric(body.iloc[:, col], errors="coerce"),
            }))
        if not blocks:
            raise SourceUnavailable(f"{self.name}: header row carried no column labels")

        long = pd.concat(blocks, ignore_index=True).dropna(subset=["date", "price"])
        seen = sorted({str(x) for x in long["commodity_raw"].unique()})
        return long, seen

    def fetch(self) -> FetchResult:
        import requests

        try:
            resp = requests.get(
                self.url,
                timeout=self.timeout,
                headers={"User-Agent": self.defaults.get("user_agent", "enso-tracker")},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailable(f"{self.name}: {exc}") from exc

        try:
            long, seen = self._parse(resp.content)
        except SourceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailable(
                f"{self.name}: workbook unreadable ({exc}); openpyxl installed?"
            ) from exc

        if long.empty:
            raise SourceUnavailable(f"{self.name}: no priced rows after parsing")

        wanted = self._lookup()
        mapping = {label: self._resolve(label, wanted) for label in seen}
        long["commodity"] = long["commodity_raw"].map(mapping)

        unmapped = sorted(l for l in seen if mapping.get(l) is None)
        if long["commodity"].notna().sum() == 0:
            raise SourceUnavailable(
                f"{self.name}: parsed {len(seen)} price columns but none matched a "
                f"configured symbol_pinksheet.\n"
                f"  workbook labels: {', '.join(seen[:18])}"
                f"{' ...' if len(seen) > 18 else ''}\n"
                f"  looking for:     {', '.join(sorted(set(wanted.values())))}"
            )

        # Keep only the commodities we model. The Pink Sheet carries ~70
        # series; leaving the other ~57 in made the QC missing-value check
        # measure the workbook's breadth rather than our coverage of it.
        out = (
            long[long["commodity"].notna()][["date", "commodity", "commodity_raw", "unit", "price"]]
            .sort_values(["commodity", "date"])
            .reset_index(drop=True)
        )
        matched = sorted(out["commodity"].unique())
        missing = sorted(set(wanted.values()) - set(matched))

        notes = [f"{len(matched)}/{len(set(wanted.values()))} configured commodities mapped"]
        if missing:
            notes.append(
                "unmapped configured commodities: " + ", ".join(missing)
                + " | nearest workbook labels: " + ", ".join(unmapped[:12])
            )

        return FetchResult(
            dataset=self.dataset,
            frame=out,
            source="World Bank Commodity Markets ('Pink Sheet')",
            source_url=self.url,
            valid_time=out["date"].max().date().isoformat(),
            notes=notes,
        )


@register("usda_wasde")
class WasdeConnector(Connector):
    """USDA WASDE supply/demand balances.

    WASDE is published as PDF plus a machine-readable Open Data endpoint.
    We use the Open Data JSON where reachable; otherwise the connector fails
    cleanly and the stock-to-use figures fall back to the configured values,
    which are labelled as such in the table footnotes.
    """

    dataset = "wasde_balances"
    OPEN_DATA = "https://api.ers.usda.gov/data/arms/surveydata"

    def fetch(self) -> FetchResult:
        try:
            payload = self.http_json(self.OPEN_DATA)
        except SourceUnavailable as exc:
            raise SourceUnavailable(
                f"{self.name}: WASDE machine-readable endpoint unavailable ({exc}). "
                f"Stock-to-use falls back to config values, flagged in the UI."
            ) from exc

        records = payload.get("data") if isinstance(payload, dict) else payload
        if not records:
            raise SourceUnavailable(f"{self.name}: empty payload")
        return FetchResult(
            dataset=self.dataset,
            frame=pd.json_normalize(records),
            source="USDA WASDE / ERS",
            source_url=self.OPEN_DATA,
        )
