"""Tests for the versioned dashboard-card API boundary."""

from __future__ import annotations

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.tautulli_active_streams.api import TautulliAuthError
from custom_components.tautulli_active_streams.card_api import (
    CARD_API_SCHEMA_VERSION,
)
from custom_components.tautulli_active_streams.card_cache import CardDataCache
from custom_components.tautulli_active_streams.const import (
    CONF_CARD_ALLOW_HISTORY,
    CONF_CARD_ALLOW_TERMINATION,
    CONF_CARD_SHOW_CLIENT_DETAILS,
    CONF_CARD_SHOW_USER_NAMES,
    DOMAIN,
)
from custom_components.tautulli_active_streams.image import ImagePathCache
from custom_components.tautulli_active_streams.serializers import (
    active_stream_envelope,
    serialize_active_stream,
    serialize_history_item,
    serialize_media_item,
    serialize_stat_item,
    serialize_user,
    serialize_user_stats,
)
from custom_components.tautulli_active_streams.websocket_api import (
    _cached_card_data,
    websocket_get_entries,
    websocket_get_history,
    websocket_get_home_stats,
    websocket_get_recently_added,
    websocket_get_user_stats,
    websocket_subscribe_active_streams,
    websocket_terminate_session,
)


class FakeConnection:
    """Minimal ActiveConnection replacement for command tests."""

    def __init__(self) -> None:
        self.results = []
        self.events = []
        self.errors = []
        self.subscriptions = {}
        self.user = SimpleNamespace(is_admin=True)

    def send_result(self, msg_id, result=None) -> None:
        self.results.append((msg_id, result))

    def send_event(self, msg_id, event) -> None:
        self.events.append((msg_id, event))

    def send_error(self, msg_id, code, message) -> None:
        self.errors.append((msg_id, code, message))


def _entry():
    return SimpleNamespace(
        entry_id="entry-1",
        unique_id="plex-server-1",
        title="Plex Test",
        domain=DOMAIN,
        state=ConfigEntryState.LOADED,
        options={},
    )


def _hass(entry=None):
    entry = entry or _entry()
    coordinator = SimpleNamespace(
        data={"sessions": []},
        last_update_success=True,
        async_add_listener=MagicMock(return_value=MagicMock()),
    )
    config_entries = SimpleNamespace(
        async_get_entry=MagicMock(return_value=entry),
        async_entries=MagicMock(return_value=[entry]),
    )
    return SimpleNamespace(
        config_entries=config_entries,
        data={
            DOMAIN: {
                entry.entry_id: {
                    "sessions_coordinator": coordinator,
                    "image_cache": ImagePathCache(),
                }
            }
        },
    )


def test_image_cache_is_bounded_and_opaque() -> None:
    """Card-facing image identifiers do not contain upstream paths."""
    cache = ImagePathCache(max_entries=2)
    first = cache.register("/library/metadata/1/thumb/1")
    second = cache.register("/library/metadata/2/thumb/2")
    third = cache.register("/library/metadata/3/thumb/3")

    assert "/library" not in first
    assert cache.resolve(first) is None
    assert cache.resolve(second) == "/library/metadata/2/thumb/2"
    assert cache.resolve(third) == "/library/metadata/3/thumb/3"


def test_signed_image_urls_are_stable_between_session_updates() -> None:
    """Four-second coordinator updates must not force browser image reloads."""
    hass = _hass()
    session = {"media_type": "movie", "thumb": "/library/metadata/1/thumb/1"}
    with patch(
        "custom_components.tautulli_active_streams.image.async_sign_path",
        side_effect=["/signed/first", "/signed/second"],
    ) as signer:
        first = serialize_active_stream(hass, _entry(), session)
        second = serialize_active_stream(hass, _entry(), session)

    assert first["images"]["poster_url"] == second["images"]["poster_url"]
    signer.assert_called_once()


