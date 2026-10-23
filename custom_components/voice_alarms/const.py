"""Constants for Voice Alarms."""

DOMAIN = "voice_alarms"

CONF_MAX_RING_TIME = "max_ring_time"
CONF_VOICE_PE_DEVICE_ID = "voice_pe_device_id"
CONF_BEDROOM_ECHO_DEVICE_ID = "bedroom_echo_device_id"

DEFAULT_MAX_RING_TIME = 30  # minutes
DEFAULT_VOICE_PE_DEVICE_ID = "65871e282cffcc90036a301f4fbfdb84"
DEFAULT_BEDROOM_ECHO_DEVICE_ID = "4bfddf4234c36dcfbda4cd5ed64afcd8"

# Device codes for storage
DEVICE_CODE_VOICE_PE = "V"
DEVICE_CODE_BEDROOM_ECHO = "E"

# Alarm entry format: HH:MM<repeat><device>
# repeat: o=once, d=daily, w=weekdays, e=weekends
# device: V=Voice PE, E=Bedroom Echo
ALARM_ENTRY_REGEX = r"^([01]?\d|2[0-3]):[0-5]\d[odwe][VE]$"