import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import STATE_OFF, CONF_URL, CONF_API_KEY, CONF_VERIFY_SSL
from homeassistant.helpers.entity import EntityCategory
from .const import DOMAIN, DEFAULT_SESSION_COUNT
from homeassistant.components.sensor import SensorStateClass, SensorDeviceClass
from datetime import datetime, timedelta


_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Tautulli stream sensors and diagnostic sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    num_sensors = entry.options.get("num_sensors", DEFAULT_SESSION_COUNT)
    entities = [TautulliStreamSensor(coordinator, entry, i) for i in range(num_sensors)]

    diagnostic_sensors = [
        TautulliDiagnosticSensor(coordinator, entry, "stream_count"),
        TautulliDiagnosticSensor(coordinator, entry, "stream_count_direct_play"),
        TautulliDiagnosticSensor(coordinator, entry, "stream_count_direct_stream"),
        TautulliDiagnosticSensor(coordinator, entry, "stream_count_transcode"),
        TautulliDiagnosticSensor(coordinator, entry, "total_bandwidth"),
        TautulliDiagnosticSensor(coordinator, entry, "lan_bandwidth"),
        TautulliDiagnosticSensor(coordinator, entry, "wan_bandwidth"),
    ]
    
    async_add_entities(entities, True)
    async_add_entities(diagnostic_sensors, True)

class TautulliStreamSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Tautulli stream sensor."""
    def __init__(self, coordinator, entry, index):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._index = index
        self._attr_unique_id = f"plex_session_{index + 1}_tautulli"
        self._attr_name = f"Plex Session {index + 1} (Tautulli)"
        self._attr_icon = "mdi:plex"
        self._attr_device_info = self.device_info

    @property
    def state(self):
        """Return the state of the sensor."""
        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) > self._index:
            return sessions[self._index].get("state", STATE_OFF)
        return STATE_OFF

    @property
    def extra_state_attributes(self):
        """Return extra attributes for the sensor, including a cleaned-up image URL."""
        sessions = self.coordinator.data.get("sessions", [])
        if len(sessions) > self._index:
            session = sessions[self._index]

            base_url = self._entry.data.get(CONF_URL)
            api_key = self._entry.data.get(CONF_API_KEY)

            if not api_key or not base_url:
                return {}

           # Check the 'use_image_proxy' option from the config entry options.
            image_proxy = self._entry.options.get("image_proxy", False)
            image_url = None
            if image_proxy:
                # Build the URL for the proxy
                if session.get("grandparent_thumb"):
                    image_url = (
                        f"/api/tautulli/image?img={session.get('grandparent_thumb')}"
                        "&width=300&height=450&fallback=poster&refresh=true"
                    )
                elif session.get("thumb"):
                    image_url = (
                        f"/api/tautulli/image?img={session.get('thumb')}"
                        "&width=300&height=450&fallback=poster&refresh=true"
                    )
            else:
                # Use the direct URL from the session (if available) or fall back
                if session.get("grandparent_thumb"):
                    image_url = (
                        f"{base_url}/api/v2?apikey={api_key}&cmd=pms_image_proxy"
                        f"&img={session.get('grandparent_thumb')}&width=300&height=450"
                        "&fallback=poster&refresh=true"
                    )
                elif session.get("thumb"):
                    image_url = (
                        f"{base_url}/api/v2?apikey={api_key}&cmd=pms_image_proxy"
                        f"&img={session.get('thumb')}&width=300&height=450"
                        "&fallback=poster&refresh=true"
                    )
                else:
                    image_url = None
                    
            attributes = {
                "user": session.get("user"),
                "email": session.get("email"),
                "progress_percent": session.get("progress_percent"),
                "media_type": session.get("media_type"),
                "full_title": session.get("full_title"),
                "grandparent_thumb": session.get("grandparent_thumb"),
                "thumb": session.get("thumb"),
                "image_url": image_url,  
                "parent_media_index": session.get("parent_media_index"),
                "media_index": session.get("media_index"),
                "year": session.get("year"),
                "product": session.get("product"),
                "player": session.get("player"),
                "device": session.get("device"),
                "platform": session.get("platform"),
                "location": session.get("location"),
                "ip_address": session.get("ip_address"),
                "ip_address_public": session.get("ip_address_public"),
                "local": session.get("local"),
                "relayed": session.get("relayed"),
                "bandwidth": session.get("bandwidth"),
                "video_resolution": session.get("video_resolution"),
                "stream_start_time": session.get("start_time"),
                "stream_duration": formatted_duration,
                "stream_remaining": formatted_remaining,
                "Stream_paused_duration": session.get("Stream_paused_duration"),
                "stream_video_resolution": session.get("stream_video_resolution"),
                "transcode_decision": session.get("transcode_decision"),
                "start_time_raw": session.get("start_time_raw"),
            }

            # Check if extra attributes are enabled in options.
            if self._entry.options.get("advanced_attributes"):
                if session.get("stream_duration"):
                    total_ms = float(session.get("stream_duration"))
                    total_seconds = total_ms / 1000  # convert milliseconds to seconds
                    hours = int(total_seconds // 3600)
                    minutes = int((total_seconds % 3600) // 60)
                    seconds = int(total_seconds % 60)
                    formatted_duration = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    formatted_duration = None
                    
                # Calculate remaining time
                if session.get("view_offset"):
                    remain_total_ms = float(session.get("stream_duration")) - float(session.get("view_offset"))
                    remain_total_seconds = remain_total_ms / 1000  # convert milliseconds to seconds
                    remain_hours = int(remain_total_seconds // 3600)
                    remain_minutes = int((remain_total_seconds % 3600) // 60)
                    remain_seconds = int(remain_total_seconds % 60)
                    formatted_remaining = f"{remain_hours}:{remain_minutes:02d}:{remain_seconds:02d}"

                    # Calculate ETA
                    eta = datetime.now() + timedelta(seconds=remain_total_seconds)
                    formatted_eta = eta.strftime("%I:%M %p").lower()
                else:
                    formatted_remaining = None
                    formatted_eta = None
                
                attributes.update({
                    # Additional keys fetched from the session
                    "user_friendly_name": session.get("friendly_name"),
                    "username": session.get("username"),
                    "user_thumb": session.get("user_thumb"),
                    "session_id": session.get("session_id"),
                    "library_name": session.get("library_name"),
                    "container": session.get("container"),
                    "aspect_ratio": session.get("aspect_ratio"),
                    "video_codec": session.get("video_codec"),
                    "video_framerate": session.get("video_framerate"),
                    "video_profile": session.get("video_profile"),
                    "video_dovi_profile": session.get("video_dovi_profile"),
                    "video_dynamic_range": session.get("video_dynamic_range"),
                    "video_color_space": session.get("video_color_space"),
                    "audio_codec": session.get("audio_codec"),
                    "audio_channels": session.get("audio_channels"),
                    "audio_channel_layout": session.get("audio_channel_layout"),
                    "audio_profile": session.get("audio_profile"),
                    "audio_bitrate": session.get("audio_bitrate"),
                    "audio_language": session.get("audio_language"),
                    "audio_language_code": session.get("audio_language_code"),
                    "subtitle_language": session.get("subtitle_language"),
                    "container_decision": session.get("stream_container_decision"),
                    "audio_decision": session.get("audio_decision"),
                    "video_decision": session.get("video_decision"),
                    "subtitle_decision": session.get("subtitle_decision"),
                    "transcode_container": session.get("transcode_container"),
                    "transcode_audio_codec": session.get("transcode_audio_codec"),
                    "transcode_video_codec": session.get("transcode_video_codec"),
                    "transcode_throttled": session.get("transcode_throttled"),
                    "transcode_progress": session.get("transcode_progress"),
                    "transcode_speed": session.get("transcode_speed"),
                    "stream_container": session.get("stream_container"),
                    "stream_bitrate": session.get("stream_bitrate"),
                    "stream_video_bitrate": session.get("stream_video_bitrate"),
                    "stream_video_codec": session.get("stream_video_codec"),
                    "stream_video_framerate": session.get("stream_video_framerate"),
                    "stream_video_resolution": session.get("stream_video_resolution"),
                    "stream_video_full_resolution": session.get("stream_video_full_resolution"),
                    "stream_video_dovi_profile": session.get("stream_video_dovi_profile"),
                    "stream_video_decision": session.get("stream_video_decision"),
                    "stream_audio_bitrate": session.get("stream_audio_bitrate"),
                    "stream_audio_codec": session.get("stream_audio_codec"),
                    "stream_audio_channels": session.get("stream_audio_channels"),
                    "stream_audio_channel_layout": session.get("stream_audio_channel_layout"),
                    "stream_audio_language": session.get("stream_audio_language"),
                    "stream_audio_language_code": session.get("stream_audio_language_code"),
                })
            return attributes
        return {}

    @property
    def device_info(self):
        """Return device info so all sensors are grouped under one device."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Tautulli Active Streams",
            "manufacturer": "Richardvaio",
            "model": "Tautulli Active Streams",
            "entry_type": "service",
        }
    

class TautulliDiagnosticSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Tautulli diagnostic sensor."""

    def __init__(self, coordinator, entry, metric):
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._metric = metric
        self._attr_unique_id = f"tautulli_{entry.entry_id}_{metric}"
        self.entity_id = f"sensor.tautulli_{metric}" 
        self._attr_name = f"{metric.replace('_', ' ').title()}" 
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = self.device_info
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        if metric in ["total_bandwidth", "lan_bandwidth", "wan_bandwidth"]:
            self._attr_native_unit_of_measurement = "Mbit"
        else:
            self._attr_native_unit_of_measurement = None

    @property
    def state(self):
        """Return the main diagnostic value."""
        diagnostics = self.coordinator.data.get("diagnostics", {})
        raw_value = diagnostics.get(self._metric, 0)
        if self._metric in ["total_bandwidth", "lan_bandwidth", "wan_bandwidth"]:
            try:
                converted = round(float(raw_value) / 1000, 1)
                return converted
            except Exception as err:
                _LOGGER.error("Error converting bandwidth: %s", err)
                return raw_value
        return raw_value
            

    @property
    def extra_state_attributes(self):
        """Return additional diagnostic attributes (if any)."""
        if self._metric != "stream_count":
            return {}
        sessions = self.coordinator.data.get("sessions", [])
        filtered_sessions = []
        for session in sessions:
            filtered_sessions.append({
                "username": (session.get("username") or "").lower(),
                "user": (session.get("user") or "").lower(),
                "full_title": session.get("full_title"),
                "stream_start_time": session.get("start_time"),
                "start_time_raw": session.get("start_time_raw"),
                "Stream_paused_duration_sec": session.get("Stream_paused_duration_sec"),
                "session_id": session.get("session_id"),
            })
        return {"sessions": filtered_sessions}

    @property
    def icon(self):
        """Return an icon based on the sensor type."""
        icon_map = {
            "stream_count": "mdi:plex",  
            "stream_count_direct_play": "mdi:play-circle",
            "stream_count_direct_stream": "mdi:play-network",
            "stream_count_transcode": "mdi:cog",
            "total_bandwidth": "mdi:download-network",
            "lan_bandwidth": "mdi:lan",
            "wan_bandwidth": "mdi:wan",
        }
        return icon_map.get(self._metric, "mdi:chart-bar") 

    @property
    def device_info(self):
        """Diagnostic sensors grouped under Tautulli Active Streams."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Tautulli Active Streams",
            "manufacturer": "Richardvaio",
            "model": "Tautulli Active Streams",
            "entry_type": "service",
        }
        