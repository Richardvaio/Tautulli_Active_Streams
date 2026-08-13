import ipaddress

DOMAIN = "tautulli_active_streams"

# ---------------------------
# Default / fallback settings
# ---------------------------
DEFAULT_SESSION_INTERVAL = 4
DEFAULT_STATISTICS_INTERVAL = 1800
DEFAULT_STATISTICS_DAYS = 30
DEFAULT_STATISTICS_CYCLE_DAY = 1
DEFAULT_STATISTICS_PERIOD = "rolling"

# ---------------------------
# Configuration option keys
# ---------------------------
CONF_SESSION_INTERVAL = "session_interval"
CONF_ENABLE_STATISTICS = "enable_statistics"
CONF_STATS_MONTH_TO_DATE = "stats_month_to_date"
CONF_STATISTICS_INTERVAL = "statistics_interval"
CONF_STATISTICS_DAYS = "statistics_days"
CONF_STATISTICS_CYCLE_DAY = "statistics_cycle_day"
CONF_STATISTICS_PERIOD = "statistics_period"
CONF_ADVANCED_ATTRIBUTES = "advanced_attributes"
CONF_ENABLE_IP_GEOLOCATION = "enable_ip_geolocation"
CONF_EXPOSE_DETAILED_LOCATION = "expose_detailed_location"
CONF_GEO_PROVIDER = "geo_provider"

MAX_HISTORY_RECORDS = 25000

GEO_PROVIDER_TAUTULLI = "tautulli"
GEO_PROVIDER_IP_API = "ip-api"

STATISTICS_PERIOD_CALENDAR_MONTH = "calendar_month"
STATISTICS_PERIOD_CUSTOM_MONTH = "custom_month"
STATISTICS_PERIOD_ROLLING = "rolling"
STATISTICS_PERIODS = {
    STATISTICS_PERIOD_ROLLING: "Rolling period",
    STATISTICS_PERIOD_CALENDAR_MONTH: "Calendar month",
    STATISTICS_PERIOD_CUSTOM_MONTH: "Custom monthly cycle",
}

# ---------------------------
# Plex configuration keys
# ---------------------------
CONF_PLEX_ENABLED = "plex_enabled"
CONF_PLEX_TOKEN = "plex_token"
CONF_PLEX_BASEURL = "plex_base_url"
CONF_PLEX_VERIFY_SSL = "plex_verify_ssl"


def format_seconds_to_min_sec(total_seconds: float) -> str:
    """Convert seconds into human-readable duration."""
    total_seconds = int(total_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def is_private_ip(ip: str) -> bool:
    """Return True if the IP address is private/reserved (not publicly routable)."""
    try:
        addr = ipaddress.ip_address(ip)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
        )
    except ValueError:
        return True  # If it can't be parsed, skip the lookup
