from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TautulliAPI
from .const import (
    GEO_PROVIDER_IP_API,
    GEO_PROVIDER_TAUTULLI,
)

_LOGGER = logging.getLogger(__name__)


class IPGeoCache:
    """
    Cache that resolves IPs to geolocation data.
    Supports two providers:
      - 'tautulli': Uses Tautulli's built-in get_geoip_lookup (MaxMind GeoLite2)
      - 'ip-api':   Uses ip-api.com (free, no key, typically more accurate)
    Results are cached for 1 hour per IP.
    """

    def __init__(self, api: TautulliAPI, provider: str = GEO_PROVIDER_TAUTULLI):
        self._api = api
        self._provider = provider
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}

    @property
    def provider(self) -> str:
        """Return the current geo provider name."""
        return self._provider

    @provider.setter
    def provider(self, value: str) -> None:
        """Update the provider. Clears cache when provider changes."""
        if value != self._provider:
            self._cache.clear()
            self._provider = value

    async def lookup_ip(self, hass: HomeAssistant, ip: str) -> dict[str, Any]:
        """Return cached or freshly resolved geolocation data for an IP."""
        now = time.time()
        cached = self._cache.get(ip)
        if cached:
            geo_data, expiry = cached
            if now < expiry:
                return geo_data  # still valid in cache

        # Not in cache or expired => fetch
        if self._provider == GEO_PROVIDER_IP_API:
            geo_data = await self._lookup_ip_api(hass, ip)
        else:
            geo_data = await self._api.get_geoip_lookup(ip)

        self._cache[ip] = (geo_data, now + 3600)  # 1h
        return geo_data

    async def _lookup_ip_api(self, hass: HomeAssistant, ip: str) -> dict[str, Any]:
        """
        Query ip-api.com and normalise the response to match Tautulli's
        get_geoip_lookup field names so downstream code works unchanged.
        Free tier: 45 req/min, no API key required.
        """
        import aiohttp

        session = async_get_clientsession(hass)
        url = f"http://ip-api.com/json/{ip}?fields=status,message,continent,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,query"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "ip-api.com returned HTTP %s for %s", resp.status, ip
                    )
                    return {}
                data = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            _LOGGER.warning("ip-api.com lookup failed for %s: %s", ip, err)
            return {}

        if data.get("status") != "success":
            _LOGGER.debug(
                "ip-api.com returned non-success for %s: %s", ip, data.get("message")
            )
            return {}

        # Map ip-api.com fields â†’ Tautulli-compatible field names
        return {
            "city": data.get("city"),
            "code": data.get("countryCode"),
            "continent": data.get("continent"),
            "country": data.get("country"),
            "latitude": data.get("lat"),
            "longitude": data.get("lon"),
            "postal_code": data.get("zip"),
            "region": data.get("regionName"),
            "timezone": data.get("timezone"),
            "accuracy": None,  # ip-api.com doesn't provide an accuracy radius
            "isp": data.get("isp"),
        }


# ------------------------------------------- #


# ---------------------------
# Integration Setup
# ---------------------------
