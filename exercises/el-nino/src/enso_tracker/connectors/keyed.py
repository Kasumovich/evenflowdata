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
 
    Requires ``CDSAPI_URL`` and ``CDSAPI_KEY`` (or ``~/.cdsapirc``). Downloads
    monthly-mean SST for the forecast horizon, computes the Nino 3.4 box mean
    per ensemble member, and returns one row per (member, lead).
    """
 
    dataset = "c3s_seasonal_nino34"
 
    def fetch_authenticated(self) -> FetchResult:
        try:
            import cdsapi
            import xarray as xr
        except ImportError as exc:
            raise SourceUnavailable(
                f"{self.name}: cdsapi/xarray not installed. CI installs "
                f"'.[dev,forecast]'; locally use pip install '.[forecast]'."
            ) from exc
 
        client = cdsapi.Client(
            url=os.environ["CDSAPI_URL"], key=os.environ["CDSAPI_KEY"]
        )
 
        # The CDS was rebuilt in 2024-25 and renamed this key: the old API took
        # "format", the current one takes "data_format" and rejects the old
        # spelling on some datasets. We cannot tell from here which the live
        # endpoint wants -- this connector has never run against a real
        # credential -- so try the current spelling first and fall back, rather
        # than guessing once and reporting a bare failure. Guessing a remote
        # contract and shipping it untested is how this project lost two days.
        base: dict[str, Any] = {
            "originating_centre": self.spec.get("originating_centre", "ecmwf"),
            "variable": self.spec.get("variables", ["sea_surface_temperature"]),
            "product_type": ["monthly_mean"],
            "year": str(pd.Timestamp.utcnow().year),
            "month": f"{pd.Timestamp.utcnow().month:02d}",
            "leadtime_month": [str(i) for i in range(1, 13)],
            "area": [
                NINO34_BOX["lat"][1], NINO34_BOX["lon"][0] - 360,
                NINO34_BOX["lat"][0], NINO34_BOX["lon"][1] - 360,
            ],
        }
 
        attempts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "c3s.nc"
            for key in ("data_format", "format"):
                request = dict(base, **{key: "netcdf"})
                try:
                    client.retrieve(self.spec["dataset"], request, str(target))
                    break
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"{key}=netcdf -> {type(exc).__name__}: {exc}")
            else:
                raise SourceUnavailable(
                    f"{self.name}: CDS retrieve failed for "
                    f"{self.spec['dataset']}.\n  " + "\n  ".join(attempts)
                    + "\n  If this reads as a licence error, the dataset's terms "
                      "must be accepted once in the CDS web interface before the "
                      "API will serve it."
                )
 
            ds = xr.open_dataset(target)
            var = next(iter(ds.data_vars))
            box = ds[var].mean(dim=[d for d in ds[var].dims if d in ("latitude", "longitude")])
            df = box.to_dataframe().reset_index()
 
        df = df.rename(columns={var: "sst"})
        if "sst" in df and df["sst"].max() > 100:      # Kelvin -> Celsius
            df["sst"] = df["sst"] - 273.15
        df["source_system"] = base["originating_centre"]
 
        # A forecast's observation date is its initialisation, not the moment we
        # downloaded it. Omitting this made the freshness machinery treat the
        # result as "age unknown", which sorts last and would have let a stale
        # seed outrank a current forecast.
        init = f"{base['year']}-{base['month']}-01"
 
        return FetchResult(
            dataset=self.dataset,
            frame=df,
            source=f"Copernicus C3S ({base['originating_centre']})",
            source_url=self.url,
            valid_time=init,
            notes=[f"initialised {init}, {len(df)} member-lead rows"],
        )
 
 
@register("cds_reanalysis")
class CDSReanalysisConnector(CDSSeasonalConnector):
    """ERA5 monthly means -- supplies the 1991-2020 anomaly baseline."""
    dataset = "era5_monthly"
 
 
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