def test_active_serializer_is_normalized_and_privacy_safe() -> None:
    """Raw credentials, IPs, paths and machine IDs never cross the card API."""
    hass = _hass()
    session = {
        "session_id": "session-1",
        "user_id": "42",
        "user": "Viewer",
        "state": "playing",
        "media_type": "track",
        "rating_key": "99",
        "title": "Track",
        "parent_title": "Album",
        "grandparent_title": "Artist",
        "media_index": "7",
        "summary": "A safe media summary.",
        "genres": ["Rock"],
        "duration": "240000",
        "view_offset": "60000",
        "progress_percent": "25.0",
        "parent_thumb": "/library/metadata/99/thumb/1",
        "art": "/library/metadata/99/art/1",
        "ip_address": "203.0.113.10",
        "machine_id": "private-machine",
        "file": "/private/media/song.flac",
        "api_key": "secret",
    }

    with patch(
        "custom_components.tautulli_active_streams.image.async_sign_path",
        side_effect=lambda _hass, path, _expiry: path,
    ):
        result = serialize_active_stream(hass, _entry(), session)

    assert result["id"] == "plex-server-1:session-1"
    assert result["user"]["id"] == "plex-server-1:42"
    assert result["playback"]["remaining_ms"] == 180000
    assert result["media"]["hierarchy"]["artist"] == "Artist"
    assert result["media"]["hierarchy"]["album"] == "Album"
    assert result["media"]["hierarchy"]["track_number"] == 7
    assert result["media"]["summary"] == "A safe media summary."
    assert result["media"]["genres"] == ["Rock"]
    assert result["images"]["poster_aspect"] == "1/1"
    serialized = repr(result)
    for forbidden in (
        "203.0.113.10",
        "private-machine",
        "/private/media",
        "secret",
        "/library/metadata",
    ):
        assert forbidden not in serialized


def test_get_entries_reports_schema_and_capabilities() -> None:
    """The editor can discover compatible loaded entries."""
    connection = FakeConnection()
    websocket_get_entries(_hass(), connection, {"id": 1})

    payload = connection.results[0][1]
    assert payload["schema_version"] == CARD_API_SCHEMA_VERSION
    assert payload["entries"][0]["entry_id"] == "entry-1"
    assert payload["entries"][0]["capabilities"]["active_streams"] is True


def test_active_subscription_sends_initial_and_coordinator_updates() -> None:
    """Subscriptions return an initial event and install an unsubscribe callback."""
    hass = _hass()
    connection = FakeConnection()
    with patch(
        "custom_components.tautulli_active_streams.websocket_api.active_stream_envelope",
        return_value={"schema_version": 1, "items": []},
    ):
        websocket_subscribe_active_streams(
            hass,
            connection,
            {"id": 2, "entry_id": "entry-1"},
        )

    assert connection.errors == []
    assert connection.results == [(2, None)]
    assert connection.events == [(2, {"schema_version": 1, "items": []})]
    assert 2 in connection.subscriptions


def test_active_envelope_marks_failed_coordinator_data_stale() -> None:
    """A stale flag preserves last-known data without pretending it is current."""
    with patch(
        "custom_components.tautulli_active_streams.serializers.serialize_active_stream",
        return_value={"id": "one"},
    ):
        payload = active_stream_envelope(_hass(), _entry(), [{}], stale=True)
    assert payload["stale"] is True
    assert payload["items"] == [{"id": "one"}]


async def test_card_cache_reuses_fresh_data_and_falls_back_to_stale() -> None:
    """Demand-driven views retain their last successful result on failure."""
    cache = CardDataCache()
    calls = 0

    async def success():
        nonlocal calls
        calls += 1
        return ["value"]

    value, stale = await cache.get_or_fetch("key", 60, success)
    assert (value, stale, calls) == (["value"], False, 1)
    value, stale = await cache.get_or_fetch("key", 60, success)
    assert (value, stale, calls) == (["value"], False, 1)

    async def failure():
        raise RuntimeError("offline")

    value, stale = await cache.get_or_fetch("key", 0, failure)
    assert value == ["value"]
    assert stale is True


def test_media_and_history_serializers_are_allowlisted() -> None:
    """List endpoints never expose Tautulli keys, IPs, or media file paths."""
    hass = _hass()
    item = {
        "row_id": "7",
        "rating_key": "99",
        "user_id": "42",
        "user": "Viewer",
        "media_type": "movie",
        "title": "Film",
        "duration": "7200000",
        "started": "1700000000",
        "thumb": "/library/metadata/99/thumb/1",
        "ip_address": "203.0.113.10",
        "file": "/private/film.mkv",
        "api_key": "secret",
    }
    with patch(
        "custom_components.tautulli_active_streams.image.async_sign_path",
        side_effect=lambda _hass, path, _expiry: path,
    ):
        media = serialize_media_item(hass, _entry(), item)
        history = serialize_history_item(hass, _entry(), item)

    assert media["duration_seconds"] == 7200
    assert history["id"] == "plex-server-1:history:7"
    serialized = repr((media, history))
    for forbidden in (
        "203.0.113.10",
        "/private/film.mkv",
        "secret",
        "/library/metadata",
    ):
        assert forbidden not in serialized


