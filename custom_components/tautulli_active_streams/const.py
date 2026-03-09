import logging

DOMAIN = "tautulli_active_streams"

# ---------------------------
# Default / fallback settings
# ---------------------------
DEFAULT_SESSION_INTERVAL = 4
DEFAULT_NUM_SENSORS = 5
DEFAULT_STATISTICS_INTERVAL = 1800
DEFAULT_STATISTICS_DAYS = 30

# ---------------------------
# Configuration option keys
# ---------------------------
CONF_SESSION_INTERVAL = "session_interval"
CONF_NUM_SENSORS = "num_sensors"
CONF_ENABLE_STATISTICS = "enable_statistics"
CONF_STATS_MONTH_TO_DATE = "stats_month_to_date"
CONF_STATISTICS_INTERVAL = "statistics_interval"
CONF_STATISTICS_DAYS = "statistics_days"
CONF_ADVANCED_ATTRIBUTES = "advanced_attributes"
CONF_IMAGE_PROXY = "image_proxy"
CONF_ENABLE_IP_GEOLOCATION = "enable_ip_geolocation"
CONF_GEO_PROVIDER = "geo_provider"

GEO_PROVIDER_TAUTULLI = "tautulli"
GEO_PROVIDER_IP_API = "ip-api"

# ---------------------------
# Plex configuration keys
# ---------------------------
CONF_PLEX_ENABLED = "plex_enabled"
CONF_PLEX_TOKEN = "plex_token"
CONF_PLEX_BASEURL = "plex_base_url"


def format_seconds_to_min_sec(total_seconds: float) -> str:
    """Convert seconds into 'Mm Ss' format."""
    total_seconds = int(total_seconds)
    minutes = total_seconds // 60
    secs = total_seconds % 60
    return f"{minutes}m {secs}s"


LOGGER = logging.getLogger(__package__)


def is_private_ip(ip: str) -> bool:
    """Return True if the IP address is private/reserved (not publicly routable)."""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        return True  # If it can't be parsed, skip the lookup
