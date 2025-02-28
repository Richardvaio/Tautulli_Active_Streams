import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    DEFAULT_SESSION_INTERVAL,
    DEFAULT_NUM_SENSORS,
    DEFAULT_STATISTICS_INTERVAL,
    DEFAULT_STATISTICS_DAYS,
    CONF_SESSION_INTERVAL,
    CONF_NUM_SENSORS,
    CONF_ENABLE_STATISTICS,
    CONF_STATISTICS_INTERVAL,
    CONF_STATISTICS_DAYS,
    CONF_ADVANCED_ATTRIBUTES,
    CONF_IMAGE_PROXY,
    LOGGER,
)
from .api import TautulliAPI


class TautulliConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles the configuration flow for Tautulli Active Streams."""

    VERSION = 1

    def _show_setup_form(self, errors=None, user_input=None):
        """
        Step 1: Only ask for server_name, URL, API key, verify_ssl.
        """
        user_input = user_input or {}

        data_schema = vol.Schema({
            vol.Optional("server_name", default=user_input.get("server_name", "")): str,
            vol.Required(CONF_URL, default=user_input.get(CONF_URL, "")): str,
            vol.Required(CONF_API_KEY, default=user_input.get(CONF_API_KEY, "")): str,
            vol.Optional(CONF_VERIFY_SSL, default=user_input.get(CONF_VERIFY_SSL, True)): bool,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors or {}
        )

    async def async_step_user(self, user_input=None):
        """
        Step 1 logic: Validate connectivity, store partial data.
        """
        errors = {}
        if user_input is not None:
            # Check if already configured
            existing_entry = self._async_abort_entries_match({CONF_URL: user_input[CONF_URL]})
            if existing_entry:
                return self.async_abort(reason="already_configured")

            url = user_input[CONF_URL].strip()
            verify_ssl = user_input.get(CONF_VERIFY_SSL, True)
            server_name = user_input.get("server_name", "").strip()

            # Test Tautulli connectivity
            session = async_get_clientsession(self.hass, verify_ssl)
            api = TautulliAPI(url, user_input[CONF_API_KEY], session, verify_ssl)
            try:
                resp = await api.get_activity()
                if not resp:
                    raise ValueError("Invalid API response")
            except Exception:
                errors["base"] = "cannot_connect"
                return self._show_setup_form(errors, user_input)

            # If success, store partial data and proceed to step 2
            self._flow_data = {
                "server_name": server_name,
                "url": url,
                "api_key": user_input[CONF_API_KEY],
                "verify_ssl": verify_ssl,
            }
            return await self.async_step_options()

        # If no user input yet, show step1 form
        return self._show_setup_form()

    async def async_step_options(self, user_input=None):
        """
        Step 2: ask for:
          - session_interval (active streams)
          - num_sensors
          - image_proxy
          - advanced_attributes
          - enable_statistics
          - stats_interval (for watch history)
          - stats_days is set automatically if enable_stats is on
        """
        errors = {}
        if user_input is not None:
            # retrieve partial data from step1
            sname = self._flow_data["server_name"]
            url = self._flow_data["url"]
            key = self._flow_data["api_key"]
            sslv = self._flow_data["verify_ssl"]

            # read step2 fields
            session_interval = user_input.get(CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL)
            num_sensors = user_input.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)
            image_proxy = user_input.get("image_proxy", False)
            adv_attrs = user_input.get("advanced_attributes", False)
            enable_stats = user_input.get("enable_statistics", False)
            stats_interval = user_input.get("stats_interval", DEFAULT_STATISTICS_INTERVAL)

            # If stats is enabled, default stats_days to 30, else 0
            stats_days = DEFAULT_STATISTICS_DAYS if enable_stats else 0

            # final integration title
            final_title = sname if sname else "Tautulli Active Streams"

            return self.async_create_entry(
                title=final_title,
                data={
                    CONF_URL: url,
                    CONF_API_KEY: key,
                    CONF_VERIFY_SSL: sslv,
                },
                options={
                    # store the session_interval for active streams
                    CONF_SESSION_INTERVAL: session_interval,
                    CONF_NUM_SENSORS: num_sensors,
                    CONF_IMAGE_PROXY: image_proxy,
                    CONF_ADVANCED_ATTRIBUTES: adv_attrs,
                    CONF_ENABLE_STATISTICS: enable_stats,
                    CONF_STATISTICS_DAYS: stats_days,
                    # new: stats_interval for watch history
                    "stats_interval": stats_interval,
                },
            )

        # Show step2 form
        user_input = user_input or {}
        data_schema = vol.Schema({
            vol.Required(CONF_SESSION_INTERVAL, default=DEFAULT_SESSION_INTERVAL): int,
            vol.Required(CONF_NUM_SENSORS, default=DEFAULT_NUM_SENSORS): int,
            vol.Optional("image_proxy", default=False): bool,
            vol.Optional("advanced_attributes", default=False): bool,
            vol.Optional("enable_statistics", default=False): bool,
            # new: stats_interval for watch history
            vol.Optional(CONF_STATISTICS_INTERVAL, default=DEFAULT_STATISTICS_INTERVAL): int,
        })
        return self.async_show_form(
            step_id="options",
            data_schema=data_schema,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler for post-install reconfiguration."""
        return TautulliOptionsFlowHandler(config_entry)


class TautulliOptionsFlowHandler(config_entries.OptionsFlow):
    """
    Options flow displayed if the user opens "Configure" after the integration is installed.
    """
    def __init__(self, config_entry):
        self._config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input=None):
        """
        Manage all advanced fields here too, including stats_interval and stats_days.
        """
        if user_input is not None:
            # If user changed anything, we store it
            return self.async_create_entry(title="", data=user_input)

        # Show the same fields from Step 2, including stats_interval
        advanced_schema = vol.Schema({
            vol.Required(
                CONF_SESSION_INTERVAL,
                default=self.options.get(CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL)
            ): int,
            vol.Required(
                CONF_NUM_SENSORS,
                default=self.options.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)
            ): int,
            vol.Optional(
                CONF_IMAGE_PROXY,
                default=self.options.get(CONF_IMAGE_PROXY, False)
            ): bool,
            vol.Optional(
                CONF_ADVANCED_ATTRIBUTES,
                default=self.options.get(CONF_ADVANCED_ATTRIBUTES, False)
            ): bool,
            vol.Optional(
                CONF_ENABLE_STATISTICS,
                default=self.options.get(CONF_ENABLE_STATISTICS, False)
            ): bool,
            vol.Optional(
                CONF_STATISTICS_DAYS,
                default=self.options.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS)
            ): int,
            # stats_interval for watch history
            vol.Optional(
                CONF_STATISTICS_INTERVAL,
                default=self.options.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL)
            ): int,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=advanced_schema
        )
