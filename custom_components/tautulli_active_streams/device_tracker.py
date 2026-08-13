"""Device tracker entities for Tautulli user IP geolocation."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify
from homeassistant.util.dt import now as ha_now

from .const import (
    CONF_ENABLE_IP_GEOLOCATION,
    CONF_EXPOSE_DETAILED_LOCATION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _as_float(value: Any) -> float | None:
    """Return a finite float or None."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(result):
        return None
    return result


def _has_valid_coordinates(stats: dict[str, Any]) -> bool:
    """Return whether a user-stat record contains valid GPS coordinates."""
    latitude = _as_float(stats.get("geo_latitude"))
    longitude = _as_float(stats.get("geo_longitude"))
    return (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _user_stats_by_id(coordinator) -> dict[str, tuple[str, dict[str, Any]]]:
    """Return the newest user-stat record for each stable Plex user ID."""
    if not coordinator.data:
        return {}

    users: dict[str, tuple[str, dict[str, Any]]] = {}
    for stats in coordinator.data.get("user_stats", {}).values():
        user_id = stats.get("user_id")
        if user_id is None:
            continue

        key = str(user_id)
        existing = users.get(key)
        if existing is None or stats.get("last_started_ts", 0) > existing[1].get(
            "last_started_ts", 0
        ):
            users[key] = (stats.get("username", "Unknown"), stats)
    return users


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up config-entry-backed Tautulli location trackers."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["history_coordinator"]
    tracked_user_ids: set[str] = set()

    @callback
    def _async_add_new_trackers() -> None:
        """Add trackers when a user first has a usable geolocation."""
        if not entry.options.get(CONF_ENABLE_IP_GEOLOCATION, False):
            return

        new_entities = []
        for user_id, (username, stats) in _user_stats_by_id(coordinator).items():
            if user_id in tracked_user_ids or not _has_valid_coordinates(stats):
                continue

            tracked_user_ids.add(user_id)
            new_entities.append(
                TautulliUserLocationTracker(
                    coordinator=coordinator,
                    entry=entry,
                    user_id=user_id,
                    username=username,
                )
            )

        if new_entities:
            async_add_entities(new_entities)
            _LOGGER.debug(
                "Added Tautulli location trackers for Plex user IDs: %s",
                [entity.user_id for entity in new_entities],
            )

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_trackers))
    _async_add_new_trackers()


class TautulliUserLocationTracker(CoordinatorEntity, TrackerEntity):
    """Approximate location of a user's most recent public streaming IP."""

    _attr_icon = "mdi:account"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator, entry: ConfigEntry, user_id: str, username: str):
        """Initialize a Tautulli user location tracker."""
        super().__init__(coordinator)
        self._entry = entry
        self._user_id = user_id
        self._username = username

        self._attr_unique_id = f"{entry.entry_id}_user_{user_id}_location"
        self._attr_name = f"{username}: Tautulli last stream location"

        # Do not claim the old device_tracker.tautulli_<username> ID. Legacy
        # device_tracker.see entries can still be restored from known_devices.yaml.
        self._suggested_object_id = f"tautulli_active_streams_{slugify(username)}"

    @property
    def user_id(self) -> str:
        """Return the stable Plex user ID."""
        return self._user_id

    @property
    def suggested_object_id(self) -> str:
        """Return a collision-safe default object ID across supported HA versions."""
        return self._suggested_object_id

    @property
    def source_type(self) -> SourceType:
        """Return GPS as the tracker source type."""
        return SourceType.GPS

    def _current_record(self) -> tuple[str, dict[str, Any]] | None:
        """Return the current username and stats for this Plex user."""
        return _user_stats_by_id(self.coordinator).get(self._user_id)

    @property
    def available(self) -> bool:
        """Return whether a current public-IP location is available."""
        if (
            not super().available
            or not self._entry.options.get(CONF_ENABLE_IP_GEOLOCATION, False)
            or (record := self._current_record()) is None
        ):
            return False
        return _has_valid_coordinates(record[1])

    @property
    def latitude(self) -> float | None:
        """Return latitude for the latest public streaming IP."""
        if (record := self._current_record()) is None:
            return None
        latitude = _as_float(record[1].get("geo_latitude"))
        return latitude if latitude is not None and -90 <= latitude <= 90 else None

    @property
    def longitude(self) -> float | None:
        """Return longitude for the latest public streaming IP."""
        if (record := self._current_record()) is None:
            return None
        longitude = _as_float(record[1].get("geo_longitude"))
        return longitude if longitude is not None and -180 <= longitude <= 180 else None

    @property
    def location_accuracy(self) -> float:
        """Return IP geolocation accuracy in meters when provided."""
        if (record := self._current_record()) is None:
            return 0
        accuracy = _as_float(record[1].get("geo_accuracy"))
        return max(0, accuracy) if accuracy is not None else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details about the most recent public streaming IP."""
        if (record := self._current_record()) is None:
            return {"plex_user_id": self._user_id}

        username, stats = record
        attributes: dict[str, Any] = {
            "plex_user_id": self._user_id,
            "username": username,
            "city": stats.get("geo_city"),
            "region": stats.get("geo_region"),
            "country": stats.get("geo_country"),
            "country_code": stats.get("geo_code"),
            "continent": stats.get("geo_continent"),
            "timezone": stats.get("geo_timezone"),
        }
        if self._entry.options.get(CONF_EXPOSE_DETAILED_LOCATION, False):
            attributes["ip_address"] = stats.get("last_ip")
            attributes["postal_code"] = stats.get("geo_postal_code")

        if last_stopped_ts := stats.get("last_stopped_ts"):
            last_watched = datetime.fromtimestamp(
                last_stopped_ts, tz=ha_now().tzinfo
            ).strftime("%I:%M%p %d-%m-%Y")
            attributes["last_watched"] = last_watched.lstrip("0")

        return attributes

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the display name and state from coordinator data."""
        if (record := self._current_record()) is not None:
            self._username = record[0]
            self._attr_name = f"{self._username}: Tautulli last stream location"
        super()._handle_coordinator_update()
