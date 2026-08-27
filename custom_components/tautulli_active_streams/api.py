import asyncio
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


class TautulliConnectionError(Exception):
    """Raised when Tautulli cannot be reached (timeout, DNS, refused, etc.)."""


class TautulliAuthError(Exception):
    """Raised when Tautulli returns an auth failure (bad API key)."""


class TautulliAPIError(TautulliConnectionError):
    """Raised when Tautulli returns a command-level error envelope."""


class TautulliAPI:
    """Handles communication with the Tautulli API."""

    def __init__(self, url, api_key, session, verify_ssl=True, timeout=10):
        """
        Initialize the API client.

        :param url: Base URL of your Tautulli instance.
        :param api_key: Your Tautulli API key.
        :param session: An aiohttp ClientSession (provided by Home Assistant).
        :param verify_ssl: Whether to verify SSL certificates.
        :param timeout: Request timeout in seconds (default 10).
        """
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._session = session
        self._verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=timeout)

        self._base_url = f"{self._url}/api/v2"

    @property
    def base_url(self) -> str:
        """Return the Tautulli base URL."""
        return self._url

    @property
    def api_key(self) -> str:
        """Return the Tautulli API key."""
        return self._api_key

    @property
    def session(self):
        """Return the aiohttp session."""
        return self._session

    @property
    def verify_ssl(self) -> bool:
        """Return whether upstream TLS certificates are verified."""
        return self._verify_ssl

    @property
    def _safe_base_url(self) -> str:
        """Return the base URL without the API key for safe logging."""
        return f"{self._base_url}?apikey=[REDACTED]&cmd="

    @staticmethod
    def _validate_payload(payload):
        """Raise an authentication error for API-wide credential failures."""
        response = payload.get("response", {}) if isinstance(payload, dict) else {}
        if response.get("result") != "error":
            return payload
        message = str(response.get("message", "Unknown Tautulli error"))
        lowered = message.lower()
        if "api key" in lowered or "apikey" in lowered or "unauthorized" in lowered:
            raise TautulliAuthError(message)
        raise TautulliAPIError(message)

    async def _decode_response(self, response, cmd: str, method: str):
        """Decode a Tautulli response while preserving command-level errors."""
        status = response.status
        try:
            payload = await response.json()
        except (aiohttp.ContentTypeError, ValueError) as json_err:
            if status in (401, 403):
                raise TautulliAuthError(
                    f"Tautulli rejected the API key (HTTP {status})"
                ) from json_err
            if status != 200:
                raise TautulliConnectionError(
                    f"Non-JSON HTTP {status} response from Tautulli {method} {cmd}"
                ) from json_err
            raise TautulliConnectionError(
                f"Invalid JSON from Tautulli for {cmd}: {json_err}"
            ) from json_err

        if status in (401, 403):
            response_data = (
                payload.get("response", {}) if isinstance(payload, dict) else {}
            )
            message = str(
                response_data.get(
                    "message", f"Tautulli rejected the API key (HTTP {status})"
                )
            )
            raise TautulliAuthError(message)

        if status >= 500:
            raise TautulliConnectionError(
                f"Tautulli server error during {method} {cmd} (HTTP {status})"
            )

        if status != 200:
            # Tautulli 2.18+ returns HTTP 400 for command failures. Validate the
            # response envelope so callers receive the useful upstream message.
            self._validate_payload(payload)
            raise TautulliAPIError(f"Tautulli rejected {method} {cmd} (HTTP {status})")

        return self._validate_payload(payload)

    async def _call_tautulli(self, cmd, params=None, method="GET"):
        """
        Generic helper to call any Tautulli API command.
        Raises TautulliConnectionError on network/timeout failures.
        """
        if params is None:
            params = {}

        url = f"{self._base_url}?apikey={self._api_key}&cmd={cmd}"
        method = method.upper()

        _LOGGER.debug("TautulliAPI: calling cmd=%s method=%s", cmd, method)

        try:
            if method == "POST":
                async with self._session.post(
                    url, data=params, timeout=self._timeout, ssl=self._verify_ssl
                ) as response:
                    return await self._decode_response(response, cmd, method)
            else:
                async with self._session.get(
                    url, params=params, timeout=self._timeout, ssl=self._verify_ssl
                ) as response:
                    return await self._decode_response(response, cmd, method)
        except TautulliConnectionError:
            raise  # Re-raise our own exceptions
        except TautulliAuthError:
            raise
        except asyncio.TimeoutError as err:
            raise TautulliConnectionError(
                f"Tautulli API request '{cmd}' timed out after {self._timeout.total}s"
            ) from err
        except (aiohttp.ClientError, OSError) as err:
            err_msg = str(err).replace(self._api_key, "[REDACTED]")
            raise TautulliConnectionError(
                f"Connection error calling Tautulli {cmd}: {err_msg}"
            ) from err
        except Exception as err:
            err_msg = str(err).replace(self._api_key, "[REDACTED]")
            _LOGGER.error("Unexpected error calling Tautulli %s: %s", cmd, err_msg)
            raise TautulliConnectionError(
                f"Unexpected error calling Tautulli {cmd}: {err_msg}"
            ) from err

    async def get_activity(self):
        """
        Retrieve active session data from Tautulli.
        Raises TautulliConnectionError if Tautulli cannot be reached.
        """
        resp = await self._call_tautulli("get_activity", method="GET")
        if not resp:
            return {"sessions": [], "diagnostics": {}}

        response_data = resp.get("response", {}).get("data", {})

        diagnostics = {
            "stream_count": response_data.get("stream_count", 0),
            "stream_count_direct_play": response_data.get(
                "stream_count_direct_play", 0
            ),
            "stream_count_direct_stream": response_data.get(
                "stream_count_direct_stream", 0
            ),
            "stream_count_transcode": response_data.get("stream_count_transcode", 0),
            "total_bandwidth": response_data.get("total_bandwidth", 0),
            "lan_bandwidth": response_data.get("lan_bandwidth", 0),
            "wan_bandwidth": response_data.get("wan_bandwidth", 0),
        }
        return {
            "sessions": response_data.get("sessions", []),
            "diagnostics": diagnostics,
        }

    async def get_server_info(self):
        """
        Validate connection to Tautulli by calling get_server_info.
        Raises TautulliConnectionError if Tautulli cannot be reached.
        Raises TautulliAuthError if the API key is invalid.
        Returns the full response dict on success.
        """
        # _call_tautulli raises TautulliConnectionError on network failures
        resp = await self._call_tautulli("get_server_info", method="GET")

        if not resp:
            raise TautulliConnectionError(
                "Empty response from Tautulli — check URL and network"
            )

        result = resp.get("response", {}).get("result")
        if result == "success":
            return resp

        # Tautulli returns result=error with a message for bad keys
        msg = resp.get("response", {}).get("message", "Unknown error")
        if "invalid" in msg.lower() or "api" in msg.lower():
            raise TautulliAuthError(f"Invalid API key: {msg}")
        raise TautulliConnectionError(f"Tautulli error: {msg}")

    async def get_history(self, **params):
        """
        Retrieve history data from Tautulli.
        Raises TautulliConnectionError if Tautulli cannot be reached.
        """
        resp = await self._call_tautulli("get_history", params=params, method="GET")
        if not resp:
            return {}
        return resp.get("response", {}).get("data", {})

    async def get_recently_added(
        self,
        *,
        start: int = 0,
        count: int = 20,
        media_type: str | None = None,
        section_id: str | None = None,
    ) -> dict:
        """Return a bounded page from the Plex dashboard's recent media."""
        params = {"start": max(0, start), "count": min(50, max(1, count))}
        if media_type:
            params["media_type"] = media_type
        if section_id:
            params["section_id"] = section_id
        resp = await self._call_tautulli("get_recently_added", params=params)
        return resp.get("response", {}).get("data", {}) if resp else {}

    async def get_home_stats(
        self,
        *,
        stat_id: str,
        time_range: int = 30,
        stats_type: str = "plays",
        start: int = 0,
        count: int = 10,
        section_id: str | None = None,
        user_id: str | None = None,
    ) -> dict | list[dict]:
        """Return one bounded Tautulli home-stat collection."""
        params = {
            "stat_id": stat_id,
            "time_range": min(3650, max(1, time_range)),
            "stats_type": stats_type,
            "stats_start": max(0, start),
            "stats_count": min(50, max(1, count)),
            "grouping": 1,
        }
        if section_id:
            params["section_id"] = section_id
        if user_id:
            params["user_id"] = user_id
        resp = await self._call_tautulli("get_home_stats", params=params)
        data = resp.get("response", {}).get("data", {}) if resp else {}
        return data if isinstance(data, (dict, list)) else {}

    async def get_user_names(self) -> list[dict]:
        """Return the minimal stable user selector data."""
        resp = await self._call_tautulli("get_user_names")
        data = resp.get("response", {}).get("data", []) if resp else []
        return data if isinstance(data, list) else []

    async def get_library_names(self) -> list[dict]:
        """Return the minimal library selector data."""
        resp = await self._call_tautulli("get_library_names")
        data = resp.get("response", {}).get("data", []) if resp else []
        return data if isinstance(data, list) else []

    async def get_geoip_lookup(self, ip_address: str) -> dict:
        """
        Retrieve geolocation data from Tautulli for the given IP address.
        Tautulli must have GeoIP set up.
        Returns a dict with that "data" subobject or {} on error.
        """
        # We'll call the base method to do Tautulli API:
        params = {"ip_address": ip_address}
        resp = await self._call_tautulli("get_geoip_lookup", params=params)
        if not resp:
            return {}

        # e.g., resp["response"]["data"] might be the relevant part:
        response_data = resp.get("response", {})
        if response_data.get("result") == "success":
            return response_data.get("data", {})
        else:
            return {}

    async def terminate_session(self, session_id, message=""):
        """Kill a Tautulli session by session_id.

        Returns True on success, raises on failure.
        """
        params = {"session_id": session_id, "message": message}
        resp = await self._call_tautulli(
            "terminate_session", params=params, method="POST"
        )
        result = resp.get("response", {}).get("result")
        if result != "success":
            msg = resp.get("response", {}).get("message", "Unknown error")
            _LOGGER.warning("terminate_session %s failed: %s", session_id, msg)
            return False
        return True
