"""Authenticated WebSocket API consumed by the dashboard card."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback

from .api import TautulliAuthError, TautulliConnectionError
from .card_api import CARD_API_SCHEMA_VERSION, card_capabilities
from .const import CONF_CARD_ALLOW_HISTORY, CONF_CARD_ALLOW_TERMINATION, DOMAIN
from .serializers import (
    active_stream_envelope,
    card_envelope,
    serialize_history_item,
    serialize_library,
    serialize_media_item,
    serialize_stat_item,
    serialize_user,
    serialize_user_stats,
)

ERR_ENTRY_NOT_FOUND = "entry_not_found"
ERR_ENTRY_NOT_LOADED = "entry_not_loaded"


async def _cached_card_data(
    hass, entry, runtime, connection, msg_id: int, key: str, ttl: float, fetch
):
    """Fetch cached card data with reauthentication and clean errors."""
    try:
        return await runtime.card_cache.get_or_fetch(key, ttl, fetch)
    except TautulliAuthError:
        entry.async_start_reauth(hass)
        connection.send_error(
            msg_id, "authentication_failed", "Tautulli reauthentication is required"
        )
    except TautulliConnectionError:
        connection.send_error(
            msg_id, "cannot_connect", "Tautulli is temporarily unavailable"
        )
    return None


def _loaded_entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None, ERR_ENTRY_NOT_FOUND
    if entry.state is not ConfigEntryState.LOADED:
        return None, ERR_ENTRY_NOT_LOADED
    data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(data, dict) or "sessions_coordinator" not in data:
        return None, ERR_ENTRY_NOT_LOADED
    return (entry, data), None


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_entries"})
@callback
def websocket_get_entries(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List loaded Tautulli entries for the card editor."""
    entries = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        entries.append(
            {
                "entry_id": entry.entry_id,
                CONF_NAME: entry.title,
                "server_id": str(entry.unique_id or entry.entry_id),
                "schema_version": CARD_API_SCHEMA_VERSION,
                "capabilities": card_capabilities(entry),
            }
        )
    connection.send_result(
        msg["id"],
        {"schema_version": CARD_API_SCHEMA_VERSION, "entries": entries},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe_active_streams",
        vol.Required("entry_id"): str,
    }
)
@callback
def websocket_subscribe_active_streams(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Subscribe to privacy-safe, normalized active-stream updates."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        connection.send_error(msg["id"], error, "Tautulli entry is not available")
        return
    entry, data = resolved
    coordinator = data["sessions_coordinator"]

    @callback
    def send_update() -> None:
        coordinator_data = coordinator.data or {}
        connection.send_event(
            msg["id"],
            active_stream_envelope(
                hass,
                entry,
                list(coordinator_data.get("sessions", [])),
                stale=not coordinator.last_update_success,
            ),
        )

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(send_update)
    connection.send_result(msg["id"])
    send_update()


def _send_entry_error(connection, msg_id: int, error: str | None) -> None:
    connection.send_error(
        msg_id, error or ERR_ENTRY_NOT_LOADED, "Tautulli entry is not available"
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_recently_added",
        vol.Required("entry_id"): str,
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("limit", default=20): vol.All(int, vol.Range(min=1, max=50)),
        vol.Optional("media_type"): vol.In(["movie", "show", "artist"]),
        vol.Optional("section_id"): str,
    }
)
@websocket_api.async_response
async def websocket_get_recently_added(hass, connection, msg) -> None:
    """Return a cached page of normalized recently-added media."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        _send_entry_error(connection, msg["id"], error)
        return
    entry, data = resolved
    runtime = data["runtime"]
    offset, limit = msg["offset"], msg["limit"]
    media_type, section_id = msg.get("media_type"), msg.get("section_id")
    key = f"recent:{offset}:{limit}:{media_type or '*'}:{section_id or '*'}"

    async def fetch():
        return await runtime.api.get_recently_added(
            start=offset,
            count=limit,
            media_type=media_type,
            section_id=section_id,
        )

    cached = await _cached_card_data(
        hass, entry, runtime, connection, msg["id"], key, 300, fetch
    )
    if cached is None:
        return
    payload, stale = cached
    rows = payload.get("recently_added", []) if isinstance(payload, dict) else []
    rows = rows if isinstance(rows, list) else []
    items = [serialize_media_item(hass, entry, row) for row in rows]
    next_offset = offset + len(items) if len(items) == limit else None
    connection.send_result(
        msg["id"],
        card_envelope(entry, items, stale=stale, next_offset=next_offset),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_home_stats",
        vol.Required("entry_id"): str,
        vol.Required("stat_id"): vol.In(
            [
                "top_movies",
                "popular_movies",
                "top_tv",
                "popular_tv",
                "top_music",
                "popular_music",
                "top_libraries",
                "top_users",
                "top_platforms",
                "last_watched",
                "most_concurrent",
            ]
        ),
        vol.Optional("time_range", default=30): vol.All(
            int, vol.Range(min=1, max=3650)
        ),
        vol.Optional("metric", default="plays"): vol.In(["plays", "duration"]),
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("limit", default=10): vol.All(int, vol.Range(min=1, max=50)),
        vol.Optional("section_id"): str,
        vol.Optional("user_id"): str,
    }
)
@websocket_api.async_response
async def websocket_get_home_stats(hass, connection, msg) -> None:
    """Return one cached, normalized popular/top statistic collection."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        _send_entry_error(connection, msg["id"], error)
        return
    entry, data = resolved
    runtime = data["runtime"]
    key = "stats:{stat_id}:{time_range}:{metric}:{offset}:{limit}:{section}:{user}".format(
        stat_id=msg["stat_id"],
        time_range=msg["time_range"],
        metric=msg["metric"],
        offset=msg["offset"],
        limit=msg["limit"],
        section=msg.get("section_id", "*"),
        user=msg.get("user_id", "*"),
    )

    async def fetch():
        return await runtime.api.get_home_stats(
            stat_id=msg["stat_id"],
            time_range=msg["time_range"],
            stats_type=msg["metric"],
            start=msg["offset"],
            count=msg["limit"],
            section_id=msg.get("section_id"),
            user_id=msg.get("user_id"),
        )

    cached = await _cached_card_data(
        hass, entry, runtime, connection, msg["id"], key, 900, fetch
    )
    if cached is None:
        return
    payload, stale = cached
    rows: list[dict[str, Any]] = []
    groups = [payload] if isinstance(payload, dict) else payload
    for group in groups if isinstance(groups, list) else []:
        if isinstance(group, dict) and isinstance(group.get("rows"), list):
            rows.extend(row for row in group["rows"] if isinstance(row, dict))
    if not rows and isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
    items = [
        serialize_stat_item(
            hass,
            entry,
            row,
            rank=msg["offset"] + index + 1,
            kind=msg["stat_id"],
            metric=msg["metric"],
        )
        for index, row in enumerate(rows[: msg["limit"]])
    ]
    next_offset = msg["offset"] + len(items) if len(items) == msg["limit"] else None
    connection.send_result(
        msg["id"], card_envelope(entry, items, stale=stale, next_offset=next_offset)
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/get_users", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def websocket_get_users(hass, connection, msg) -> None:
    """Return stable user selector values."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        _send_entry_error(connection, msg["id"], error)
        return
    entry, data = resolved
    runtime = data["runtime"]
    cached = await _cached_card_data(
        hass,
        entry,
        runtime,
        connection,
        msg["id"],
        "users",
        3600,
        runtime.api.get_user_names,
    )
    if cached is None:
        return
    rows, stale = cached
    items = [serialize_user(entry, row) for row in rows if isinstance(row, dict)]
    connection.send_result(msg["id"], card_envelope(entry, items, stale=stale))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/get_libraries", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def websocket_get_libraries(hass, connection, msg) -> None:
    """Return stable library selector values."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        _send_entry_error(connection, msg["id"], error)
        return
    entry, data = resolved
    runtime = data["runtime"]
    cached = await _cached_card_data(
        hass,
        entry,
        runtime,
        connection,
        msg["id"],
        "libraries",
        3600,
        runtime.api.get_library_names,
    )
    if cached is None:
        return
    rows, stale = cached
    items = [serialize_library(entry, row) for row in rows if isinstance(row, dict)]
    connection.send_result(msg["id"], card_envelope(entry, items, stale=stale))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/get_user_stats", vol.Required("entry_id"): str}
)
@callback
def websocket_get_user_stats(hass, connection, msg) -> None:
    """Return current bounded user aggregates from the history coordinator."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        _send_entry_error(connection, msg["id"], error)
        return
    entry, data = resolved
    coordinator = data["history_coordinator"]
    user_stats = (coordinator.data or {}).get("user_stats", {})
    items = [
        serialize_user_stats(entry, stats)
        for stats in user_stats.values()
        if isinstance(stats, dict)
    ]
    items.sort(key=lambda item: item["total_duration_seconds"], reverse=True)
    connection.send_result(
        msg["id"],
        card_envelope(
            entry,
            items,
            stale=not coordinator.last_update_success,
            total=len(items),
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_history",
        vol.Required("entry_id"): str,
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("limit", default=25): vol.All(int, vol.Range(min=1, max=100)),
        vol.Optional("user_id"): str,
        vol.Optional("media_type"): vol.In(["movie", "episode", "track"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_get_history(hass, connection, msg) -> None:
    """Return an admin-only, paginated history view."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        _send_entry_error(connection, msg["id"], error)
        return
    entry, data = resolved
    if not entry.options.get(CONF_CARD_ALLOW_HISTORY, True):
        connection.send_error(
            msg["id"], "history_disabled", "Card history access is disabled"
        )
        return
    runtime = data["runtime"]
    params = {
        "start": msg["offset"],
        "length": msg["limit"],
        "grouping": 0,
        "order_column": "date",
        "order_dir": "desc",
    }
    if msg.get("user_id"):
        params["user_id"] = msg["user_id"]
    if msg.get("media_type"):
        params["media_type"] = msg["media_type"]
    key = f"history:{msg['offset']}:{msg['limit']}:{msg.get('user_id', '*')}:{msg.get('media_type', '*')}"

    async def fetch():
        return await runtime.api.get_history(**params)

    cached = await _cached_card_data(
        hass, entry, runtime, connection, msg["id"], key, 60, fetch
    )
    if cached is None:
        return
    payload, stale = cached
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    items = [
        serialize_history_item(hass, entry, row)
        for row in rows
        if isinstance(row, dict)
    ]
    total = (
        int(payload.get("recordsFiltered", len(items)))
        if isinstance(payload, dict)
        else len(items)
    )
    next_offset = (
        msg["offset"] + len(items) if msg["offset"] + len(items) < total else None
    )
    connection.send_result(
        msg["id"],
        card_envelope(entry, items, stale=stale, next_offset=next_offset, total=total),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/terminate_session",
        vol.Required("entry_id"): str,
        vol.Required("session_id"): str,
        vol.Optional("message", default="Stream ended by admin."): vol.All(
            str, vol.Length(max=250)
        ),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_terminate_session(hass, connection, msg) -> None:
    """Terminate one entry-scoped active session after card confirmation."""
    resolved, error = _loaded_entry(hass, msg["entry_id"])
    if error:
        _send_entry_error(connection, msg["id"], error)
        return
    entry, data = resolved
    if not entry.options.get(CONF_CARD_ALLOW_TERMINATION, False):
        connection.send_error(
            msg["id"],
            "termination_disabled",
            "Card stream termination is disabled",
        )
        return
    sessions = list((data["sessions_coordinator"].data or {}).get("sessions", []))
    session = next(
        (
            row
            for row in sessions
            if str(row.get("session_id") or "") == msg["session_id"]
        ),
        None,
    )
    if session is None:
        connection.send_error(
            msg["id"], "session_not_found", "The stream is no longer active"
        )
        return
    try:
        succeeded = await data["api"].terminate_session(
            msg["session_id"], msg["message"]
        )
    except TautulliAuthError:
        entry.async_start_reauth(hass)
        connection.send_error(
            msg["id"],
            "authentication_failed",
            "Tautulli reauthentication is required",
        )
        return
    except TautulliConnectionError:
        connection.send_error(
            msg["id"], "cannot_connect", "Tautulli is temporarily unavailable"
        )
        return
    if succeeded:
        await data["sessions_coordinator"].async_request_refresh()
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "session_id": msg["session_id"],
            "succeeded": succeeded,
        },
    )


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register card API commands once per Home Assistant instance."""
    websocket_api.async_register_command(hass, websocket_get_entries)
    websocket_api.async_register_command(hass, websocket_subscribe_active_streams)
    websocket_api.async_register_command(hass, websocket_get_recently_added)
    websocket_api.async_register_command(hass, websocket_get_home_stats)
    websocket_api.async_register_command(hass, websocket_get_users)
    websocket_api.async_register_command(hass, websocket_get_libraries)
    websocket_api.async_register_command(hass, websocket_get_user_stats)
    websocket_api.async_register_command(hass, websocket_get_history)
    websocket_api.async_register_command(hass, websocket_terminate_session)
