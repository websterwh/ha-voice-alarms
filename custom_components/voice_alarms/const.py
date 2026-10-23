"""Constants for Voice Alarms."""

DOMAIN = "voice_alarms"

CONF_MAX_RING_TIME = "max_ring_time"
CONF_SATELLITE_DEVICES = "satellite_devices"

DEFAULT_MAX_RING_TIME = 30  # minutes

# Alarm entry format: HH:MM<repeat><device_index>
# repeat: o=once, d=daily, w=weekdays, e=weekends
# device_index: 0, 1, 2... corresponding to configured satellites
ALARM_ENTRY_REGEX = r"^([01]?\d|2[0-3]):[0-5]\d[odwe]\d+$"