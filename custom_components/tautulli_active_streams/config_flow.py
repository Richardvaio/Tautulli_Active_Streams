from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TautulliAPI, TautulliAuthError, TautulliConnectionError
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
    CONF_STATISTICS_CYCLE_DAY,
    CONF_STATISTICS_DAYS,
    CONF_STATISTICS_INTERVAL,
    CONF_STATISTICS_PERIOD,
    CONF_STATS_MONTH_TO_DATE,
    DEFAULT_SESSION_INTERVAL,
    DEFAULT_STATISTICS_CYCLE_DAY,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_STATISTICS_INTERVAL,
    DEFAULT_STATISTICS_PERIOD,
    DOMAIN,
    GEO_PROVIDER_IP_API,
    GEO_PROVIDER_TAUTULLI,
    STATISTICS_PERIOD_CALENDAR_MONTH,
    STATISTICS_PERIOD_CUSTOM_MONTH,
    STATISTICS_PERIOD_ROLLING,
    STATISTICS_PERIODS,
)
from .flow_helpers import (
    PlexAuthError,
    PlexConnectionError,
)
from .flow_helpers import (
    async_validate_plex as _async_validate_plex,
)
from .flow_helpers import (
    normalize_base_url as _normalize_base_url,
)
from .flow_helpers import (
    password_selector as _password_selector,
)
from .flow_helpers import (
    server_data as _server_data,
)
from .flow_helpers import (
    server_unique_id as _server_unique_id,
)
from .options_flow import TautulliOptionsFlowHandler

_LOGGER = logging.getLogger(__name__)

CONF_SERVER_NAME = "server_name"


class TautulliConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configuration flow for Tautulli Active Streams."""

    VERSION = 1

    def __init__(self) -> None:
        self._flow_data: dict[str, Any] = {}
        self._plex_base_from_tautulli = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect and validate the required Tautulli connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                url = _normalize_base_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                api_key = user_input[CONF_API_KEY].strip()
                verify_ssl = user_input.get(CONF_VERIFY_SSL, True)
                session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
                api = TautulliAPI(url, api_key, session, verify_ssl)

                try:
                    response = await api.get_server_info()
                except TautulliAuthError:
                    errors["base"] = "invalid_api_key"
                except (TautulliConnectionError, aiohttp.ClientConnectionError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected Tautulli setup error")
                    errors["base"] = "unknown"
                else:
                    server_data = _server_data(response)
                    await self.async_set_unique_id(_server_unique_id(response, url))
                    self._abort_if_unique_id_configured()

                    server_name = user_input.get(CONF_SERVER_NAME, "").strip()
                    server_name = server_name or server_data.get("pms_name", "")
                    self._flow_data.update(
                        {
                            CONF_SERVER_NAME: server_name,
                            CONF_URL: url,
                            CONF_API_KEY: api_key,
                            CONF_VERIFY_SSL: verify_ssl,
                        }
                    )
                    self._plex_base_from_tautulli = str(
                        server_data.get("pms_url", "")
                    ).rstrip("/")
                    return await self.async_step_features()

        values = user_input or {}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SERVER_NAME,
                    default=values.get(CONF_SERVER_NAME, ""),
                ): str,
                vol.Required(CONF_URL, default=values.get(CONF_URL, "")): str,
                vol.Required(CONF_API_KEY): _password_selector("new-password"),
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=values.get(CONF_VERIFY_SSL, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_features(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose the optional features to configure during setup."""
        if user_input is not None:
            self._flow_data.update(
                {
                    CONF_SESSION_INTERVAL: user_input[CONF_SESSION_INTERVAL],
                    CONF_ADVANCED_ATTRIBUTES: user_input[CONF_ADVANCED_ATTRIBUTES],
                    CONF_ENABLE_IP_GEOLOCATION: user_input[CONF_ENABLE_IP_GEOLOCATION],
                    CONF_ENABLE_STATISTICS: user_input[CONF_ENABLE_STATISTICS],
                    CONF_PLEX_ENABLED: user_input[CONF_PLEX_ENABLED],
                    # Keep valid defaults even while optional features are disabled.
                    CONF_GEO_PROVIDER: GEO_PROVIDER_TAUTULLI,
                    CONF_EXPOSE_DETAILED_LOCATION: False,
                    CONF_STATS_MONTH_TO_DATE: False,
                    CONF_STATISTICS_PERIOD: DEFAULT_STATISTICS_PERIOD,
                    CONF_STATISTICS_CYCLE_DAY: DEFAULT_STATISTICS_CYCLE_DAY,
                    CONF_STATISTICS_DAYS: DEFAULT_STATISTICS_DAYS,
                    CONF_STATISTICS_INTERVAL: DEFAULT_STATISTICS_INTERVAL,
                }
            )
            if self._flow_data[CONF_ENABLE_IP_GEOLOCATION]:
                return await self.async_step_location()
            return await self._async_next_setup_step(after="location")

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SESSION_INTERVAL,
                    default=DEFAULT_SESSION_INTERVAL,
                ): vol.All(int, vol.Range(min=1)),
                vol.Optional(CONF_ADVANCED_ATTRIBUTES, default=False): bool,
                vol.Optional(CONF_ENABLE_IP_GEOLOCATION, default=False): bool,
                vol.Optional(CONF_ENABLE_STATISTICS, default=False): bool,
                vol.Optional(CONF_PLEX_ENABLED, default=False): bool,
            }
        )
        return self.async_show_form(step_id="features", data_schema=schema)

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure optional geolocation and privacy settings."""
        if user_input is not None:
            self._flow_data.update(user_input)
            return await self._async_next_setup_step(after="location")

        schema = vol.Schema(
            {
                vol.Required(CONF_GEO_PROVIDER, default=GEO_PROVIDER_TAUTULLI): vol.In(
                    [GEO_PROVIDER_TAUTULLI, GEO_PROVIDER_IP_API]
                ),
                vol.Optional(CONF_EXPOSE_DETAILED_LOCATION, default=False): bool,
            }
        )
        return self.async_show_form(step_id="location", data_schema=schema)

    async def async_step_statistics(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose the watch-history statistics period."""
        if user_input is not None:
            self._flow_data.update(user_input)
            period = user_input[CONF_STATISTICS_PERIOD]
            self._flow_data[CONF_STATS_MONTH_TO_DATE] = (
                period == STATISTICS_PERIOD_CALENDAR_MONTH
            )
            if period in {
                STATISTICS_PERIOD_ROLLING,
                STATISTICS_PERIOD_CUSTOM_MONTH,
            }:
                return await self.async_step_statistics_range()
            return await self._async_next_setup_step(after="statistics")

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STATISTICS_PERIOD,
                    default=DEFAULT_STATISTICS_PERIOD,
                ): vol.In(STATISTICS_PERIODS),
                vol.Optional(
                    CONF_STATISTICS_INTERVAL,
                    default=DEFAULT_STATISTICS_INTERVAL,
                ): vol.All(int, vol.Range(min=60)),
            }
        )
        return self.async_show_form(step_id="statistics", data_schema=schema)

    async def async_step_statistics_range(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect the setting required by the selected statistics period."""
        if user_input is not None:
            self._flow_data.update(user_input)
            return await self._async_next_setup_step(after="statistics")

        period = self._flow_data[CONF_STATISTICS_PERIOD]
        if period == STATISTICS_PERIOD_ROLLING:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_STATISTICS_DAYS,
                        default=DEFAULT_STATISTICS_DAYS,
                    ): vol.All(int, vol.Range(min=1, max=365))
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_STATISTICS_CYCLE_DAY,
                        default=DEFAULT_STATISTICS_CYCLE_DAY,
                    ): vol.All(int, vol.Range(min=1, max=31))
                }
            )
        return self.async_show_form(step_id="statistics_range", data_schema=schema)

    async def _async_next_setup_step(
        self, after: str
    ) -> config_entries.ConfigFlowResult:
        """Continue through only the setup pages enabled by the user."""
        if after == "location" and self._flow_data[CONF_ENABLE_STATISTICS]:
            return await self.async_step_statistics()
        if self._flow_data[CONF_PLEX_ENABLED]:
            return await self.async_step_plex()
        return self._create_tautulli_entry()

    async def async_step_plex(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect and validate optional Plex enrichment credentials."""
        errors: dict[str, str] = {}
        values = user_input or {}

        if user_input is not None:
            token = user_input.get(CONF_PLEX_TOKEN, "").strip()
            plex_verify_ssl = user_input.get(CONF_PLEX_VERIFY_SSL, True)
            if not token:
                errors[CONF_PLEX_TOKEN] = "plex_token_required"

            try:
                base_url = _normalize_base_url(
                    user_input.get(CONF_PLEX_BASEURL, "")
                    or self._plex_base_from_tautulli
                )
            except ValueError:
                errors[CONF_PLEX_BASEURL] = "invalid_url"
            else:
                if not errors:
                    session = async_get_clientsession(
                        self.hass, verify_ssl=plex_verify_ssl
                    )
                    try:
                        await _async_validate_plex(
                            session, base_url, token, plex_verify_ssl
                        )
                    except PlexAuthError:
                        errors[CONF_PLEX_TOKEN] = "invalid_plex_token"
                    except PlexConnectionError:
                        errors["base"] = "cannot_connect_plex"
                    else:
                        self._flow_data[CONF_PLEX_TOKEN] = token
                        self._flow_data[CONF_PLEX_BASEURL] = base_url
                        self._flow_data[CONF_PLEX_VERIFY_SSL] = plex_verify_ssl
                        return self._create_tautulli_entry()

        schema = vol.Schema(
            {
                vol.Required(CONF_PLEX_TOKEN): _password_selector("new-password"),
                vol.Required(
                    CONF_PLEX_BASEURL,
                    default=values.get(
                        CONF_PLEX_BASEURL, self._plex_base_from_tautulli
                    ),
                ): str,
                vol.Optional(CONF_PLEX_VERIFY_SSL, default=True): bool,
            }
        )
        return self.async_show_form(step_id="plex", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start reauthentication for an existing Tautulli entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate a replacement Tautulli API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            url = entry.data[CONF_URL]
            verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
            session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
            api = TautulliAPI(url, api_key, session, verify_ssl)
            try:
                response = await api.get_server_info()
            except TautulliAuthError:
                errors["base"] = "invalid_api_key"
            except (TautulliConnectionError, aiohttp.ClientConnectionError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected Tautulli reauthentication error")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_server_unique_id(response, url))
                self._abort_if_unique_id_mismatch(reason="wrong_server")
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_API_KEY: api_key},
                )
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {vol.Required(CONF_API_KEY): _password_selector("new-password")}
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Update non-authentication Tautulli connection details."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        values = user_input or {}

        if user_input is not None:
            try:
                url = _normalize_base_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                verify_ssl = user_input.get(CONF_VERIFY_SSL, True)
                session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
                api = TautulliAPI(url, entry.data[CONF_API_KEY], session, verify_ssl)
                try:
                    response = await api.get_server_info()
                except TautulliAuthError:
                    errors["base"] = "invalid_api_key_reauth"
                except (TautulliConnectionError, aiohttp.ClientConnectionError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected Tautulli reconfiguration error")
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(_server_unique_id(response, url))
                    self._abort_if_unique_id_mismatch(reason="wrong_server")
                    data = _server_data(response)
                    server_name = user_input.get(CONF_SERVER_NAME, "").strip()
                    server_name = server_name or data.get("pms_name") or entry.title
                    self.hass.config_entries.async_update_entry(
                        entry,
                        title=server_name,
                        data={
                            **entry.data,
                            CONF_SERVER_NAME: server_name,
                            CONF_URL: url,
                            CONF_VERIFY_SSL: verify_ssl,
                        },
                    )
                    return self.async_abort(reason="reconfigure_successful")

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SERVER_NAME,
                    default=values.get(
                        CONF_SERVER_NAME,
                        entry.data.get(CONF_SERVER_NAME, entry.title),
                    ),
                ): str,
                vol.Required(
                    CONF_URL,
                    default=values.get(CONF_URL, entry.data.get(CONF_URL, "")),
                ): str,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=values.get(
                        CONF_VERIFY_SSL,
                        entry.data.get(CONF_VERIFY_SSL, True),
                    ),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    def _create_tautulli_entry(self) -> config_entries.ConfigFlowResult:
        """Create a config entry from the validated setup data."""
        if not self._flow_data[CONF_PLEX_ENABLED]:
            self._flow_data[CONF_PLEX_TOKEN] = ""
            self._flow_data[CONF_PLEX_BASEURL] = ""
            self._flow_data[CONF_PLEX_VERIFY_SSL] = True

        data = {
            CONF_URL: self._flow_data[CONF_URL],
            CONF_API_KEY: self._flow_data[CONF_API_KEY],
            CONF_VERIFY_SSL: self._flow_data[CONF_VERIFY_SSL],
            CONF_SERVER_NAME: self._flow_data.get(CONF_SERVER_NAME, ""),
            CONF_PLEX_ENABLED: self._flow_data[CONF_PLEX_ENABLED],
            CONF_PLEX_TOKEN: self._flow_data[CONF_PLEX_TOKEN],
            CONF_PLEX_BASEURL: self._flow_data[CONF_PLEX_BASEURL],
            CONF_PLEX_VERIFY_SSL: self._flow_data[CONF_PLEX_VERIFY_SSL],
        }
        options = {
            CONF_SESSION_INTERVAL: self._flow_data[CONF_SESSION_INTERVAL],
            CONF_ENABLE_IP_GEOLOCATION: self._flow_data[CONF_ENABLE_IP_GEOLOCATION],
            CONF_GEO_PROVIDER: self._flow_data[CONF_GEO_PROVIDER],
            CONF_EXPOSE_DETAILED_LOCATION: self._flow_data[
                CONF_EXPOSE_DETAILED_LOCATION
            ],
            CONF_ADVANCED_ATTRIBUTES: self._flow_data[CONF_ADVANCED_ATTRIBUTES],
            CONF_ENABLE_STATISTICS: self._flow_data[CONF_ENABLE_STATISTICS],
            CONF_STATS_MONTH_TO_DATE: self._flow_data[CONF_STATS_MONTH_TO_DATE],
            CONF_STATISTICS_PERIOD: self._flow_data[CONF_STATISTICS_PERIOD],
            CONF_STATISTICS_CYCLE_DAY: self._flow_data[
                CONF_STATISTICS_CYCLE_DAY
            ],
            CONF_STATISTICS_INTERVAL: self._flow_data[CONF_STATISTICS_INTERVAL],
            CONF_STATISTICS_DAYS: self._flow_data[CONF_STATISTICS_DAYS],
            CONF_PLEX_ENABLED: self._flow_data[CONF_PLEX_ENABLED],
        }
        return self.async_create_entry(
            title=self._flow_data.get(CONF_SERVER_NAME) or "Tautulli Active Streams",
            data=data,
            options=options,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TautulliOptionsFlowHandler:
        """Return the options flow handler."""
        return TautulliOptionsFlowHandler(config_entry)
