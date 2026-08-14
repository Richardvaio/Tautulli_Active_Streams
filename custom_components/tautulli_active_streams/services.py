from __future__ import annotations

import asyncio
import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_MESSAGE = "message"
ATTR_SESSION_ID = "session_id"
ATTR_USER = "user"
ATTR_USER_ID = "user_id"
DEFAULT_TERMINATION_MESSAGE = "Stream ended by admin."

_BASE_FIELDS = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_MESSAGE, default=DEFAULT_TERMINATION_MESSAGE): cv.string,
}

KILL_ALL_SCHEMA = vol.Schema(_BASE_FIELDS)

KILL_USER_SCHEMA = vol.Schema(
    {
        **_BASE_FIELDS,
        vol.Optional(ATTR_USER): cv.string,
        vol.Optional(ATTR_USER_ID): cv.string,
    }
)

KILL_SESSION_SCHEMA = vol.Schema(
    {
        **_BASE_FIELDS,
        vol.Required(ATTR_SESSION_ID): cv.string,
    }
)


async def _async_require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Reject a user-initiated destructive action from a non-admin account."""
    if call.context.user_id is None:
        # Automations and other trusted internal calls may not have a user.
        return
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None or not user.is_admin:
        raise HomeAssistantError("Administrator permission is required")


def _loaded_entries(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Return loaded config entries with the required runtime objects."""
    return {
        entry_id: data
        for entry_id, data in hass.data.get(DOMAIN, {}).items()
        if isinstance(data, dict) and "api" in data and "sessions_coordinator" in data
    }


def _resolve_entry(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[str, dict[str, Any]]:
    """Resolve exactly one entry, requiring a selection when ambiguous."""
    entries = _loaded_entries(hass)
    requested_entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if requested_entry_id:
        data = entries.get(requested_entry_id)
        if data is None:
            raise ServiceValidationError(
                f"Tautulli config entry '{requested_entry_id}' is not loaded"
            )
        return requested_entry_id, data

    if not entries:
        raise ServiceValidationError("No Tautulli config entry is loaded")
    if len(entries) > 1:
        raise ServiceValidationError(
            "config_entry_id is required when multiple Tautulli servers are loaded"
        )
    return next(iter(entries.items()))


def _active_sessions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a snapshot of active sessions for one config entry."""
    coordinator = data["sessions_coordinator"]
    if not coordinator.data:
        return []
    return list(coordinator.data.get("sessions", []))


async def _async_terminate(
    api: Any, sessions: list[dict[str, Any]], message: str
) -> dict[str, Any]:
    """Terminate sessions and return a structured result."""
    session_ids = [session.get("session_id") for session in sessions]
    session_ids = [str(session_id) for session_id in session_ids if session_id]
    if not session_ids:
        return {"requested": 0, "succeeded": [], "failed": []}

    results = await asyncio.gather(
        *(
            api.terminate_session(session_id, message=message)
            for session_id in session_ids
        ),
        return_exceptions=True,
    )
    succeeded: list[str] = []
    failed: list[dict[str, str]] = []
    for session_id, result in zip(session_ids, results, strict=True):
        if result is True:
            succeeded.append(session_id)
            continue
        reason = "Tautulli rejected the request"
        if isinstance(result, Exception):
            reason = result.__class__.__name__
            _LOGGER.warning(
                "Tautulli session termination failed for session %s: %s",
                session_id,
                reason,
            )
        failed.append({"session_id": session_id, "reason": reason})

    return {
        "requested": len(session_ids),
        "succeeded": succeeded,
        "failed": failed,
    }


async def async_setup_kill_stream_services(
    hass: HomeAssistant, _entry: Any, _api: Any
) -> None:
    """Register entry-scoped, administrator-protected termination actions."""

    async def handle_kill_all_streams(call: ServiceCall) -> dict[str, Any] | None:
        await _async_require_admin(hass, call)
        entry_id, data = _resolve_entry(hass, call)
        result = await _async_terminate(
            data["api"], _active_sessions(data), call.data[ATTR_MESSAGE]
        )
        response = {"config_entry_id": entry_id, **result}
        return response if call.return_response else None

    async def handle_kill_user_streams(call: ServiceCall) -> dict[str, Any] | None:
        await _async_require_admin(hass, call)
        entry_id, data = _resolve_entry(hass, call)
        requested_user_id = str(call.data.get(ATTR_USER_ID, "")).strip()
        requested_name = str(call.data.get(ATTR_USER, "")).strip().casefold()
        if not requested_user_id and not requested_name:
            raise ServiceValidationError("user_id or user is required")

        matched = []
        for session in _active_sessions(data):
            if (
                requested_user_id
                and str(session.get("user_id", "")) == requested_user_id
            ):
                matched.append(session)
                continue
            names = {
                str(session.get(field) or "").casefold()
                for field in ("user", "username", "friendly_name")
            }
            if requested_name and requested_name in names:
                matched.append(session)

        result = await _async_terminate(data["api"], matched, call.data[ATTR_MESSAGE])
        response = {"config_entry_id": entry_id, **result}
        return response if call.return_response else None

    async def handle_kill_session_stream(call: ServiceCall) -> dict[str, Any] | None:
        await _async_require_admin(hass, call)
        entry_id, data = _resolve_entry(hass, call)
        session_id = call.data[ATTR_SESSION_ID].strip()
        matched = [
            session
            for session in _active_sessions(data)
            if str(session.get("session_id", "")) == session_id
        ]
        if not matched:
            raise ServiceValidationError(
                f"Session '{session_id}' is not active on the selected Tautulli server"
            )
        result = await _async_terminate(data["api"], matched, call.data[ATTR_MESSAGE])
        response = {"config_entry_id": entry_id, **result}
        return response if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        "kill_all_streams",
        handle_kill_all_streams,
        schema=KILL_ALL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "kill_user_streams",
        handle_kill_user_streams,
        schema=KILL_USER_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "kill_session_stream",
        handle_kill_session_stream,
        schema=KILL_SESSION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    _LOGGER.debug("Tautulli termination actions registered")
