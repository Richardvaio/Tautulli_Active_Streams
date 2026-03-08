import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.components.button import ButtonEntity

from .const import DOMAIN, CONF_ENABLE_STATISTICS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """
    Set up the Tautulli 'Fetch History' button if statistics are enabled.
    """
    # If user disabled statistics, skip creating the button
    if not entry.options.get(CONF_ENABLE_STATISTICS, False):
        _LOGGER.debug("Statistics disabled => Not creating Fetch Watch History button.")
        return

    data = hass.data[DOMAIN][entry.entry_id]
    history_coordinator = data["history_coordinator"]

    # Create the button entity
    new_button = TautulliFetchHistoryButton(
        coordinator=history_coordinator,
        entry=entry,
    )
    async_add_entities([new_button], update_before_add=False)


class TautulliFetchHistoryButton(CoordinatorEntity, ButtonEntity):
    """
    A button entity that triggers an immediate fetch of Tautulli's watch history.
    """

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry

        self._attr_name = "Fetch Watch History"
        self._attr_unique_id = f"{entry.entry_id}_fetch_watch_history"

        # Tie this button to the same device as user-stats sensors
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_statistics_device")},
            name=f"{entry.title} Statistics",
            manufacturer="Richardvaio",
            model="Tautulli Statistics",
        )

    async def async_press(self) -> None:
        """
        Called when the user presses the button in the UI.
        We'll simply request a refresh from the 'history_coordinator'.
        """
        _LOGGER.debug("Button pressed: fetching watch-history data now...")
        await self.coordinator.async_request_refresh()