def test_media_serializer_normalizes_season_hierarchy() -> None:
    """Season rows use their parent as the show and their own index as season."""
    media = serialize_media_item(
        _hass(),
        _entry(),
        {
            "rating_key": "season-10",
            "parent_rating_key": "show-1",
            "grandparent_rating_key": "",
            "media_type": "season",
            "title": "Season 10",
            "full_title": "Season 10",
            "parent_title": "Below Deck Mediterranean",
            "grandparent_title": "",
            "media_index": "10",
            "parent_media_index": "1",
        },
    )

    assert media["hierarchy"] == {
        "show": "Below Deck Mediterranean",
        "season": "Season 10",
        "episode": None,
        "season_number": 10,
        "artist": None,
        "album": None,
        "track": None,
        "parent_id": "show-1",
        "grandparent_id": None,
    }


def test_active_serializer_enforces_entry_privacy_options() -> None:
    """Card configuration cannot reveal fields disabled at the backend."""
    entry = _entry()
    entry.options = {
        CONF_CARD_SHOW_USER_NAMES: False,
        CONF_CARD_SHOW_CLIENT_DETAILS: False,
    }
    result = serialize_active_stream(
        _hass(entry),
        entry,
        {
            "session_id": "session-1",
            "user_id": "42",
            "user": "Viewer",
            "product": "Plex Web",
            "player": "Browser",
        },
    )

    assert result["user"] == {
        "id": None,
        "user_id": None,
        "display_name": None,
    }
    assert result["client"] is None


def test_all_user_card_serializers_enforce_name_privacy() -> None:
    """Selectors, history and rankings cannot bypass the name privacy option."""
    entry = _entry()
    entry.options = {CONF_CARD_SHOW_USER_NAMES: False}
    hass = _hass(entry)

    selector = serialize_user(entry, {"user_id": "42", "friendly_name": "Viewer"})
    history = serialize_history_item(
        hass,
        entry,
        {"row_id": "7", "user_id": "42", "friendly_name": "Viewer"},
    )
    ranking = serialize_stat_item(
        hass,
        entry,
        {"user_id": "42", "friendly_name": "Viewer", "total_plays": 10},
        rank=1,
        kind="top_users",
        metric="plays",
    )

    assert selector["display_name"] == "Private user"
    assert history["user"] == {
        "id": None,
        "user_id": None,
        "display_name": None,
    }
    assert ranking["media"]["title"] == "Private user"
    assert "Viewer" not in repr((selector, history, ranking))


def test_user_stats_serializer_excludes_location_and_history_maps() -> None:
    """User insight cards receive aggregates, never IPs or raw history maps."""
    stats = {
        "user_id": "42",
        "username": "Viewer",
        "total_plays": 10,
        "total_play_duration_sec": 7200,
        "total_completion_rate": 81.5,
        "last_ip": "203.0.113.10",
        "geo_latitude": 51.5,
        "device_map": {"Secret device": 10},
    }
    result = serialize_user_stats(_entry(), stats)

    assert result["id"] == "plex-server-1:42"
    assert result["total_duration_seconds"] == 7200
    assert result["completion_percent"] == 81.5
    serialized = repr(result)
    assert "203.0.113.10" not in serialized
    assert "51.5" not in serialized
    assert "device_map" not in serialized


@pytest.mark.asyncio
async def test_card_fetch_auth_error_starts_reauthentication() -> None:
    """Demand-driven card endpoints recover from expired Tautulli keys."""
    hass = _hass()
    entry = _entry()
    entry.async_start_reauth = MagicMock()
    runtime = SimpleNamespace(card_cache=CardDataCache())
    connection = FakeConnection()

    async def invalid_key():
        raise TautulliAuthError("Invalid apikey")

    result = await _cached_card_data(
        hass, entry, runtime, connection, 9, "recent", 300, invalid_key
    )

    assert result is None
    entry.async_start_reauth.assert_called_once_with(hass)
    assert connection.errors[0][1] == "authentication_failed"


