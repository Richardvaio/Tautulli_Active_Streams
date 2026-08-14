import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TautulliAPI
from .card_cache import CardDataCache
from .const import (
    CONF_ENABLE_STATISTICS,
    CONF_GEO_PROVIDER,
    CONF_SESSION_INTERVAL,
    CONF_STATISTICS_INTERVAL,
    DEFAULT_SESSION_INTERVAL,
    DEFAULT_STATISTICS_INTERVAL,
    DOMAIN,
    GEO_PROVIDER_TAUTULLI,
)
from .coordinators import TautulliHistoryCoordinator, TautulliSessionsCoordinator
from .geo import IPGeoCache
from .image import ImagePathCache
from .runtime import TautulliRuntimeData
from .services import async_setup_kill_stream_services  # kill-stream services
from .views import TautulliImageView
from .websocket_api import async_register_websocket_commands

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BUTTON, Platform.DEVICE_TRACKER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Create two coordinators:
      - sessions_coordinator => uses CONF_SESSION_INTERVAL
      - history_coordinator  => uses CONF_STATISTICS_INTERVAL
    Then set up kill-stream services, forward to sensor platform, etc.
    """
    hass.data.setdefault(DOMAIN, {})

    # 1) Create TautulliAPI object
    url = entry.data.get(CONF_URL)
    api_key = entry.data.get(CONF_API_KEY)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    api = TautulliAPI(url, api_key, session, verify_ssl)

    # Create the IPGeoCache (shared) so we can pass it to both coordinators
    geo_provider = entry.options.get(CONF_GEO_PROVIDER, GEO_PROVIDER_TAUTULLI)
    geo_cache = IPGeoCache(api, provider=geo_provider)

    # 2) Build your session + history coordinators
    session_interval = entry.options.get(
        CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL
    )
    stats_interval = entry.options.get(
        CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL
    )

    sessions_coordinator = TautulliSessionsCoordinator(
        hass=hass,
        logger=_LOGGER,
        api=api,
        update_interval=timedelta(seconds=session_interval),
        config_entry=entry,
        geo_cache=geo_cache,
    )

    history_coordinator = TautulliHistoryCoordinator(
        hass=hass,
        logger=_LOGGER,
        api=api,
        update_interval=timedelta(seconds=stats_interval),
        config_entry=entry,
        geo_cache=geo_cache,
    )

    # 3) Do first refresh
    await sessions_coordinator.async_config_entry_first_refresh()
    await history_coordinator.async_config_entry_first_refresh()

    # 4) Store everything in hass.data
    image_cache = ImagePathCache()
    card_cache = CardDataCache()
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "sessions_coordinator": sessions_coordinator,
        "history_coordinator": history_coordinator,
        "geo_cache": geo_cache,
        "image_cache": image_cache,
        "card_cache": card_cache,
        "runtime": TautulliRuntimeData(
            api=api,
            sessions=sessions_coordinator,
            history=history_coordinator,
            geo_cache=geo_cache,
            image_cache=image_cache,
            card_cache=card_cache,
        ),
    }

    # 5) Register the authenticated image view once across multiple entries.
    if "tautulli_image_view_registered" not in hass.data:
        hass.http.register_view(TautulliImageView)
        hass.data["tautulli_image_view_registered"] = True

    if "tautulli_card_api_registered" not in hass.data:
        async_register_websocket_commands(hass)
        hass.data["tautulli_card_api_registered"] = True

    # 6) Forward to sensor + button
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 7) Setup kill-stream services (only once for the first entry)
    if not hass.services.has_service(DOMAIN, "kill_all_streams"):
        try:
            await async_setup_kill_stream_services(hass, entry, api)
        except Exception:
            _LOGGER.exception("Exception during kill stream service registration")

    # Store old stats toggle
    history_coordinator.old_stats_toggle = entry.options.get(
        CONF_ENABLE_STATISTICS, False
    )

    # 8) Listen for options changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


# ---------------------------
#  Update Options
# ---------------------------
async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after an options, reauthentication, or reconfiguration update."""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data:
        return

    history_coordinator = data["history_coordinator"]
    old_stats = history_coordinator.old_stats_toggle
    new_stats = entry.options.get(CONF_ENABLE_STATISTICS, False)
    if old_stats and not new_stats:
        await async_remove_statistics_sensors(hass, entry)
        await async_remove_history_button(hass, entry)

    # The config flow updates the entry and lets this listener perform the one
    # supported reload. This avoids the double-reload race deprecated in
    # Home Assistant 2026.6 and scheduled to fail in 2026.12.
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------
#  Unload
# ---------------------------
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, {})

        # Unsubscribe dynamic stats listeners
        for unsub in data.get("stats_unsub_listeners", []):
            unsub()

        # Unsubscribe dynamic session listeners
        for unsub in data.get("session_unsub_listeners", []):
            unsub()

        # Only remove the kill services if this is the *last* config entry for this domain
        remaining_entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining_entries:
            for service in [
                "kill_all_streams",
                "kill_user_streams",
                "kill_session_stream",
            ]:
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def async_remove_statistics_sensors(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove all user-stats sensors (those with '_stats_') plus the device."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)

    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    for ent in entries:
        if "_stats_" in ent.unique_id or ent.unique_id.endswith("_stats"):
            _LOGGER.debug(
                "Removing user-stats sensor entity: %s (unique_id: %s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)

    # Also remove the stats device
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_statistics_device")}
    )
    if device:
        _LOGGER.debug("Removing user-stats device: %s (%s)", device.name, device.id)
        device_reg.async_remove_device(device.id)


async def async_remove_history_button(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Remove the 'Fetch Watch History' button entity (if it exists).
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)

    unique_id = f"{entry.entry_id}_fetch_watch_history"
    button_entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    if button_entity_id:
        _LOGGER.debug("Removing the fetch-watch-history button: %s", button_entity_id)
        registry.async_remove(button_entity_id)


async def async_remove_all_user_stats_sensors(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """
    Remove *all* user-stats sensors for this config entry,
    ignoring whether Tautulli still has that user or not.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)

    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    for ent in entries:
        if "_stats_" in ent.unique_id or ent.unique_id.endswith("_stats"):
            _LOGGER.debug(
                "Removing user-stats sensor entity: %s (unique_id: %s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)
