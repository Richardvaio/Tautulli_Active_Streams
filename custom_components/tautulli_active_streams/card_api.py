"""Shared version and capability declarations for dashboard clients."""

from __future__ import annotations

from typing import Final

CARD_API_SCHEMA_VERSION: Final = 1

CAPABILITIES: Final = {
    "active_streams": True,
    "active_stream_subscription": True,
    "recently_added": True,
    "home_stats": True,
    "users": True,
    "user_stats": True,
    "libraries": True,
    "history": True,
    "stream_termination": False,
}


def card_capabilities(entry) -> dict[str, bool]:
    """Return entry-specific capabilities and explicit privacy permissions."""
    from .const import CONF_CARD_ALLOW_HISTORY, CONF_CARD_ALLOW_TERMINATION

    capabilities = dict(CAPABILITIES)
    capabilities["history"] = entry.options.get(CONF_CARD_ALLOW_HISTORY, True)
    capabilities["stream_termination"] = entry.options.get(
        CONF_CARD_ALLOW_TERMINATION, False
    )
    return capabilities
