"""Market connectors -- Yahoo continuous futures (keyless) and World Bank Pink Sheet.

The Pink Sheet is the reference series for anything without a liquid futures
contract (rice FOB Thailand, fishmeal, urea). Yahoo covers the exchange-traded
complex. Between them the market tables populate with zero credentials; CME,
ICE and Bursa clients in ``keyed.py`` add official settlements and the forward
curve when accounts are available.
"""

from __future__ import annotations

import logging
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
    fishmeal and fertiliser. Distributed as a multi-sheet workbook with two
    header rows above the data.
    """

    dataset = "pinksheet_prices"
    SHEET = "Monthly Prices"

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
            raw = pd.read_excel(BytesIO(resp.content), sheet_name=self.SHEET, header=[0, 1])
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailable(
                f"{self.name}: workbook unreadable ({exc}); openpyxl installed?"
            ) from exc

        raw = raw.rename(columns={raw.columns[0]: ("period", "")})
        long = raw.melt(id_vars=[raw.columns[0]], var_name="series", value_name="price")
        long["commodity_raw"] = long["series"].map(
            lambda t: str(t[0]).strip() if isinstance(t, tuple) else str(t).strip()
        )
        long["unit"] = long["series"].map(
            lambda t: str(t[1]).strip() if isinstance(t, tuple) and len(t) > 1 else ""
        )
        long["period"] = long[raw.columns[0]].astype(str).str.strip()
        long["date"] = pd.to_datetime(long["period"], format="%YM%m", errors="coerce")
        long = long.dropna(subset=["date"])
        long["price"] = pd.to_numeric(long["price"], errors="coerce")
        long = long.dropna(subset=["price"])

        wanted = {
            meta["symbol_pinksheet"].lower(): key
            for key, meta in self.cfg.commodity_index.items()
            if meta.get("symbol_pinksheet")
        }
        long["commodity"] = (
            long["commodity_raw"].str.upper().str.replace(r"[^A-Z0-9]+", "_", regex=True)
            .str.strip("_").str.lower().map(wanted)
        )

        out = long[["date", "commodity", "commodity_raw", "unit", "price"]]
        out = out.sort_values(["commodity_raw", "date"]).reset_index(drop=True)

        matched = int(out["commodity"].notna().sum())
        if out.empty:
            raise SourceUnavailable(f"{self.name}: no rows after parsing")

        return FetchResult(
            dataset=self.dataset,
            frame=out,
            source="World Bank Commodity Markets ('Pink Sheet')",
            source_url=self.url,
            valid_time=out["date"].max().date().isoformat(),
            notes=[f"{matched} rows mapped to configured commodities"],
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
