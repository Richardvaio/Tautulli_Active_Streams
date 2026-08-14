"""Tests for entry-scoped stream termination actions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.tautulli_active_streams.const import DOMAIN
from custom_components.tautulli_active_streams.services import (
    _async_terminate,
    _resolve_entry,
)


def _runtime() -> dict:
    return {
        "api": SimpleNamespace(),
        "sessions_coordinator": SimpleNamespace(data={"sessions": []}),
    }


def test_entry_is_required_when_multiple_servers_are_loaded() -> None:
    """A destructive action must never guess between servers."""
    hass = SimpleNamespace(data={DOMAIN: {"first": _runtime(), "second": _runtime()}})
    call = SimpleNamespace(data={})

    with pytest.raises(ServiceValidationError, match="config_entry_id is required"):
        _resolve_entry(hass, call)


def test_explicit_entry_is_resolved() -> None:
    """An explicit entry scopes the action to exactly one server."""
    selected = _runtime()
    hass = SimpleNamespace(data={DOMAIN: {"first": _runtime(), "selected": selected}})
    call = SimpleNamespace(data={"config_entry_id": "selected"})

    assert _resolve_entry(hass, call) == ("selected", selected)


@pytest.mark.asyncio
async def test_termination_returns_per_session_results() -> None:
    """Successful, rejected and failed requests are all reported."""
    api = SimpleNamespace(
        terminate_session=AsyncMock(
            side_effect=[True, False, RuntimeError("connection lost")]
        )
    )
    sessions = [
        {"session_id": "one"},
        {"session_id": "two"},
        {"session_id": "three"},
    ]

    result = await _async_terminate(api, sessions, "Finished")

    assert result == {
        "requested": 3,
        "succeeded": ["one"],
        "failed": [
            {"session_id": "two", "reason": "Tautulli rejected the request"},
            {"session_id": "three", "reason": "RuntimeError"},
        ],
    }
