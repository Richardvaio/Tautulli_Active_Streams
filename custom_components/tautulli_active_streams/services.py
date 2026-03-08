import logging
import asyncio
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

KILL_ALL_SCHEMA = vol.Schema({
    vol.Optional("message", default="Stream ended by admin."): cv.string,
})

KILL_USER_SCHEMA = vol.Schema({
    vol.Required("user"): cv.string,
    vol.Optional("message", default="Stream ended by admin."): cv.string,
})

KILL_SESSION_SCHEMA = vol.Schema({
    vol.Required("session_id"): cv.string,
    vol.Optional("message", default="Stream ended by admin."): cv.string,
})


async def async_setup_kill_stream_services(hass: HomeAssistant, entry, api) -> None:
    """Register kill-stream services for Tautulli Active Streams."""

    # This dictionary has keys like {"api": ..., "sessions_coordinator": ..., "history_coordinator": ...}
    data_dict = hass.data[DOMAIN].get(entry.entry_id)
    if not data_dict:
        _LOGGER.error("No integration data found for entry_id=%s, cannot register kill-stream services.", entry.entry_id)
        return

    sessions_coordinator = data_dict.get("sessions_coordinator")
    if not sessions_coordinator:
        _LOGGER.error("No sessions_coordinator found for entry_id=%s, cannot register kill-stream services.", entry.entry_id)
        return

    async def handle_kill_all_streams(call: ServiceCall) -> None:
        message = call.data.get("message")
        if not sessions_coordinator.data:
            _LOGGER.debug("Coordinator data not available yet.")
            return
        sessions = sessions_coordinator.data.get("sessions", [])
        if not sessions:
            _LOGGER.debug("No active sessions found to kill.")
            return

        _LOGGER.info("Terminating %d active sessions. message=%s", len(sessions), message)
        tasks = []
        for s in sessions:
            sid = s.get("session_id")
            if sid:
                tasks.append(api.terminate_session(sid, message=message))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if not isinstance(r, Exception))
        _LOGGER.info("Killed %d/%d sessions successfully", success, len(sessions))

    async def handle_kill_user_streams(call: ServiceCall) -> None:
        user = call.data["user"].strip().lower()
        message = call.data.get("message")
        if not sessions_coordinator.data:
            _LOGGER.debug("Coordinator data not available yet.")
            return
        sessions = sessions_coordinator.data.get("sessions", [])
        if not sessions:
            _LOGGER.debug("No active sessions found to kill by user '%s'.", user)
            return

        matched = []
        for s in sessions:
            # Check if the user name is in any of these fields
            names = [
                (s.get("user") or "").lower(),
                (s.get("username") or "").lower(),
                (s.get("friendly_name") or "").lower(),
            ]
            if any(user == x for x in names):
                matched.append(s)

        if not matched:
            _LOGGER.debug("No sessions found for user '%s'", user)
            return

        _LOGGER.info("Terminating %d sessions for user '%s'. message=%s", len(matched), user, message)
        tasks = []
        for s in matched:
            sid = s.get("session_id")
            if sid:
                tasks.append(api.terminate_session(sid, message=message))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if not isinstance(r, Exception))
        _LOGGER.info("Killed %d/%d sessions for user '%s'", success, len(matched), user)

    async def handle_kill_session_stream(call: ServiceCall) -> None:
        sid = call.data["session_id"].strip()
        message = call.data.get("message", "Stream ended by admin.")
        if not sessions_coordinator.data:
            _LOGGER.debug("Coordinator data not available yet.")
            return
        sessions = sessions_coordinator.data.get("sessions", [])
        if not sessions:
            _LOGGER.debug("No sessions found to kill.")
            return

        if sid not in [x.get("session_id") for x in sessions]:
            _LOGGER.warning("Session %s not found in active list", sid)

        try:
            await api.terminate_session(sid, message=message)
            _LOGGER.info("Terminated session %s", sid)
        except Exception as exc:
            _LOGGER.error("Error killing session %s: %s", sid, exc)

    # Register the three kill-stream services
    hass.services.async_register(
        DOMAIN, "kill_all_streams", handle_kill_all_streams, schema=KILL_ALL_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "kill_user_streams", handle_kill_user_streams, schema=KILL_USER_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "kill_session_stream", handle_kill_session_stream, schema=KILL_SESSION_SCHEMA
    )

    _LOGGER.debug("Tautulli kill-stream services set up for entry_id=%s.", entry.entry_id)
