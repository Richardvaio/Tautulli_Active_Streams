from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_ENABLE_STATISTICS,
    DOMAIN,
)
from .statistics_sensor import TautulliDiagnosticSensor, TautulliUserStatsSensor
from .stream_sensor import TautulliStreamSensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tautulli sensors."""
    # Force a refresh to ensure we have latest data
    data = hass.data[DOMAIN][entry.entry_id]
    sessions_coordinator = data["sessions_coordinator"]
    history_coordinator = data["history_coordinator"]

    # 1) Create one guaranteed session sensor (Plex Session 1)
    session_sensors = [TautulliStreamSensor(sessions_coordinator, entry, 0)]

    # Shared set of active stream sensors for a lightweight display timer.
    active_stream_sensors: set[TautulliStreamSensor] = set()
    data["active_stream_sensors"] = active_stream_sensors

    async def _shared_tick(now: datetime) -> None:
        """Update lightweight duration fields without polling Tautulli."""
        sensors = [s for s in list(active_stream_sensors) if s.hass and s.entity_id]
        if sensors:
            await asyncio.gather(
                *(sensor._update_every_second(now) for sensor in sensors),
                return_exceptions=True,
            )

    unsub_shared_timer = async_track_time_interval(
        hass, _shared_tick, timedelta(seconds=5)
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
                new_sensors.append(TautulliStreamSensor(sessions_coordinator, entry, i))
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
                    # Remove from active set immediately to prevent stale tick
                    for s in list(active_stream_sensors):
                        if s._attr_unique_id == uid:
                            active_stream_sensors.discard(s)
                            break
                    registry.async_remove(entity_id)
                    _LOGGER.debug("Removed session sensor: %s", entity_id)
            current_sensor_count[0] = target

    unsub = sessions_coordinator.async_add_listener(_sync_session_sensors)
    data.setdefault("session_unsub_listeners", []).append(unsub)

    # 2) Create diagnostic sensors
    diagnostic_sensors = [
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count"),
        TautulliDiagnosticSensor(
            sessions_coordinator, entry, "stream_count_direct_play"
        ),
        TautulliDiagnosticSensor(
            sessions_coordinator, entry, "stream_count_direct_stream"
        ),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "stream_count_transcode"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "total_bandwidth"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "lan_bandwidth"),
        TautulliDiagnosticSensor(sessions_coordinator, entry, "wan_bandwidth"),
    ]

    # 3) (Optional) Create user stats sensors if "enable_statistics" is on
    stats_sensors = []
    if entry.options.get(CONF_ENABLE_STATISTICS, False):
        # --- Migration: replace username/index-based IDs with stable Plex user IDs ---
        registry = er.async_get(hass)
        stable_id_pattern = re.compile(rf"^{re.escape(entry.entry_id)}_user_.+_stats$")
        registry_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        # Track stable user IDs so display-name changes do not create new entities.
        tracked_user_ids: set[str] = set()

        user_stats = history_coordinator.data.get("user_stats", {})
        if user_stats:
            for username, stats_dict in user_stats.items():
                user_id = stats_dict.get("user_id")
                if user_id is None:
                    _LOGGER.warning(
                        "Skipping statistics entity for %s because Tautulli returned no user ID",
                        username,
                    )
                    continue
                stable_unique_id = f"{entry.entry_id}_user_{user_id}_stats"
                legacy_unique_id = f"{entry.entry_id}_{username.lower()}_stats_"
                legacy_entry = next(
                    (
                        entity
                        for entity in registry_entries
                        if entity.unique_id == legacy_unique_id
                    ),
                    None,
                )
                stable_entry = next(
                    (
                        entity
                        for entity in registry_entries
                        if entity.unique_id == stable_unique_id
                    ),
                    None,
                )
                if legacy_entry and not stable_entry:
                    registry.async_update_entity(
                        legacy_entry.entity_id,
                        new_unique_id=stable_unique_id,
                    )
                    _LOGGER.debug(
                        "Migrated statistics entity %s to stable Plex user ID %s",
                        legacy_entry.entity_id,
                        user_id,
                    )
                stats_sensors.append(
                    TautulliUserStatsSensor(
                        coordinator=history_coordinator,
                        entry=entry,
                        user_id=str(user_id),
                        username=username,
                        stats=stats_dict,
                    )
                )
                tracked_user_ids.add(str(user_id))

            # Remove only obsolete legacy entries that could not be mapped to
            # a current stable Plex user ID.
            for ent in registry_entries:
                if "_stats_" in ent.unique_id and not stable_id_pattern.match(
                    ent.unique_id
                ):
                    _LOGGER.debug(
                        "Removing unmapped legacy statistics entity %s", ent.entity_id
                    )
                    registry.async_remove(ent.entity_id)

        else:
            _LOGGER.debug(
                "enable_statistics is True, but no user_stats found in history_coordinator.data."
            )

        # Keep established user entities available when a valid date range
        # (for example a new month) contains no plays for that user.
        for ent in registry_entries:
            if not stable_id_pattern.match(ent.unique_id):
                continue
            user_id = ent.unique_id.removeprefix(
                f"{entry.entry_id}_user_"
            ).removesuffix("_stats")
            if user_id in tracked_user_ids:
                continue
            original_name = (
                ent.original_name or ent.name or f"Plex User {user_id} Stats"
            )
            username = original_name.removesuffix(" Stats")
            stats_sensors.append(
                TautulliUserStatsSensor(
                    coordinator=history_coordinator,
                    entry=entry,
                    user_id=user_id,
                    username=username,
                    stats={},
                )
            )
            tracked_user_ids.add(user_id)

        # --- Dynamic discovery: add sensors for users that appear later ---
        def _check_new_users() -> None:
            """Coordinator listener that creates sensors for newly discovered users."""
            if not history_coordinator.data:
                return
            new_sensors = []
            new_user_ids = []
            for username, stats_dict in history_coordinator.data.get(
                "user_stats", {}
            ).items():
                user_id = stats_dict.get("user_id")
                if user_id is None or str(user_id) in tracked_user_ids:
                    continue
                user_id = str(user_id)
                new_sensors.append(
                    TautulliUserStatsSensor(
                        coordinator=history_coordinator,
                        entry=entry,
                        user_id=user_id,
                        username=username,
                        stats=stats_dict,
                    )
                )
                tracked_user_ids.add(user_id)
                new_user_ids.append(user_id)
            if not new_sensors:
                return
            async_add_entities(new_sensors, True)
            _LOGGER.debug(
                "Dynamically added stats sensors for Plex user IDs: %s", new_user_ids
            )

        # Store unsub so it can be cleaned up on unload
        unsub = history_coordinator.async_add_listener(_check_new_users)
        data.setdefault("stats_unsub_listeners", []).append(unsub)

    # Add everything to Home Assistant
    async_add_entities(session_sensors, True)
    async_add_entities(diagnostic_sensors, True)
    async_add_entities(stats_sensors, True)
