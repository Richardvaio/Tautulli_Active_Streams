"""Signed, same-origin image URL helpers for entities and dashboard clients."""

from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta
from hashlib import sha256
from time import monotonic
from typing import Any
from urllib.parse import urlencode

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant

from .const import DOMAIN


class ImagePathCache:
    """Bounded map from opaque card tokens to private upstream image paths."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._max_entries = max_entries
        self._paths: OrderedDict[str, str] = OrderedDict()
        self._signed_urls: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def register(self, image_path: str) -> str:
        """Register a path and return its stable opaque token."""
        token = sha256(image_path.encode()).hexdigest()[:32]
        self._paths[token] = image_path
        self._paths.move_to_end(token)
        while len(self._paths) > self._max_entries:
            self._paths.popitem(last=False)
        return token

    def resolve(self, token: str) -> str | None:
        """Resolve a token while refreshing its LRU position."""
        path = self._paths.get(token)
        if path is not None:
            self._paths.move_to_end(token)
        return path

    def signed_url(self, key: str, create) -> str:
        """Reuse a signed URL for 45 minutes to avoid artwork churn on updates."""
        cached = self._signed_urls.get(key)
        if cached and monotonic() - cached[1] < 45 * 60:
            self._signed_urls.move_to_end(key)
            return cached[0]
        url = create()
        self._signed_urls[key] = (url, monotonic())
        self._signed_urls.move_to_end(key)
        while len(self._signed_urls) > self._max_entries:
            self._signed_urls.popitem(last=False)
        return url


def _signed_image_url(
    hass: HomeAssistant,
    entry_id: str,
    image_path: str | None,
    *,
    width: int,
    height: int,
    fallback: str,
) -> str | None:
    """Return a one-hour signed proxy URL without exposing the upstream path."""
    if not image_path:
        return None
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id, {})
    image_cache = entry_data.get("image_cache")
    if not isinstance(image_cache, ImagePathCache):
        return None
    image_token = image_cache.register(image_path)
    unsigned_path = "/api/tautulli/image?" + urlencode(
        {
            "entry_id": entry_id,
            "token": image_token,
            "width": width,
            "height": height,
            "fallback": fallback,
            "refresh": "false",
        }
    )
    cache_key = f"{image_token}:{width}:{height}:{fallback}"
    return image_cache.signed_url(
        cache_key,
        lambda: async_sign_path(hass, unsigned_path, timedelta(hours=1)),
    )


def active_stream_images(
    hass: HomeAssistant, entry_id: str, session: dict[str, Any]
) -> dict[str, str | None]:
    """Build media-aware artwork for one normalized active stream."""
    media_type = str(session.get("media_type") or "").lower()
    if media_type == "track":
        poster_path = session.get("parent_thumb") or session.get("thumb")
        poster = _signed_image_url(
            hass,
            entry_id,
            poster_path,
            width=600,
            height=600,
            fallback="cover",
        )
        poster_aspect = "1/1"
    else:
        poster_path = session.get("grandparent_thumb") or session.get("thumb")
        poster = _signed_image_url(
            hass,
            entry_id,
            poster_path,
            width=600,
            height=900,
            fallback="poster",
        )
        poster_aspect = "2/3"

    backdrop = _signed_image_url(
        hass,
        entry_id,
        session.get("art"),
        width=1280,
        height=720,
        fallback="art",
    )
    return {
        "poster_url": poster,
        "poster_aspect": poster_aspect,
        "backdrop_url": backdrop,
        "backdrop_aspect": "16/9",
    }


def media_item_images(
    hass: HomeAssistant, entry_id: str, item: dict[str, Any]
) -> dict[str, str | None]:
    """Build normalized artwork for recent, history, and statistics items."""
    media_type = str(item.get("media_type") or "").lower()
    if media_type in {"track", "album", "artist"}:
        poster_path = (
            item.get("parent_thumb")
            or item.get("grandparent_thumb")
            or item.get("thumb")
        )
        width, height, aspect, fallback = 600, 600, "1/1", "cover"
    elif media_type in {"episode", "season", "show"}:
        poster_path = item.get("grandparent_thumb") or item.get("thumb")
        width, height, aspect, fallback = 600, 900, "2/3", "poster"
    else:
        poster_path = item.get("thumb") or item.get("grandparent_thumb")
        width, height, aspect, fallback = 600, 900, "2/3", "poster"
    return {
        "poster_url": _signed_image_url(
            hass,
            entry_id,
            poster_path,
            width=width,
            height=height,
            fallback=fallback,
        ),
        "poster_aspect": aspect,
        "backdrop_url": _signed_image_url(
            hass,
            entry_id,
            item.get("art"),
            width=1280,
            height=720,
            fallback="art",
        ),
        "backdrop_aspect": "16/9",
    }
