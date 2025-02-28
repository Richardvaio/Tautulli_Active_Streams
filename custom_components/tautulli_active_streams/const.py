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
CONF_STATISTICS_INTERVAL = "statistics_interval"
CONF_STATISTICS_DAYS = "statistics_days"
CONF_ADVANCED_ATTRIBUTES = "advanced_attributes"
CONF_IMAGE_PROXY = "image_proxy"

DEFAULT_VERIFY_SSL = False

LOGGER = logging.getLogger(__package__)
