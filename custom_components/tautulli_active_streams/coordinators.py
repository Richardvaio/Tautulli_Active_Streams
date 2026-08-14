from __future__ import annotations

import asyncio
import calendar
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import now as ha_now

from .api import TautulliAPI, TautulliAuthError
from .const import (
    CONF_ENABLE_IP_GEOLOCATION,
    CONF_ENABLE_STATISTICS,
    CONF_STATISTICS_CYCLE_DAY,
    CONF_STATISTICS_DAYS,
    CONF_STATISTICS_PERIOD,
    CONF_STATS_MONTH_TO_DATE,
    DEFAULT_STATISTICS_CYCLE_DAY,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_STATISTICS_PERIOD,
    MAX_HISTORY_RECORDS,
    STATISTICS_PERIOD_CALENDAR_MONTH,
    STATISTICS_PERIOD_CUSTOM_MONTH,
    STATISTICS_PERIOD_ROLLING,
    format_seconds_to_min_sec,
    is_private_ip,
)

if TYPE_CHECKING:
    from .geo import IPGeoCache

_LOGGER = logging.getLogger(__name__)


def _as_int(value: Any, default: int = 0) -> int:
    """Normalize Tautulli integer fields, which may arrive as strings."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    """Normalize Tautulli decimal fields, including empty strings and nulls."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def statistics_period(options: dict[str, Any]) -> str:
    """Return the configured period, migrating the legacy month toggle."""
    period = options.get(CONF_STATISTICS_PERIOD)
    if period in {
        STATISTICS_PERIOD_ROLLING,
        STATISTICS_PERIOD_CALENDAR_MONTH,
        STATISTICS_PERIOD_CUSTOM_MONTH,
    }:
        return period
    if options.get(CONF_STATS_MONTH_TO_DATE, False):
        return STATISTICS_PERIOD_CALENDAR_MONTH
    return DEFAULT_STATISTICS_PERIOD