@pytest.mark.asyncio
async def test_recent_media_endpoint_is_bounded_and_normalized() -> None:
    """Recently added rows pass through the schema-v1 media allowlist."""
    entry = _entry()
    hass = _hass(entry)
    api = SimpleNamespace(
        get_recently_added=AsyncMock(
            return_value={
                "recently_added": [
                    {"rating_key": "99", "media_type": "movie", "title": "Film"}
                ]
            }
        )
    )
    hass.data[DOMAIN][entry.entry_id]["runtime"] = SimpleNamespace(
        api=api, card_cache=CardDataCache()
    )
    connection = FakeConnection()

    await unwrap(websocket_get_recently_added)(
        hass,
        connection,
        {"id": 10, "entry_id": entry.entry_id, "offset": 0, "limit": 20},
    )

    payload = connection.results[0][1]
    assert payload["schema_version"] == 1
    assert payload["items"][0]["title"] == "Film"
    assert payload["items"][0]["id"] == "plex-server-1:99"
    api.get_recently_added.assert_awaited_once_with(
        start=0, count=20, media_type=None, section_id=None
    )


@pytest.mark.asyncio
async def test_home_stats_endpoint_preserves_rank_and_metric() -> None:
    """Popular-media results retain deterministic ranking metadata."""
    entry = _entry()
    hass = _hass(entry)
    api = SimpleNamespace(
        get_home_stats=AsyncMock(
            return_value=[{"rows": [{"title": "Film", "total_plays": 12}]}]
        )
    )
    hass.data[DOMAIN][entry.entry_id]["runtime"] = SimpleNamespace(
        api=api, card_cache=CardDataCache()
    )
    connection = FakeConnection()

    await unwrap(websocket_get_home_stats)(
        hass,
        connection,
        {
            "id": 11,
            "entry_id": entry.entry_id,
            "stat_id": "popular_movies",
            "time_range": 30,
            "metric": "plays",
            "offset": 0,
            "limit": 10,
        },
    )

    item = connection.results[0][1]["items"][0]
    assert item["rank"] == 1
    assert item["metric"] == "plays"
    assert item["total_plays"] == 12


def test_user_stats_endpoint_sorts_by_watch_duration() -> None:
    """User activity cards receive stable, duration-ranked aggregates."""
    hass = _hass()
    coordinator = SimpleNamespace(
        data={
            "user_stats": {
                "1": {"user_id": "1", "total_play_duration_sec": 60},
                "2": {"user_id": "2", "total_play_duration_sec": 3600},
            }
        },
        last_update_success=True,
    )
    hass.data[DOMAIN]["entry-1"]["history_coordinator"] = coordinator
    connection = FakeConnection()

    websocket_get_user_stats(hass, connection, {"id": 12, "entry_id": "entry-1"})

    payload = connection.results[0][1]
    assert [item["total_duration_seconds"] for item in payload["items"]] == [
        3600,
        60,
    ]
    assert payload["total"] == 2


@pytest.mark.asyncio
async def test_history_endpoint_respects_integration_permission() -> None:
    """Administrator status cannot bypass the integration history switch."""
    entry = _entry()
    entry.options = {CONF_CARD_ALLOW_HISTORY: False}
    hass = _hass(entry)
    connection = FakeConnection()

    await unwrap(websocket_get_history)(
        hass,
        connection,
        {"id": 13, "entry_id": entry.entry_id, "offset": 0, "limit": 25},
    )

    assert connection.results == []
    assert connection.errors[0][1] == "history_disabled"


@pytest.mark.asyncio
async def test_termination_is_entry_scoped_and_refreshes_activity() -> None:
    """Card termination targets one known session and refreshes its entry."""
    entry = _entry()
    entry.options = {CONF_CARD_ALLOW_TERMINATION: True}
    hass = _hass(entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]["sessions_coordinator"]
    coordinator.data = {"sessions": [{"session_id": "session-1"}]}
    coordinator.async_request_refresh = AsyncMock()
    api = SimpleNamespace(terminate_session=AsyncMock(return_value=True))
    hass.data[DOMAIN][entry.entry_id]["api"] = api
    connection = FakeConnection()

    await unwrap(websocket_terminate_session)(
        hass,
        connection,
        {
            "id": 14,
            "entry_id": entry.entry_id,
            "session_id": "session-1",
            "message": "Finished",
        },
    )

    api.terminate_session.assert_awaited_once_with("session-1", "Finished")
    coordinator.async_request_refresh.assert_awaited_once()
    assert connection.results[0][1]["succeeded"] is True
