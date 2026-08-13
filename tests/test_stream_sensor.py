"""Privacy and metadata tests for active-stream entities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.tautulli_active_streams.const import (
    CONF_ENABLE_IP_GEOLOCATION,
    CONF_EXPOSE_DETAILED_LOCATION,
)
from custom_components.tautulli_active_streams.stream_sensor import (
    TautulliStreamSensor,
)


def _sensor(options: dict) -> TautulliStreamSensor:
    sensor = object.__new__(TautulliStreamSensor)
    sensor._entry = SimpleNamespace(options=options, data={})
    sensor.coordinator = SimpleNamespace(
        data={
            "sessions": [
                {
                    "state": "playing",
                    "user": "Viewer",
                    "ip_address": "192.0.2.10",
                    "ip_address_public": "198.51.100.20",
                    "geo_city": "London",
                    "geo_region": "England",
                    "geo_country": "United Kingdom",
                    "geo_code": "GB",
                    "geo_latitude": 51.5,
                    "geo_longitude": -0.1,
                    "geo_postal_code": "AA1 1AA",
                }
            ]
        }
    )
    sensor._index = 0
    sensor._signed_image_urls = {}
    sensor._plex_metadata = {}
    sensor._paused_duration_str = "0m 0s"
    return sensor


def test_basic_attributes_do_not_expose_network_or_location() -> None:
    """Privacy-sensitive attributes are absent by default."""
    attributes = _sensor({}).extra_state_attributes

    assert "ip_address" not in attributes
    assert "ip_address_public" not in attributes
    assert "geo_city" not in attributes
    assert "geo_latitude" not in attributes


def test_coarse_location_requires_geolocation_opt_in() -> None:
    """Geolocation opt-in exposes coarse fields but never raw IPs."""
    attributes = _sensor(
        {CONF_ENABLE_IP_GEOLOCATION: True}
    ).extra_state_attributes

    assert attributes["geo_city"] == "London"
    assert attributes["geo_country"] == "United Kingdom"
    assert "ip_address" not in attributes
    assert "geo_latitude" not in attributes


def test_detailed_location_requires_separate_opt_in() -> None:
    """Raw IP and precise fields require the detailed privacy option."""
    attributes = _sensor(
        {
            CONF_ENABLE_IP_GEOLOCATION: True,
            CONF_EXPOSE_DETAILED_LOCATION: True,
        }
    ).extra_state_attributes

    assert attributes["ip_address_public"] == "198.51.100.20"
    assert attributes["geo_latitude"] == 51.5
    assert attributes["geo_postal_code"] == "AA1 1AA"


@pytest.mark.asyncio
async def test_new_rating_key_clears_previous_plex_metadata(monkeypatch) -> None:
    """A reassigned session slot cannot retain details from its old title."""
    sensor = object.__new__(TautulliStreamSensor)
    sensor._entry = SimpleNamespace(
        data={
            "plex_enabled": True,
            "plex_token": "token",
            "plex_base_url": "http://plex",
            "plex_verify_ssl": True,
        }
    )
    sensor.coordinator = SimpleNamespace(
        data={"sessions": [{"rating_key": "new"}]}
    )
    sensor._index = 0
    sensor._last_rating_key = "old"
    sensor._plex_metadata = {"title": "Old title"}
    sensor._credits_offset_ms = 100
    sensor._in_credits = True
    sensor._metadata_fetched = False
    sensor._auth_warning_emitted = False
    sensor.hass = SimpleNamespace()

    monkeypatch.setattr(
        "custom_components.tautulli_active_streams.stream_sensor.async_get_clientsession",
        lambda _hass: SimpleNamespace(),
    )
    fetch = AsyncMock(return_value=(None, {}, 500))
    monkeypatch.setattr(
        "custom_components.tautulli_active_streams.stream_sensor.async_fetch_plex_metadata",
        fetch,
    )

    await sensor._fetch_full_metadata()

    assert sensor._plex_metadata == {}
    assert sensor._credits_offset_ms is None
    assert sensor._in_credits is False
