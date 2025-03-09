import logging
import asyncio
import time
import aiohttp
from datetime import datetime, timedelta

from homeassistant.helpers import device_registry as dr
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.const import CONF_URL, CONF_API_KEY, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er

from .api import TautulliAPI
from .services import async_setup_kill_stream_services  # kill-stream services
from .views import TautulliImageView
from .const import (
    DOMAIN,
    DEFAULT_SESSION_INTERVAL,
    DEFAULT_NUM_SENSORS,
    DEFAULT_STATISTICS_INTERVAL,
    DEFAULT_STATISTICS_DAYS,
    CONF_ENABLE_STATISTICS,
    CONF_SESSION_INTERVAL,
    CONF_NUM_SENSORS,
    CONF_STATISTICS_INTERVAL,
    CONF_STATISTICS_DAYS,
    CONF_ENABLE_IP_GEOLOCATION, 
    LOGGER as _LOGGER,
)

PLATFORMS = ["sensor", "button"]


def format_seconds_to_min_sec(total_seconds: float) -> str:
    """Convert seconds to a 'Mm Ss' string."""
    total_seconds = int(total_seconds)
    minutes = total_seconds // 60
    secs = total_seconds % 60
    return f"{minutes}m {secs}s"


# ---------------------------
# Coordinator A (Sessions)
# ---------------------------
class TautulliSessionsCoordinator(DataUpdateCoordinator):
    """
    Coordinator that handles active sessions (fetched via get_activity) and
    tracks paused durations, session start times, etc.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        api: TautulliAPI,
        update_interval: timedelta,
        config_entry: ConfigEntry,
        geo_cache,  # reference to the IP-geo cache

    ):
        super().__init__(hass, logger, name="TautulliSessions", update_interval=update_interval)
        self.config_entry = config_entry
        self.api = api
        self._geo_cache = geo_cache  # store reference to the geo cache

        self.start_times = {}
        self.paused_since = {}

        # For controlling how many session sensors we want
        self.sensor_count = config_entry.options.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)

        # Store the old stats days so we can detect day-range changes later
        self.old_stats_days = config_entry.options.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS)

        # Also store old stats toggle
        self.old_stats_toggle = config_entry.options.get(CONF_ENABLE_STATISTICS, False)
        
        
    async def _async_update_data(self):
        """Fetch from Tautulli get_activity, track paused durations, etc."""
        data = {}
        try:
            resp = await self.api.get_activity()
            data.update(resp)
        except Exception as err:
            _LOGGER.warning("Failed to update Tautulli sessions: %s", err)
            data = {}

        sessions = data.get("sessions", [])
        now = time.time()

        # Maintain a set of current IDs
        current_ids = set()
        for s in sessions:
            sid = s.get("session_id")
            if not sid:
                continue
            current_ids.add(sid)
            if sid not in self.start_times:
                self.start_times[sid] = now

        # Remove old session IDs
        for old_sid in list(self.start_times.keys()):
            if old_sid not in current_ids:
                del self.start_times[old_sid]
                self.paused_since.pop(old_sid, None)

        # Track paused durations
        for s in sessions:
            sid = s.get("session_id")
            raw_ts = self.start_times.get(sid)
            if raw_ts:
                dt = datetime.fromtimestamp(raw_ts)
                s["start_time_raw"] = raw_ts
                s["start_time"] = dt.strftime("%I:%M %p")
            else:
                s["start_time_raw"] = None
                s["start_time"] = None

            state = (s.get("state") or "").lower()
            if state == "paused":
                if sid not in self.paused_since:
                    self.paused_since[sid] = now
                paused_sec = now - self.paused_since[sid]
                s["Stream_paused_duration_sec"] = paused_sec
                s["Stream_paused_duration"] = format_seconds_to_min_sec(paused_sec)
            else:
                if sid in self.paused_since:
                    del self.paused_since[sid]
                s["Stream_paused_duration_sec"] = 0
                s["Stream_paused_duration"] = "0m 0s"

        # If IP geolocation is on => do lookups
        if self.config_entry.options.get(CONF_ENABLE_IP_GEOLOCATION, False):
            for s in sessions:
                ip = s.get("ip_address_public") or s.get("ip_address")
                if ip:
                    # call the geo cache
                    geo_data = await self._geo_cache.lookup_ip(self.hass, ip)
                    s["geo_city"] = geo_data.get("city", "Unknown")
                    s["geo_code"] = geo_data.get("code")
                    s["geo_continent"] = geo_data.get("continent")
                    s["geo_country"] = geo_data.get("country")
                    s["geo_latitude"] = geo_data.get("latitude")
                    s["geo_longitude"] = geo_data.get("longitude")
                    s["geo_postal_code"] = geo_data.get("postal_code")
                    s["geo_region"] = geo_data.get("region")
                    s["geo_timezone"] = geo_data.get("timezone")
                    s["geo_accuracy"] = geo_data.get("accuracy")
                
        data["sessions"] = sessions
        return data


# ---------------------------
# Coordinator B (History)
# ---------------------------
class TautulliHistoryCoordinator(DataUpdateCoordinator):
    """
    Coordinator that handles watch history (fetched via get_history) and
    aggregates user stats if enable_statistics = True.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        api: TautulliAPI,
        update_interval: timedelta,
        config_entry: ConfigEntry,
        geo_cache,  # same approach
    ):
        super().__init__(hass, logger, name="TautulliHistory", update_interval=update_interval)
        self.config_entry = config_entry
        self.api = api
        self._geo_cache = geo_cache

        
    async def _async_update_data(self):
        """If stats are on, fetch watch history and parse user_stats."""
        data = {}
        if self.config_entry.options.get(CONF_ENABLE_STATISTICS, False):
            days = self.config_entry.options.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS)
            after_date = datetime.now() - timedelta(days=days)
            after_str = after_date.strftime("%Y-%m-%d")

            try:
                hist_resp = await self.api.get_history(
                    after=after_str,
                    order_column="date",
                    order_dir="desc",
                    length=9999
                )
                data["history"] = hist_resp
                data["user_stats"] = self._parse_user_history(hist_resp)
            except Exception as err:
                _LOGGER.warning("Failed to fetch Tautulli history: %s", err)
                data["history"] = {}
                data["user_stats"] = {}
        else:
            data["history"] = {}
            data["user_stats"] = {}

        # If IP geolocation is on, geolocate each user's last IP
        # AND create device_tracker entries for each record
        if self.config_entry.options.get(CONF_ENABLE_IP_GEOLOCATION, False):
            # Grab the raw record list
            records = data["history"].get("data", []) if data["history"] else []
            await self._do_user_ip_geolocation(data["user_stats"], records)
        return data


    def _parse_user_history(self, hist_resp):
        """Parse watch history and accumulate user stats for each user."""
        user_stats = {}
        if not hist_resp:
            return user_stats

        records = hist_resp.get("data", [])
        for item in records:
            user = item.get("user", "Unknown")
            if user not in user_stats:
                user_stats[user] = {
                    "total_plays": 0,
                    "total_play_duration_sec": 0,
                    "movie_plays": 0,
                    "tv_plays": 0,
                    "paused_count": 0,
                    "paused_duration_sec": 0,
                    "completion_sum": 0.0,
                    "direct_play_count": 0,
                    "direct_stream_count": 0,
                    "transcode_count": 0,
                    "streams_count": 0,
                    "last_transcode_ts": 0,  # Track the timestamp of the last transcode
                    "transcode_devices_map": {},
                    "watched_morning": 0,
                    "watched_midday": 0,
                    "watched_afternoon": 0,
                    "watched_evening": 0,
                    "lan_plays": 0,
                    "wan_plays": 0,
                    "weekday_plays": [0] * 7,
                    "device_map": {},
                    "longest_play_sec": 0,
                    "audio_lang_map": {},
                    "play_start_times": [],
                    "shows_map": {},
                    "movies_map": {},
                    # store last IP and last time we saw it
                    "last_ip": None,
                    "last_started_ts": 0,
                    "last_stopped_ts": 0,
                    # store location 
                    "geo_city": None,
                    "geo_region": None,
                    "geo_country": None,
                }

            stats = user_stats[user]

            # read IP address if available
            ip_addr = item.get("ip_address")
            started_ts = item.get("started", 0)
            # if this record is more recent than our stored "last_started_ts", update last_ip
            if ip_addr and started_ts and started_ts > stats["last_started_ts"]:
                stats["last_ip"] = ip_addr
                stats["last_started_ts"] = started_ts


            # Pause logic: if paused_counter > 0, increment paused_count
            paused_seconds = item.get("paused_counter", 0)
            if paused_seconds > 0:
                stats["paused_count"] += 1
            stats["paused_duration_sec"] += paused_seconds

            # If transcoding, track device & last transcode time
            transcode_decision = (item.get("transcode_decision") or "").lower()
            if "transcode" in transcode_decision:
                stats["transcode_count"] += 1
                device = item.get("player", "Unknown")
                stats["transcode_devices_map"][device] = (
                    stats["transcode_devices_map"].get(device, 0) + 1
                )

                # If this record's started_ts is newer, update last_transcode_ts
                started_ts = item.get("started", 0)
                if started_ts and started_ts > stats["last_transcode_ts"]:
                    stats["last_transcode_ts"] = started_ts

            # Count total plays, streams, etc.
            media_type = (item.get("media_type") or "").lower()
            stats["total_plays"] += 1
            stats["streams_count"] += 1

            if media_type == "movie":
                stats["movie_plays"] += 1
            elif media_type == "episode":
                stats["tv_plays"] += 1

            duration_sec = item.get("duration", 0)
            stats["total_play_duration_sec"] += duration_sec
            stats["completion_sum"] += float(item.get("watched_status", 0))

            # If direct play/stream vs. transcode
            if "transcode" in transcode_decision:
                pass  # already handled
            elif "direct play" in transcode_decision:
                stats["direct_play_count"] += 1
            elif "direct stream" in transcode_decision:
                stats["direct_stream_count"] += 1

            # All device usage
            device_all = item.get("player", "Unknown")
            stats["device_map"][device_all] = stats["device_map"].get(device_all, 0) + 1

            # Track longest play
            if duration_sec > stats["longest_play_sec"]:
                stats["longest_play_sec"] = duration_sec

            # Audio language
            audio_lang = item.get("audio_language", "Unknown")
            stats["audio_lang_map"][audio_lang] = stats["audio_lang_map"].get(audio_lang, 0) + 1

            # Start time analysis
            started_ts = item.get("started", 0)
            if started_ts:
                stats["play_start_times"].append(started_ts)
                dt_obj = datetime.fromtimestamp(started_ts)
                hour = dt_obj.hour
                if 0 <= hour < 6:
                    stats["watched_morning"] += 1
                elif 6 <= hour < 12:
                    stats["watched_midday"] += 1
                elif 12 <= hour < 18:
                    stats["watched_afternoon"] += 1
                else:
                    stats["watched_evening"] += 1

                wday = dt_obj.weekday()  # Monday=0 ... Sunday=6
                stats["weekday_plays"][wday] += 1

            # LAN vs WAN
            location = (item.get("location") or "").lower()
            if location == "wan":
                stats["wan_plays"] += 1
            else:
                stats["lan_plays"] += 1

            # Show/Movie counters
            if media_type == "episode":
                show_title = item.get("grandparent_title", "Unknown Show")
                stats["shows_map"][show_title] = (
                    stats["shows_map"].get(show_title, 0) + 1
                )
            elif media_type == "movie":
                movie_title = item.get("title", "Unknown Movie")
                stats["movies_map"][movie_title] = (
                    stats["movies_map"].get(movie_title, 0) + 1
                )

            # track 'stopped' to find last_stopped_ts
            stopped_ts = item.get("stopped", 0)
            if stopped_ts and stopped_ts > stats["last_stopped_ts"]:
                stats["last_stopped_ts"] = stopped_ts
                
        # Final calculations for each user
        for user, stats in user_stats.items():
            total_plays = stats["total_plays"] or 1

            # transcode devices
            td_map = stats["transcode_devices_map"]
            if td_map:
                sorted_td = sorted(td_map.items(), key=lambda x: x[1], reverse=True)
                top_td_list = [f"{dev}({count})" for dev, count in sorted_td[:3]]
                stats["common_transcode_devices"] = ", ".join(top_td_list)
            else:
                stats["common_transcode_devices"] = ""

            # last transcode date
            ltt = stats["last_transcode_ts"]
            if ltt > 0:
                dt_obj = datetime.fromtimestamp(ltt)
                stats["last_transcode_date"] = dt_obj.strftime("%Y-%m-%d %H:%M")
            else:
                stats["last_transcode_date"] = ""

            # compute days_since_last_watch if we have last_stopped_ts
            last_stop = stats.get("last_stopped_ts", 0)
            if last_stop > 0:
                now_ts = time.time()
                diff_sec = now_ts - last_stop
                diff_days = diff_sec / 86400.0
                stats["days_since_last_watch"] = round(diff_days, 1)
            else:
                stats["days_since_last_watch"] = None
                
            # preferred watch day
            day_index = max(range(7), key=lambda i: stats["weekday_plays"][i])
            weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            stats["preferred_watch_day"] = weekdays[day_index]

            # preferred watch time
            time_map = {
                "morning": stats["watched_morning"],
                "afternoon": stats["watched_afternoon"],
                "midday": stats["watched_midday"],
                "evening": stats["watched_evening"],
            }
            best_time = max(time_map, key=time_map.get)
            stats["preferred_watch_time"] = best_time

            # total_play_duration
            duration_sec = stats["total_play_duration_sec"]
            hours = duration_sec // 3600
            mins = (duration_sec % 3600) // 60
            stats["total_play_duration"] = f"{hours}h {mins}m"

            # total_paused_duration
            p_sec = stats["paused_duration_sec"]
            p_hours = p_sec // 3600
            p_mins = (p_sec % 3600) // 60
            stats["total_paused_duration"] = f"{p_hours}h {p_mins}m"

            # total_completion_rate
            comp_rate = (stats["completion_sum"] / total_plays) * 100
            stats["total_completion_rate"] = round(comp_rate, 1)

            # transcode_percentage
            t_count = stats["transcode_count"]
            t_percent = (t_count / total_plays) * 100
            stats["transcode_percentage"] = round(t_percent, 1)

            # most_used_device
            dev_map = stats["device_map"]
            if dev_map:
                sorted_devs = sorted(dev_map.items(), key=lambda x: x[1], reverse=True)
                stats["most_used_device"] = sorted_devs[0][0]
            else:
                stats["most_used_device"] = ""

            # longest_play
            lp_sec = stats["longest_play_sec"]
            if lp_sec > 0:
                lp_hours = lp_sec // 3600
                lp_mins = (lp_sec % 3600) // 60
                stats["longest_play"] = f"{lp_hours}h {lp_mins}m"
            else:
                stats["longest_play"] = "0h 0m"

            # audio_lang_map
            lang_map = stats["audio_lang_map"]
            if lang_map:
                sorted_lang = sorted(lang_map.items(), key=lambda x: x[1], reverse=True)
                stats["common_audio_language"] = sorted_lang[0][0]
            else:
                stats["common_audio_language"] = "Unknown"

            # average_play_gap
            start_times = stats["play_start_times"]
            if len(start_times) > 1:
                sorted_st = sorted(start_times)
                total_gap_sec = 0
                gap_count = 0
                for i in range(len(sorted_st) - 1):
                    gap_val = sorted_st[i + 1] - sorted_st[i]
                    if gap_val > 0:
                        total_gap_sec += gap_val
                        gap_count += 1
                if gap_count > 0:
                    avg_gap_sec = total_gap_sec / gap_count
                    avg_gap_hours = round(avg_gap_sec / 3600, 2)
                    stats["average_play_gap"] = f"{avg_gap_hours}h"
                else:
                    stats["average_play_gap"] = "N/A"
            else:
                stats["average_play_gap"] = "N/A"

            # most_popular_show
            shows_map = stats["shows_map"]
            if shows_map:
                sorted_shows = sorted(shows_map.items(), key=lambda x: x[1], reverse=True)
                stats["most_popular_show"] = sorted_shows[0][0]
            else:
                stats["most_popular_show"] = ""

            # most_popular_movie
            movies_map = stats["movies_map"]
            if movies_map:
                sorted_movies = sorted(movies_map.items(), key=lambda x: x[1], reverse=True)
                stats["most_popular_movie"] = sorted_movies[0][0]
            else:
                stats["most_popular_movie"] = ""

        return user_stats

    async def _do_user_ip_geolocation(self, all_user_stats, records):
        """Loop over user stats, geolocate them, etc."""
        if not all_user_stats:
            return
    
        for username, stats in all_user_stats.items():
            ip = stats.get("last_ip")
            if not ip:
                continue
    
            # 1) Get entire geo dict from Tautulli's get_geoip_lookup
            geodata = await self._geo_cache.lookup_ip(self.hass, ip)
            if not geodata:
                continue
    
            # 2) Parse the lat/long/city/region/country from Tautulli's result
            lat = geodata.get("latitude")
            lon = geodata.get("longitude")
            city = geodata.get("city")
            region = geodata.get("region")
            country = geodata.get("country")
    
            # 3) Store them back in stats if you like
            if lat is not None and lon is not None:
                stats["latitude"] = lat
                stats["longitude"] = lon
    
            if city:
                stats["geo_city"] = city
            if region:
                stats["geo_region"] = region
            if country:
                stats["geo_country"] = country
    
            # 3.5) Prepare "last_watched" from your last_stopped_ts if available
            last_stopped_ts = stats.get("last_stopped_ts")
            last_watched_str = None
            if last_stopped_ts:
                dt_obj = datetime.fromtimestamp(last_stopped_ts)
                # Format with 12-hr clock, strip leading zero from the hour if present:
                raw_str = dt_obj.strftime("%I:%M%p %d-%m-%Y")  # e.g. "02:38PM 12-03-2025"
                last_watched_str = raw_str.lstrip("0")         # becomes "2:38PM 12-03-2025"
    
            # 4) Create or update the device_tracker in Home Assistant
            dev_id = f"tautulli_{username.lower().replace(' ', '_').replace('.', '')}"
    
            # Build the attributes dictionary
            attributes = {
                "ip_address": ip,
                "city": city,
                "region": region,
                "country": country,
            }
            if last_watched_str:
                attributes["last_watched"] = last_watched_str
    
            await self.hass.services.async_call(
                "device_tracker",
                "see",
            {
                    "dev_id": dev_id,
                    "host_name": f"{username}: Tautulli",
                    "gps": (lat, lon) if lat is not None and lon is not None else (0, 0),
                    "source_type": "gps",
                    "attributes": attributes,
                },
                blocking=False,
            )

            
