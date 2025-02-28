import logging
import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class TautulliImageView(HomeAssistantView):
    """Handle image proxy requests for Tautulli."""
    url = "/api/tautulli/image"
    name = "api:tautulli:image"
    requires_auth = False  # Set to True if you want to restrict access

    async def get(self, request: web.Request):
        """Proxy image requests."""
        hass = request.app["hass"]

        entry_id = request.query.get("entry_id")
        img = request.query.get("img")
        width = request.query.get("width", "300")
        height = request.query.get("height", "450")
        fallback = request.query.get("fallback", "poster")
        refresh = request.query.get("refresh", "true")

        if not entry_id:
            return web.Response(status=400, text="Missing entry_id parameter")
        if not img:
            return web.Response(status=400, text="Missing img parameter")

        all_entries = hass.data.get(DOMAIN, {})
        my_entry_dict = all_entries.get(entry_id)  # e.g. { "api": TautulliAPI(...), ... }
        if not my_entry_dict:
            _LOGGER.error("No data found for Tautulli entry_id: %s", entry_id)
            return web.Response(status=404, text="No matching Tautulli entry_id found")

        # Get the TautulliAPI (or base_url/api_key) from the dict
        api_obj = my_entry_dict.get("api")
        if not api_obj:
            _LOGGER.error("No API object found for entry_id: %s", entry_id)
            return web.Response(status=404, text="No Tautulli API object found")

        base_url = api_obj._url
        api_key = api_obj._api_key

        if not base_url or not api_key:
            return web.Response(status=500, text="Missing Tautulli base URL or API key")

        # Construct the Tautulli image URL
        tautulli_image_url = (
            f"{base_url}/api/v2?apikey={api_key}&cmd=pms_image_proxy"
            f"&img={img}&width={width}&height={height}"
            f"&fallback={fallback}&refresh={refresh}"
        )
        _LOGGER.debug("Forwarding Tautulli image request to: %s", tautulli_image_url)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(tautulli_image_url, timeout=10) as response:
                    if response.status != 200:
                        _LOGGER.error("Error fetching Tautulli image, status: %s", response.status)
                        return web.Response(
                            status=response.status,
                            text=f"Error fetching image (HTTP {response.status})"
                        )
                    image_data = await response.read()
                    return web.Response(body=image_data, content_type="image/jpeg")
        except Exception as err:
            _LOGGER.exception("Exception fetching Tautulli image: %s", err)
            return web.Response(status=500, text="Error fetching image")
