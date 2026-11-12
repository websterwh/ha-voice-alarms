"""Assist sentence triggers for Voice Alarms.

Home Assistant only reads sentence templates from ``<config>/custom_sentences``,
so a custom integration has to register its phrases with the conversation agent
manager instead. They are registered as sentence triggers, which the default
agent matches before any LLM, and which still run when the pipeline has "prefer
handling commands locally" enabled.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components.conversation import ConversationInput, get_agent_manager
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import AlarmApi, async_resolve_api
from .const import DEFAULT_REPEAT, FALLBACK_RESPONSE, REPEATS

_LOGGER = logging.getLogger(__name__)

SET_SENTENCES: dict[str, list[str]] = {
    "once": [
        "set [a|an|my] alarm (for|at) {time}",
        "set [a|an|my] alarm {time}",
        "wake me up (at|by) {time}",
        "alarm (for|at) {time}",
    ],
    "daily": [
        "set [a|an|my] (daily|every day|everyday|recurring|repeating) alarm (for|at) {time}",
        "set [a|an|my] (daily|every day|everyday|recurring|repeating) alarm {time}",
    ],
    "weekdays": [
        "set [a|an|my] (weekday|weekdays) alarm (for|at) {time}",
        "set [a|an|my] (weekday|weekdays) alarm {time}",
    ],
    "weekends": [
        "set [a|an|my] (weekend|weekends) alarm (for|at) {time}",
        "set [a|an|my] (weekend|weekends) alarm {time}",
    ],
}
STOP_SENTENCES = ["(stop|dismiss|silence|turn off|shut off) [the|my] alarm[s]"]
CANCEL_ALL_SENTENCES = ["(cancel|delete|remove|clear) (all|every) [of] [my|the] alarms"]
CANCEL_SENTENCES = [
    "(cancel|delete|remove|clear) [the|my|an|a] alarm [(for|at) {time}]",
    "(cancel|delete|remove|clear) [the|my] {time} alarm",
]
LIST_SENTENCES = [
    "list [my] alarms",
    "(what|which) alarms [do I have|are set]",
    "[do I have|are there] any alarms",
]
TEST_SENTENCES = ["test [the|my] alarm[s]"]

_TRAILING_REPEAT = (
    (re.compile(r"\s+(?:on\s+)?(?:week\s?days?|work\s?days?)$", re.I), "weekdays"),
    (re.compile(r"\s+(?:on\s+)?week\s?ends?$", re.I), "weekends"),
    (re.compile(r"\s+(?:every\s?day|daily|each day)$", re.I), "daily"),
)
_MERIDIEM = re.compile(r"\b([ap])\s*\.?\s*m\b\.?", re.IGNORECASE)
_TIME = re.compile(
    r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[AP]M)?$", re.IGNORECASE
)


def normalize_time(text: str) -> str:
    """Turn Whisper renderings such as '4.30 a.m.' or '430 AM' into '4:30 AM'."""
    value = _MERIDIEM.sub(lambda match: f" {match.group(1).upper()}M", text.strip())
    value = re.sub(r"\s+", " ", value).strip(" .,!?")
    value = re.sub(r"^(?:for|at|by)\s+", "", value, flags=re.IGNORECASE)
    meridiem = ""
    if value.upper().endswith(("AM", "PM")):
        meridiem = " " + value[-2:].upper()
        value = value[:-2].strip(" .")
    if re.fullmatch(r"\d{1,2}[.: ]\d{2}", value):
        value = re.sub(r"[. ]", ":", value)
    elif meridiem and re.fullmatch(r"\d{3,4}", value):
        value = f"{value[:-2]}:{value[-2:]}"
    return f"{value}{meridiem}"


def split_repeat(text: str, default: str) -> tuple[str, str]:
    """Split a trailing repeat phrase ('7 AM every day') off a spoken time."""
    value = text.strip(" .,!?")
    for pattern, repeat in _TRAILING_REPEAT:
        if pattern.search(value):
            return pattern.sub("", value), repeat
    return value, default


def parse_time(text: str) -> tuple[int, int] | None:
    """Return the 24-hour hour and minute for a spoken time."""
    match = _TIME.match(normalize_time(text))
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("meridiem") or "").upper()
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        if meridiem == "AM" and hour == 12:
            hour = 0
        elif meridiem == "PM" and hour != 12:
            hour += 12
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute


def speak_time(hour: int, minute: int) -> str:
    """Format a stored alarm time for speech."""
    meridiem = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {meridiem}"


def repeat_suffix(repeat: str) -> str:
    """Describe a repeat mode in speech."""
    return {
        "daily": " every day",
        "weekdays": " on weekdays",
        "weekends": " on weekends",
    }.get(repeat, "")


def _alarm_time(alarm: dict[str, Any]) -> tuple[int, int] | None:
    hour = alarm.get("hour")
    minute = alarm.get("minute")
    if not isinstance(hour, int) or not isinstance(minute, int):
        return None
    return hour, minute


async def async_execute(
    api: AlarmApi,
    operation: str,
    time: str | None = None,
    repeat: str | None = None,
) -> str:
    """Run one alarm command and return the sentence to speak."""
    try:
        return await _async_execute(api, operation, time, repeat)
    except HomeAssistantError as err:
        _LOGGER.warning("Voice alarm %s failed on %s: %s", operation, api.name, err)
        return FALLBACK_RESPONSE


async def _async_execute(
    api: AlarmApi,
    operation: str,
    time: str | None,
    repeat: str | None,
) -> str:
    if operation == "set":
        spoken_time, repeat_value = split_repeat(time or "", repeat or DEFAULT_REPEAT)
        normalized = normalize_time(spoken_time)
        parsed = parse_time(normalized)
        if parsed is None:
            return FALLBACK_RESPONSE
        if repeat_value not in REPEATS:
            repeat_value = DEFAULT_REPEAT
        await api.async_call(
            "alarm_set", {"at": normalized, "days": repeat_value, "label": ""}
        )
        if not await api.async_has_alarm(*parsed, repeat_value):
            return FALLBACK_RESPONSE
        return f"Alarm set for {speak_time(*parsed)}{repeat_suffix(repeat_value)}."

    if operation == "stop":
        await api.async_call("alarm_stop")
        return "Alarm stopped."

    if operation == "test":
        await api.async_call("alarm_test")
        return "Playing the alarm sound."

    alarms = await api.async_alarms()

    if operation == "list":
        if not alarms:
            return "No alarms are set."
        spoken = []
        for alarm in sorted(alarms, key=lambda item: (item.get("hour", 0), item.get("minute", 0))):
            parsed = _alarm_time(alarm)
            if parsed is None:
                continue
            spoken.append(
                f"{speak_time(*parsed)}{repeat_suffix(alarm.get('repeat') or 'once')}"
            )
        return "Alarms: " + ", ".join(spoken) + "." if spoken else "No alarms are set."

    if operation == "cancel_all":
        if not alarms:
            return "No alarms are set."
        # The package's alarm_delete always takes both variables; empty values
        # mean "any id" and "any time", which clears the whole table.
        await api.async_call("alarm_delete", {"alarm_id": "", "at": ""})
        return "All alarms cancelled."

    if operation == "cancel":
        if not alarms:
            return "No alarms are set."
        if time:
            parsed = parse_time(time)
            if parsed is None:
                return FALLBACK_RESPONSE
            if not any(_alarm_time(alarm) == parsed for alarm in alarms):
                return "No alarm is set for that time."
            await api.async_call("alarm_delete", {"alarm_id": "", "at": f"{parsed[0]}:{parsed[1]:02d}"})
            return "Alarm cancelled."
        if len(alarms) > 1:
            return "Say which alarm time to cancel, or cancel all alarms."
        parsed = _alarm_time(alarms[0])
        if parsed is None:
            return FALLBACK_RESPONSE
        await api.async_call("alarm_delete", {"alarm_id": "", "at": f"{parsed[0]}:{parsed[1]:02d}"})
        return "Alarm cancelled."

    return FALLBACK_RESPONSE


def async_register_intents(hass: HomeAssistant) -> list[CALLBACK_TYPE]:
    """Register every alarm phrase with the default conversation agent."""
    manager = get_agent_manager(hass)

    def handler(
        build: Callable[[dict[str, str]], dict[str, Any]],
    ) -> Callable[[ConversationInput, Any], Awaitable[str]]:
        async def handle(user_input: ConversationInput, result: Any) -> str:
            api = async_resolve_api(hass, user_input.satellite_id)
            if api is None:
                return FALLBACK_RESPONSE
            slots = {
                name: str(entity.value).strip()
                for name, entity in result.entities.items()
            }
            _LOGGER.debug("Voice alarm %r -> %s", user_input.text, build(slots))
            return await async_execute(api, **build(slots))

        return handle

    registrations: list[
        tuple[list[str], Callable[[ConversationInput, Any], Awaitable[str]]]
    ] = [
        (
            sentences,
            handler(
                lambda slots, repeat=repeat: {
                    "operation": "set",
                    "time": slots.get("time", ""),
                    "repeat": repeat,
                }
            ),
        )
        for repeat, sentences in SET_SENTENCES.items()
    ]
    registrations += [
        (STOP_SENTENCES, handler(lambda _: {"operation": "stop"})),
        (CANCEL_ALL_SENTENCES, handler(lambda _: {"operation": "cancel_all"})),
        (
            CANCEL_SENTENCES,
            handler(lambda slots: {"operation": "cancel", "time": slots.get("time", "")}),
        ),
        (LIST_SENTENCES, handler(lambda _: {"operation": "list"})),
        (TEST_SENTENCES, handler(lambda _: {"operation": "test"})),
    ]

    return [
        manager.register_trigger(sentences, callback)
        for sentences, callback in registrations
    ]
