"""Privacy-safe serializers for the dashboard WebSocket API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .card_api import CARD_API_SCHEMA_VERSION, card_capabilities
from .const import CONF_CARD_SHOW_CLIENT_DETAILS, CONF_CARD_SHOW_USER_NAMES
from .image import active_stream_images, media_item_images


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _server_id(entry: ConfigEntry) -> str:
    return str(entry.unique_id or entry.entry_id)


def serialize_active_stream(
    hass: HomeAssistant, entry: ConfigEntry, session: dict[str, Any]
) -> dict[str, Any]:
    """Normalize one active stream through an explicit privacy allowlist."""
    server_id = _server_id(entry)
    session_id = _text(session.get("session_id"))
    session_key = _text(session.get("session_key"))
    upstream_id = session_id or session_key or "unknown"
    user_id = _text(session.get("user_id"))
    rating_key = _text(session.get("rating_key"))
    duration_ms = max(0, _integer(session.get("duration")))
    view_offset_ms = max(0, _integer(session.get("view_offset")))
    remaining_ms = max(0, duration_ms - view_offset_ms)
    source_audio_atmos = bool(_integer(session.get("audio_atmos")))
    raw_stream_audio_atmos = session.get("stream_audio_atmos")
    stream_audio_atmos = bool(_integer(raw_stream_audio_atmos))
    output_audio_atmos = (
        stream_audio_atmos
        if raw_stream_audio_atmos not in (None, "")
        else source_audio_atmos
    )
    show_user = entry.options.get(CONF_CARD_SHOW_USER_NAMES, True)
    show_client = entry.options.get(CONF_CARD_SHOW_CLIENT_DETAILS, True)

    return {
        "id": f"{server_id}:{upstream_id}",
        "session_id": session_id,
        "session_key": session_key,
        "state": (_text(session.get("state")) or "unknown").lower(),
        "user": {
            "id": f"{server_id}:{user_id}" if user_id and show_user else None,
            "user_id": user_id if show_user else None,
            "display_name": (
                _text(session.get("friendly_name"))
                or _text(session.get("user"))
                or _text(session.get("username"))
                or "Unknown"
            )
            if show_user
            else None,
        },
        "media": {
            "id": f"{server_id}:{rating_key}" if rating_key else None,
            "rating_key": rating_key,
            "type": (_text(session.get("media_type")) or "unknown").lower(),
            "title": _text(session.get("title")),
            "full_title": _text(session.get("full_title")),
            "parent_title": _text(session.get("parent_title")),
            "grandparent_title": _text(session.get("grandparent_title")),
            "year": _integer(session.get("year")) or None,
            "summary": _text(session.get("summary")),
            "content_rating": _text(session.get("content_rating")),
            "rating": _number(session.get("rating")) or None,
            "audience_rating": _number(session.get("audience_rating")) or None,
            "genres": _string_list(session.get("genres")),
            "studio": _text(session.get("studio")),
            "season_number": _integer(session.get("parent_media_index")) or None,
            "episode_number": _integer(session.get("media_index")) or None,
            "track_number": _integer(session.get("media_index")) or None,
            "hierarchy": {
                "show": _text(session.get("grandparent_title")),
                "season": _text(session.get("parent_title")),
                "episode": _integer(session.get("media_index")) or None,
                "episode_number": _integer(session.get("media_index")) or None,
                "season_number": _integer(session.get("parent_media_index")) or None,
                "artist": _text(session.get("grandparent_title")),
                "album": _text(session.get("parent_title")),
                "track": _integer(session.get("media_index")) or None,
                "track_number": _integer(session.get("media_index")) or None,
            },
            "live": bool(_integer(session.get("live"))),
            "channel": _text(session.get("channel_name"))
            or _text(session.get("channel_title")),
        },
        "playback": {
            "progress_percent": max(
                0.0, min(100.0, _number(session.get("progress_percent")))
            ),
            "duration_ms": duration_ms,
            "view_offset_ms": view_offset_ms,
            "remaining_ms": remaining_ms,
            "paused_seconds": max(
                0, _integer(session.get("stream_paused_duration_sec"))
            ),
            "started_at": _text(session.get("start_time")),
            "eta": _text(session.get("stream_eta")),
        },
        "client": {
            "product": _text(session.get("product")),
            "player": _text(session.get("player")),
            "device": _text(session.get("device")),
            "platform": _text(session.get("platform")),
        }
        if show_client
        else None,
        "quality": {
            "decision": _text(session.get("transcode_decision")),
            "bandwidth_kbps": max(0, _integer(session.get("bandwidth"))),
            "video_resolution": _text(session.get("stream_video_full_resolution"))
            or _text(session.get("video_full_resolution"))
            or _text(session.get("stream_video_resolution"))
            or _text(session.get("video_resolution")),
            "video_codec": _text(session.get("stream_video_codec")),
            "audio_codec": _text(session.get("stream_audio_codec"))
            or _text(session.get("audio_codec")),
            "audio_channel_layout": _text(session.get("stream_audio_channel_layout"))
            or _text(session.get("audio_channel_layout")),
            "audio_bitrate_kbps": max(
                0,
                _integer(
                    session.get("stream_audio_bitrate") or session.get("audio_bitrate")
                ),
            ),
            "audio_atmos": source_audio_atmos,
            "stream_audio_atmos": stream_audio_atmos,
            "atmos": output_audio_atmos,
        },
        "images": active_stream_images(hass, entry.entry_id, session),
    }


def active_stream_envelope(
    hass: HomeAssistant,
    entry: ConfigEntry,
    sessions: list[dict[str, Any]],
    *,
    stale: bool,
) -> dict[str, Any]:
    """Return the schema-v1 active-stream event envelope."""
    return {
        "schema_version": CARD_API_SCHEMA_VERSION,
        "entry_id": entry.entry_id,
        "server": {"id": _server_id(entry), "name": entry.title},
        "generated_at": datetime.now(UTC).isoformat(),
        "stale": stale,
        "capabilities": card_capabilities(entry),
        "items": [serialize_active_stream(hass, entry, item) for item in sessions],
    }


def _timestamp(value: Any) -> str | None:
    """Normalize Unix seconds to an ISO UTC timestamp."""
    seconds = _integer(value)
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [text for item in value if (text := _text(item))]


def serialize_media_item(
    hass: HomeAssistant, entry: ConfigEntry, item: dict[str, Any]
) -> dict[str, Any]:
    """Normalize a media row without exposing paths or upstream objects."""
    server_id = _server_id(entry)
    rating_key = _text(item.get("rating_key"))
    media_type = (_text(item.get("media_type")) or "unknown").lower()
    duration_ms = max(0, _integer(item.get("duration")))
    hierarchy = {
        "show": _text(item.get("grandparent_title")),
        "season": _text(item.get("parent_title")),
        "episode": _integer(item.get("media_index")) or None,
        "season_number": _integer(item.get("parent_media_index")) or None,
        "artist": _text(item.get("grandparent_title")),
        "album": _text(item.get("parent_title")),
        "track": _integer(item.get("media_index")) or None,
        "parent_id": _text(item.get("parent_rating_key")),
        "grandparent_id": _text(item.get("grandparent_rating_key")),
    }
    if media_type == "season":
        hierarchy.update(
            {
                "show": _text(item.get("parent_title")),
                "season": _text(item.get("title")),
                "episode": None,
                "season_number": _integer(item.get("media_index")) or None,
                "artist": None,
                "album": None,
                "track": None,
            }
        )
    return {
        "id": f"{server_id}:{rating_key}" if rating_key else None,
        "rating_key": rating_key,
        "guid": _text(item.get("guid")),
        "type": media_type,
        "title": _text(item.get("title")),
        "full_title": _text(item.get("full_title")),
        "year": _integer(item.get("year")) or None,
        "added_at": _timestamp(item.get("added_at")),
        "updated_at": _timestamp(item.get("updated_at")),
        "duration_seconds": duration_ms // 1000,
        "summary": _text(item.get("summary")),
        "tagline": _text(item.get("tagline")),
        "content_rating": _text(item.get("content_rating")),
        "rating": _number(item.get("rating")) or None,
        "audience_rating": _number(item.get("audience_rating")) or None,
        "genres": _string_list(item.get("genres")),
        "studio": _text(item.get("studio")),
        "hierarchy": hierarchy,
        "library": {
            "id": _text(item.get("section_id")),
            "name": _text(item.get("library_name")) or _text(item.get("section_name")),
        },
        "images": media_item_images(hass, entry.entry_id, item),
    }


def card_envelope(
    entry: ConfigEntry,
    items: list[dict[str, Any]],
    *,
    stale: bool,
    next_offset: int | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    """Wrap demand-driven card data in the common schema-v1 envelope."""
    result: dict[str, Any] = {
        "schema_version": CARD_API_SCHEMA_VERSION,
        "entry_id": entry.entry_id,
        "server": {"id": _server_id(entry), "name": entry.title},
        "generated_at": datetime.now(UTC).isoformat(),
        "stale": stale,
        "capabilities": card_capabilities(entry),
        "items": items,
    }
    if next_offset is not None:
        result["next_offset"] = next_offset
    if total is not None:
        result["total"] = total
    return result


def serialize_user(entry: ConfigEntry, item: dict[str, Any]) -> dict[str, Any]:
    """Serialize only stable selector-safe user fields."""
    user_id = _text(item.get("user_id"))
    show_user = entry.options.get(CONF_CARD_SHOW_USER_NAMES, True)
    return {
        "id": f"{_server_id(entry)}:{user_id}" if user_id else None,
        "user_id": user_id,
        "display_name": (
            _text(item.get("friendly_name")) or "Unknown"
            if show_user
            else "Private user"
        ),
    }


def serialize_library(entry: ConfigEntry, item: dict[str, Any]) -> dict[str, Any]:
    """Serialize only stable selector-safe library fields."""
    section_id = _text(item.get("section_id"))
    return {
        "id": f"{_server_id(entry)}:{section_id}" if section_id else None,
        "section_id": section_id,
        "name": _text(item.get("section_name"))
        or _text(item.get("library_name"))
        or _text(item.get("name"))
        or "Unknown",
        "type": (_text(item.get("section_type")) or "unknown").lower(),
    }


def serialize_history_item(
    hass: HomeAssistant, entry: ConfigEntry, item: dict[str, Any]
) -> dict[str, Any]:
    """Serialize a history record through a strict privacy allowlist."""
    server_id = _server_id(entry)
    row_id = _text(item.get("row_id")) or _text(item.get("reference_id"))
    user_id = _text(item.get("user_id"))
    show_user = entry.options.get(CONF_CARD_SHOW_USER_NAMES, True)
    return {
        "id": f"{server_id}:history:{row_id}" if row_id else None,
        "started_at": _timestamp(item.get("started")),
        "stopped_at": _timestamp(item.get("stopped")),
        "play_duration_seconds": max(0, _integer(item.get("play_duration"))),
        "completion_percent": max(
            0.0, min(100.0, _number(item.get("percent_complete")))
        ),
        "completion_level": max(0.0, min(1.0, _number(item.get("watched_status")))),
        "user": {
            "id": f"{server_id}:{user_id}" if user_id and show_user else None,
            "user_id": user_id if show_user else None,
            "display_name": (
                _text(item.get("friendly_name")) or _text(item.get("user")) or "Unknown"
            )
            if show_user
            else None,
        },
        "media": serialize_media_item(hass, entry, item),
        "playback": {
            "platform": _text(item.get("platform")),
            "player": _text(item.get("player")),
            "decision": _text(item.get("transcode_decision")),
        },
    }


def serialize_stat_item(
    hass: HomeAssistant,
    entry: ConfigEntry,
    item: dict[str, Any],
    *,
    rank: int,
    kind: str,
    metric: str,
) -> dict[str, Any]:
    """Serialize one aggregate home-stat row without overclaiming media IDs."""
    show_user = entry.options.get(CONF_CARD_SHOW_USER_NAMES, True)
    display_title = (
        _text(item.get("title"))
        or _text(item.get("friendly_name"))
        or _text(item.get("user"))
        or _text(item.get("section_name"))
        or _text(item.get("library_name"))
        or _text(item.get("platform"))
        or _text(item.get("player"))
    )
    if kind == "top_users" and not show_user:
        display_title = "Private user"
    media_source = item if item.get("title") else {**item, "title": display_title}
    return {
        "rank": rank,
        "kind": kind,
        "metric": metric,
        "total_plays": max(
            0, _integer(item.get("total_plays") or item.get("total_count"))
        ),
        "total_duration_seconds": max(0, _integer(item.get("total_duration"))),
        "unique_viewers": max(
            0,
            _integer(
                item.get("users_watched")
                or item.get("user_count")
                or item.get("total_users")
            ),
        ),
        "last_played_at": _timestamp(item.get("last_watch") or item.get("last_played")),
        "media": serialize_media_item(hass, entry, media_source),
    }


def serialize_user_stats(entry: ConfigEntry, stats: dict[str, Any]) -> dict[str, Any]:
    """Serialize one pre-aggregated user summary without location or IP data."""
    server_id = _server_id(entry)
    user_id = _text(stats.get("user_id"))
    show_user = entry.options.get(CONF_CARD_SHOW_USER_NAMES, True)
    show_client = entry.options.get(CONF_CARD_SHOW_CLIENT_DETAILS, True)
    return {
        "id": f"{server_id}:{user_id}" if user_id else None,
        "user_id": user_id,
        "display_name": _text(stats.get("username")) if show_user else None,
        "total_plays": max(0, _integer(stats.get("total_plays"))),
        "total_duration_seconds": max(
            0, _integer(stats.get("total_play_duration_sec"))
        ),
        "movie_plays": max(0, _integer(stats.get("movie_plays"))),
        "tv_plays": max(0, _integer(stats.get("tv_plays"))),
        "completion_percent": max(
            0.0, min(100.0, _number(stats.get("total_completion_rate")))
        ),
        "transcode_percent": max(
            0.0, min(100.0, _number(stats.get("transcode_percentage")))
        ),
        "popular_movie": _text(stats.get("most_popular_movie")),
        "popular_show": _text(stats.get("most_popular_show")),
        "preferred_day": _text(stats.get("preferred_watch_day")),
        "preferred_time": _text(stats.get("preferred_watch_time")),
        "last_seen_at": _timestamp(stats.get("last_stopped_ts")),
        "most_used_device": _text(stats.get("most_used_device"))
        if show_client
        else None,
        "direct_play_count": max(0, _integer(stats.get("direct_play_count"))),
        "direct_stream_count": max(0, _integer(stats.get("direct_stream_count"))),
        "transcode_count": max(0, _integer(stats.get("transcode_count"))),
    }
