import logging
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

from .const import (
    DOMAIN,
    DEFAULT_NUM_SENSORS,
    CONF_NUM_SENSORS,
    CONF_ENABLE_STATISTICS
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """
    Set up the Tautulli stream sensors, diagnostic sensors, and user stats sensors
    using two different coordinators:
      - sessions_coordinator for sessions/diagnostics
      - history_coordinator for user-based stats
    """

    # Retrieve both coordinators
    data = hass.data[DOMAIN][entry.entry_id]
    sessions_coordinator = data["sessions_coordinator"]
    history_coordinator = data["history_coordinator"]

    # Number of active stream sensors to create
    num_sensors = entry.options.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)

    #
    # 1) Create a sensor for each "active stream" slot
    #
    session_sensors = []
    for i in range(num_sensors):
        session_sensors.append(
            TautulliStreamSensor(sessions_coordinator, entry, i)
        )

    #
    # 2) Create diagnostic sensors (read from sessions_coordinator)
    #
    diagnostic_sensors = [
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count_direct_play"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count_direct_stream"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count_transcode"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "total_bandwidth"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "lan_bandwidth"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "wan_bandwidth"),
    ]

    #
    # 3) (Optional) Create user stats sensors if "enable_statistics" is on
    #
    stats_sensors = []
    if entry.options.get(CONF_ENABLE_STATISTICS, False):
        user_stats = history_coordinator.data.get("user_stats", {})
        if user_stats:
            i = 0
            for username, stats_dict in user_stats.items():
                stats_sensors.append(
                    TautulliUserStatsSensor(
                        coordinator=history_coordinator,
                        entry=entry,
                        username=username,
                        stats=stats_dict,
                        index=i
                    )
                )
                i += 1
        else:
            _LOGGER.debug(
                "enable_statistics is True, but no user_stats found in history_coordinator.data."
            )

    # Add everything to Home Assistant
    async_add_entities(session_sensors, True)
    async_add_entities(diagnostic_sensors, True)
    async_add_entities(stats_sensors, True)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))


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

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self._entry.entry_id}_active_streams")},
            "name": f"{self._entry.title} Active Streams",
            "manufacturer": "Richardvaio",
            "model": "Tautulli Active Streams",
            "entry_type": "service",
        }

    @property
    def state(self):
        """Return the session's state (e.g., 'playing', 'paused', or STATE_OFF)."""
        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) > self._index:
            return sessions[self._index].get("state", STATE_OFF)
        return STATE_OFF

    @property
    def extra_state_attributes(self):
        """
        Return extra attributes for the sensor (basic or advanced),
        including an image_url if we have a valid base_url & api_key.
        """
        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) <= self._index:
            return {}

        session = sessions[self._index]

        base_url = self._entry.data.get(CONF_URL)
        api_key = self._entry.data.get(CONF_API_KEY)
        image_proxy = self._entry.options.get("image_proxy", False)
        advanced = self._entry.options.get("advanced_attributes", False)

        attributes = {}

        # Build an image URL if base_url & api_key
        thumb_url = session.get("grandparent_thumb") or session.get("thumb")
        image_url = None
        if base_url and api_key and thumb_url:
            if image_proxy:
                # Local proxy
                image_url = (
                    f"/api/tautulli/image"
                    f"?entry_id={self._entry.entry_id}"
                    f"&img={thumb_url}"
                    "&width=300&height=450&fallback=poster&refresh=true"
                )
            else:
                # Direct Tautulli URL
                image_url = (
                    f"{base_url}/api/v2"
                    f"?apikey={api_key}"
                    f"&cmd=pms_image_proxy"
                    f"&img={thumb_url}"
                    "&width=300&height=450&fallback=poster&refresh=true"
                )

        # Basic attributes
        attributes.update({
            "user": session.get("user"),
            "progress_percent": session.get("progress_percent"),
            "media_type": session.get("media_type"),
            "full_title": session.get("full_title"),
            "grandparent_thumb": session.get("grandparent_thumb"),
            "thumb": session.get("thumb"),
            "image_url": image_url,
            "parent_media_index": session.get("parent_media_index"),
            "media_index": session.get("media_index"),
            "year": session.get("year"),
            "product": session.get("product"),
            "player": session.get("player"),
            "device": session.get("device"),
            "platform": session.get("platform"),
            "location": session.get("location"),
            "ip_address": session.get("ip_address"),
            "ip_address_public": session.get("ip_address_public"),
            "local": session.get("local"),
            "relayed": session.get("relayed"),
            "bandwidth": session.get("bandwidth"),
            "video_resolution": session.get("video_resolution"),
            "stream_video_resolution": session.get("stream_video_resolution"),
            "transcode_decision": session.get("transcode_decision"),
        })

        # If advanced attributes are off, return now
        if not advanced:
            return attributes

        # Advanced is on:
        # Convert stream_duration from ms -> H:MM:SS
        if session.get("stream_duration"):
            total_ms = float(session["stream_duration"])
            hours = int(total_ms // 3600000)
            minutes = int((total_ms % 3600000) // 60000)
            seconds = int((total_ms % 60000) // 1000)
            formatted_duration = f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            formatted_duration = None

        # Calculate remaining time
        if session.get("view_offset") and session.get("stream_duration"):
            remain_ms = float(session["stream_duration"]) - float(session["view_offset"])
            remain_seconds = remain_ms / 1000
            remain_hours = int(remain_seconds // 3600)
            remain_minutes = int((remain_seconds % 3600) // 60)
            remain_secs = int(remain_seconds % 60)
            formatted_remaining = f"{remain_hours}:{remain_minutes:02d}:{remain_secs:02d}"

            # ETA
            eta = datetime.now() + timedelta(seconds=remain_seconds)
            formatted_eta = eta.strftime("%I:%M %p").lower()
        else:
            formatted_remaining = None
            formatted_eta = None

        # Additional advanced keys
        attributes.update({
            "user_friendly_name": session.get("friendly_name"),
            "username": session.get("username"),
            "user_thumb": session.get("user_thumb"),
            "session_id": session.get("session_id"),
            "library_name": session.get("library_name"),
            "container": session.get("container"),
            "aspect_ratio": session.get("aspect_ratio"),
            "video_codec": session.get("video_codec"),
            "video_framerate": session.get("video_framerate"),
            "video_profile": session.get("video_profile"),
            "video_dovi_profile": session.get("video_dovi_profile"),
            "video_dynamic_range": session.get("video_dynamic_range"),
            "video_color_space": session.get("video_color_space"),
            "audio_codec": session.get("audio_codec"),
            "audio_channels": session.get("audio_channels"),
            "audio_channel_layout": session.get("audio_channel_layout"),
            "audio_profile": session.get("audio_profile"),
            "audio_bitrate": session.get("audio_bitrate"),
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
            "stream_start_time": session.get("start_time"),
            "stream_duration": formatted_duration,
            "stream_remaining": formatted_remaining,
            "Stream_paused_duration": session.get("Stream_paused_duration"),
            "stream_video_resolution": session.get("stream_video_resolution"),
            "stream_container": session.get("stream_container"),
            "stream_bitrate": session.get("stream_bitrate"),
            "stream_video_bitrate": session.get("stream_video_bitrate"),
            "stream_video_codec": session.get("stream_video_codec"),
            "stream_video_framerate": session.get("stream_video_framerate"),
            "stream_video_resolution": session.get("stream_video_resolution"),
            "stream_video_full_resolution": session.get("stream_video_full_resolution"),
            "stream_video_dovi_profile": session.get("stream_video_dovi_profile"),
            "stream_video_decision": session.get("stream_video_decision"),
            "stream_audio_bitrate": session.get("stream_audio_bitrate"),
            "stream_audio_codec": session.get("stream_audio_codec"),
            "stream_audio_channels": session.get("stream_audio_channels"),
            "stream_audio_channel_layout": session.get("stream_audio_channel_layout"),
            "stream_audio_language": session.get("stream_audio_language"),
            "stream_audio_language_code": session.get("stream_audio_language_code"),
        })

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
        self.entity_id = f"sensor.tautulli_{metric}" 
        self._attr_name = f"{metric.replace('_', ' ').title()}" 
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = self.device_info
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        if metric in ["total_bandwidth", "lan_bandwidth", "wan_bandwidth"]:
            self._attr_native_unit_of_measurement = "Mbps"
        else:
            self._attr_native_unit_of_measurement = None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self._entry.entry_id}_active_streams")},
            "name": f"{self._entry.title} Active Streams",
            "manufacturer": "Richardvaio",
            "model": "Tautulli Active Streams",
            "entry_type": "service",
        }

    @property
    def state(self):
        """Return the main diagnostic value from 'diagnostics'."""
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
        sessions = self.coordinator.data.get("sessions", [])
        filtered_sessions = []
        for s in sessions:
            filtered_sessions.append({
                "username": (s.get("username") or "").lower(),
                "user": (s.get("user") or "").lower(),
                "full_title": s.get("full_title"),
                "stream_start_time": s.get("start_time"),
                "start_time_raw": s.get("start_time_raw"),
                "Stream_paused_duration_sec": s.get("Stream_paused_duration_sec"),
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

    def __init__(self, coordinator, entry: ConfigEntry, username: str, stats: dict, index: int):
        super().__init__(coordinator)
        self._entry = entry
        self._username = username
        self._stats = stats
        self._index = index

        # Must have "_stats_" so the removal code can detect it
        self._attr_unique_id = f"{entry.entry_id}_{username.lower()}_{index}_stats_"
        self._attr_name = f"{username} Stats"

        # Put these sensors under a separate device named "<Integration Title> Statistics"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_statistics_device")},
            "name": f"{entry.title} Statistics",
            "manufacturer": "Richardvaio",
            "model": "Tautulli Statistics",
        }

    @property
    def state(self):
        user_stats = self.coordinator.data["user_stats"].get(self._username, {})
        return user_stats.get("total_play_duration", "0h 0m")
    

    @property
    def extra_state_attributes(self):
        """Return watch-history stats from self._stats (parsed from get_history)."""
        return {
            "total_plays": self._stats.get("total_plays", 0),
            "movie_plays": self._stats.get("movie_plays", 0),
            "tv_plays": self._stats.get("tv_plays", 0),
            "total_play_duration": self._stats.get("total_play_duration", "0h 0m"),
            "longest_play": self._stats.get("longest_play", "0h 0m"),
            "common_audio_language": self._stats.get("common_audio_language", "Unknown"),
            "transcode_count": self._stats.get("transcode_count", 0),
            "direct_play_count": self._stats.get("direct_play_count", 0),
            "direct_stream_count": self._stats.get("direct_stream_count", 0),
            "streams_count": self._stats.get("streams_count", 0),
            "transcode_percentage": self._stats.get("transcode_percentage", 0.0),
            "common_transcode_reasons": self._stats.get("common_transcode_reasons", []),
            "common_transcode_devices": self._stats.get("common_transcode_devices", ""),
            "most_popular_show": self._stats.get("most_popular_show", ""),
            "most_popular_movie": self._stats.get("most_popular_movie", ""),
            "most_used_device": self._stats.get("most_used_device", ""),
            "total_completion_rate": self._stats.get("total_completion_rate", 0.0),
            "paused_count": self._stats.get("paused_count", 0),
            "total_paused_duration": self._stats.get("total_paused_duration", "0h 0m"),
            "last_transcode_date": self._stats.get("last_transcode_date", ""),
            "watched_morning": self._stats.get("watched_morning", 0),
            "watched_afternoon": self._stats.get("watched_afternoon", 0),
            "watched_midday": self._stats.get("watched_midday", 0),
            "watched_evening": self._stats.get("watched_evening", 0),
            "preferred_watch_time": self._stats.get("preferred_watch_time", ""),
            "lan_plays": self._stats.get("lan_plays", 0),
            "wan_plays": self._stats.get("wan_plays", 0),
            "weekday_plays": self._stats.get("weekday_plays", []),
            "average_play_gap": self._stats.get("average_play_gap", "N/A"),
        }

    async def async_update(self):
        """
        If the coordinator data changes, re-fetch this user's stats
        from history_coordinator.data["user_stats"] if needed.
        """
        all_stats = self.coordinator.data.get("user_stats", {})
        self._stats = all_stats.get(self._username, {})



async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    enable_stats = entry.options.get(CONF_ENABLE_STATISTICS, False)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    if not enable_stats:
        # remove existing stats sensors
        for entity_entry in list(ent_reg.entities.values()):
            if (
                entity_entry.config_entry_id == entry.entry_id
                and "_stats_" in (entity_entry.unique_id or "")
            ):
                ent_reg.async_remove(entity_entry.entity_id)

        # remove the stats device
        for device_entry in list(dev_reg.devices.values()):
            if (
                entry.entry_id in device_entry.config_entries
                and any(
                    iden[0] == DOMAIN and iden[1] == f"{entry.entry_id}_statistics_device"
                    for iden in device_entry.identifiers
                )
            ):
                dev_reg.async_remove_device(device_entry.id)

    # **Either way, reload** so that:
    # - if stats is disabled, we confirm no new stats sensors will be created
    # - if stats is enabled, we re-run async_setup_entry and add them
    await hass.config_entries.async_reload(entry.entry_id)

