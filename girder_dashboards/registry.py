"""Registry of dashboards that are *available* in this Girder instance.

A dashboard is a small, self-contained JS app that operates on the data
gathered in a Girder instance. It has two halves:

* a Python declaration (this registry) describing that the dashboard exists
  and what its card looks like, and
* a ``web_client`` bundle that registers a Backbone view under the same key
  via ``girder.plugins.dashboards.registerDashboard``.

The Python half is what makes the set of available dashboards discoverable on
the server, so the admin config page can enable/disable them and so cards can
be rendered without loading every dashboard implementation up front.

Plugins register their dashboards from ``load()``::

    from girder_dashboards import registerDashboard

    class MyPlugin(GirderPlugin):
        def load(self, info):
            getPlugin("dashboards").load(info)
            registerDashboard(
                "my-dashboard",
                name="My Dashboard",
                description="Does something useful.",
            )
"""

import logging
import re
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)

#: Keys are used in URLs and as the join between the server-side definition and
#: the client-side view, so keep them to a conservative slug alphabet.
KEY_REGEX = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

DEFAULT_ICON = "icon-gauge"


@dataclass(frozen=True)
class DashboardDefinition:
    """Metadata describing a single available dashboard."""

    key: str
    name: str
    description: str = ""
    image: str | None = None
    icon: str = DEFAULT_ICON
    settings: dict = field(default_factory=dict)

    def asdict(self) -> dict:
        return asdict(self)


_dashboards: dict[str, DashboardDefinition] = {}
_listeners: list = []


def addRegistrationListener(listener) -> None:
    """Call ``listener(definition)`` whenever a dashboard is registered.

    The dashboards plugin uses this to give every newly registered dashboard a
    document, so plugins that load *after* it still get provisioned without
    waiting for a restart. A listener that raises is logged and ignored, since a
    bookkeeping failure must not take a plugin's ``load()`` down with it.
    """
    if listener not in _listeners:
        _listeners.append(listener)


def _notify(definition: DashboardDefinition) -> None:
    for listener in _listeners:
        try:
            listener(definition)
        except Exception:
            logger.exception(
                "Dashboard registration listener failed for '%s'", definition.key
            )


def registerDashboard(
    key: str,
    name: str,
    description: str = "",
    image: str | None = None,
    icon: str = DEFAULT_ICON,
    settings: dict | None = None,
) -> DashboardDefinition:
    """Declare a dashboard as available.

    Registering the same key twice replaces the previous definition, which
    keeps the call idempotent across plugin reloads.

    :param key: Slug identifying the dashboard; must match the key the
        web client registers its view under.
    :param name: Human readable name shown on the dashboard card.
    :param description: Longer text shown on the dashboard card.
    :param image: URL (or data URI) of the card image. ``None`` falls back to
        rendering ``icon`` instead.
    :param icon: Fontello icon class used when there is no image.
    :param settings: Default per-dashboard settings, editable by admins.
    :returns: The registered definition.
    """
    if not key or not KEY_REGEX.match(key):
        raise ValueError(
            f"Invalid dashboard key {key!r}: must match {KEY_REGEX.pattern}"
        )
    if not name:
        raise ValueError(f"Dashboard {key!r} must have a non-empty name")
    if settings is not None and not isinstance(settings, dict):
        raise ValueError(f"Dashboard {key!r} settings must be a dict")

    if key in _dashboards:
        logger.debug("Replacing already registered dashboard %r", key)

    definition = DashboardDefinition(
        key=key,
        name=name,
        description=description or "",
        image=image,
        icon=icon or DEFAULT_ICON,
        settings=dict(settings or {}),
    )
    _dashboards[key] = definition
    _notify(definition)
    return definition


def unregisterDashboard(key: str) -> None:
    """Remove a dashboard from the registry, if present."""
    _dashboards.pop(key, None)


def getDashboard(key: str) -> DashboardDefinition | None:
    """Return the definition registered under ``key``, or ``None``."""
    return _dashboards.get(key)


def listDashboards() -> list[DashboardDefinition]:
    """Return all registered definitions, sorted by name."""
    return sorted(_dashboards.values(), key=lambda d: (d.name.lower(), d.key))


def registeredKeys() -> list[str]:
    """Return the keys of all registered dashboards."""
    return list(_dashboards.keys())
