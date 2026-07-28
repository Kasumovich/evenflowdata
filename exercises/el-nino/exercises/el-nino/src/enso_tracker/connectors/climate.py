"""Keyless climate connectors -- CPC ONI, weekly Nino indices, ERSSTv5, IRI plume.

These four are the backbone of the dashboard and none of them needs an
account. Between them they give the observed state, the official alert
status, and the public model plume.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd

from .base import Connector, FetchResult, SourceUnavailable, register

log = logging.getLogger(__name__)

SEASONS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
           "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]


@register("cpc_oni")
class CPCOniConnector(Connector):
    """Oceanic Nino Index, ERSSTv5, era-specific 30-year climatology.

    CPC publishes the same index in two shapes and this connector was pointed
    at the wrong one. ``ONI_v5.php`` is a **wide** HTML table -- one row per
    year, twelve overlapping-season columns, anomaly only. ``oni.ascii.txt`` is
    a **long** table -- ``SEAS YR TOTAL ANOM``, one row per season, with the
    absolute SST as well as the anomaly. This parser was written for the long
    form; the URL pointed at the wide one, so every row was rejected for having
    a year where a season should be and the connector reported "0 usable rows".
    A correct parser and a correct URL are not the same fix, and having one
    without the other looks exactly like a dead source.

    Both shapes are handled now, long form first, because a source that
    publishes the same numbers two ways will eventually move one of them.
    """

    dataset = "oni_observed"

    def _parse_long(self, text: str) -> tuple[list[dict[str, Any]], int]:
        """``  DJF 1950  24.72  -1.53`` -- season, year, total SST, anomaly."""
        rows: list[dict[str, Any]] = []
        dropped = 0
        for line in text.splitlines():
            parts = line.split()
            if len(parts) != 4 or parts[0].upper() not in SEASONS:
                dropped += 1
                continue
            try:
                rows.append({
                    "season": parts[0].upper(),
                    "year": int(parts[1]),
                    "sst": float(parts[2]),
                    "oni": float(parts[3]),
                })
            except ValueError:
                dropped += 1
        return rows, dropped

    def _parse_wide(self, html: str) -> tuple[list[dict[str, Any]], int]:
        """One row per year, one column per season, anomaly only.

        ``sst`` is absent in this shape and is reported as NaN rather than
        invented -- the ONI itself is the anomaly, so nothing downstream needs
        the absolute value, and a fabricated one would be indistinguishable
        from a measured one three months later.
        """
        rows: list[dict[str, Any]] = []
        dropped = 0
        for raw in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
            cells = [
                re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", raw, flags=re.S | re.I)
            ]
            if not cells or not re.fullmatch(r"(18|19|20|21)\d{2}", cells[0]):
                dropped += 1
                continue
            year = int(cells[0])
            for offset, value in enumerate(cells[1:13]):
                if offset >= len(SEASONS):
                    break
                try:
                    anomaly = float(value)
                except ValueError:
                    continue        # a year in progress has empty trailing cells
                rows.append({
                    "season": SEASONS[offset],
                    "year": year,
                    "sst": float("nan"),
                    "oni": anomaly,
                })
        return rows, dropped

    def fetch(self) -> FetchResult:
        body = self.http_get(self.url)

        rows, dropped = self._parse_long(body)
        shape = "long (SEAS YR TOTAL ANOM)"
        if not rows and "<tr" in body.lower():
            rows, dropped = self._parse_wide(body)
            shape = "wide (year x season HTML table)"

        if not rows:
            head = " | ".join(body.splitlines()[:3])[:200]
            raise SourceUnavailable(
                f"{self.name}: parsed 0 usable rows from {self.url} in either the "
                f"long or wide layout. Refusing to return an empty frame. "
                f"First lines received: {head!r}"
            )

        df = pd.DataFrame(rows)
        df["season_index"] = df["season"].map({s: i for i, s in enumerate(SEASONS)})
        df = df.sort_values(["year", "season_index"]).reset_index(drop=True)

        # An ONI season is a 3-month mean centred on its middle month, so the
        # newest row is already ~6 weeks behind the calendar even when CPC is
        # perfectly on schedule. Declaring that here is what stops the staleness
        # alarm from reading a healthy monthly feed as fresh-to-the-second.
        last = df.iloc[-1]
        centre = SEASONS.index(last["season"]) + 1
        valid = f"{int(last['year'])}-{centre:02d}-15"

        return FetchResult(
            dataset=self.dataset,
            frame=df,
            source="NOAA CPC ONI v5",
            source_url=self.url,
            valid_time=valid,
            notes=[
                f"parsed {len(df)} seasons as {shape}; newest "
                f"{last['season']} {int(last['year'])} (centred {valid})",
            ] + ([f"dropped {dropped} non-data rows"] if dropped else []),
        )


@register("cpc_weekly")
class CPCWeeklyConnector(Connector):
    """Weekly Nino 1+2 / 3 / 3.4 / 4 SST and anomaly, fixed-width text.

    Format: ``DDMMMYYYY  SST ANOM  SST ANOM  SST ANOM  SST ANOM``.
    """

    dataset = "weekly_nino"

    def fetch(self) -> FetchResult:
        text = self.http_get(self.url)
        pattern = re.compile(
            r"^\s*(\d{2}[A-Z]{3}\d{4})\s+" + r"([-\d.]+)\s+([-\d.]+)\s+" * 4,
            flags=re.M,
        )
        rows = []
        for m in pattern.finditer(text):
            try:
                rows.append({
                    "week_ending": pd.to_datetime(m.group(1), format="%d%b%Y"),
                    "nino12": float(m.group(3)),
                    "nino3": float(m.group(5)),
                    "nino34": float(m.group(7)),
                    "nino4": float(m.group(9)),
                })
            except (ValueError, IndexError):
                continue

        if not rows:
            raise SourceUnavailable(f"{self.name}: no parseable weekly rows")

        df = pd.DataFrame(rows).sort_values("week_ending").reset_index(drop=True)
        return FetchResult(
            dataset=self.dataset,
            frame=df,
            source="NOAA CPC weekly SST indices",
            source_url=self.url,
            valid_time=df["week_ending"].iloc[-1].date().isoformat(),
        )


@register("cpc_ersst")
class CPCErsstConnector(Connector):
    """Monthly Nino 3.4 anomaly from PSL's whitespace-delimited archive.

    Layout: a header line ``startyear endyear``, then ``year v1 ... v12``,
    then a missing-value sentinel and free-text provenance notes.
    """

    dataset = "nino34_monthly"

    def fetch(self) -> FetchResult:
        text = self.http_get(self.url)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            raise SourceUnavailable(f"{self.name}: empty response")

        try:
            start_year, end_year = (int(x) for x in lines[0].split()[:2])
        except ValueError as exc:
            raise SourceUnavailable(f"{self.name}: unreadable header") from exc

        missing = -99.99
        for ln in reversed(lines):
            parts = ln.split()
            if len(parts) == 1:
                try:
                    missing = float(parts[0])
                    break
                except ValueError:
                    continue

        rows = []
        for ln in lines[1:]:
            parts = ln.split()
            if len(parts) != 13:
                continue
            try:
                year = int(parts[0])
            except ValueError:
                continue
            if not start_year <= year <= end_year:
                continue
            for month, token in enumerate(parts[1:], start=1):
                value = float(token)
                if abs(value - missing) < 1e-6:
                    continue
                rows.append({
                    "date": pd.Timestamp(year=year, month=month, day=1),
                    "nino34_anom": value,
                })

        if not rows:
            raise SourceUnavailable(f"{self.name}: no data rows")

        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        return FetchResult(
            dataset=self.dataset,
            frame=df,
            source="NOAA PSL / ERSSTv5 Nino 3.4",
            source_url=self.url,
            valid_time=df["date"].iloc[-1].date().isoformat(),
        )


@register("iri_plume")
class IRIPlumeConnector(Connector):
    """IRI/CPC model plume.

    IRI publishes the plume primarily as a figure; the machine-readable
    companion is a CSV of per-model seasonal forecasts. We take the CSV when
    it is present and otherwise report unavailable rather than scraping
    numbers out of an image, which would be unciteable.
    """

    dataset = "iri_plume"
    CSV_CANDIDATES = (
        "https://iri.columbia.edu/~forecast/ensofcst/Data/ensofcst_ALL.csv",
        "https://iri.columbia.edu/~forecast/ensofcst/Data/figure4_data.csv",
    )

    def fetch(self) -> FetchResult:
        last_error: Exception | None = None
        for candidate in self.CSV_CANDIDATES:
            try:
                text = self.http_get(candidate)
            except SourceUnavailable as exc:
                last_error = exc
                continue
            from io import StringIO
            try:
                df = pd.read_csv(StringIO(text))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            if df.empty:
                continue
            df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
            return FetchResult(
                dataset=self.dataset,
                frame=df,
                source="IRI/CPC ENSO forecast plume",
                source_url=candidate,
            )

        raise SourceUnavailable(
            f"{self.name}: no machine-readable plume available ({last_error}). "
            f"The dashboard falls back to the stored ensemble summary rather "
            f"than digitising the published figure."
        )


@register("generic_http_json")
class GenericJSONConnector(Connector):
    """Thin generic JSON puller for the agency bulletin sources.

    Many national agencies (SENAMHI, BMKG, IMARPE, FAO GIEWS, IPC) publish
    bulletins as HTML or PDF rather than as an API. Where a JSON endpoint
    exists this connector reads it; where one does not, it fails cleanly and
    the corresponding dashboard panel shows 'awaiting source' rather than a
    fabricated number. Point ``url`` at a real endpoint to activate.
    """

    dataset = "generic_json"

    def fetch(self) -> FetchResult:
        payload = self.http_json(self.url)
        if isinstance(payload, dict):
            for key in ("data", "results", "items", "features"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list) or not payload:
            raise SourceUnavailable(f"{self.name}: no tabular payload at {self.url}")
        return FetchResult(
            dataset=f"{self.name}",
            frame=pd.json_normalize(payload),
            source=self.spec.get("description", self.name),
            source_url=self.url,
        )
