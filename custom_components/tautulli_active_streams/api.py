import logging
import aiohttp
import asyncio

_LOGGER = logging.getLogger(__name__)


class TautulliConnectionError(Exception):
    """Raised when Tautulli cannot be reached (timeout, DNS, refused, etc.)."""


class TautulliAuthError(Exception):
    """Raised when Tautulli returns an auth failure (bad API key)."""


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
    def _safe_base_url(self) -> str:
        """Return the base URL without the API key for safe logging."""
        return f"{self._base_url}?apikey=[REDACTED]&cmd="

    async def _call_tautulli(self, cmd, params=None, method="GET"):
        """
        Generic helper to call any Tautulli API command.
        """
        if params is None:
            params = {}

        url = f"{self._base_url}?apikey={self._api_key}&cmd={cmd}"
        method = method.upper()

        _LOGGER.debug(
            "TautulliAPI: calling cmd=%s method=%s",
            cmd, method
        )

        try:
            if method == "POST":
                # Some Tautulli commands might require POST in the future
                async with self._session.post(
                    url,
                    data=params,
                    timeout=self._timeout,
                    ssl=self._verify_ssl
                ) as response:
                    if response.status == 200:
                        try:
                            return await response.json()
                        except Exception as json_err:
                            _LOGGER.warning("Invalid JSON from Tautulli: %s", json_err)
                            return {}
                    else:
                        _LOGGER.warning("Non-200 status from Tautulli POST: %s", response.status)
                        return {}
            else:
                # Default to GET
                async with self._session.get(
                    url,
                    params=params,
                    timeout=self._timeout,
                    ssl=self._verify_ssl
                ) as response:
                    if response.status == 200:
                        try:
                            return await response.json()
                        except Exception as json_err:
                            _LOGGER.warning("Invalid JSON from Tautulli: %s", json_err)
                            return {}
                    else:
                        _LOGGER.warning("Non-200 status from Tautulli GET: %s", response.status)
                        return {}
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Tautulli API request '%s' timed out after %s seconds.",
                cmd, self._timeout.total
            )
            return {}
        except Exception as err:
            # Sanitize the error message to avoid leaking the API key
            err_msg = str(err).replace(self._api_key, "[REDACTED]")
            _LOGGER.error("Exception calling Tautulli %s: %s", cmd, err_msg)
            return {}

    async def get_activity(self):
        """
        Retrieve active session data from Tautulli.
        """
        resp = await self._call_tautulli("get_activity", method="GET")
        if not resp:
            return {"sessions": [], "diagnostics": {}}

        response_data = resp.get("response", {}).get("data", {})

        diagnostics = {
            "stream_count": response_data.get("stream_count", 0),
            "stream_count_direct_play": response_data.get("stream_count_direct_play", 0),
            "stream_count_direct_stream": response_data.get("stream_count_direct_stream", 0),
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
        try:
            resp = await self._call_tautulli("get_server_info", method="GET")
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as err:
            raise TautulliConnectionError(f"Cannot connect to Tautulli: {err}") from err

        if not resp:
            raise TautulliConnectionError("Empty response from Tautulli — check URL and network")

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
        """
        resp = await self._call_tautulli("get_history", params=params, method="GET")
        if not resp:
            return {}
        return resp.get("response", {}).get("data", {})

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
        """Kill a Tautulli session by session_id."""
        params = {"session_id": session_id, "message": message}
        resp = await self._call_tautulli("terminate_session", params=params, method="GET")
        return resp
