import logging
import re

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Only allow Plex-style image paths (e.g. /library/metadata/12345/thumb/6789)
# Rejects path traversal (..) and other non-Plex patterns
_VALID_IMG_PATTERN = re.compile(r"^/[\w/.-]+$")
_MIN_IMAGE_SIZE = 16
_MAX_IMAGE_SIZE = 2000
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_FALLBACKS = {
    "art",
    "art-live",
    "art-live-full",
    "cover",
    "poster",
    "poster-live",
    "user",
}


def _image_dimension(value: str) -> int | None:
    """Return a bounded image dimension or None when invalid."""
    try:
        dimension = int(value)
    except (TypeError, ValueError):
        return None
    if not _MIN_IMAGE_SIZE <= dimension <= _MAX_IMAGE_SIZE:
        return None
    return dimension


class TautulliImageView(HomeAssistantView):
    """Handle image proxy requests for Tautulli."""

    url = "/api/tautulli/image"
    name = "api:tautulli:image"
    requires_auth = True

    async def get(self, request: web.Request):
        """Proxy image requests to Tautulli's pms_image_proxy endpoint."""
        hass = request.app["hass"]

        entry_id = request.query.get("entry_id")
        img = request.query.get("img")
        width = _image_dimension(request.query.get("width", "300"))
        height = _image_dimension(request.query.get("height", "450"))
        fallback = request.query.get("fallback", "poster")
        refresh = request.query.get("refresh", "true")

        # Validate required parameters
        if not entry_id:
            return web.Response(status=400, text="Missing entry_id parameter")
        if not img:
            return web.Response(status=400, text="Missing img parameter")
        if width is None or height is None:
            return web.Response(
                status=400,
                text=f"Image dimensions must be between {_MIN_IMAGE_SIZE} and {_MAX_IMAGE_SIZE}",
            )
        if fallback not in _ALLOWED_FALLBACKS:
            return web.Response(status=400, text="Invalid fallback parameter")
        if refresh not in {"true", "false"}:
            return web.Response(status=400, text="Invalid refresh parameter")

        # Sanitize img parameter — must look like a Plex media path
        if not _VALID_IMG_PATTERN.match(img):
            return web.Response(status=400, text="Invalid img parameter")

        # Reject path traversal attempts
        if ".." in img:
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

        tautulli_image_url = f"{base_url}/api/v2"
        params = {
            "apikey": api_key,
            "cmd": "pms_image_proxy",
            "img": img,
            "width": width,
            "height": height,
            "fallback": fallback,
            "refresh": refresh,
        }

        _LOGGER.debug("Forwarding Tautulli image request for entry_id=%s", entry_id)

        # Fetch the image using the same session
        try:
            async with session.get(
                tautulli_image_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    _LOGGER.error(
                        "Error fetching Tautulli image, status: %s", response.status
                    )
                    return web.Response(
                        status=response.status,
                        text=f"Error fetching image (HTTP {response.status})",
                    )
                content_type = response.headers.get("Content-Type", "")
                if not content_type.lower().startswith("image/"):
                    return web.Response(
                        status=502, text="Tautulli returned non-image content"
                    )
                if (
                    response.content_length
                    and response.content_length > _MAX_IMAGE_BYTES
                ):
                    return web.Response(status=502, text="Image response is too large")

                chunks: list[bytes] = []
                bytes_read = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    bytes_read += len(chunk)
                    if bytes_read > _MAX_IMAGE_BYTES:
                        return web.Response(
                            status=502, text="Image response is too large"
                        )
                    chunks.append(chunk)
                image_data = b"".join(chunks)
                return web.Response(
                    body=image_data,
                    content_type=content_type.split(";", 1)[0],
                    headers={"Cache-Control": "private, max-age=300"},
                )

        except Exception as err:  # noqa: BLE001 - HTTP view error boundary
            err_msg = str(err).replace(api_key, "[REDACTED]")
            _LOGGER.error("Exception fetching Tautulli image: %s", err_msg)
            return web.Response(status=500, text="Error fetching image")
