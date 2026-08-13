from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.sensor import (
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL, STATE_OFF
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import now as ha_now

from .const import (
    CONF_ADVANCED_ATTRIBUTES,
    CONF_ENABLE_IP_GEOLOCATION,
    CONF_EXPOSE_DETAILED_LOCATION,
    DOMAIN,
    format_seconds_to_min_sec,
)
from .coordinators import TautulliSessionsCoordinator
from .plex_metadata import async_fetch_plex_metadata

_LOGGER = logging.getLogger(__name__)


class TautulliStreamSensor(
    CoordinatorEntity[TautulliSessionsCoordinator], SensorEntity
):
    """
    Representation of a Tautulli stream sensor,
    reading from the sessions_coordinator for session data.
    """

    # Stream metadata changes frequently and can be very large. Keep the state
    # history (playing/paused/off) without duplicating attributes in Recorder.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self,
        coordinator: TautulliSessionsCoordinator,
        entry: ConfigEntry,
        index: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._index = index
        # The unique_id ends with _tautulli so the removal code can match
        self._attr_unique_id = f"plex_session_{index + 1}_{entry.entry_id}_tautulli"
        self._attr_name = f"Plex Session {index + 1} (Tautulli)"
        self._attr_icon = "mdi:plex"

        # local paused duration tracking
        self._paused_start = None
        self._paused_duration_sec = 0
        self._paused_duration_str = "0m 0s"

        # new: track credits
        self._credits_offset_ms = None  # raw credits offset in milliseconds
        self._in_credits = False

        # Plex metadata stored per-sensor (avoids mutating shared coordinator data)
        self._plex_metadata = {}

        # Add new tracking variables
        self._last_state = STATE_OFF
        self._last_rating_key = None
        self._metadata_fetched = False
        self._auth_warning_emitted = False
        self._last_written_state = None
        self._last_written_attrs = None
        self._signed_image_urls: dict[str, str] = {}
        self._signed_image_source: tuple[str | None, str | None] = (None, None)
        self._image_signature_refresh_at = 0.0

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_active_streams")},
            name=f"{self._entry.title} Active Streams",
            manufacturer="Richardvaio",
            model="Tautulli Active Streams",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """
        Called when this sensor is added to HA.
        Register with the shared per-second timer.
        """
        await super().async_added_to_hass()
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        active_set = entry_data.get("active_stream_sensors")
        if active_set is not None:
            active_set.add(self)
        await self._refresh_signed_image_urls()

    async def async_will_remove_from_hass(self) -> None:
        """
        Called when removing the sensor. Unregister from the shared timer.
        """
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        active_set = entry_data.get("active_stream_sensors")
        if active_set is not None:
            active_set.discard(self)
        await super().async_will_remove_from_hass()

    async def _update_every_second(self, now: datetime) -> None:
        """Called every second to update pause duration and credits only."""
        # Guard against coordinator data not yet available
        if not self.coordinator.data:
            return

        # 1) Update local paused-time tracking
        self._update_pause_duration()
        await self._refresh_signed_image_urls()

        # 2) Check if we need to fetch metadata
        current_state = self.native_value
        sessions = self.coordinator.data.get("sessions", [])

        if len(sessions) > self._index:
            session = sessions[self._index]
            current_rating_key = session.get("rating_key")

            # Fetch metadata if:
            # - State changed from OFF to anything else
            # - Rating key changed (different content)
            # - Metadata hasn't been fetched yet
            if (
                (self._last_state == STATE_OFF and current_state != STATE_OFF)
                or (current_rating_key and current_rating_key != self._last_rating_key)
                or (not self._metadata_fetched and current_state != STATE_OFF)
            ):
                await self._fetch_full_metadata()

            # Always update credits detection
            await self._update_credits_only()

            # Update tracking variables
            self._last_state = current_state
            self._last_rating_key = current_rating_key
        else:
            self._last_state = STATE_OFF
            self._last_rating_key = None
            self._metadata_fetched = False
            self._auth_warning_emitted = False
            self._credits_offset_ms = None
            self._in_credits = False
            self._plex_metadata = {}
            self._signed_image_urls = {}
            self._signed_image_source = (None, None)
            self._image_signature_refresh_at = 0.0

        # Only write state if something changed (avoid 300 DB writes/min)
        new_state = self.native_value
        new_attrs = self.extra_state_attributes
        if (
            new_state != self._last_written_state
            or new_attrs != self._last_written_attrs
        ):
            self._last_written_state = new_state
            self._last_written_attrs = new_attrs
            self.async_write_ha_state()

    async def _fetch_full_metadata(self) -> None:
        """Fetch full metadata from Plex when needed."""
        plex_enabled = self._entry.data.get("plex_enabled")
        plex_token = self._entry.data.get("plex_token")
        plex_base_url = self._entry.data.get("plex_base_url")
        plex_verify_ssl = self._entry.data.get("plex_verify_ssl", True)

        if not all([plex_enabled, plex_token, plex_base_url]):
            return

        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) <= self._index:
            return

        session = sessions[self._index]
        rating_key = session.get("rating_key")
        if not rating_key:
            return

        # A dynamic slot can be reassigned to another session. Clear metadata
        # before fetching a different item so details from the previous rating
        # key can never be rendered against the new stream.
        if rating_key != self._last_rating_key:
            self._plex_metadata = {}
            self._credits_offset_ms = None
            self._in_credits = False

        try:
            http_session = async_get_clientsession(self.hass)
            credits_offset, metadata, status = await async_fetch_plex_metadata(
                plex_base_url,
                plex_token,
                rating_key,
                http_session,
                plex_verify_ssl,
            )

            if status in (401, 403):
                if not self._auth_warning_emitted:
                    _LOGGER.warning(
                        "Plex metadata authorization failed (status=%s). "
                        "Skipping metadata enrichment for this stream; check plex_token in the "
                        "'%s' config entry.",
                        status,
                        self._entry.title,
                    )
                    self._auth_warning_emitted = True
                # Prevent per-second retry storms for the same session.
                self._metadata_fetched = True
                self._credits_offset_ms = None
                return

            if status and status != 200:
                # Prevent per-second retry storms for the same session.
                self._metadata_fetched = True
                self._credits_offset_ms = None
                return

            # Store credits offset as integer milliseconds for future checks
            self._credits_offset_ms = credits_offset if credits_offset else None

            # Store metadata locally (never mutate shared coordinator data)
            if metadata:
                self._plex_metadata = metadata

            # Mark this stream as attempted, even when metadata is empty.
            self._metadata_fetched = True
            self._auth_warning_emitted = False

        except Exception as err:  # noqa: BLE001 - optional metadata must not break state
            _LOGGER.warning("Error fetching full metadata: %s", err)

    async def _update_credits_only(self) -> None:
        """Only check credits position, no full metadata fetch."""
        if self._credits_offset_ms is None:
            return

        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) <= self._index:
            return

        session = sessions[self._index]
        try:
            view_offset = int(session.get("view_offset", 0))
            self._in_credits = view_offset >= self._credits_offset_ms
        except (ValueError, TypeError):
            self._in_credits = False

    def _update_pause_duration(self) -> None:
        """
        Increments local pause counter if the state is 'paused'.
        Resets if it's not paused.
        """
        current_state = (self.native_value or "").lower()
        if current_state == "paused":
            if self._paused_start is None:
                self._paused_start = time.time()
            elapsed = time.time() - self._paused_start
            self._paused_duration_sec = int(elapsed)
            self._paused_duration_str = format_seconds_to_min_sec(
                self._paused_duration_sec
            )
        else:
            self._paused_start = None
            self._paused_duration_sec = 0
            self._paused_duration_str = "0m 0s"

    async def _refresh_signed_image_urls(self) -> None:
        """Create short-lived HA-signed proxy URLs for frontend image elements."""
        if not self.hass or not self.coordinator.data:
            return
        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) <= self._index:
            return

        session = sessions[self._index]
        if session.get("media_type") == "track":
            thumb_path = session.get("parent_thumb") or session.get("thumb")
        else:
            thumb_path = session.get("grandparent_thumb") or session.get("thumb")
        art_path = session.get("art")
        source = (thumb_path, art_path)
        now = time.monotonic()
        if (
            source == self._signed_image_source
            and now < self._image_signature_refresh_at
        ):
            return

        signed_urls: dict[str, str] = {}
        definitions = {
            "image_url": (thumb_path, 300, 450, "poster"),
            "art_url": (art_path, 1920, 1080, "art"),
        }
        for key, (image_path, width, height, fallback) in definitions.items():
            if not image_path:
                continue
            unsigned_path = "/api/tautulli/image?" + urlencode(
                {
                    "entry_id": self._entry.entry_id,
                    "img": image_path,
                    "width": width,
                    "height": height,
                    "fallback": fallback,
                    "refresh": "false",
                }
            )
            signed_urls[key] = async_sign_path(
                self.hass,
                unsigned_path,
                timedelta(hours=1),
            )

        self._signed_image_urls = signed_urls
        self._signed_image_source = source
        self._image_signature_refresh_at = now + (45 * 60)

    @property
    def native_value(self) -> StateType:
        """Return the current Tautulli session state (playing, paused, etc.)"""
        if not self.coordinator.data:
            return STATE_OFF
        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) > self._index:
            return sessions[self._index].get("state", STATE_OFF)
        return STATE_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Return extra attributes for the sensor (basic or advanced),
        plus new 'in_credits' info if Plex integration is enabled.
        """
        plex_enabled = self._entry.data.get("plex_enabled")
        plex_token = self._entry.data.get("plex_token")
        plex_base_url = self._entry.data.get("plex_base_url")

        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) <= self._index:
            return {}

        session = sessions[self._index]

        # Merge Plex metadata (stored per-sensor) into session view
        if self._plex_metadata:
            session = {**session, **self._plex_metadata}

        advanced = self._entry.options.get(CONF_ADVANCED_ATTRIBUTES, False)

        attributes = dict(self._signed_image_urls)

        # Build image URLs — always use the authenticated proxy to avoid
        # exposing the Tautulli API key in sensor attributes.
        # For music (media_type == "track"), prefer parent_thumb (album cover)
        # over grandparent_thumb (artist poster).
        # Basic
        attributes["user"] = session.get("user")
        attributes["progress_percent"] = session.get("progress_percent")
        attributes["media_type"] = session.get("media_type")
        attributes["full_title"] = session.get("full_title")
        attributes["parent_media_index"] = session.get("parent_media_index")
        attributes["media_index"] = session.get("media_index")
        attributes["year"] = session.get("year")
        attributes["product"] = session.get("product")
        attributes["player"] = session.get("player")
        attributes["device"] = session.get("device")
        attributes["platform"] = session.get("platform")
        attributes["location"] = session.get("location")
        attributes["local"] = session.get("local")
        attributes["relayed"] = session.get("relayed")
        attributes["bandwidth"] = session.get("bandwidth")
        attributes["video_resolution"] = session.get("video_resolution")
        attributes["stream_video_resolution"] = session.get("stream_video_resolution")
        attributes["transcode_decision"] = session.get("transcode_decision")
        attributes["stream_paused_duration"] = self._paused_duration_str
        attributes["live"] = session.get("live")
        attributes["grandparent_title"] = session.get("grandparent_title")
        attributes["parent_title"] = session.get("parent_title")
        attributes["title"] = session.get("title")
        attributes["audio_codec"] = session.get("audio_codec")
        attributes["audio_channel_layout"] = session.get("audio_channel_layout")
        attributes["audio_bitrate"] = session.get("audio_bitrate")
        attributes["stream_audio_codec"] = session.get("stream_audio_codec")
        attributes["stream_audio_channel_layout"] = session.get(
            "stream_audio_channel_layout"
        )
        attributes["stream_audio_bitrate"] = session.get("stream_audio_bitrate")

        # Coarse geolocation is only exposed when the user explicitly enables
        # geolocation. Raw public IPs, coordinates and postal codes require the
        # separate detailed-location opt-in below.
        if self._entry.options.get(CONF_ENABLE_IP_GEOLOCATION, False):
            attributes["geo_city"] = session.get("geo_city")
            attributes["geo_region"] = session.get("geo_region")
            attributes["geo_country"] = session.get("geo_country")
            attributes["geo_code"] = session.get("geo_code")

        if self._entry.options.get(CONF_EXPOSE_DETAILED_LOCATION, False):
            attributes["ip_address"] = session.get("ip_address")
            attributes["ip_address_public"] = session.get("ip_address_public")
            attributes["geo_latitude"] = session.get("geo_latitude")
            attributes["geo_longitude"] = session.get("geo_longitude")
            attributes["geo_postal_code"] = session.get("geo_postal_code")

        # Stream timing — always available so Lovelace cards work without advanced mode
        attributes["stream_start_time"] = session.get("start_time")

        if session.get("stream_duration"):
            total_ms = float(session["stream_duration"])
            hours = int(total_ms // 3600000)
            minutes = int((total_ms % 3600000) // 60000)
            seconds = int((total_ms % 60000) // 1000)
            attributes["stream_duration"] = f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            attributes["stream_duration"] = None

        if session.get("view_offset") and session.get("stream_duration"):
            remain_ms = float(session["stream_duration"]) - float(
                session["view_offset"]
            )
            remain_seconds = remain_ms / 1000
            remain_hours = int(remain_seconds // 3600)
            remain_minutes = int((remain_seconds % 3600) // 60)
            remain_secs = int(remain_seconds % 60)
            attributes["stream_remaining"] = (
                f"{remain_hours}:{remain_minutes:02d}:{remain_secs:02d}"
            )

            eta = datetime.now(tz=ha_now().tzinfo) + timedelta(seconds=remain_seconds)
            hour_12 = eta.strftime("%I").lstrip("0") or "12"
            minute = eta.strftime("%M")
            ampm = eta.strftime("%p").lower()
            attributes["stream_eta"] = f"{hour_12}:{minute} {ampm}"
        else:
            attributes["stream_remaining"] = None
            attributes["stream_eta"] = None

        # If advanced is off, return now
        if advanced:
            # Advanced is ON, so add more
            attributes.update(
                {
                    "user_friendly_name": session.get("friendly_name"),
                    "username": session.get("username"),
                    "user_thumb": session.get("user_thumb"),
                    "session_id": session.get("session_id"),
                    "library_name": session.get("library_name"),
                    "channel_call_sign": session.get("channel_call_sign"),
                    "channel_title": session.get("channel_title"),
                    "container": session.get("container"),
                    "aspect_ratio": session.get("aspect_ratio"),
                    "video_codec": session.get("video_codec"),
                    "video_framerate": session.get("video_framerate"),
                    "video_profile": session.get("video_profile"),
                    "video_dovi_profile": session.get("video_dovi_profile"),
                    "video_dynamic_range": session.get("video_dynamic_range"),
                    "video_color_space": session.get("video_color_space"),
                    "audio_channels": session.get("audio_channels"),
                    "audio_profile": session.get("audio_profile"),
                    "audio_language": session.get("audio_language"),
                    "audio_language_code": session.get("audio_language_code"),
                    "subtitle_language": session.get("subtitle_language"),
                    "container_decision": session.get("stream_container_decision"),
                    "audio_decision": session.get("audio_decision"),
                    "video_decision": session.get("video_decision"),
                    "subtitle_decision": session.get("subtitle_decision"),
                    "transcode_container": session.get("transcode_container"),
                    "transcode_audio_codec": session.get("transcode_audio_codec"),
                    "transcode_video_codec": session.get("transcode_video_codec"),
                    "transcode_throttled": session.get("transcode_throttled"),
                    "transcode_progress": session.get("transcode_progress"),
                    "transcode_speed": session.get("transcode_speed"),
                    "stream_container": session.get("stream_container"),
                    "stream_bitrate": session.get("stream_bitrate"),
                    "stream_video_bitrate": session.get("stream_video_bitrate"),
                    "stream_video_codec": session.get("stream_video_codec"),
                    "stream_video_framerate": session.get("stream_video_framerate"),
                    "stream_video_full_resolution": session.get(
                        "stream_video_full_resolution"
                    ),
                    "stream_video_dovi_profile": session.get(
                        "stream_video_dovi_profile"
                    ),
                    "stream_video_decision": session.get("stream_video_decision"),
                    "stream_audio_channels": session.get("stream_audio_channels"),
                    "stream_audio_language": session.get("stream_audio_language"),
                    "stream_audio_language_code": session.get(
                        "stream_audio_language_code"
                    ),
                }
            )

            # ---- Source Media Details ----
            # Format total source duration (HH:MM:SS)
            if session.get("duration"):
                try:
                    dur_ms = float(session["duration"])
                    dur_hours = int(dur_ms // 3600000)
                    dur_minutes = int((dur_ms % 3600000) // 60000)
                    dur_seconds = int((dur_ms % 60000) // 1000)
                    attributes["duration"] = (
                        f"{dur_hours}:{dur_minutes:02d}:{dur_seconds:02d}"
                    )
                except (ValueError, TypeError):
                    attributes["duration"] = session["duration"]

            attributes.update(
                {
                    # User & Platform
                    "user_id": session.get("user_id"),
                    "platform_name": session.get("platform_name"),
                    "platform_version": session.get("platform_version"),
                    "product_version": session.get("product_version"),
                    "machine_id": session.get("machine_id"),
                    # Source Media
                    "original_title": session.get("original_title"),
                    "parent_title": session.get("parent_title"),
                    "sort_title": session.get("sort_title"),
                    "bitrate": session.get("bitrate"),
                    "video_full_resolution": session.get("video_full_resolution"),
                    "video_bit_depth": session.get("video_bit_depth"),
                    "video_bitrate": session.get("video_bitrate"),
                    "video_scan_type": session.get("video_scan_type"),
                    "video_height": session.get("video_height"),
                    "video_width": session.get("video_width"),
                    "video_language": session.get("video_language"),
                    "video_language_code": session.get("video_language_code"),
                    "audio_sample_rate": session.get("audio_sample_rate"),
                    "audio_bitrate_mode": session.get("audio_bitrate_mode"),
                    "file": session.get("file"),
                    "file_size": session.get("file_size"),
                    "optimized_version": session.get("optimized_version"),
                    "optimized_version_title": session.get("optimized_version_title"),
                    # Stream Details
                    "quality_profile": session.get("quality_profile"),
                    "stream_audio_decision": session.get("stream_audio_decision"),
                    "stream_audio_sample_rate": session.get("stream_audio_sample_rate"),
                    "stream_video_dynamic_range": session.get(
                        "stream_video_dynamic_range"
                    ),
                    "stream_video_bit_depth": session.get("stream_video_bit_depth"),
                    "stream_video_scan_type": session.get("stream_video_scan_type"),
                    "stream_video_color_primaries": session.get(
                        "stream_video_color_primaries"
                    ),
                    "stream_video_color_range": session.get("stream_video_color_range"),
                    "stream_video_color_space": session.get("stream_video_color_space"),
                    "stream_video_color_trc": session.get("stream_video_color_trc"),
                    "stream_video_height": session.get("stream_video_height"),
                    "stream_video_width": session.get("stream_video_width"),
                    "stream_aspect_ratio": session.get("stream_aspect_ratio"),
                    # Subtitle Details
                    "subtitles": session.get("subtitles"),
                    "subtitle_codec": session.get("subtitle_codec"),
                    "subtitle_forced": session.get("subtitle_forced"),
                    "subtitle_language_code": session.get("subtitle_language_code"),
                    "subtitle_location": session.get("subtitle_location"),
                    "subtitle_container": session.get("subtitle_container"),
                    "stream_subtitle_codec": session.get("stream_subtitle_codec"),
                    "stream_subtitle_language": session.get("stream_subtitle_language"),
                    "stream_subtitle_language_code": session.get(
                        "stream_subtitle_language_code"
                    ),
                    "stream_subtitle_forced": session.get("stream_subtitle_forced"),
                    "stream_subtitle_location": session.get("stream_subtitle_location"),
                    "stream_subtitle_decision": session.get("stream_subtitle_decision"),
                    "stream_subtitle_container": session.get(
                        "stream_subtitle_container"
                    ),
                    # Transcode Hardware
                    "transcode_hw_decoding": session.get("transcode_hw_decoding"),
                    "transcode_hw_encoding": session.get("transcode_hw_encoding"),
                    "transcode_hw_full_pipeline": session.get(
                        "transcode_hw_full_pipeline"
                    ),
                    "transcode_hw_decode_title": session.get(
                        "transcode_hw_decode_title"
                    ),
                    "transcode_hw_encode_title": session.get(
                        "transcode_hw_encode_title"
                    ),
                    "transcode_hw_requested": session.get("transcode_hw_requested"),
                    "transcode_protocol": session.get("transcode_protocol"),
                    "transcode_audio_channels": session.get("transcode_audio_channels"),
                    "transcode_height": session.get("transcode_height"),
                    "transcode_width": session.get("transcode_width"),
                    # Connection
                    "secure": session.get("secure"),
                    "relay": session.get("relay"),
                    # Live TV
                    "live_uuid": session.get("live_uuid"),
                    "channel_id": session.get("channel_id"),
                    "channel_identifier": session.get("channel_identifier"),
                    "channel_stream": session.get("channel_stream"),
                    "channel_thumb": session.get("channel_thumb"),
                    "channel_vcn": session.get("channel_vcn"),
                    # IDs & References
                    "session_key": session.get("session_key"),
                    "section_id": session.get("section_id"),
                    "guid": session.get("guid"),
                    "grandparent_guid": session.get("grandparent_guid"),
                    "grandparent_rating_key": session.get("grandparent_rating_key"),
                    "parent_guid": session.get("parent_guid"),
                    "parent_rating_key": session.get("parent_rating_key"),
                    "rating_key": session.get("rating_key"),
                    # Tautulli Metadata (available without Plex)
                    "directors": session.get("directors"),
                    "writers": session.get("writers"),
                    "actors": session.get("actors"),
                    "genres": session.get("genres"),
                    "labels": session.get("labels"),
                    "content_rating": session.get("content_rating"),
                    "summary": session.get("summary"),
                    "tagline": session.get("tagline"),
                    "studio": session.get("studio"),
                    "originally_available_at": session.get("originally_available_at"),
                    "rating": session.get("rating"),
                    "audience_rating": session.get("audience_rating"),
                }
            )

        # ------------------------------------------------------
        # PLEX ENRICHMENTS (requires plex_enabled)
        # Provides: credits detection, Rotten Tomatoes/IMDB
        # ratings, cast with roles, country, external GUIDs,
        # library file path/section info, timestamps, view count.
        # ------------------------------------------------------
        if plex_enabled and plex_token and plex_base_url:
            # Cast with roles (Plex XML provides actor + character role)
            cast = session.get("cast")
            if cast:
                attributes["cast"] = cast

            # Country (from Plex XML)
            country = session.get("country")
            if country:
                attributes["country"] = country

            # External GUIDs (imdb://, tmdb://, tvdb:// from Plex XML)
            guids = session.get("guids")
            if guids:
                attributes["guids"] = guids

            # Library file path (from Plex XML Parts)
            library_folder = session.get("library_folder")
            if library_folder and advanced:
                attributes["library_folder"] = library_folder

            # Library section info (from Plex XML)
            library_section_title = session.get("library_section_title")
            if library_section_title:
                attributes["library_section_title"] = library_section_title

            library_section_id = session.get("library_section_id")
            if library_section_id:
                attributes["library_section_id"] = library_section_id

            # Rotten Tomatoes & IMDB Ratings (from Plex XML Rating tags)
            for attr_name in (
                "rotten_tomatoes_rating",
                "rotten_tomatoes_audience_rating",
                "imdb_rating",
            ):
                value = session.get(attr_name)
                if value:
                    attributes[attr_name] = value

            # Timestamps (from Plex XML Video attributes)
            for field in ("addedAt", "updatedAt", "lastViewedAt"):
                try:
                    timestamp = session.get(field)
                    if timestamp is not None and timestamp != "":
                        if isinstance(timestamp, str):
                            timestamp = float(timestamp)
                        elif not isinstance(timestamp, (int, float)):
                            continue
                        try:
                            date = datetime.fromtimestamp(timestamp, tz=ha_now().tzinfo)
                            field_name = field[0].lower() + field[1:]
                            attributes[field_name] = date.strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, OSError) as err:
                            _LOGGER.debug(
                                "Invalid timestamp value for %s=%s: %s",
                                field,
                                timestamp,
                                err,
                            )
                except (ValueError, TypeError) as err:
                    _LOGGER.debug(
                        "Could not process timestamp field %s: %s", field, err
                    )

            # View Count (from Plex XML)
            view_count = session.get("view_count") or session.get("viewCount")
            if view_count is not None:
                try:
                    attributes["view_count"] = int(view_count)
                except (ValueError, TypeError):
                    _LOGGER.debug("Invalid view count value: %s", view_count)

            # Credits Detection
            attributes["in_credits"] = self._in_credits
            if self._credits_offset_ms is not None:
                minutes = self._credits_offset_ms // 60000
                seconds = (self._credits_offset_ms % 60000) // 1000
                attributes["credits_start_time"] = f"{minutes}m {seconds}s"

        return attributes
