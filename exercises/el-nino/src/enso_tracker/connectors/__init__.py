"""Connector package.

Importing this module populates the registry. Submodules are imported for
their side effects (the ``@register`` decorators); the ``noqa`` markers are
deliberate.
"""

from .base import (  # noqa: F401
    Connector,
    FetchResult,
    KeyedConnector,
    SourceUnavailable,
    get_connector,
    register,
    registered,
)
from . import climate  # noqa: F401,E402
from . import markets  # noqa: F401,E402
from . import keyed    # noqa: F401,E402
from . import seed     # noqa: F401,E402

__all__ = [
    "Connector",
    "FetchResult",
    "KeyedConnector",
    "SourceUnavailable",
    "get_connector",
    "register",
    "registered",
]
