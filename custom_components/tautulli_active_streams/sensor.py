import logging
import re
import time
from datetime import datetime, timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import STATE_OFF, CONF_URL, CONF_API_KEY
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.sensor import SensorStateClass, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.dt import now as ha_now
import aiohttp
import xml.etree.ElementTree as ET

from .const import (
    DOMAIN,
    CONF_ENABLE_STATISTICS,
    CONF_ADVANCED_ATTRIBUTES,
    CONF_ENABLE_IP_GEOLOCATION,
    format_seconds_to_min_sec,
    )

_LOGGER = logging.getLogger(__name__)


async def _fetch_plex_metadata(plex_base_url, plex_token, rating_key, session):
    """
    Query Plex for metadata including chapters, markers, and other attributes.
    Returns a tuple of (credits_offset, metadata_dict, http_status).
    """
    url = (
        f"{plex_base_url}/library/metadata/{rating_key}"
        f"?includeChapters=1&includeMarkers=1"
    )
    headers = {"X-Plex-Token": plex_token}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                _LOGGER.debug(
                    "Plex metadata fetch failed for rating_key=%s: status=%s, reason=%s",
                    rating_key, resp.status, resp.reason
                )
                return None, {}, resp.status

            # Parse XML
            xml_body = await resp.text()
            root = ET.fromstring(xml_body)

            # Check various XML paths for metadata
            video_el = root.find(".//Video")

            # If no Video element found, return empty results
            if video_el is None:
                return None, {}, resp.status

            # Initialize metadata dict
            metadata = {}

            # 1) Credits offset from markers/chapters
            credits_offset = None
            for marker in video_el.findall("Marker"):
                if marker.attrib.get("type") == "credits":
                    credits_offset = int(marker.attrib.get("startTimeOffset", 0))
                    break

            if not credits_offset:
                for chapter in video_el.findall("Chapter"):
                    if "credit" in chapter.attrib.get("tag", "").lower():
                        credits_offset = int(chapter.attrib.get("startTimeOffset", 0))
                        break

            # 2) Parse Director tags
            directors = []
            for director in video_el.findall(".//Director"):
                if "tag" in director.attrib:
                    directors.append(director.attrib["tag"])
            if directors:
                metadata["directors"] = directors

            # 3) Parse Role/Cast tags
            cast = []
            for role in video_el.findall(".//Role"):
                cast_entry = {
                    "actor": role.attrib.get("tag"),
                    "role": role.attrib.get("role")
                }
                cast.append(cast_entry)
            if cast:
                metadata["cast"] = cast

            # 4) Parse Genre tags
            genres = []
            for genre in video_el.findall(".//Genre"):
                if "tag" in genre.attrib:
                    genres.append(genre.attrib["tag"])
            if genres:
                metadata["genres"] = genres

            # 5) Parse Writer tags
            writers = []
            for writer in video_el.findall(".//Writer"):
                if "tag" in writer.attrib:
                    writers.append(writer.attrib["tag"])
            if writers:
                metadata["writers"] = writers

            # 6) Parse Country tags
            countries = []
            for country in video_el.findall(".//Country"):
                if "tag" in country.attrib:
                    countries.append(country.attrib["tag"])
            if countries:
                metadata["country"] = countries[0]  # Take first country

            # 7) Parse Guid tags for external IDs
            guids = []
            for guid in video_el.findall(".//Guid"):
                if "id" in guid.attrib:
                    guids.append(guid.attrib["id"])
            if guids:
                metadata["guids"] = guids

            # 8) Get Media/Part info for file location
            media = video_el.find(".//Media")
            if media is not None:
                part = media.find("Part")
                if part is not None and "file" in part.attrib:
                    metadata["library_folder"] = part.attrib["file"]

            # 9) Get library section info from parent container
            library = root.find("LibrarySection")
            if library is not None:
                if "title" in library.attrib:
                    metadata["library_section_title"] = library.attrib["title"]
                if "id" in library.attrib:
                    metadata["library_section_id"] = library.attrib["id"]

            # 10) Parse Rating tags
            for rating in video_el.findall(".//Rating"):
                image = rating.attrib.get("image", "")
                value = rating.attrib.get("value")
                if value:
                    if "rottentomatoes://image.rating.ripe" in image:
                        metadata["rotten_tomatoes_rating"] = value
                    elif "rottentomatoes://image.rating.upright" in image:
                        metadata["rotten_tomatoes_audience_rating"] = value
                    elif "imdb://image.rating" in image:
                        metadata["imdb_rating"] = value

            # 11) Get basic metadata from Video attributes
            basic_fields = [
                "title", "summary", "year", "rating", "studio",
                "tagline", "contentRating", "originallyAvailableAt",
                "audienceRating", "viewCount", "addedAt", "updatedAt",
                "lastViewedAt"
            ]
            for field in basic_fields:
                if field in video_el.attrib:
                    metadata[field] = video_el.attrib[field]

            return credits_offset, metadata, 200

    except Exception as err:
        _LOGGER.debug("Error fetching Plex metadata for rating_key=%s: %s", rating_key, err)
        return None, {}, None


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Tautulli sensors."""
    # Force a refresh to ensure we have latest data
    data = hass.data[DOMAIN][entry.entry_id]
    sessions_coordinator = data["sessions_coordinator"]
    history_coordinator = data["history_coordinator"]

    # 1) Create one guaranteed session sensor (Plex Session 1)
    session_sensors = [TautulliStreamSensor(sessions_coordinator, entry, 0)]

    # Shared set of active stream sensors for the shared 1-second timer
    active_stream_sensors: set[TautulliStreamSensor] = set()
    data["active_stream_sensors"] = active_stream_sensors

    async def _shared_tick(now):
        """Single 1-second timer that updates all active stream sensors."""
        for sensor in list(active_stream_sensors):
            await sensor._update_every_second(now)

    unsub_shared_timer = async_track_time_interval(
        hass, _shared_tick, timedelta(seconds=1)
    )
    data.setdefault("session_unsub_listeners", []).append(unsub_shared_timer)

    # Track current sensor count for dynamic add/remove
    current_sensor_count = [1]  # use list to allow mutation in closure

    def _sync_session_sensors() -> None:
        """Coordinator listener: add or remove sensors to match active stream count."""
        if not sessions_coordinator.data:
            return
        active_count = len(sessions_coordinator.data.get("sessions", []))
        # Always keep at least 1 sensor
        target = max(1, active_count)

        if target > current_sensor_count[0]:
            # Add sensors for new streams
            new_sensors = []
            for i in range(current_sensor_count[0], target):
                new_sensors.append(
                    TautulliStreamSensor(sessions_coordinator, entry, i)
                )
            current_sensor_count[0] = target
            async_add_entities(new_sensors, True)
            _LOGGER.debug(
                "Dynamically added %d session sensor(s) (total: %d)",
                len(new_sensors),
                current_sensor_count[0],
            )
        elif target < current_sensor_count[0]:
            # Remove sensors that no longer have an active stream
            registry = er.async_get(hass)
            for i in range(target, current_sensor_count[0]):
                uid = f"plex_session_{i + 1}_{entry.entry_id}_tautulli"
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, uid)
                if entity_id:
                    registry.async_remove(entity_id)
                    _LOGGER.debug("Removed session sensor: %s", entity_id)
            current_sensor_count[0] = target

    unsub = sessions_coordinator.async_add_listener(_sync_session_sensors)
    data.setdefault("session_unsub_listeners", []).append(unsub)

    # 2) Create diagnostic sensors
    diagnostic_sensors = [
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count_direct_play"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count_direct_stream"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count_transcode"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "total_bandwidth"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "lan_bandwidth"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "wan_bandwidth"),
    ]

    # 3) (Optional) Create user stats sensors if "enable_statistics" is on
    stats_sensors = []
    if entry.options.get(CONF_ENABLE_STATISTICS, False):
        # --- Migration: remove old index-based unique_ids ---
        registry = er.async_get(hass)
        old_id_pattern = re.compile(
            rf"^{re.escape(entry.entry_id)}_.*_\d+_stats_$"
        )
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
            if old_id_pattern.match(ent.unique_id):
                _LOGGER.debug(
                    "Migrating old index-based stats entity: %s (unique_id: %s)",
                    ent.entity_id,
                    ent.unique_id,
                )
                registry.async_remove(ent.entity_id)

        # Track which users already have sensors so the listener can add new ones
        tracked_users: set[str] = set()

        user_stats = history_coordinator.data.get("user_stats", {})
        if user_stats:
            for username, stats_dict in user_stats.items():
                stats_sensors.append(
                    TautulliUserStatsSensor(
                        coordinator=history_coordinator,
                        entry=entry,
                        username=username,
                        stats=stats_dict,
                    )
                )
                tracked_users.add(username)
        else:
            _LOGGER.debug(
                "enable_statistics is True, but no user_stats found in history_coordinator.data."
            )

        # --- Dynamic discovery: add sensors for users that appear later ---
        def _check_new_users() -> None:
            """Coordinator listener that creates sensors for newly discovered users."""
            if not history_coordinator.data:
                return
            current_users = set(
                history_coordinator.data.get("user_stats", {}).keys()
            )
            new_users = current_users - tracked_users
            if not new_users:
                return
            new_sensors = []
            for username in new_users:
                new_sensors.append(
                    TautulliUserStatsSensor(
                        coordinator=history_coordinator,
                        entry=entry,
                        username=username,
                        stats=history_coordinator.data["user_stats"][username],
                    )
                )
                tracked_users.add(username)
            async_add_entities(new_sensors, True)
            _LOGGER.debug(
                "Dynamically added stats sensors for new users: %s", new_users
            )

        # Store unsub so it can be cleaned up on unload
        unsub = history_coordinator.async_add_listener(_check_new_users)
        data.setdefault("stats_unsub_listeners", []).append(unsub)

    # Add everything to Home Assistant
    async_add_entities(session_sensors, True)
    async_add_entities(diagnostic_sensors, True)
    async_add_entities(stats_sensors, True)


class TautulliStreamSensor(CoordinatorEntity, SensorEntity):
    """
    Representation of a Tautulli stream sensor,
    reading from the sessions_coordinator for session data.
    """

    def __init__(self, coordinator, entry, index):
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

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_active_streams")},
            name=f"{self._entry.title} Active Streams",
            manufacturer="Richardvaio",
            model="Tautulli Active Streams",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self):
        """
        Called when this sensor is added to HA.
        Register with the shared per-second timer.
        """
        await super().async_added_to_hass()
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        active_set = entry_data.get("active_stream_sensors")
        if active_set is not None:
            active_set.add(self)

    async def async_will_remove_from_hass(self):
        """
        Called when removing the sensor. Unregister from the shared timer.
        """
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        active_set = entry_data.get("active_stream_sensors")
        if active_set is not None:
            active_set.discard(self)
        await super().async_will_remove_from_hass()

    async def _update_every_second(self, now):
        """Called every second to update pause duration and credits only."""
        # Guard against coordinator data not yet available
        if not self.coordinator.data:
            return

        # 1) Update local paused-time tracking
        self._update_pause_duration()

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
            if (self._last_state == STATE_OFF and current_state != STATE_OFF) or \
               (current_rating_key and current_rating_key != self._last_rating_key) or \
               (not self._metadata_fetched and current_state != STATE_OFF):
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

        # Only write state if something changed (avoid 300 DB writes/min)
        new_state = self.native_value
        new_attrs = self.extra_state_attributes
        if new_state != self._last_written_state or new_attrs != self._last_written_attrs:
            self._last_written_state = new_state
            self._last_written_attrs = new_attrs
            self.async_write_ha_state()

    async def _fetch_full_metadata(self):
        """Fetch full metadata from Plex when needed."""
        plex_enabled = self._entry.data.get("plex_enabled")
        plex_token = self._entry.data.get("plex_token")
        plex_base_url = self._entry.data.get("plex_base_url")

        if not all([plex_enabled, plex_token, plex_base_url]):
            return

        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) <= self._index:
            return

        session = sessions[self._index]
        rating_key = session.get("rating_key")
        if not rating_key:
            return

        try:
            http_session = async_get_clientsession(self.hass)
            credits_offset, metadata, status = await _fetch_plex_metadata(
                plex_base_url, plex_token, rating_key, http_session
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

        except Exception as err:
            _LOGGER.warning("Error fetching full metadata: %s", err)

    async def _update_credits_only(self):
        """Only check credits position, no full metadata fetch."""
        if self._credits_offset_ms is None:
            return

        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) <= self._index:
            return

        session = sessions[self._index]
        try:
            view_offset = int(session.get("view_offset", 0))
            self._in_credits = (view_offset >= self._credits_offset_ms)
        except (ValueError, TypeError):
            self._in_credits = False

    def _update_pause_duration(self):
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
            self._paused_duration_str = format_seconds_to_min_sec(self._paused_duration_sec)
        else:
            self._paused_start = None
            self._paused_duration_sec = 0
            self._paused_duration_str = "0m 0s"

    @property
    def native_value(self):
        """Return the current Tautulli session state (playing, paused, etc.)"""
        if not self.coordinator.data:
            return STATE_OFF
        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) > self._index:
            return sessions[self._index].get("state", STATE_OFF)
        return STATE_OFF

    @property
    def extra_state_attributes(self):
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

        base_url = self._entry.data.get(CONF_URL)
        api_key = self._entry.data.get(CONF_API_KEY)
        advanced = self._entry.options.get(CONF_ADVANCED_ATTRIBUTES, False)

        attributes = {}

        # Build image URLs — always use the authenticated proxy to avoid
        # exposing the Tautulli API key in sensor attributes.
        # For music (media_type == "track"), prefer parent_thumb (album cover)
        # over grandparent_thumb (artist poster).
        if session.get("media_type") == "track":
            thumb_url = session.get("parent_thumb") or session.get("thumb")
        else:
            thumb_url = session.get("grandparent_thumb") or session.get("thumb")
        if thumb_url and base_url and api_key:
            attributes["image_url"] = (
                f"/api/tautulli/image"
                f"?entry_id={self._entry.entry_id}"
                f"&img={thumb_url}"
                "&width=300&height=450&fallback=poster&refresh=true"
            )

        # Build an art URL
        art_path = session.get("art")
        if art_path and base_url and api_key:
            attributes["art_url"] = (
                f"/api/tautulli/image"
                f"?entry_id={self._entry.entry_id}"
                f"&img={art_path}"
                "&width=1920&height=1080&fallback=art&refresh=true"
            )

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
        attributes["ip_address"] = session.get("ip_address")
        attributes["ip_address_public"] = session.get("ip_address_public")
        attributes["geo_city"] = session.get("geo_city")
        attributes["geo_region"] = session.get("geo_region")
        attributes["geo_country"] = session.get("geo_country")
        attributes["geo_code"] = session.get("geo_code")
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
        attributes["stream_audio_channel_layout"] = session.get("stream_audio_channel_layout")
        attributes["stream_audio_bitrate"] = session.get("stream_audio_bitrate")

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
            remain_ms = float(session["stream_duration"]) - float(session["view_offset"])
            remain_seconds = remain_ms / 1000
            remain_hours = int(remain_seconds // 3600)
            remain_minutes = int((remain_seconds % 3600) // 60)
            remain_secs = int(remain_seconds % 60)
            attributes["stream_remaining"] = f"{remain_hours}:{remain_minutes:02d}:{remain_secs:02d}"

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
            attributes.update({
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
                "stream_video_full_resolution": session.get("stream_video_full_resolution"),
                "stream_video_dovi_profile": session.get("stream_video_dovi_profile"),
                "stream_video_decision": session.get("stream_video_decision"),
                "stream_audio_channels": session.get("stream_audio_channels"),
                "stream_audio_language": session.get("stream_audio_language"),
                "stream_audio_language_code": session.get("stream_audio_language_code"),
            })

            # ---- Source Media Details ----
            # Format total source duration (HH:MM:SS)
            if session.get("duration"):
                try:
                    dur_ms = float(session["duration"])
                    dur_hours = int(dur_ms // 3600000)
                    dur_minutes = int((dur_ms % 3600000) // 60000)
                    dur_seconds = int((dur_ms % 60000) // 1000)
                    attributes["duration"] = f"{dur_hours}:{dur_minutes:02d}:{dur_seconds:02d}"
                except (ValueError, TypeError):
                    attributes["duration"] = session["duration"]

            attributes.update({
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
                "stream_video_dynamic_range": session.get("stream_video_dynamic_range"),
                "stream_video_bit_depth": session.get("stream_video_bit_depth"),
                "stream_video_scan_type": session.get("stream_video_scan_type"),
                "stream_video_color_primaries": session.get("stream_video_color_primaries"),
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
                "stream_subtitle_language_code": session.get("stream_subtitle_language_code"),
                "stream_subtitle_forced": session.get("stream_subtitle_forced"),
                "stream_subtitle_location": session.get("stream_subtitle_location"),
                "stream_subtitle_decision": session.get("stream_subtitle_decision"),
                "stream_subtitle_container": session.get("stream_subtitle_container"),

                # Transcode Hardware
                "transcode_hw_decoding": session.get("transcode_hw_decoding"),
                "transcode_hw_encoding": session.get("transcode_hw_encoding"),
                "transcode_hw_full_pipeline": session.get("transcode_hw_full_pipeline"),
                "transcode_hw_decode_title": session.get("transcode_hw_decode_title"),
                "transcode_hw_encode_title": session.get("transcode_hw_encode_title"),
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
            })

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
            if library_folder:
                attributes["library_folder"] = library_folder

            # Library section info (from Plex XML)
            library_section_title = session.get("library_section_title")
            if library_section_title:
                attributes["library_section_title"] = library_section_title

            library_section_id = session.get("library_section_id")
            if library_section_id:
                attributes["library_section_id"] = library_section_id

            # Rotten Tomatoes & IMDB Ratings (from Plex XML Rating tags)
            for attr_name in ("rotten_tomatoes_rating", "rotten_tomatoes_audience_rating", "imdb_rating"):
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
                            _LOGGER.debug("Invalid timestamp value for %s=%s: %s", field, timestamp, err)
                except (ValueError, TypeError) as err:
                    _LOGGER.debug("Could not process timestamp field %s: %s", field, err)

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



class TautulliDiagnosticSensor(CoordinatorEntity, SensorEntity):
    """
    Representation of a Tautulli diagnostic sensor,
    also using the sessions_coordinator to read 'diagnostics'.
    """

    def __init__(self, coordinator, entry, metric):
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._metric = metric
        self._attr_unique_id = f"tautulli_{entry.entry_id}_{metric}"
        self._attr_name = f"{metric.replace('_', ' ').title()}"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_state_class = SensorStateClass.MEASUREMENT

        if metric in ["total_bandwidth", "lan_bandwidth", "wan_bandwidth"]:
            self._attr_device_class = SensorDeviceClass.DATA_RATE
            self._attr_native_unit_of_measurement = "Mbit/s"
        else:
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = None

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_active_streams")},
            name=f"{self._entry.title} Active Streams",
            manufacturer="Richardvaio",
            model="Tautulli Active Streams",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self):
        """Return the main diagnostic value from 'diagnostics'."""
        if not self.coordinator.data:
            return 0
        diagnostics = self.coordinator.data.get("diagnostics", {})
        raw_value = diagnostics.get(self._metric, 0)

        if self._metric in ["total_bandwidth", "lan_bandwidth", "wan_bandwidth"]:
            try:
                return round(float(raw_value) / 1000, 1)
            except Exception as err:
                _LOGGER.error("Error converting bandwidth: %s", err)
                return raw_value

        return raw_value

    @property
    def extra_state_attributes(self):
        """Return additional diagnostic attributes (e.g. session list)."""
        if self._metric != "stream_count":
            return {}
        if not self.coordinator.data:
            return {}
        sessions = self.coordinator.data.get("sessions", [])
        filtered_sessions = []
        for s in sessions:
            filtered_sessions.append({
                "username": (s.get("username") or "").lower(),
                "user": (s.get("user") or "").lower(),
                "state": (s.get("state") or "").lower(),
                "full_title": s.get("full_title"),
                "stream_start_time": s.get("start_time"),
                "start_time_raw": s.get("start_time_raw"),
                "stream_paused_duration_sec": s.get("stream_paused_duration_sec"),
                "session_id": s.get("session_id"),
            })
        return {"sessions": filtered_sessions}

    @property
    def icon(self):
        icon_map = {
            "stream_count": "mdi:plex",
            "stream_count_direct_play": "mdi:play-circle",
            "stream_count_direct_stream": "mdi:play-network",
            "stream_count_transcode": "mdi:cog",
            "total_bandwidth": "mdi:download-network",
            "lan_bandwidth": "mdi:lan",
            "wan_bandwidth": "mdi:wan",
        }
        return icon_map.get(self._metric, "mdi:chart-bar")


class TautulliUserStatsSensor(CoordinatorEntity, SensorEntity):
    """
    One sensor per user, each with '_stats_' in its unique_id,
    referencing history_coordinator.data for user_stats.
    """

    def __init__(self, coordinator, entry: ConfigEntry, username: str, stats: dict):
        super().__init__(coordinator)
        self._entry = entry
        self._username = username
        self._stats = stats

        # Must have "_stats_" so the removal code can detect it
        self._attr_unique_id = f"{entry.entry_id}_{username.lower()}_stats_"
        self._attr_name = f"{username} Stats"

        # Put these sensors under a separate device named "<Integration Title> Statistics"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_statistics_device")},
            name=f"{entry.title} Statistics",
            manufacturer="Richardvaio",
            model="Tautulli Statistics",
        )

    @property
    def icon(self) -> str:
        return "mdi:account"
        
    @property
    def native_value(self):
        if not self.coordinator.data:
            return "0h 0m"
        user_stats = self.coordinator.data.get("user_stats", {}).get(self._username, {})
        return user_stats.get("total_play_duration", "0h 0m")

    def _handle_coordinator_update(self) -> None:
        """Update stats from coordinator data when it changes."""
        if self.coordinator.data:
            all_stats = self.coordinator.data.get("user_stats", {})
            self._stats = all_stats.get(self._username, {})
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self):
        """Return watch-history stats from self._stats (parsed from get_history)."""
        return {
            # --- Basic Play Counts ---
            "total_plays": self._stats.get("total_plays", 0),
            "movie_plays": self._stats.get("movie_plays", 0),
            "tv_plays": self._stats.get("tv_plays", 0),

            # --- Duration & Completion & Pause Metrics ---
            "total_play_duration": self._stats.get("total_play_duration", "0h 0m"),
            "total_completion_rate": self._stats.get("total_completion_rate", 0.0),
            "longest_play": self._stats.get("longest_play", "0h 0m"),
            "average_play_gap": self._stats.get("average_play_gap", "N/A"),
            "paused_count": self._stats.get("paused_count", 0),
            "total_paused_duration": self._stats.get("total_paused_duration", "0h 0m"),

            # --- Popular Titles ---
            "most_popular_show": self._stats.get("most_popular_show", ""),
            "most_popular_movie": self._stats.get("most_popular_movie", ""),
            
            # --- Watch Times --- Weekday & Gaps ---
            "days_since_last_watch": self._stats.get("days_since_last_watch"),
            "preferred_watch_time": self._stats.get("preferred_watch_time", ""),
            "preferred_watch_day": self._stats.get("preferred_watch_day", ""),
            "weekday_plays": self._stats.get("weekday_plays", []),
            "watched_morning": self._stats.get("watched_morning", 0),
            "watched_afternoon": self._stats.get("watched_afternoon", 0),
            "watched_midday": self._stats.get("watched_midday", 0),
            "watched_evening": self._stats.get("watched_evening", 0),
            
            # --- Transcode / Playback Types ---
            "transcode_count": self._stats.get("transcode_count", 0),
            "direct_play_count": self._stats.get("direct_play_count", 0),
            "direct_stream_count": self._stats.get("direct_stream_count", 0),
            "transcode_percentage": self._stats.get("transcode_percentage", 0.0),
            "common_transcode_devices": self._stats.get("common_transcode_devices", ""),
            "last_transcode_date": self._stats.get("last_transcode_date", ""),

            # --- Device Usage ---
            "most_used_device": self._stats.get("most_used_device", ""),
            "common_audio_language": self._stats.get("common_audio_language", "Unknown"),
            
            # --- Geo Location ---
            "geo_city": self._stats.get("geo_city"),
            "geo_region": self._stats.get("geo_region"),
            "geo_country": self._stats.get("geo_country"),
            "geo_code": self._stats.get("geo_code"),
            "geo_latitude": self._stats.get("geo_latitude"),
            "geo_longitude": self._stats.get("geo_longitude"),
            "geo_continent": self._stats.get("geo_continent"),
            "geo_postal_code": self._stats.get("geo_postal_code"),
            "geo_timezone": self._stats.get("geo_timezone"),

            # --- LAN vs WAN ---
            "lan_plays": self._stats.get("lan_plays", 0),
            "wan_plays": self._stats.get("wan_plays", 0),
        }