def statistics_start(now: datetime, options: dict[str, Any]) -> datetime:
    """Calculate the inclusive local start of the selected statistics period."""
    period = statistics_period(options)
    if period == STATISTICS_PERIOD_ROLLING:
        days = max(
            1, _as_int(options.get(CONF_STATISTICS_DAYS), DEFAULT_STATISTICS_DAYS)
        )
        return now - timedelta(days=days)

    if period == STATISTICS_PERIOD_CALENDAR_MONTH:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    cycle_day = min(
        31,
        max(
            1,
            _as_int(
                options.get(CONF_STATISTICS_CYCLE_DAY),
                DEFAULT_STATISTICS_CYCLE_DAY,
            ),
        ),
    )
    year = now.year
    month = now.month
    current_day = min(cycle_day, calendar.monthrange(year, month)[1])
    if now.day < current_day:
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
    start_day = min(cycle_day, calendar.monthrange(year, month)[1])
    return now.replace(
        year=year,
        month=month,
        day=start_day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


class TautulliSessionsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordinator that handles active sessions (fetched via get_activity) and
    tracks paused durations, session start times, etc.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        api: TautulliAPI,
        update_interval: timedelta,
        config_entry: ConfigEntry,
        geo_cache: IPGeoCache,
    ) -> None:
        super().__init__(
            hass, logger, name="TautulliSessions", update_interval=update_interval
        )
        self.config_entry = config_entry
        self.api = api
        self._geo_cache = geo_cache  # store reference to the geo cache

        self.start_times: dict[str, float] = {}
        self.paused_since: dict[str, float] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch from Tautulli get_activity, track paused durations, etc."""
        try:
            data = await self.api.get_activity()
        except TautulliAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise UpdateFailed(
                "Tautulli authentication failed; reauthentication required"
            ) from err
        except Exception as err:
            raise UpdateFailed(f"Failed to update Tautulli sessions: {err}") from err

        if not data:
            data = {"sessions": [], "diagnostics": {}}

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
                dt = datetime.fromtimestamp(raw_ts, tz=ha_now().tzinfo)
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
                s["stream_paused_duration_sec"] = paused_sec
                s["stream_paused_duration"] = format_seconds_to_min_sec(paused_sec)
            else:
                if sid in self.paused_since:
                    del self.paused_since[sid]
                s["stream_paused_duration_sec"] = 0
                s["stream_paused_duration"] = "0m 0s"

        # If IP geolocation is on => do lookups concurrently
        if self.config_entry.options.get(CONF_ENABLE_IP_GEOLOCATION, False):
            # Collect sessions needing lookups
            lookup_tasks = []
            lookup_indices = []
            for idx, s in enumerate(sessions):
                ip = s.get("ip_address_public") or s.get("ip_address")
                if ip and not is_private_ip(ip):
                    lookup_tasks.append(self._geo_cache.lookup_ip(self.hass, ip))
                    lookup_indices.append(idx)

            if lookup_tasks:
                results = await asyncio.gather(*lookup_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        _LOGGER.debug("GeoIP lookup failed: %s", result)
                        continue
                    s = sessions[lookup_indices[i]]
                    geo_data = result
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
class TautulliHistoryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordinator that handles watch history (fetched via get_history) and
    aggregates user stats if enable_statistics = True.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        api: TautulliAPI,
        update_interval: timedelta,
        config_entry: ConfigEntry,
        geo_cache: IPGeoCache,
    ) -> None:
        super().__init__(
            hass, logger, name="TautulliHistory", update_interval=update_interval
        )
        self.config_entry = config_entry
        self.api = api
        self._geo_cache = geo_cache

        # store old stats toggle
        self.old_stats_toggle = config_entry.options.get(CONF_ENABLE_STATISTICS, False)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and aggregate history for the selected statistics period."""
        data = {}

        # Check if user enabled statistics
        if self.config_entry.options.get(CONF_ENABLE_STATISTICS, False):
            after_date = statistics_start(ha_now(), dict(self.config_entry.options))

            after_str = after_date.strftime("%Y-%m-%d")

            try:
                hist_resp = await self.api.get_history(
                    after=after_str,
                    grouping=0,
                    order_column="date",
                    order_dir="desc",
                    length=MAX_HISTORY_RECORDS,
                )
                data["history"] = hist_resp
                data["user_stats"] = self._parse_user_history(hist_resp)
            except TautulliAuthError as err:
                self.config_entry.async_start_reauth(self.hass)
                raise UpdateFailed(
                    "Tautulli authentication failed; reauthentication required"
                ) from err
            except Exception as err:
                raise UpdateFailed(f"Failed to fetch Tautulli history: {err}") from err
        else:
            data["history"] = {}
            data["user_stats"] = {}

        # If IP geolocation is on => geolocate user IPs
        if self.config_entry.options.get(CONF_ENABLE_IP_GEOLOCATION, False):
            await self._do_user_ip_geolocation(data["user_stats"])

        return data

    def _parse_user_history(
        self, hist_resp: dict[str, Any] | None
    ) -> dict[str, dict[str, Any]]:
        """Parse watch history and accumulate user stats for each user."""
        user_stats = {}
        if not hist_resp:
            return user_stats

        records = hist_resp.get("data", [])
        for item in records:
            user = item.get("user", "Unknown")
            upstream_user_id = item.get("user_id")
            user_key = (
                str(upstream_user_id)
                if upstream_user_id is not None
                else f"name:{user}"
            )
            if user_key not in user_stats:
                user_stats[user_key] = {
                    "username": user,
                    "user_id": item.get("user_id"),
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
                    "watched_night": 0,
                    "watched_morning": 0,
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
                    "last_username_ts": 0,
                    # store location
                    "geo_city": None,
                    "geo_region": None,
                    "geo_country": None,
                    "geo_code": None,
                    "geo_continent": None,
                    "geo_latitude": None,
                    "geo_longitude": None,
                    "geo_postal_code": None,
                    "geo_timezone": None,
                    "geo_accuracy": None,
                }

            stats = user_stats[user_key]

            # Plex user IDs are stable when a display name changes. Keep the
            # upstream ID with the aggregated data so config-entry entities can
            # use it as their registry identity.
            if stats.get("user_id") is None and item.get("user_id") is not None:
                stats["user_id"] = item["user_id"]

            # read IP address if available
            ip_addr = item.get("ip_address")
            started_ts = _as_int(item.get("started"))
            if started_ts >= stats["last_username_ts"]:
                stats["username"] = user
                stats["last_username_ts"] = started_ts
            # if this record is more recent than our stored "last_started_ts", update last_ip
            if ip_addr and started_ts and started_ts > stats["last_started_ts"]:
                stats["last_ip"] = ip_addr
                stats["last_started_ts"] = started_ts

            # Pause logic: if paused_counter > 0, increment paused_count
            paused_seconds = _as_int(item.get("paused_counter"))
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
                started_ts = _as_int(item.get("started"))
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

            duration_sec = _as_int(item.get("duration"))
            stats["total_play_duration_sec"] += duration_sec
            stats["completion_sum"] += _as_float(item.get("watched_status"))

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
            stats["longest_play_sec"] = max(stats["longest_play_sec"], duration_sec)

            # Audio language
            audio_lang = item.get("audio_language", "Unknown")
            stats["audio_lang_map"][audio_lang] = (
                stats["audio_lang_map"].get(audio_lang, 0) + 1
            )

            # Start time analysis
            started_ts = _as_int(item.get("started"))
            if started_ts:
                stats["play_start_times"].append(started_ts)
                dt_obj = datetime.fromtimestamp(started_ts, tz=ha_now().tzinfo)
                hour = dt_obj.hour
                if 0 <= hour < 6:
                    stats["watched_night"] += 1
                elif 6 <= hour < 12:
                    stats["watched_morning"] += 1
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
            stopped_ts = _as_int(item.get("stopped"))
            if stopped_ts and stopped_ts > stats["last_stopped_ts"]:
                stats["last_stopped_ts"] = stopped_ts

        # Final calculations for each user
        for stats in user_stats.values():
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
                dt_obj = datetime.fromtimestamp(ltt, tz=ha_now().tzinfo)
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
                "evening": stats["watched_evening"],
                "night": stats["watched_night"],
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
                sorted_shows = sorted(
                    shows_map.items(), key=lambda x: x[1], reverse=True
                )
                stats["most_popular_show"] = sorted_shows[0][0]
            else:
                stats["most_popular_show"] = ""

            # most_popular_movie
            movies_map = stats["movies_map"]
            if movies_map:
                sorted_movies = sorted(
                    movies_map.items(), key=lambda x: x[1], reverse=True
                )
                stats["most_popular_movie"] = sorted_movies[0][0]
            else:
                stats["most_popular_movie"] = ""

        return user_stats

    async def _do_user_ip_geolocation(
        self, all_user_stats: dict[str, dict[str, Any]]
    ) -> None:
        """Loop over user stats, geolocate them, etc."""
        if not all_user_stats:
            return

        for stats in all_user_stats.values():
            ip = stats.get("last_ip")
            if not ip or is_private_ip(ip):
                continue

            # 1) Geo lookup (provider chosen in config)
            geodata = await self._geo_cache.lookup_ip(self.hass, ip)
            if not geodata:
                continue

            # 2) For each field Tautulli might return:
            city = geodata.get("city")
            region = geodata.get("region")
            country = geodata.get("country")
            code = geodata.get("code")
            continent = geodata.get("continent")
            postal_code = geodata.get("postal_code")
            timezone = geodata.get("timezone")
            accuracy = geodata.get("accuracy")
            lat = geodata.get("latitude")
            lon = geodata.get("longitude")

            # 3) Store them in your stats dict
            stats["geo_city"] = city if city else "Unknown"
            stats["geo_region"] = region if region else "Unknown"
            stats["geo_country"] = country if country else "Unknown"
            stats["geo_code"] = code if code else "Unknown"
            stats["geo_continent"] = continent if continent else "Unknown"
            stats["geo_postal_code"] = postal_code if postal_code else "Unknown"
            stats["geo_timezone"] = timezone if timezone else "Unknown"
            stats["geo_accuracy"] = accuracy if accuracy is not None else None
            # If you also want lat/lon stored:
            stats["geo_latitude"] = lat if lat is not None else None
            stats["geo_longitude"] = lon if lon is not None else None
