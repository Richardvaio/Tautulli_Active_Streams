"""Tests for the Tautulli API client."""

from __future__ import annotations

from typing import Any, Self

import pytest

from custom_components.tautulli_active_streams.api import (
    TautulliAPI,
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
