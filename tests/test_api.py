"""Tests for the Tautulli API client."""

from __future__ import annotations

from typing import Any, Self

import pytest

from custom_components.tautulli_active_streams.api import (
    TautulliAPI,
    TautulliAPIError,
    TautulliAuthError,
)


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    """Minimal client session for GET and POST tests."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.get_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.post_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.get_calls.append((args, kwargs))
        return self.response

    def post(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.post_calls.append((args, kwargs))
        return self.response


def _api(response: FakeResponse) -> TautulliAPI:
    return TautulliAPI("http://tautulli", "secret", FakeSession(response))


def test_ssl_setting_is_available_to_companion_requests() -> None:
    """The image proxy must use the same TLS policy as API requests."""
    api = TautulliAPI(
        "https://tautulli", "secret", FakeSession(FakeResponse(200, {})), False
    )

    assert api.verify_ssl is False


@pytest.mark.asyncio
async def test_error_envelope_preserves_auth_error() -> None:
    """An invalid-key payload must initiate reauthentication."""
    api = _api(
        FakeResponse(
            200,
            {
                "response": {
                    "result": "error",
                    "message": "Invalid apikey",
                    "data": {},
                }
            },
        )
    )

    with pytest.raises(TautulliAuthError):
        await api.get_activity()


@pytest.mark.asyncio
async def test_command_error_envelope_is_not_treated_as_empty_success() -> None:
    """HTTP 200 does not hide a failed Tautulli API command."""
    api = _api(
        FakeResponse(
            200,
            {
                "response": {
                    "result": "error",
                    "message": "Invalid section_id",
                    "data": {},
                }
            },
        )
    )

    with pytest.raises(TautulliAPIError):
        await api.get_recently_added(section_id="missing")


@pytest.mark.asyncio
async def test_http_400_preserves_tautulli_command_error() -> None:
    """Tautulli 2.18 command failures retain their HTTP 400 error message."""
    api = _api(
        FakeResponse(
            400,
            {
                "response": {
                    "result": "error",
                    "message": "Invalid section_id",
                    "data": {},
                }
            },
        )
    )

    with pytest.raises(TautulliAPIError, match="Invalid section_id"):
        await api.get_recently_added(section_id="missing")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_http_auth_status_raises_auth_error(status: int) -> None:
    """HTTP authentication failures must not be reported as connectivity."""
    api = _api(FakeResponse(status, {}))

    with pytest.raises(TautulliAuthError):
        await api.get_server_info()


@pytest.mark.asyncio
async def test_terminate_session_uses_post_and_validates_result() -> None:
    """Termination uses the state-changing POST path required by Tautulli."""
    response = FakeResponse(
        200,
        {"response": {"result": "success", "message": None, "data": {}}},
    )
    session = FakeSession(response)
    api = TautulliAPI("http://tautulli", "secret", session)

    assert await api.terminate_session("session-1", "Finished") is True
    assert len(session.post_calls) == 1
    assert session.post_calls[0][1]["data"] == {
        "session_id": "session-1",
        "message": "Finished",
    }


@pytest.mark.asyncio
async def test_recently_added_is_bounded_and_filtered() -> None:
    """Recent-media requests clamp payload size and pass explicit filters."""
    response = FakeResponse(
        200,
        {"response": {"result": "success", "data": {"recently_added": []}}},
    )
    session = FakeSession(response)
    api = TautulliAPI("http://tautulli", "secret", session)

    await api.get_recently_added(start=5, count=500, media_type="movie", section_id="2")

    assert session.get_calls[0][1]["params"] == {
        "start": 5,
        "count": 50,
        "media_type": "movie",
        "section_id": "2",
    }


@pytest.mark.asyncio
async def test_home_stats_uses_one_explicit_stat_collection() -> None:
    """Home statistics remain bounded and deterministic across Tautulli settings."""
    response = FakeResponse(
        200,
        {"response": {"result": "success", "data": [{"rows": []}]}},
    )
    session = FakeSession(response)
    api = TautulliAPI("http://tautulli", "secret", session)

    await api.get_home_stats(stat_id="popular_movies", count=75)

    params = session.get_calls[0][1]["params"]
    assert params["stat_id"] == "popular_movies"
    assert params["stats_count"] == 50
    assert params["grouping"] == 1
