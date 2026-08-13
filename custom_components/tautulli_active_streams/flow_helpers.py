from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)


class PlexAuthError(Exception):
    """Raised when Plex rejects a token."""


class PlexConnectionError(Exception):
    """Raised when the configured Plex server cannot be reached."""


def password_selector(autocomplete: str = "current-password") -> TextSelector:
    """Return a masked text selector suitable for credentials."""
    return TextSelector(
        TextSelectorConfig(
            type=TextSelectorType.PASSWORD,
            autocomplete=autocomplete,
        )
    )


def normalize_base_url(value: str) -> str:
    """Normalize and validate an HTTP(S) base URL."""
    value = value.strip()
    if not value:
        raise ValueError("URL is required")
    lowered = value.lower()
    if "://" in value and not lowered.startswith(("http://", "https://")):
        raise ValueError("Unsupported URL scheme")
    if not lowered.startswith(("http://", "https://")):
        value = f"http://{value}"

    try:
        parsed = urlsplit(value)
        # Accessing port also validates malformed/out-of-range port values.
        _ = parsed.port
    except ValueError as err:
        raise ValueError("Invalid URL") from err

    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or any(char.isspace() for char in parsed.netloc)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid URL")

    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def server_data(response: dict[str, Any]) -> dict[str, Any]:
    """Extract Tautulli server data from a validated response."""
    data = response.get("response", {}).get("data", {})
    return data if isinstance(data, dict) else {}


def server_unique_id(response: dict[str, Any], fallback_url: str) -> str:
    """Return the stable Plex identifier exposed by Tautulli."""
    return str(server_data(response).get("pms_identifier") or fallback_url)


async def async_validate_plex(
    session: aiohttp.ClientSession,
    base_url: str,
    token: str,
    verify_ssl: bool,
) -> None:
    """Verify Plex connectivity and token access to the server libraries."""
    try:
        async with session.get(
            f"{base_url}/library/sections",
            headers={"X-Plex-Token": token},
            timeout=aiohttp.ClientTimeout(total=10),
            ssl=verify_ssl,
        ) as response:
            if response.status in (401, 403):
                raise PlexAuthError
            if response.status != 200:
                raise PlexConnectionError(f"Plex returned HTTP {response.status}")
            await response.read()
    except PlexAuthError:
        raise
    except (TimeoutError, asyncio.TimeoutError, aiohttp.ClientError, OSError) as err:
        raise PlexConnectionError("Unable to connect to Plex") from err
