"""Constants for Voice Alarms."""

DOMAIN = "voice_alarms"

CONF_MAX_RING_TIME = "max_ring_time"
CONF_SATELLITE_DEVICES = "satellite_devices"
CONF_LOCAL_SATELLITES = "local_satellites"
CONF_ALARM_MODE = "alarm_mode"
CONF_SOUND = "sound"

DEFAULT_MAX_RING_TIME = 30  # minutes
DEFAULT_ALARM_MODE = "ha_fallback"
DEFAULT_SOUND = "Timer finished"

ALARM_MODE_HA_FALLBACK = "ha_fallback"
ALARM_MODE_LOCAL_PREFERRED = "local_preferred"
