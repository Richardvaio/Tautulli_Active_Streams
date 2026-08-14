"""Privacy-safe diagnostics for Tautulli Active Streams."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .card_api import CARD_API_SCHEMA_VERSION, card_capabilities
from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return useful state without credentials, IPs, paths, or media titles."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    sessions = data.get("sessions_coordinator")
    history = data.get("history_coordinator")
    session_data = sessions.data if sessions and sessions.data else {}
    history_data = history.data if history and history.data else {}
    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "state": entry.state.value,
            "options": dict(entry.options),
        },
        "card_api": {
            "schema_version": CARD_API_SCHEMA_VERSION,
            "capabilities": card_capabilities(entry),
        },
        "coordinators": {
            "sessions": {
                "last_update_success": bool(sessions and sessions.last_update_success),
                "active_count": len(session_data.get("sessions", [])),
                "diagnostics": dict(session_data.get("diagnostics", {})),
            },
            "history": {
                "last_update_success": bool(history and history.last_update_success),
                "user_count": len(history_data.get("user_stats", {})),
            },
        },
        "device_count": len(devices),
    }
