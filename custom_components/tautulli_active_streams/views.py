import logging
import re
from urllib.parse import quote
import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Only allow Plex-style image paths (e.g. /library/metadata/12345/thumb/6789)
_VALID_IMG_PATTERN = re.compile(r"^/[\w/.-]+$")


class TautulliImageView(HomeAssistantView):
    """Handle image proxy requests for Tautulli."""

    url = "/api/tautulli/image"
    name = "api:tautulli:image"
    requires_auth = False

    async def get(self, request: web.Request):
        """Proxy image requests to Tautulli's pms_image_proxy endpoint."""
        hass = request.app["hass"]

        entry_id = request.query.get("entry_id")
        img = request.query.get("img")
        width = request.query.get("width", "300")
        height = request.query.get("height", "450")
        fallback = request.query.get("fallback", "poster")
        refresh = request.query.get("refresh", "true")

        # Validate required parameters
        if not entry_id:
            return web.Response(status=400, text="Missing entry_id parameter")
        if not img:
            return web.Response(status=400, text="Missing img parameter")

        # Sanitize img parameter — must look like a Plex media path
        if not _VALID_IMG_PATTERN.match(img):
            return web.Response(status=400, text="Invalid img parameter")

        # Look up the stored data for this entry_id
        all_entries = hass.data.get(DOMAIN, {})
        my_entry_dict = all_entries.get(entry_id)

        if not my_entry_dict:
            _LOGGER.error("No data found for Tautulli entry_id: %s", entry_id)
            return web.Response(status=404, text="No matching Tautulli entry_id found")

        # Extract the TautulliAPI object
        api_obj = my_entry_dict.get("api")
        if not api_obj:
            _LOGGER.error("No API object found for entry_id: %s", entry_id)
            return web.Response(status=404, text="No Tautulli API object found")

        base_url = api_obj.base_url
        api_key = api_obj.api_key
        session = api_obj.session

        if not base_url or not api_key:
            return web.Response(status=500, text="Missing Tautulli base URL or API key")

        # Construct the Tautulli image URL with properly encoded img path
        tautulli_image_url = (
            f"{base_url}/api/v2"
            f"?apikey={api_key}"
            f"&cmd=pms_image_proxy"
            f"&img={quote(img, safe='/')}"
            f"&width={quote(width)}"
            f"&height={quote(height)}"
            f"&fallback={quote(fallback)}"
            f"&refresh={quote(refresh)}"
        )

        _LOGGER.debug("Forwarding Tautulli image request for entry_id=%s", entry_id)

        # Fetch the image using the same session
        try:
            async with session.get(
                tautulli_image_url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
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
