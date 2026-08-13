from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp
from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


async def async_fetch_plex_metadata(
    plex_base_url: str,
    plex_token: str,
    rating_key: str,
    session: ClientSession,
    verify_ssl: bool = True,
) -> tuple[int | None, dict[str, Any], int | None]:
    """
    Query Plex for metadata including chapters, markers, and other attributes.
    Returns a tuple of (credits_offset, metadata_dict, http_status).
    """
    url = (
        f"{plex_base_url}/library/metadata/{rating_key}"
        f"?includeChapters=1&includeMarkers=1"
    )
    headers = {"X-Plex-Token": plex_token}
    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
            ssl=verify_ssl,
        ) as resp:
            if resp.status != 200:
                _LOGGER.debug(
                    "Plex metadata fetch failed for rating_key=%s: status=%s, reason=%s",
                    rating_key,
                    resp.status,
                    resp.reason,
                )
                return None, {}, resp.status

            # Parse XML
            xml_body = await resp.text()
            root = ET.fromstring(xml_body)

            # Check various XML paths for metadata
            video_el = root.find(".//Video")

            # If no Video element found, return empty results
            if video_el is None:
                return None, {}, resp.status

            # Initialize metadata dict
            metadata = {}

            # 1) Credits offset from markers/chapters
            credits_offset = None
            for marker in video_el.findall("Marker"):
                if marker.attrib.get("type") == "credits":
                    credits_offset = int(marker.attrib.get("startTimeOffset", 0))
                    break

            if not credits_offset:
                for chapter in video_el.findall("Chapter"):
                    if "credit" in chapter.attrib.get("tag", "").lower():
                        credits_offset = int(chapter.attrib.get("startTimeOffset", 0))
                        break

            # 2) Parse Director tags
            directors = []
            for director in video_el.findall(".//Director"):
                if "tag" in director.attrib:
                    directors.append(director.attrib["tag"])
            if directors:
                metadata["directors"] = directors

            # 3) Parse Role/Cast tags
            cast = []
            for role in video_el.findall(".//Role"):
                cast_entry = {
                    "actor": role.attrib.get("tag"),
                    "role": role.attrib.get("role"),
                }
                cast.append(cast_entry)
            if cast:
                metadata["cast"] = cast

            # 4) Parse Genre tags
            genres = []
            for genre in video_el.findall(".//Genre"):
                if "tag" in genre.attrib:
                    genres.append(genre.attrib["tag"])
            if genres:
                metadata["genres"] = genres

            # 5) Parse Writer tags
            writers = []
            for writer in video_el.findall(".//Writer"):
                if "tag" in writer.attrib:
                    writers.append(writer.attrib["tag"])
            if writers:
                metadata["writers"] = writers

            # 6) Parse Country tags
            countries = []
            for country in video_el.findall(".//Country"):
                if "tag" in country.attrib:
                    countries.append(country.attrib["tag"])
            if countries:
                metadata["country"] = countries[0]  # Take first country

            # 7) Parse Guid tags for external IDs
            guids = []
            for guid in video_el.findall(".//Guid"):
                if "id" in guid.attrib:
                    guids.append(guid.attrib["id"])
            if guids:
                metadata["guids"] = guids

            # 8) Get Media/Part info for file location
            media = video_el.find(".//Media")
            if media is not None:
                part = media.find("Part")
                if part is not None and "file" in part.attrib:
                    metadata["library_folder"] = part.attrib["file"]

            # 9) Get library section info from parent container
            library = root.find("LibrarySection")
            if library is not None:
                if "title" in library.attrib:
                    metadata["library_section_title"] = library.attrib["title"]
                if "id" in library.attrib:
                    metadata["library_section_id"] = library.attrib["id"]

            # 10) Parse Rating tags
            for rating in video_el.findall(".//Rating"):
                image = rating.attrib.get("image", "")
                value = rating.attrib.get("value")
                if value:
                    if "rottentomatoes://image.rating.ripe" in image:
                        metadata["rotten_tomatoes_rating"] = value
                    elif "rottentomatoes://image.rating.upright" in image:
                        metadata["rotten_tomatoes_audience_rating"] = value
                    elif "imdb://image.rating" in image:
                        metadata["imdb_rating"] = value

            # 11) Get basic metadata from Video attributes
            basic_fields = [
                "title",
                "summary",
                "year",
                "rating",
                "studio",
                "tagline",
                "contentRating",
                "originallyAvailableAt",
                "audienceRating",
                "viewCount",
                "addedAt",
                "updatedAt",
                "lastViewedAt",
            ]
            for field in basic_fields:
                if field in video_el.attrib:
                    metadata[field] = video_el.attrib[field]

            return credits_offset, metadata, 200

    except (
        TimeoutError,
        aiohttp.ClientError,
        ET.ParseError,
        TypeError,
        ValueError,
    ) as err:
        _LOGGER.debug(
            "Error fetching Plex metadata for rating_key=%s: %s", rating_key, err
        )
        return None, {}, None