# --------------- IPGeoCache Example --------------- #
class IPGeoCache:
    """Simple cache that calls Tautulli's get_geoip_lookup once per IP per day."""
    def __init__(self, api: TautulliAPI):
        self._api = api  # store reference to TautulliAPI
        self._cache = {}  # {ip: (geo_data_dict, expiry_time)}

    async def lookup_ip(self, hass: HomeAssistant, ip: str) -> dict:
        now = time.time()
        cached = self._cache.get(ip)
        if cached:
            geo_data, expiry = cached
            if now < expiry:
                return geo_data  # still valid in cache

        # Not in cache or expired => fetch from Tautulli
        geo_data = await self._api.get_geoip_lookup(ip)
        self._cache[ip] = (geo_data, now + 3600)  # 1h
        return geo_data
# --------------------------------------------------- #



# ---------------------------
# Integration Setup
# ---------------------------
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Create two coordinators:
      - sessions_coordinator => uses CONF_SESSION_INTERVAL
      - history_coordinator  => uses CONF_STATISTICS_INTERVAL
    Then set up kill-stream services, forward to sensor platform, etc.
    """
    hass.data.setdefault(DOMAIN, {})

    # 1) Create TautulliAPI object
    url = entry.data.get(CONF_URL)
    api_key = entry.data.get(CONF_API_KEY)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    api = TautulliAPI(url, api_key, session, verify_ssl)
    
    # Create the IPGeoCache (shared) so we can pass it to both coordinators
    geo_cache = IPGeoCache(api)
    
    # 2) Build your session + history coordinators
    session_interval = entry.options.get(CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL)
    stats_interval = entry.options.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL)

    sessions_coordinator = TautulliSessionsCoordinator(
        hass=hass,
        logger=_LOGGER,
        api=api,
        update_interval=timedelta(seconds=session_interval),
        config_entry=entry,
        geo_cache=geo_cache
    )

    history_coordinator = TautulliHistoryCoordinator(
        hass=hass,
        logger=_LOGGER,
        api=api,
        update_interval=timedelta(seconds=stats_interval),
        config_entry=entry,
        geo_cache=geo_cache
    )

    # 3) Do first refresh
    await sessions_coordinator.async_config_entry_first_refresh()
    await history_coordinator.async_config_entry_first_refresh()

    # If stats are on, do an immediate refresh for watch history
    if entry.options.get(CONF_ENABLE_STATISTICS, False):
        await history_coordinator.async_request_refresh()

    # 4) Store everything in hass.data
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "sessions_coordinator": sessions_coordinator,
        "history_coordinator": history_coordinator
    }

    # 5) Register your image view
    hass.http.register_view(TautulliImageView)
    hass.data["tautulli_integration_config"] = {"base_url": url, "api_key": api_key}

    # 6) Forward to sensor + button
    try:
        await asyncio.shield(hass.config_entries.async_forward_entry_setups(entry, PLATFORMS))
    except asyncio.CancelledError:
        _LOGGER.error("Setup of sensor platforms was cancelled")
        return False
    except Exception as ex:
        _LOGGER.error("Error forwarding entry setups: %s", ex)
        return False

    # 7) Setup kill-stream services
    try:
        await async_setup_kill_stream_services(hass, entry, api)
    except Exception as exc:
        _LOGGER.error("Exception during kill stream service registration: %s", exc, exc_info=True)

    # Store old stats toggle in the sessions_coordinator
    sessions_coordinator.old_stats_toggle = entry.options.get(CONF_ENABLE_STATISTICS, False)

    # 8) Listen for options changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


# ---------------------------
#  Removing Sensors
# ---------------------------
async def async_remove_extra_session_sensors(hass: HomeAssistant, entry: ConfigEntry):
    """Remove session sensors above the new count."""
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)

    new_count = entry.options.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)
    _LOGGER.debug("Removing session sensors above new count: %s", new_count)

    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    for ent in entries:
        if (
            ent.domain == "sensor"
            and ent.unique_id.startswith("plex_session_")
            and ent.unique_id.endswith("_tautulli")
        ):
            # unique_id e.g. "plex_session_3_<entryid>_tautulli"
            middle = ent.unique_id[len("plex_session_") : -len("_tautulli")]
            parts = middle.split("_", 1)
            if not parts:
                continue
            sensor_number_str = parts[0]
            try:
                sensor_number = int(sensor_number_str)
            except ValueError:
                _LOGGER.debug("Could not parse sensor # from %s", ent.unique_id)
                continue

            if sensor_number > new_count:
                _LOGGER.debug("Removing sensor entity: %s (index %s)", ent.entity_id, sensor_number)
                registry.async_remove(ent.entity_id)


async def async_remove_statistics_sensors(hass: HomeAssistant, entry: ConfigEntry):
    """Remove all user-stats sensors (those with '_stats_') plus the device."""
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)

    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    for ent in entries:
        if "_stats_" in ent.unique_id:
            _LOGGER.debug(
                "Removing user-stats sensor entity: %s (unique_id: %s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)

    # Also remove the stats device
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_statistics_device")})
    if device:
        _LOGGER.debug("Removing user-stats device: %s (%s)", device.name, device.id)
        device_reg.async_remove_device(device.id)


# ---------------------------
#  Unload
# ---------------------------
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Remove from hass.data
        hass.data[DOMAIN].pop(entry.entry_id, None)
        
        # Only remove the kill services if this is the *last* config entry for your domain
        remaining_entries = hass.config_entries.async_entries(DOMAIN)
        if not remaining_entries:  # or if len(...) == 0
            for service in ["kill_all_streams", "kill_user_streams", "kill_session_stream"]:
                # Optional: check if service actually exists before removing
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok

# ---------------------------
#  Update Options
# ---------------------------
async def async_update_options(hass: HomeAssistant, entry: ConfigEntry):
    """
    Triggered when user changes any integration option (like sensor count, stats toggle, or stats days).
    We'll remove or reload as needed to reflect changes.
    """
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data:
        return

    sessions_coordinator = data["sessions_coordinator"]
    history_coordinator = data["history_coordinator"]

    # Gather old/new stats toggle
    old_stats = sessions_coordinator.old_stats_toggle
    new_stats = entry.options.get(CONF_ENABLE_STATISTICS, False)
    sessions_coordinator.old_stats_toggle = new_stats

    # Gather old/new sensor counts
    old_sensors = sessions_coordinator.sensor_count
    new_sensors = entry.options.get(CONF_NUM_SENSORS, DEFAULT_NUM_SENSORS)

    # Gather old/new stats day range
    old_days = sessions_coordinator.old_stats_days
    new_days = entry.options.get(CONF_STATISTICS_DAYS, 30)
    # Store the new value for next time
    sessions_coordinator.old_stats_days = new_days

    # Decide if we need a reload
    reload_needed = False

    # Did stats toggle change?
    if old_stats != new_stats:
        _LOGGER.debug("Stats toggled from %s to %s; reload needed", old_stats, new_stats)
        reload_needed = True

    # Did sensor count change?
    if old_sensors != new_sensors:
        _LOGGER.debug("Sensor count changed from %s to %s; reload needed", old_sensors, new_sensors)
        reload_needed = True

    # Did stats days range change?
    if old_days != new_days:
        _LOGGER.debug("Stats day range changed from %s to %s; reload needed", old_days, new_days)
        reload_needed = True

    # If major changes, do a reload. But first, do partial refresh + remove.
    if reload_needed:
        # 1) If they lowered sensors, remove extras first
        if new_sensors < old_sensors:
            await async_remove_extra_session_sensors(hass, entry)

        # 2) If they turned stats off, remove stats sensors & device and the watch-history button
        if old_stats and not new_stats:
            await async_remove_statistics_sensors(hass, entry)
            await async_remove_history_button(hass, entry)

        # 3) PARTIAL REFRESH to get the new data (especially if days changed),
        #    so we know which user-stats are still valid.
        await sessions_coordinator.async_request_refresh()
        await history_coordinator.async_request_refresh()

        # 4) Remove outdated user sensors for any users no longer present in the new data
        current_stats = history_coordinator.data.get("user_stats", {})
        await async_remove_outdated_user_sensors(hass, entry, current_stats)

        # 5) Reload so sensor/button code picks up changes or re-adds new user sensors
        await hass.config_entries.async_reload(entry.entry_id)

    else:
        # No major changes => do partial refresh only
        new_session_int = entry.options.get(CONF_SESSION_INTERVAL, DEFAULT_SESSION_INTERVAL)
        new_stats_int = entry.options.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL)
        sessions_coordinator.update_interval = timedelta(seconds=new_session_int)
        history_coordinator.update_interval = timedelta(seconds=new_stats_int)

        sessions_coordinator.sensor_count = new_sensors

        await sessions_coordinator.async_request_refresh()
        await history_coordinator.async_request_refresh()

        # Remove any user sensors for users who might have disappeared
        current_stats = history_coordinator.data.get("user_stats", {})
        await async_remove_outdated_user_sensors(hass, entry, current_stats)



async def async_remove_history_button(hass: HomeAssistant, entry: ConfigEntry):
    """
    Remove the 'Fetch Watch History' button entity (if it exists).
    """
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)

    unique_id = f"{entry.entry_id}_fetch_watch_history"
    button_entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    if button_entity_id:
        _LOGGER.debug("Removing the fetch-watch-history button: %s", button_entity_id)
        registry.async_remove(button_entity_id)


async def async_remove_outdated_user_sensors(hass: HomeAssistant, entry: ConfigEntry, current_stats: dict):
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)
    valid_users = set(current_stats.keys())

    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    for ent in entries:
        if "_stats_" not in ent.unique_id:
            continue
        parts = ent.unique_id.split("_")
        if len(parts) < 4:
            continue
        sensor_username = parts[1]  # second element in unique_id
        if sensor_username not in valid_users:
            _LOGGER.debug("Removing outdated user-stats sensor: %s (username=%s)", ent.entity_id, sensor_username)
            registry.async_remove(ent.entity_id)
