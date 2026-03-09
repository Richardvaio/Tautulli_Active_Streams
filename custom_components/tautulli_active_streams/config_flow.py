import aiohttp
import asyncio
import voluptuous as vol
from typing import Any, Dict  # Make sure Any is imported
from homeassistant import config_entries
from homeassistant.config_entries import FlowResult
from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    # Basic Tautulli constants
    CONF_SESSION_INTERVAL,
    DEFAULT_SESSION_INTERVAL,
    CONF_NUM_SENSORS,
    DEFAULT_NUM_SENSORS,
    CONF_ENABLE_STATISTICS,
    DEFAULT_STATISTICS_INTERVAL,
    DEFAULT_STATISTICS_DAYS,
    CONF_STATS_MONTH_TO_DATE,
    CONF_STATISTICS_INTERVAL,
    CONF_STATISTICS_DAYS,
    CONF_ADVANCED_ATTRIBUTES,
    CONF_IMAGE_PROXY,
    CONF_ENABLE_IP_GEOLOCATION,
    CONF_GEO_PROVIDER,
    GEO_PROVIDER_TAUTULLI,
    GEO_PROVIDER_IP_API,
    # Plex constants
    CONF_PLEX_ENABLED,
    CONF_PLEX_TOKEN,
    CONF_PLEX_BASEURL,
    LOGGER,
)
from .api import TautulliAPI


class TautulliConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configuration flow for Tautulli Active Streams."""
    VERSION = 1

    def __init__(self):
        self._flow_data = {}
        self._plex_base_from_tautulli = ""

    async def async_step_user(self, user_input=None):
        """
        Step 1: Basic Tautulli info. If valid, go to step_advanced.
        """
        errors = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            if not url.startswith(("http://", "https://")):
                url = f"http://{url}"
            verify_ssl = user_input.get(CONF_VERIFY_SSL, True)

            session = async_get_clientsession(self.hass, verify_ssl)
            api = TautulliAPI(url, user_input[CONF_API_KEY], session, verify_ssl)

            try:
                resp = await api.get_server_info()
                if not isinstance(resp, dict) or "response" not in resp:
                    raise ValueError(f"Malformed API response: {resp}")

                if resp["response"].get("result") != "success":
                    errors["base"] = "invalid_api_key"
                else:
                    server_name = user_input.get("server_name", "").strip()
                    if not server_name:
                        server_name = resp["response"]["data"].get("pms_name", "")

                    # Set unique ID to prevent duplicate entries for the same server
                    pms_identifier = resp["response"]["data"].get("pms_identifier", url)
                    await self.async_set_unique_id(pms_identifier)
                    self._abort_if_unique_id_configured()

                    self._flow_data.update({
                        "server_name": server_name,
                        CONF_URL: url,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_VERIFY_SSL: verify_ssl,
                    })
                    self._plex_base_from_tautulli = resp["response"]["data"].get("pms_url", "")

                    return await self.async_step_advanced()

            except aiohttp.ClientConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as e:
                LOGGER.exception("Error in Tautulli config flow user step: %s", e)
                errors["base"] = "unknown"

        return self._show_tautulli_form(errors, user_input)

    def _show_tautulli_form(self, errors=None, user_input=None):
        user_input = user_input or {}
        schema = vol.Schema({
            vol.Optional("server_name", default=user_input.get("server_name", "")): str,
            vol.Required(CONF_URL, default=user_input.get(CONF_URL, "")): str,
            vol.Required(CONF_API_KEY, default=user_input.get(CONF_API_KEY, "")): str,
            vol.Optional(CONF_VERIFY_SSL, default=user_input.get(CONF_VERIFY_SSL, True)): bool,
        })
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors or {}
        )

    async def async_step_advanced(self, user_input=None):
        """
        Step 2: advanced toggles (session intervals, stats, plus plex toggle).
        """
        if user_input is not None:
            session_interval = user_input.get(CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL)
            num_sensors = user_input.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)
            image_proxy = user_input.get(CONF_IMAGE_PROXY, False)
            geo = user_input.get(CONF_ENABLE_IP_GEOLOCATION, False)
            geo_provider = user_input.get(CONF_GEO_PROVIDER, GEO_PROVIDER_TAUTULLI)
            adv_attrs = user_input.get(CONF_ADVANCED_ATTRIBUTES, False)
            enable_stats = user_input.get(CONF_ENABLE_STATISTICS, False)
            stats_mtd = user_input.get(CONF_STATS_MONTH_TO_DATE, False)
            stats_interval = user_input.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL)
            stats_days = user_input.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS) if enable_stats else 0

            plex_enabled_new = user_input.get("enable_plex_integration", False)

            self._flow_data.update({
                CONF_SESSION_INTERVAL: session_interval,
                CONF_NUM_SENSORS: num_sensors,
                CONF_IMAGE_PROXY: image_proxy,
                CONF_ENABLE_IP_GEOLOCATION: geo,
                CONF_GEO_PROVIDER: geo_provider,
                CONF_ADVANCED_ATTRIBUTES: adv_attrs,
                CONF_ENABLE_STATISTICS: enable_stats,
                CONF_STATS_MONTH_TO_DATE: stats_mtd,
                CONF_STATISTICS_INTERVAL: stats_interval,
                CONF_STATISTICS_DAYS: stats_days,
            })

            if plex_enabled_new:
                return await self.async_step_plex()
            else:
                return self._create_tautulli_entry()

        return self._show_advanced_form()

    def _show_advanced_form(self, errors=None):
        schema = vol.Schema({
            vol.Required(CONF_SESSION_INTERVAL, default=DEFAULT_SESSION_INTERVAL): vol.All(int, vol.Range(min=1)),
            vol.Required(CONF_NUM_SENSORS, default=DEFAULT_NUM_SENSORS): vol.All(int, vol.Range(min=1)),
            vol.Optional(CONF_IMAGE_PROXY, default=False): bool,
            vol.Optional(CONF_ENABLE_IP_GEOLOCATION, default=False): bool,
            vol.Optional(CONF_GEO_PROVIDER, default=GEO_PROVIDER_TAUTULLI): vol.In(
                [GEO_PROVIDER_TAUTULLI, GEO_PROVIDER_IP_API]
            ),
            vol.Optional("enable_plex_integration", default=False): bool,
            vol.Optional(CONF_ADVANCED_ATTRIBUTES, default=False): bool,
            vol.Optional(CONF_ENABLE_STATISTICS, default=False): bool,
            vol.Optional(CONF_STATS_MONTH_TO_DATE, default=False): bool,
            vol.Optional(CONF_STATISTICS_DAYS, default=DEFAULT_STATISTICS_DAYS): vol.All(int, vol.Range(min=1)),
            vol.Optional(CONF_STATISTICS_INTERVAL, default=DEFAULT_STATISTICS_INTERVAL): vol.All(int, vol.Range(min=60)),
        })
        return self.async_show_form(
            step_id="advanced", data_schema=schema, errors=errors or {}
        )

    async def async_step_plex(self, user_input=None):
        """
        Step 3: If plex was toggled on, gather plex_token + plex_base_url.
        """
        errors = {}
        if user_input is not None:
            plex_token = user_input.get(CONF_PLEX_TOKEN, "").strip()
            if not plex_token:
                errors[CONF_PLEX_TOKEN] = "plex_token_required"
            elif len(plex_token) < 20:
                errors[CONF_PLEX_TOKEN] = "invalid_plex_token"

            if not errors:
                self._flow_data[CONF_PLEX_TOKEN] = plex_token
                self._flow_data[CONF_PLEX_ENABLED] = True

                plex_base_input = user_input.get(CONF_PLEX_BASEURL, "").strip()
                if plex_base_input:
                    self._flow_data[CONF_PLEX_BASEURL] = plex_base_input
                else:
                    self._flow_data[CONF_PLEX_BASEURL] = self._plex_base_from_tautulli

                return self._create_tautulli_entry()

        default_base = self._plex_base_from_tautulli
        plex_schema = vol.Schema({
            vol.Required(CONF_PLEX_TOKEN, default=""): str,
            vol.Optional(CONF_PLEX_BASEURL, default=default_base): str,
        })
        return self.async_show_form(
            step_id="plex", data_schema=plex_schema, errors=errors
        )

    def _create_tautulli_entry(self):
        """
        Build the config entry data+options from self._flow_data.
        """
        # If plex wasn't toggled on
        if CONF_PLEX_ENABLED not in self._flow_data:
            self._flow_data[CONF_PLEX_ENABLED] = False
            self._flow_data[CONF_PLEX_TOKEN] = ""
            self._flow_data[CONF_PLEX_BASEURL] = ""

        data = {
            CONF_URL: self._flow_data[CONF_URL],
            CONF_API_KEY: self._flow_data[CONF_API_KEY],
            CONF_VERIFY_SSL: self._flow_data[CONF_VERIFY_SSL],
            "server_name": self._flow_data.get("server_name", ""),
            # Add Plex fields to data too
            CONF_PLEX_ENABLED: self._flow_data[CONF_PLEX_ENABLED],
            CONF_PLEX_TOKEN: self._flow_data[CONF_PLEX_TOKEN],
            CONF_PLEX_BASEURL: self._flow_data[CONF_PLEX_BASEURL],
        }

        options = {
            CONF_SESSION_INTERVAL: self._flow_data[CONF_SESSION_INTERVAL],
            CONF_NUM_SENSORS: self._flow_data[CONF_NUM_SENSORS],
            CONF_IMAGE_PROXY: self._flow_data[CONF_IMAGE_PROXY],
            CONF_ENABLE_IP_GEOLOCATION: self._flow_data[CONF_ENABLE_IP_GEOLOCATION],
            CONF_GEO_PROVIDER: self._flow_data.get(CONF_GEO_PROVIDER, GEO_PROVIDER_TAUTULLI),
            CONF_ADVANCED_ATTRIBUTES: self._flow_data[CONF_ADVANCED_ATTRIBUTES],
            CONF_ENABLE_STATISTICS: self._flow_data[CONF_ENABLE_STATISTICS],
            CONF_STATS_MONTH_TO_DATE: self._flow_data[CONF_STATS_MONTH_TO_DATE],
            CONF_STATISTICS_INTERVAL: self._flow_data[CONF_STATISTICS_INTERVAL],
            CONF_STATISTICS_DAYS: self._flow_data[CONF_STATISTICS_DAYS],
            CONF_PLEX_ENABLED: self._flow_data[CONF_PLEX_ENABLED],
        }

        return self.async_create_entry(
            title=self._flow_data.get("server_name") or "Tautulli Active Streams",
            data=data,
            options=options,
        )


    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return TautulliOptionsFlowHandler(config_entry)


class TautulliOptionsFlowHandler(config_entries.OptionsFlow):
    """
    Post-setup options. If user toggles plex from off => on, gather token/base.
    We'll also update config_entry.data so the sensor code sees them in `.data`.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry):
        # Don't store config_entry directly to avoid deprecation warning
        self.entry_id = config_entry.entry_id
        self.options = dict(config_entry.options)
        self._plex_enabled_old = self.options.get(CONF_PLEX_ENABLED, False)

    async def async_step_init(self, user_input=None):
        """Show toggles. If plex toggled from off => on, go to plex_setup."""
        if user_input is not None:
            self.options[CONF_SESSION_INTERVAL] = user_input[CONF_SESSION_INTERVAL]
            self.options[CONF_NUM_SENSORS] = user_input[CONF_NUM_SENSORS]
            self.options[CONF_IMAGE_PROXY] = user_input[CONF_IMAGE_PROXY]
            self.options[CONF_ENABLE_IP_GEOLOCATION] = user_input[CONF_ENABLE_IP_GEOLOCATION]
            self.options[CONF_GEO_PROVIDER] = user_input.get(CONF_GEO_PROVIDER, GEO_PROVIDER_TAUTULLI)
            self.options[CONF_ADVANCED_ATTRIBUTES] = user_input[CONF_ADVANCED_ATTRIBUTES]
            self.options[CONF_ENABLE_STATISTICS] = user_input[CONF_ENABLE_STATISTICS]
            self.options[CONF_STATS_MONTH_TO_DATE] = user_input[CONF_STATS_MONTH_TO_DATE]
            self.options[CONF_STATISTICS_DAYS] = user_input[CONF_STATISTICS_DAYS]
            self.options[CONF_STATISTICS_INTERVAL] = user_input[CONF_STATISTICS_INTERVAL]

            plex_enabled_new = user_input.get(CONF_PLEX_ENABLED, False)
            self.options[CONF_PLEX_ENABLED] = plex_enabled_new

            # If user toggled plex from off => on
            if not self._plex_enabled_old and plex_enabled_new:
                return await self.async_step_plex_setup()

            # else finalize
            self._update_config_entry_data()
            return self.async_create_entry(title="", data=self.options)

        data_schema = vol.Schema({
            vol.Required(
                CONF_SESSION_INTERVAL,
                default=self.options.get(CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL)
            ): vol.All(int, vol.Range(min=1)),
            vol.Required(
                CONF_NUM_SENSORS,
                default=self.options.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)
            ): vol.All(int, vol.Range(min=1)),

            vol.Optional(
                CONF_IMAGE_PROXY, default=self.options.get(CONF_IMAGE_PROXY, False)
            ): bool,
            vol.Optional(
                CONF_ENABLE_IP_GEOLOCATION, default=self.options.get(CONF_ENABLE_IP_GEOLOCATION, False)
            ): bool,
            vol.Optional(
                CONF_GEO_PROVIDER, default=self.options.get(CONF_GEO_PROVIDER, GEO_PROVIDER_TAUTULLI)
            ): vol.In([GEO_PROVIDER_TAUTULLI, GEO_PROVIDER_IP_API]),

            vol.Optional(
                CONF_PLEX_ENABLED, default=self.options.get(CONF_PLEX_ENABLED, False)
            ): bool,

            vol.Optional(
                CONF_ADVANCED_ATTRIBUTES, default=self.options.get(CONF_ADVANCED_ATTRIBUTES, False)
            ): bool,
            vol.Optional(
                CONF_ENABLE_STATISTICS, default=self.options.get(CONF_ENABLE_STATISTICS, False)
            ): bool,
            vol.Optional(
                CONF_STATS_MONTH_TO_DATE, default=self.options.get(CONF_STATS_MONTH_TO_DATE, False)
            ): bool,
            vol.Optional(
                CONF_STATISTICS_DAYS, default=self.options.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS)
            ): vol.All(int, vol.Range(min=1)),
            vol.Optional(
                CONF_STATISTICS_INTERVAL, default=self.options.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL)
            ): vol.All(int, vol.Range(min=60)),
        })
        return self.async_show_form(step_id="init", data_schema=data_schema)

    async def async_step_plex_setup(self, user_input=None):
        """If user toggles plex on, gather plex token/base. Then sync to data."""
        errors = {}
        if user_input is not None:
            plex_token = user_input.get(CONF_PLEX_TOKEN, "").strip()
            # Validate token before saving
            if not plex_token:
                errors[CONF_PLEX_TOKEN] = "plex_token_required"
            elif len(plex_token) < 20:
                errors[CONF_PLEX_TOKEN] = "invalid_plex_token"
                
            if not errors:
                # Store plex_token in data only (encrypted), not in options
                self._plex_token_new = plex_token
                plex_base = user_input.get(CONF_PLEX_BASEURL, "").strip()
                self._plex_base_new = plex_base
                self.options[CONF_PLEX_ENABLED] = True
                
                # Sync to config entry data before creating entry
                self._update_config_entry_data()
                return self.async_create_entry(title="", data=self.options)

        fallback_token = self.options.get(CONF_PLEX_TOKEN, "")
        fallback_baseurl = self.options.get(CONF_PLEX_BASEURL, "")
        plex_schema = vol.Schema({
            vol.Required(CONF_PLEX_TOKEN, default=fallback_token): str,
            vol.Optional(CONF_PLEX_BASEURL, default=fallback_baseurl): str,        })
        return self.async_show_form(
            step_id="plex_setup",
            data_schema=plex_schema,
            errors=errors
        )
        
    def _update_config_entry_data(self):
        """
        Sync the plex fields from self.options into config_entry.data
        so the sensor code can read them from entry.data.
        Credentials (plex_token) are stored only in data, never in options.
        """
        # Get the current config entry from the hass instance using entry_id
        config_entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if not config_entry:
            LOGGER.error("Could not find config entry with id %s", self.entry_id)
            return
            
        new_data = dict(config_entry.data)

        # Update Plex fields in data
        new_data[CONF_PLEX_ENABLED] = self.options.get(CONF_PLEX_ENABLED, False)
        new_data[CONF_PLEX_BASEURL] = getattr(self, "_plex_base_new", new_data.get(CONF_PLEX_BASEURL, ""))

        # Only update plex_token if a new one was provided
        if hasattr(self, "_plex_token_new"):
            new_data[CONF_PLEX_TOKEN] = self._plex_token_new

        self.hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            options=self.options
        )
