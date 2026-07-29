"""Credentialed connectors: Copernicus CDS/ADS, NMME THREDDS, exchange feeds.

Each follows the provider's documented request contract. They stay dormant
until the env vars named in ``config/sources.yaml`` are set, at which point
the corresponding dashboard layers switch from composite-derived to
model-derived with no code change. The switch is visible in the UI: layers
sourced from live gridded data lose the "composite" provenance badge.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .base import FetchResult, KeyedConnector, SourceUnavailable, register

log = logging.getLogger(__name__)

# Nino 3.4 box, the standard definition.
NINO34_BOX = {"lat": (-5.0, 5.0), "lon": (190.0, 240.0)}


@register("cds_seasonal")
class CDSSeasonalConnector(KeyedConnector):
    """Copernicus C3S seasonal forecasts via the CDS API.

    Needs ``CDSAPI_KEY`` (and ``CDSAPI_URL``). Downloads monthly-mean SST over
    the Nino 3.4 box for the forecast horizon and returns one row per
    (member, lead).

    Two things the first live attempt taught us, both reported by the CDS as a
    single unhelpful "Request has not produced a valid combination of values":

    * ``system`` is mandatory for the seasonal datasets. ``originating_centre``
      alone is not enough -- a centre publishes several numbered systems and the
      API will not choose one for you.
    * ``leadtime_month`` must stay inside the system's actual horizon. We asked
      for 1-12; ECMWF SEAS5 publishes 1-6, so every request was invalid no
      matter what else was right.

    Candidate systems are tried in order because the current ECMWF system
    number changes with each model upgrade, and a hard-coded one silently
    expires. Every attempt is reported on failure -- guessing a remote contract
    and reporting only the last guess is what made this take two rounds.
    """

    #: Newest first. 51 is SEAS5.1, 5 is the long-standing SEAS5.
    DEFAULT_SYSTEMS = ["51", "5"]

    #: Seconds to wait for one CDS job before abandoning it. The CDS is a
    #: queue in front of a tape archive: a request can sit in "accepted" for
    #: an unbounded time, and cdsapi polls forever by default. One slow
    #: retrieval used to stall the whole refresh -- and because this workflow
    #: holds a concurrency group, a stalled refresh blocks every later run
    #: too. A forecast we cannot fetch in time is a seeded plume, not a
    #: reason to stop publishing.
    DEFAULT_CDS_TIMEOUT_S = 420

    @property
    def dataset(self) -> str:
        """Per-source, not per-class.

        Both C3S blocks previously wrote to one key, so whichever ran second
        silently overwrote the first -- two live sources, one surviving
        dataset, and no sign anything had been lost.
        """
        return str(self.spec.get("dataset_key") or "c3s_seasonal_nino34")

    def _retrieve_bounded(self, client, request: dict[str, Any], target: Path) -> None:
        """Run one CDS retrieval under a hard deadline.

        The worker is a daemon thread so that abandoning it cannot keep the
        process alive; the CDS job itself carries on server-side and its result
        will be cached for the next run, which is why giving up here is cheap.
        """
        import threading

        limit = float(self.spec.get("cds_timeout_s", self.DEFAULT_CDS_TIMEOUT_S))
        box: dict[str, BaseException] = {}

        def work() -> None:
            try:
                client.retrieve(self.spec["dataset"], request, str(target))
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(limit)
        if thread.is_alive():
            raise TimeoutError(
                f"CDS did not deliver within {limit:.0f}s (job continues "
                f"server-side and should be cached for the next run)"
            )
        if "error" in box:
            raise box["error"]

    def _client(self):
        try:
            import cdsapi
        except ImportError as exc:
            raise SourceUnavailable(
                f"{self.name}: cdsapi not installed. CI installs "
                f"'.[dev,forecast]'; locally use pip install '.[forecast]'."
            ) from exc
        # The URL is a constant, not a secret. Requiring it as one meant a
        # correctly-supplied key still produced a dormant source.
        return cdsapi.Client(
            url=os.environ.get("CDSAPI_URL") or "https://cds.climate.copernicus.eu/api",
            key=os.environ["CDSAPI_KEY"],
        )

    def _requests(self) -> list[dict[str, Any]]:
        """Candidate requests, most-likely first."""
        now = pd.Timestamp.utcnow()
        lead_max = int(self.spec.get("leadtime_max", 6))
        systems = [str(s) for s in self.spec.get("systems", self.DEFAULT_SYSTEMS)]
        if self.spec.get("system"):
            systems = [str(self.spec["system"])] + [
                s for s in systems if s != str(self.spec["system"])
            ]

        out = []
        for system in systems:
            for fmt_key in ("data_format", "format"):
                out.append({
                    fmt_key: "netcdf",
                    "originating_centre": self.spec.get("originating_centre", "ecmwf"),
                    "system": system,
                    "variable": self.spec.get("variables", ["sea_surface_temperature"]),
                    "product_type": ["monthly_mean"],
                    "year": str(now.year),
                    "month": f"{now.month:02d}",
                    "leadtime_month": [str(i) for i in range(1, lead_max + 1)],
                    "area": [
                        NINO34_BOX["lat"][1], NINO34_BOX["lon"][0] - 360,
                        NINO34_BOX["lat"][0], NINO34_BOX["lon"][1] - 360,
                    ],
                })
        return out

    def _label(self, request: dict[str, Any]) -> str:
        return (
            f"system={request.get('system')} "
            f"{'data_format' if 'data_format' in request else 'format'}=netcdf "
            f"lead=1-{request['leadtime_month'][-1]} "
            f"init={request['year']}-{request['month']}"
        )

    def _to_frame(self, path: Path, request: dict[str, Any]) -> pd.DataFrame:
        import xarray as xr

        ds = xr.open_dataset(path)
        var = next(iter(ds.data_vars))
        spatial = [d for d in ds[var].dims if d in ("latitude", "longitude", "lat", "lon")]
        df = ds[var].mean(dim=spatial).to_dataframe().reset_index()
        df = df.rename(columns={var: "sst"})
        if "sst" in df and pd.notna(df["sst"].max()) and df["sst"].max() > 100:
            df["sst"] = df["sst"] - 273.15      # Kelvin -> Celsius
        df["source_system"] = f"{request.get('originating_centre')}-{request.get('system')}"
        return df

    def fetch_authenticated(self) -> FetchResult:
        client = self._client()
        attempts: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cds.nc"
            for request in self._requests():
                try:
                    self._retrieve_bounded(client, request, target)
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"{self._label(request)} -> {type(exc).__name__}: {exc}")
                    continue

                df = self._to_frame(target, request)
                init = f"{request['year']}-{request['month']}-01"
                return FetchResult(
                    dataset=self.dataset,
                    frame=df,
                    source=f"Copernicus C3S ({df['source_system'].iloc[0]})",
                    source_url=self.url,
                    valid_time=init,
                    notes=[
                        f"{self._label(request)}, {len(df)} rows"
                    ] + ([f"{len(attempts)} earlier combination(s) rejected"] if attempts else []),
                )

        raise SourceUnavailable(
            f"{self.name}: every request combination was rejected for "
            f"{self.spec['dataset']}.\n  " + "\n  ".join(attempts)
            + "\n  A 400 'invalid combination' means auth and licence are fine and "
              "the parameters are not; a 403 means the dataset licence still needs "
              "accepting once in the CDS web interface."
        )


@register("cds_reanalysis")
class CDSReanalysisConnector(CDSSeasonalConnector):
    """ERA5 monthly means -- supplies the anomaly baseline.

    This used to inherit the seasonal request wholesale, which is the wrong
    shape entirely: a reanalysis has no originating_centre, no system and no
    leadtime, and its product_type is `monthly_averaged_reanalysis`. The CDS
    replied "None of the data you have requested is available yet" -- true, but
    only because we had also asked for the current month. ERA5 monthly means
    run roughly two months behind, so the current month never exists.
    """

    @property
    def dataset(self) -> str:
        return str(self.spec.get("dataset_key") or "era5_monthly")

    def _requests(self) -> list[dict[str, Any]]:
        now = pd.Timestamp.utcnow().normalize().replace(day=1)
        lag_start = int(self.spec.get("lag_months", 2))
        out = []
        # Walk backwards rather than assume a fixed publication lag: the lag is
        # a property of ERA5's release schedule, not something we control.
        for back in range(lag_start, lag_start + 4):
            month = now - pd.DateOffset(months=back)
            for fmt_key in ("data_format", "format"):
                out.append({
                    fmt_key: "netcdf",
                    "product_type": ["monthly_averaged_reanalysis"],
                    "variable": self.spec.get("variables", ["2m_temperature"]),
                    "year": str(month.year),
                    "month": f"{month.month:02d}",
                    "time": ["00:00"],
                })
        return out

    def _label(self, request: dict[str, Any]) -> str:
        return (
            f"{'data_format' if 'data_format' in request else 'format'}=netcdf "
            f"month={request['year']}-{request['month']}"
        )

    def _to_frame(self, path: Path, request: dict[str, Any]) -> pd.DataFrame:
        import xarray as xr

        ds = xr.open_dataset(path)
        df = ds.to_dataframe().reset_index()
        df["source_system"] = "era5"
        return df

    def fetch_authenticated(self) -> FetchResult:
        result = super().fetch_authenticated()
        return result


@register("cds_atmosphere")
class CDSAtmosphereConnector(KeyedConnector):
    """CAMS GFAS fire radiative power via the Atmosphere Data Store."""

    dataset = "gfas_fire"

    def fetch_authenticated(self) -> FetchResult:
        try:
            import cdsapi
            import xarray as xr
        except ImportError as exc:
            raise SourceUnavailable(f"{self.name}: install the geo extra") from exc

        client = cdsapi.Client(
            url=os.environ["ADSAPI_URL"], key=os.environ["ADSAPI_KEY"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "gfas.nc"
            client.retrieve(
                self.spec["dataset"],
                {
                    "variable": self.spec.get("variables", ["frpfire"]),
                    "date": pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
                    "format": "netcdf",
                },
                str(target),
            )
            ds = xr.open_dataset(target)
            df = ds.to_dataframe().reset_index()

        return FetchResult(
            dataset=self.dataset, frame=df,
            source="CAMS GFAS", source_url=self.url,
        )


@register("nmme_thredds")
class NMMEThreddsConnector(KeyedConnector):
    """North American Multi-Model Ensemble via OPeNDAP.

    Public THREDDS endpoints are often open, so this connector treats
    credentials as optional: if the env vars are absent it still attempts an
    anonymous read and only reports dormant if that also fails.
    """

    dataset = "nmme_nino34"

    def available(self) -> bool:
        return True     # anonymous attempt is legitimate here

    def fetch(self) -> FetchResult:
        return self.fetch_authenticated()

    def fetch_authenticated(self) -> FetchResult:
        try:
            import xarray as xr
        except ImportError as exc:
            raise SourceUnavailable(f"{self.name}: install the geo extra") from exc

        try:
            ds = xr.open_dataset(self.url)
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailable(
                f"{self.name}: OPeNDAP open failed ({exc}). Anonymous access to "
                f"the NMME THREDDS server was refused or the endpoint moved."
            ) from exc

        var = "sst" if "sst" in ds.data_vars else next(iter(ds.data_vars))
        sel = ds[var].sel(
            lat=slice(*NINO34_BOX["lat"]), lon=slice(*NINO34_BOX["lon"])
        ).mean(dim=["lat", "lon"])
        df = sel.to_dataframe().reset_index()
        df = df.rename(columns={var: "nino34"})
        df["n_members"] = ds.sizes.get("M", np.nan)

        return FetchResult(
            dataset=self.dataset, frame=df,
            source="NOAA NMME", source_url=self.url,
        )


@register("generic_http_grib")
class GenericGribConnector(KeyedConnector):
    """Token-gated GRIB/NetCDF products (JAMSTEC SINTEX-F and similar)."""

    dataset = "generic_grib"

    def fetch_authenticated(self) -> FetchResult:
        try:
            import xarray as xr
        except ImportError as exc:
            raise SourceUnavailable(f"{self.name}: install the geo extra") from exc

        token_var = (self.spec.get("env") or ["TOKEN"])[0]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "payload.nc"
            import requests
            resp = requests.get(
                self.url,
                headers={"Authorization": f"Bearer {os.environ[token_var]}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            target.write_bytes(resp.content)
            ds = xr.open_dataset(target)
            df = ds.to_dataframe().reset_index()

        return FetchResult(
            dataset=self.name, frame=df,
            source=self.spec.get("description", self.name), source_url=self.url,
        )


@register("generic_netcdf")
class GenericNetcdfConnector(GenericGribConnector):
    """Open NetCDF archives (SPEIbase)."""
    keyless = True

    def available(self) -> bool:
        return True

    def fetch(self) -> FetchResult:
        return self.fetch_authenticated()


@register("chirps")
class ChirpsConnector(KeyedConnector):
    """CHIRPS v2 monthly precipitation GeoTIFFs.

    Keyless in principle but heavy: each global monthly tile is ~15 MB and
    zonal statistics need rasterio + geopandas. Enabled by installing the geo
    extra; until then the precipitation layer is composite-derived and the map
    legend says so.
    """

    dataset = "chirps_precip"
    keyless = True

    def available(self) -> bool:
        try:
            import rasterio  # noqa: F401
            import geopandas  # noqa: F401
        except ImportError:
            return False
        return True

    def fetch(self) -> FetchResult:
        if not self.available():
            raise SourceUnavailable(
                f"{self.name}: dormant, geo extra not installed "
                f"(pip install '.[geo]'). Precipitation layer stays composite-derived."
            )
        return self.fetch_authenticated()

    def fetch_authenticated(self) -> FetchResult:
        import rasterio
        from rasterio.mask import mask
        import geopandas as gpd

        month = pd.Timestamp.utcnow().to_period("M") - 1
        tif = f"{self.url.rstrip('/')}/chirps-v2.0.{month.year}.{month.month:02d}.tif"

        world = gpd.read_file(
            os.environ.get("ENSO_ADMIN0_PATH", "data/geo/admin0.gpkg")
        )
        rows = []
        with rasterio.open(tif) as src:
            for _, row in world.iterrows():
                try:
                    clipped, _ = mask(src, [row.geometry], crop=True, nodata=np.nan)
                except Exception:  # noqa: BLE001
                    continue
                rows.append({
                    "iso3": row.get("ISO_A3") or row.get("iso3"),
                    "date": month.to_timestamp(),
                    "precip_mm": float(np.nanmean(clipped)),
                })

        if not rows:
            raise SourceUnavailable(f"{self.name}: zonal statistics produced no rows")
        return FetchResult(
            dataset=self.dataset, frame=pd.DataFrame(rows),
            source="CHIRPS v2.0", source_url=tif,
        )
