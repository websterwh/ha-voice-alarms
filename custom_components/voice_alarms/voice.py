"""Native Assist sentence triggers for Voice Alarms.

Sentence triggers are matched by the built-in conversation agent before any
intent, so these phrases never collide with built-in intents such as timers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.conversation import ConversationInput, get_agent_manager
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from . import AlarmCoordinator

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
STOP_SENTENCES = [
    "(stop|dismiss|silence|turn off|shut off) [the|my] alarm[s]",
]
CANCEL_ALL_SENTENCES = [
    "(cancel|delete|remove|clear) (all|every) [of] [my|the] alarms",
]
CANCEL_SENTENCES = [
    "(cancel|delete|remove|clear) [the|my|an|a] alarm [(for|at) {time}]",
    "(cancel|delete|remove|clear) [the|my] {time} alarm",
]
LIST_SENTENCES = [
    "list [my] alarms",
    "(what|which) alarms [do I have|are set]",
    "[do I have|are there] any alarms",
]

_TRAILING_REPEAT = (
    (re.compile(r"\s+(?:on\s+)?(?:week\s?days?|work\s?days?)$", re.I), "weekdays"),
    (re.compile(r"\s+(?:on\s+)?week\s?ends?$", re.I), "weekends"),
    (re.compile(r"\s+(?:every\s?day|daily|each day)$", re.I), "daily"),
)
_MERIDIEM = re.compile(r"\b([ap])\s*\.?\s*m\b\.?", re.IGNORECASE)


def normalize_time(text: str) -> str:
    """Turn Whisper renderings such as '4.30 a.m.' or '430 AM' into '4:30 AM'."""
    value = _MERIDIEM.sub(lambda m: f" {m.group(1).upper()}M", text.strip())
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


def resolve_satellite(
    hass: HomeAssistant, coordinator: AlarmCoordinator, user_input: ConversationInput
) -> str | None:
    """Return the configured satellite that heard the command, if unambiguous."""
    if user_input.satellite_id in coordinator.satellites:
        return user_input.satellite_id
    if user_input.device_id is None:
        return None
    registry = er.async_get(hass)
    matches = [
        satellite
        for satellite in coordinator.satellites
        if (entry := registry.async_get(satellite)) is not None
        and entry.device_id == user_input.device_id
    ]
    return matches[0] if len(matches) == 1 else None


def async_register_voice(
    hass: HomeAssistant, coordinator: AlarmCoordinator
) -> list[CALLBACK_TYPE]:
    """Register every alarm phrase with the default conversation agent."""
    manager = get_agent_manager(hass)

    def handler(
        build: Callable[[dict[str, str]], dict[str, Any]],
    ) -> Callable[[ConversationInput, Any], Awaitable[str]]:
        async def handle(user_input: ConversationInput, result: Any) -> str:
            satellite = resolve_satellite(hass, coordinator, user_input)
            if satellite is None:
                return "Say that to a configured voice satellite."
            slots = {
                name: str(entity.value).strip()
                for name, entity in result.entities.items()
            }
            command = {"satellite": satellite, **build(slots)}
            _LOGGER.debug("Voice alarm %r -> %s", user_input.text, command)
            try:
                response = await coordinator.async_command(command)
            except HomeAssistantError as err:
                _LOGGER.warning("Voice alarm command %s failed: %s", command, err)
                return f"Sorry, the alarm could not be saved on this device: {err}"
            return response["response"]

        return handle

    def set_alarm(repeat: str) -> Callable[[dict[str, str]], dict[str, Any]]:
        def build(slots: dict[str, str]) -> dict[str, Any]:
            time, spoken_repeat = split_repeat(slots.get("time", ""), repeat)
            return {
                "operation": "set",
                "time": normalize_time(time),
                "repeat": spoken_repeat,
            }

        return build

    registrations = [
        (sentences, handler(set_alarm(repeat)))
        for repeat, sentences in SET_SENTENCES.items()
    ]
    registrations += [
        (STOP_SENTENCES, handler(lambda _: {"operation": "stop"})),
        (CANCEL_ALL_SENTENCES, handler(lambda _: {"operation": "cancel_all"})),
        (
            CANCEL_SENTENCES,
            handler(
                lambda slots: {
                    "operation": "cancel",
                    "time": normalize_time(slots.get("time", "")),
                }
            ),
        ),
        (LIST_SENTENCES, handler(lambda _: {"operation": "list"})),
    ]
    return [
        manager.register_trigger(sentences, callback)
        for sentences, callback in registrations
    ]
