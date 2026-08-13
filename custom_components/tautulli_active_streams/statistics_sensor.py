from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_EXPOSE_DETAILED_LOCATION,
    DOMAIN,
)
from .coordinators import TautulliHistoryCoordinator, TautulliSessionsCoordinator

_LOGGER = logging.getLogger(__name__)


class TautulliDiagnosticSensor(
    CoordinatorEntity[TautulliSessionsCoordinator], SensorEntity
):
    """
    Representation of a Tautulli diagnostic sensor,
    also using the sessions_coordinator to read 'diagnostics'.
    """

    _unrecorded_attributes = frozenset({"sessions"})

    def __init__(
        self,
        coordinator: TautulliSessionsCoordinator,
        entry: ConfigEntry,
        metric: str,
    ) -> None:
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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_active_streams")},
            name=f"{self._entry.title} Active Streams",
            manufacturer="Richardvaio",
            model="Tautulli Active Streams",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> StateType:
        """Return the main diagnostic value from 'diagnostics'."""
        if not self.coordinator.data:
            return 0
        diagnostics = self.coordinator.data.get("diagnostics", {})
        raw_value = diagnostics.get(self._metric, 0)

        if self._metric in ["total_bandwidth", "lan_bandwidth", "wan_bandwidth"]:
            try:
                return round(float(raw_value) / 1000, 1)
            except (TypeError, ValueError) as err:
                _LOGGER.error("Error converting bandwidth: %s", err)
                return raw_value

        return raw_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional diagnostic attributes (e.g. session list)."""
        if self._metric != "stream_count":
            return {}
        if not self.coordinator.data:
            return {}
        sessions = self.coordinator.data.get("sessions", [])
        filtered_sessions = []
        for s in sessions:
            filtered_sessions.append(
                {
                    "username": s.get("username") or "",
                    "user": s.get("user") or "",
                    "state": (s.get("state") or "").lower(),
                    "full_title": s.get("full_title"),
                    "stream_start_time": s.get("start_time"),
                    "start_time_raw": s.get("start_time_raw"),
                    "stream_paused_duration_sec": s.get("stream_paused_duration_sec"),
                    "session_id": s.get("session_id"),
                }
            )
        return {"sessions": filtered_sessions}

    @property
    def icon(self) -> str:
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


class TautulliUserStatsSensor(
    CoordinatorEntity[TautulliHistoryCoordinator], SensorEntity
):
    """
    One sensor per user, each with '_stats_' in its unique_id,
    referencing history_coordinator.data for user_stats.
    """

    # The duration remains available as entity history. The large derived
    # attribute set is current-period detail and is not useful in Recorder.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self,
        coordinator: TautulliHistoryCoordinator,
        entry: ConfigEntry,
        user_id: str,
        username: str,
        stats: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._user_id = user_id
        self._username = username
        self._stats = stats

        self._attr_unique_id = f"{entry.entry_id}_user_{user_id}_stats"
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
    def native_value(self) -> StateType:
        # A valid empty period is zero, not unavailable.
        if (current := self._current_stats()) is None:
            return "0h 0m"
        return current[1].get("total_play_duration", "0h 0m")

    def _handle_coordinator_update(self) -> None:
        """Update stats from coordinator data when it changes."""
        if (current := self._current_stats()) is not None:
            self._username, self._stats = current
            self._attr_name = f"{self._username} Stats"
        elif self.coordinator.last_update_success:
            # No records in the selected period is a valid zero-result update.
            self._stats = {}
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return watch-history stats from self._stats (parsed from get_history)."""
        attributes = {
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
            "watched_evening": self._stats.get("watched_evening", 0),
            "watched_night": self._stats.get("watched_night", 0),
            # --- Transcode / Playback Types ---
            "transcode_count": self._stats.get("transcode_count", 0),
            "direct_play_count": self._stats.get("direct_play_count", 0),
            "direct_stream_count": self._stats.get("direct_stream_count", 0),
            "transcode_percentage": self._stats.get("transcode_percentage", 0.0),
            "common_transcode_devices": self._stats.get("common_transcode_devices", ""),
            "last_transcode_date": self._stats.get("last_transcode_date", ""),
            # --- Device Usage ---
            "most_used_device": self._stats.get("most_used_device", ""),
            "common_audio_language": self._stats.get(
                "common_audio_language", "Unknown"
            ),
            # --- Geo Location ---
            "geo_city": self._stats.get("geo_city"),
            "geo_region": self._stats.get("geo_region"),
            "geo_country": self._stats.get("geo_country"),
            "geo_code": self._stats.get("geo_code"),
            "geo_continent": self._stats.get("geo_continent"),
            "geo_timezone": self._stats.get("geo_timezone"),
            # --- LAN vs WAN ---
            "lan_plays": self._stats.get("lan_plays", 0),
            "wan_plays": self._stats.get("wan_plays", 0),
        }
        if self._entry.options.get(CONF_EXPOSE_DETAILED_LOCATION, False):
            attributes["geo_latitude"] = self._stats.get("geo_latitude")
            attributes["geo_longitude"] = self._stats.get("geo_longitude")
            attributes["geo_postal_code"] = self._stats.get("geo_postal_code")
        return attributes

    def _current_stats(self) -> tuple[str, dict[str, Any]] | None:
        """Return current data for the stable Plex user ID."""
        if not self.coordinator.data:
            return None
        for stats in self.coordinator.data.get("user_stats", {}).values():
            if str(stats.get("user_id")) == self._user_id:
                return stats.get("username", "Unknown"), stats
        return None
