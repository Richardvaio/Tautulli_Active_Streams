from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ADVANCED_ATTRIBUTES,
    CONF_ENABLE_IP_GEOLOCATION,
    CONF_ENABLE_STATISTICS,
    CONF_EXPOSE_DETAILED_LOCATION,
    CONF_GEO_PROVIDER,
    CONF_PLEX_BASEURL,
    CONF_PLEX_ENABLED,
    CONF_PLEX_TOKEN,
    CONF_PLEX_VERIFY_SSL,
    CONF_SESSION_INTERVAL,
    CONF_STATISTICS_DAYS,
    CONF_STATISTICS_INTERVAL,
    CONF_STATS_MONTH_TO_DATE,
    DEFAULT_SESSION_INTERVAL,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_STATISTICS_INTERVAL,
    GEO_PROVIDER_IP_API,
    GEO_PROVIDER_TAUTULLI,
)
from .flow_helpers import (
    PlexAuthError,
    PlexConnectionError,
    async_validate_plex,
    normalize_base_url,
    password_selector,
)

CONF_CONFIRM = "confirm"


class TautulliOptionsFlowHandler(config_entries.OptionsFlow):
    """Manage optional integration behaviour through sectioned forms."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.entry_id = config_entry.entry_id
        self.options = dict(config_entry.options)
        # Older setup flows stored zero while statistics were disabled.
        if self.options.get(CONF_STATISTICS_DAYS, 0) < 1:
            self.options[CONF_STATISTICS_DAYS] = DEFAULT_STATISTICS_DAYS
        self._plex_enabled_old = bool(
            config_entry.data.get(
                CONF_PLEX_ENABLED,
                self.options.get(CONF_PLEX_ENABLED, False),
            )
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show a concise menu of option groups."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "statistics", "privacy", "plex"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure polling and session attributes."""
        if user_input is not None:
            self.options.update(user_input)
            return self._finish()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SESSION_INTERVAL,
                    default=self.options.get(
                        CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=1)),
                vol.Optional(
                    CONF_ADVANCED_ATTRIBUTES,
                    default=self.options.get(CONF_ADVANCED_ATTRIBUTES, False),
                ): bool,
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_statistics(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Enable or disable watch-history statistics."""
        if user_input is not None:
            enabled = user_input[CONF_ENABLE_STATISTICS]
            self.options[CONF_ENABLE_STATISTICS] = enabled
            if not enabled:
                return self._finish()
            return await self.async_step_statistics_details()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENABLE_STATISTICS,
                    default=self.options.get(CONF_ENABLE_STATISTICS, False),
                ): bool
            }
        )
        return self.async_show_form(step_id="statistics", data_schema=schema)

    async def async_step_statistics_details(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure statistics range and polling."""
        if user_input is not None:
            self.options.update(user_input)
            return self._finish()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_STATS_MONTH_TO_DATE,
                    default=self.options.get(CONF_STATS_MONTH_TO_DATE, False),
                ): bool,
                vol.Optional(
                    CONF_STATISTICS_DAYS,
                    default=self.options.get(
                        CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS
                    ),
                ): vol.All(int, vol.Range(min=1)),
                vol.Optional(
                    CONF_STATISTICS_INTERVAL,
                    default=self.options.get(
                        CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=60)),
            }
        )
        return self.async_show_form(step_id="statistics_details", data_schema=schema)

    async def async_step_privacy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Enable or disable IP geolocation."""
        if user_input is not None:
            enabled = user_input[CONF_ENABLE_IP_GEOLOCATION]
            self.options[CONF_ENABLE_IP_GEOLOCATION] = enabled
            if not enabled:
                self.options[CONF_EXPOSE_DETAILED_LOCATION] = False
                return self._finish()
            return await self.async_step_privacy_details()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENABLE_IP_GEOLOCATION,
                    default=self.options.get(CONF_ENABLE_IP_GEOLOCATION, False),
                ): bool
            }
        )
        return self.async_show_form(step_id="privacy", data_schema=schema)

    async def async_step_privacy_details(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure provider and detailed-location exposure."""
        if user_input is not None:
            self.options.update(user_input)
            return self._finish()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_GEO_PROVIDER,
                    default=self.options.get(CONF_GEO_PROVIDER, GEO_PROVIDER_TAUTULLI),
                ): vol.In([GEO_PROVIDER_TAUTULLI, GEO_PROVIDER_IP_API]),
                vol.Optional(
                    CONF_EXPOSE_DETAILED_LOCATION,
                    default=self.options.get(CONF_EXPOSE_DETAILED_LOCATION, False),
                ): bool,
            }
        )
        return self.async_show_form(step_id="privacy_details", data_schema=schema)

    async def async_step_plex(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Enable, update, or disable Plex metadata enrichment."""
        if user_input is not None:
            enabled = user_input[CONF_PLEX_ENABLED]
            self.options[CONF_PLEX_ENABLED] = enabled
            if enabled:
                return await self.async_step_plex_details()
            if self._plex_enabled_old:
                return await self.async_step_confirm_disable_plex()
            self._clear_plex_data()
            return self._finish()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PLEX_ENABLED,
                    default=self._plex_enabled_old,
                ): bool
            }
        )
        return self.async_show_form(step_id="plex", data_schema=schema)

    async def async_step_plex_details(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate and save Plex connection details."""
        entry = self._entry()
        errors: dict[str, str] = {}
        values = user_input or {}
        existing_token = entry.data.get(CONF_PLEX_TOKEN, "")
        existing_url = entry.data.get(CONF_PLEX_BASEURL, "")
        existing_verify_ssl = entry.data.get(CONF_PLEX_VERIFY_SSL, True)

        if user_input is not None:
            token = user_input.get(CONF_PLEX_TOKEN, "").strip() or existing_token
            verify_ssl = user_input.get(CONF_PLEX_VERIFY_SSL, existing_verify_ssl)
            if not token:
                errors[CONF_PLEX_TOKEN] = "plex_token_required"

            try:
                base_url = normalize_base_url(user_input.get(CONF_PLEX_BASEURL, ""))
            except ValueError:
                errors[CONF_PLEX_BASEURL] = "invalid_url"
            else:
                if not errors:
                    session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
                    try:
                        await async_validate_plex(session, base_url, token, verify_ssl)
                    except PlexAuthError:
                        errors[CONF_PLEX_TOKEN] = "invalid_plex_token"
                    except PlexConnectionError:
                        errors["base"] = "cannot_connect_plex"
                    else:
                        self.options[CONF_PLEX_ENABLED] = True
                        self._update_plex_data(token, base_url, verify_ssl)
                        return self._finish()

        schema = vol.Schema(
            {
                vol.Optional(CONF_PLEX_TOKEN): password_selector("new-password"),
                vol.Required(
                    CONF_PLEX_BASEURL,
                    default=values.get(CONF_PLEX_BASEURL, existing_url),
                ): str,
                vol.Optional(
                    CONF_PLEX_VERIFY_SSL,
                    default=values.get(CONF_PLEX_VERIFY_SSL, existing_verify_ssl),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="plex_details", data_schema=schema, errors=errors
        )

    async def async_step_confirm_disable_plex(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm removal of saved Plex credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_CONFIRM, False):
                self.options[CONF_PLEX_ENABLED] = False
                self._clear_plex_data()
                return self._finish()
            errors[CONF_CONFIRM] = "confirmation_required"

        schema = vol.Schema({vol.Required(CONF_CONFIRM, default=False): bool})
        return self.async_show_form(
            step_id="confirm_disable_plex",
            data_schema=schema,
            errors=errors,
        )

    def _entry(self) -> config_entries.ConfigEntry:
        """Return the config entry owned by this options flow."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            raise RuntimeError(f"Config entry {self.entry_id} no longer exists")
        return entry

    def _update_plex_data(self, token: str, base_url: str, verify_ssl: bool) -> None:
        """Persist validated Plex connection data outside public options."""
        entry = self._entry()
        data = {
            **entry.data,
            CONF_PLEX_ENABLED: True,
            CONF_PLEX_TOKEN: token,
            CONF_PLEX_BASEURL: base_url,
            CONF_PLEX_VERIFY_SSL: verify_ssl,
        }
        self.hass.config_entries.async_update_entry(entry, data=data)

    def _clear_plex_data(self) -> None:
        """Remove Plex credentials after explicit disable confirmation."""
        entry = self._entry()
        data = {
            **entry.data,
            CONF_PLEX_ENABLED: False,
            CONF_PLEX_TOKEN: "",
            CONF_PLEX_BASEURL: "",
            CONF_PLEX_VERIFY_SSL: True,
        }
        self.hass.config_entries.async_update_entry(entry, data=data)

    def _finish(self) -> config_entries.ConfigFlowResult:
        """Save options without changing required connection data."""
        return self.async_create_entry(title="", data=self.options)
