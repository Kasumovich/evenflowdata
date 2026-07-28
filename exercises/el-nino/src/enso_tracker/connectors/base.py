"""Connector framework: registry, retry, and the last-good fallback.

Extension hook
--------------
A new source is a YAML block in ``config/sources.yaml`` plus a class
decorated with ``@register("my_connector")``. The orchestrator discovers it
by name; nothing in the core imports it explicitly.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

import pandas as pd

from ..config import Config, credentials_present

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type["Connector"]] = {}


def register(name: str) -> Callable[[type["Connector"]], type["Connector"]]:
    def _wrap(cls: type["Connector"]) -> type["Connector"]:
        _REGISTRY[name] = cls
        cls.connector_name = name
        return cls
    return _wrap


def get_connector(name: str) -> type["Connector"] | None:
    return _REGISTRY.get(name)


def registered() -> dict[str, type["Connector"]]:
    return dict(_REGISTRY)


class SourceUnavailable(RuntimeError):
    """Raised when a source cannot be reached or parsed. Never fatal."""


@dataclass
class FetchResult:
    dataset: str
    frame: pd.DataFrame
    source: str
    source_url: str | None = None
    provenance: str = "live"        # live | seed | cached
    stale: bool = False
    notes: list[str] = field(default_factory=list)
    #: The date the observation REFERS TO -- not when it was fetched. A feed
    #: can be reachable every six hours and still be publishing three-month-old
    #: values; only this field can detect that. ISO date, or None for a
    #: reference series that does not age.
    valid_time: str | None = None
    #: True for closed historical series that never go stale.
    reference_series: bool = False

    @property
    def empty(self) -> bool:
        return self.frame is None or self.frame.empty


class Connector(ABC):
    """Base class. Subclasses implement :meth:`fetch` only."""

    connector_name: ClassVar[str] = "base"
    #: Set False for connectors that cannot run without credentials.
    keyless: ClassVar[bool] = True

    def __init__(self, name: str, spec: dict[str, Any], cfg: Config) -> None:
        self.name = name
        self.spec = spec
        self.cfg = cfg
        self.defaults = cfg.source_defaults()

    # -- to implement ----------------------------------------------------

    @abstractmethod
    def fetch(self) -> FetchResult:
        """Retrieve and normalise. Raise SourceUnavailable on failure."""

    # -- shared plumbing -------------------------------------------------

    @property
    def url(self) -> str | None:
        return self.spec.get("url")

    @property
    def timeout(self) -> int:
        return int(self.spec.get("timeout_s", self.defaults.get("timeout_s", 45)))

    def available(self) -> bool:
        return credentials_present(self.spec)

    def http_get(self, url: str, **kwargs: Any) -> str:
        """GET with retry/backoff. Text response.

        Note for operators: this is the only place the system touches the
        network. If you are running somewhere with restricted egress, this is
        what will fail, and every caller degrades to last-good rather than
        raising through to the dashboard.
        """
        import requests

        retries = int(self.defaults.get("retries", 3))
        backoff = float(self.defaults.get("backoff_s", 5))
        headers = {"User-Agent": self.defaults.get("user_agent", "enso-tracker/1.0")}
        headers.update(kwargs.pop("headers", {}))

        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(
                    url, timeout=self.timeout, headers=headers, **kwargs
                )
                resp.raise_for_status()
                return resp.text
            except Exception as exc:  # noqa: BLE001 - deliberately broad
                last = exc
                log.warning(
                    "%s: attempt %d/%d failed for %s (%s)",
                    self.name, attempt, retries, url, exc,
                )
                if attempt < retries:
                    time.sleep(backoff * attempt)
        raise SourceUnavailable(f"{self.name}: {url} unreachable ({last})") from last

    def http_json(self, url: str, **kwargs: Any) -> Any:
        import json
        return json.loads(self.http_get(url, **kwargs))


class KeyedConnector(Connector):
    """Base for sources that need credentials.

    The implementation is real and follows each provider's documented request
    contract, but it stays dormant -- and says so -- until the env vars named
    in ``config/sources.yaml`` are present. This is why the dashboard can ship
    with genuine data today and light up the gridded/exchange layers the
    moment credentials land, with no code change.
    """

    keyless: ClassVar[bool] = False

    def fetch(self) -> FetchResult:
        if not self.available():
            missing = [v for v in self.spec.get("env", []) if not _env(v)]
            raise SourceUnavailable(
                f"{self.name}: dormant, missing credentials {missing}. "
                f"Set them in .env to activate; see docs/DEPLOYMENT.md."
            )
        return self.fetch_authenticated()

    @abstractmethod
    def fetch_authenticated(self) -> FetchResult:
        ...


def _env(var: str) -> str | None:
    import os
    return os.environ.get(var)
