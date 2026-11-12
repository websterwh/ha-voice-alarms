"""Constants for the Voice Alarms integration."""

from __future__ import annotations

DOMAIN = "voice_alarms"

CONF_PREFIX = "service_prefix"

SERVICE_COMMAND = "command"

ATTR_SATELLITE = "satellite"
ATTR_OPERATION = "operation"
ATTR_TIME = "time"
ATTR_REPEAT = "repeat"

OPERATIONS = ["set", "stop", "cancel", "cancel_all", "list", "test"]
REPEATS = ["once", "daily", "weekdays", "weekends"]
DEFAULT_REPEAT = "once"

# Used for every rejected or failed command so the assistant never answers with
# a stack trace or a half-understood request.
FALLBACK_RESPONSE = "Sorry, I didn't get that."
