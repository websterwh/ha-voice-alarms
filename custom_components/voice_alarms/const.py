"""Constants for Voice Alarms."""

DOMAIN = "voice_alarms"

CONF_MAX_RING_TIME = "max_ring_time"
CONF_SATELLITE_DEVICES = "satellite_devices"
CONF_ALARM_MODE = "alarm_mode"
CONF_TONE_BASE_URL = "tone_base_url"
CONF_LOCAL_SATELLITES = "local_satellites"

DEFAULT_MAX_RING_TIME = 30  # minutes
DEFAULT_ALARM_MODE = "ha_fallback"  # or "local_preferred"
DEFAULT_TONE_BASE_URL = ""  # empty = use baked-in tone_<n>.wav

ALARM_MODE_HA_FALLBACK = "ha_fallback"
ALARM_MODE_LOCAL_PREFERRED = "local_preferred"

# Alarm entry format: HH:MM<repeat><device_index>
# repeat: o=once, d=daily, w=weekdays, e=weekends
# device_index: 0, 1, 2... corresponding to configured satellites
ALARM_ENTRY_REGEX = r"^([01]?\d|2[0-3]):[0-5]\d[odwe]\d+$"