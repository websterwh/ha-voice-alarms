"""Per-satellite alarms with durable storage and native ESPHome synchronization."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from .const import (
    ALARM_MODE_LOCAL_PREFERRED,
    CONF_ALARM_MODE,
    CONF_LOCAL_SATELLITES,
    CONF_MAX_RING_TIME,
    CONF_SATELLITE_DEVICES,
    CONF_SOUND,
    DEFAULT_MAX_RING_TIME,
    DEFAULT_SOUND,
    DOMAIN,
)
from .models import Alarm, new_alarm, parse_time

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SATELLITE_SCHEMA = vol.Schema({vol.Required("satellite"): cv.entity_id})
COMMAND_SCHEMA = SATELLITE_SCHEMA.extend(
    {
        vol.Required("operation"): vol.In(
            ("set", "stop", "cancel", "cancel_all", "list")
        ),
        vol.Optional("time", default=""): cv.string,
        vol.Optional("repeat", default="once"): vol.In(
            ("once", "daily", "weekdays", "weekends")
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Configure through the UI only."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load alarms and register their scheduler and services."""
    coordinator = AlarmCoordinator(hass, entry)
    await coordinator.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button"])

    async def command(call: ServiceCall) -> dict:
        return await coordinator.async_command(dict(call.data))

    async def sync(call: ServiceCall) -> None:
        async with coordinator.lock:
            await coordinator.async_sync(call.data.get("satellite"), force=True)

    async def test(call: ServiceCall) -> None:
        satellite = call.data["satellite"]
        coordinator.validate_satellite(satellite)
        if satellite in coordinator.pending_clear:
            raise ServiceValidationError(
                "Reconnect this satellite to clear its old local schedule before testing."
            )
        if coordinator.is_local(satellite):
            async with coordinator.lock:
                await coordinator.async_sync(satellite, force=True)
                await coordinator.async_native(satellite, "voice_alarms_test")
        else:
            coordinator.start_ringing(satellite)

    hass.services.async_register(
        DOMAIN,
        "command",
        command,
        schema=COMMAND_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "sync",
        sync,
        schema=vol.Schema({vol.Optional("satellite"): cv.entity_id}),
    )
    hass.services.async_register(DOMAIN, "test_alarm", test, schema=SATELLITE_SCHEMA)

    # Register native intent handlers for voice commands
    intent.async_register(hass, VoiceAlarmSetIntent(coordinator))
    intent.async_register(hass, VoiceAlarmStopIntent(coordinator))
    intent.async_register(hass, VoiceAlarmCancelIntent(coordinator))
    intent.async_register(hass, VoiceAlarmListIntent(coordinator))

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    entry.async_on_unload(
        async_track_time_interval(hass, coordinator.async_tick, timedelta(seconds=5))
    )

    @callback
    def reconnect(event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            new
            and new.state not in ("unavailable", "unknown")
            and (old is None or old.state in ("unavailable", "unknown"))
        ):
            coordinator.last_payload.pop(event.data["entity_id"], None)
            entry.async_create_background_task(
                hass, coordinator.async_background_sync(), "Voice alarm reconnect sync"
            )

    entry.async_on_unload(
        async_track_state_change_event(
            hass, set(coordinator.satellites) | coordinator.pending_clear, reconnect
        )
    )
    entry.async_create_background_task(
        hass, coordinator.async_background_sync(), "Initial voice alarm sync"
    )
    return True


class AlarmCoordinator:
    """Serialize storage updates, device sync, and due-alarm dispatch."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        self.satellites = list(self.config.get(CONF_SATELLITE_DEVICES, []))
        self.local = (
            set(self.config.get(CONF_LOCAL_SATELLITES, []))
            if self.config.get(CONF_ALARM_MODE) == ALARM_MODE_LOCAL_PREFERRED
            else set()
        )
        self.max_minutes = int(
            self.config.get(CONF_MAX_RING_TIME, DEFAULT_MAX_RING_TIME)
        )
        self.sound = self.config.get(CONF_SOUND, DEFAULT_SOUND)
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.alarms: list[Alarm] = []
        self.lock = asyncio.Lock()
        self.ringing: dict[str, asyncio.Task] = {}
        self.last_payload: dict[str, str] = {}
        self.pending_clear: set[str] = set()
        self._update_listeners: list[callable] = []

    def async_add_update_listener(self, listener: callable) -> callable:
        self._update_listeners.append(listener)

        def remove() -> None:
            self._update_listeners.remove(listener)

        return remove

    async def async_load(self) -> None:
        data = await self.store.async_load()
        if data:
            self.alarms = [Alarm.from_dict(a) for a in data.get("alarms", [])]

    async def async_save(self) -> None:
        await self.store.async_save({"alarms": [a.to_dict() for a in self.alarms]})

    def validate_satellite(self, satellite: str) -> None:
        if satellite not in self.satellites:
            raise ServiceValidationError(f"Satellite {satellite} not configured")

    def is_local(self, satellite: str) -> bool:
        return satellite in self.local

    async def async_command(self, data: dict) -> dict:
        op = data["operation"]
        satellite = data["satellite"]
        self.validate_satellite(satellite)

        if op == "set":
            time_text = data.get("time", "")
            repeat = data.get("repeat", "once")
            hour, minute = parse_time(time_text)
            alarm = new_alarm(satellite, time_text, repeat, dt_util.now())
            async with self.lock:
                self.alarms.append(alarm)
                await self.async_save()
                await self.async_sync(satellite)
            return {"response": f"Alarm set for {hour:02d}:{minute:02d} {repeat}."}

        if op == "stop":
            async with self.lock:
                for alarm in self.alarms:
                    if alarm.satellite == satellite and not alarm.due(dt_util.now()):
                        continue
                    # Find currently ringing alarm for this satellite
                    if alarm.satellite == satellite:
                        # Stop the ringing
                        if self.is_local(satellite):
                            await self.async_native(satellite, "voice_alarms_stop")
                        else:
                            self.stop_ringing(satellite)
                        return {"response": "Alarm stopped."}
            return {"response": "No active alarm to stop."}

        if op == "cancel":
            time_text = data.get("time", "")
            if time_text:
                hour, minute = parse_time(time_text)
                time_text = f"{hour:02d}:{minute:02d}"
            async with self.lock:
                if time_text:
                    self.alarms = [
                        a
                        for a in self.alarms
                        if not (a.satellite == satellite and f"{a.hour:02d}:{a.minute:02d}" == time_text)
                    ]
                else:
                    self.alarms = [a for a in self.alarms if a.satellite != satellite]
                await self.async_save()
                await self.async_sync(satellite)
            return {"response": "Alarm cancelled."}

        if op == "cancel_all":
            async with self.lock:
                self.alarms = [a for a in self.alarms if a.satellite != satellite]
                await self.async_save()
                await self.async_sync(satellite)
            return {"response": "All alarms cancelled."}

        if op == "list":
            async with self.lock:
                alarms = [a for a in self.alarms if a.satellite == satellite]
            if not alarms:
                return {"response": "No alarms are set."}
            lines = [f"{a.hour:02d}:{a.minute:02d} {a.repeat}" for a in alarms]
            return {"response": "Alarms: " + ", ".join(lines)}

        raise ServiceValidationError(f"Unknown operation: {op}")

    async def async_sync(self, satellite: str | None = None, force: bool = False) -> None:
        targets = [satellite] if satellite else self.satellites
        for sat in targets:
            if not self.is_local(sat):
                continue
            payload = self._build_payload(sat)
            if not force and payload == self.last_payload.get(sat):
                continue
            try:
                await self.async_native(sat, "voice_alarms_sync", payload=payload)
                self.last_payload[sat] = payload
            except Exception as e:
                _LOGGER.warning("Failed to sync alarms to %s: %s", sat, e)

    def _build_payload(self, satellite: str) -> str:
        alarms = [a for a in self.alarms if a.satellite == satellite]
        data = {
            "alarms": [a.to_dict() for a in alarms],
            "sound": self.sound,
            "max_ring_time": self.max_minutes,
        }
        return json.dumps(data)

    async def async_native(self, satellite: str, action: str, **kwargs) -> None:
        """Call a native API action on the satellite."""
        if not self.is_local(satellite):
            return
        try:
            await self.hass.services.async_call(
                "api",
                "execute_action",
                {"entity_id": satellite, "action": action, **kwargs},
                blocking=True,
            )
        except Exception as e:
            _LOGGER.warning("Native action %s failed for %s: %s", action, satellite, e)

    def start_ringing(self, satellite: str) -> None:
        if satellite in self.ringing:
            return

        async def _ring() -> None:
            try:
                while True:
                    await self.hass.services.async_call(
                        "media_player",
                        "play_media",
                        {
                            "entity_id": f"media_player.{satellite.split('.')[-1]}_media_player",
                            "media_content_id": self.sound,
                            "media_content_type": "music",
                            "announce": True,
                        },
                        blocking=True,
                    )
                    await asyncio.sleep(3)
            except asyncio.CancelledError:
                pass

        self.ringing[satellite] = asyncio.create_task(_ring())

    def stop_ringing(self, satellite: str) -> None:
        task = self.ringing.pop(satellite, None)
        if task:
            task.cancel()

    async def async_tick(self, now: datetime) -> None:
        due_alarms = []
        async with self.lock:
            for alarm in self.alarms:
                if alarm.due(now):
                    due_alarms.append(alarm)
                    alarm.last_fired = now.strftime("%Y-%m-%dT%H:%M")

        for alarm in due_alarms:
            if self.is_local(alarm.satellite):
                await self.async_native(alarm.satellite, "voice_alarms_test")
            else:
                self.start_ringing(alarm.satellite)

            if alarm.repeat == "once":
                async with self.lock:
                    self.alarms = [a for a in self.alarms if a.id != alarm.id]
                await self.async_save()

        if due_alarms:
            await self.async_save()

    async def async_background_sync(self) -> None:
        await asyncio.sleep(5)
        async with self.lock:
            await self.async_sync()


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear schedules on devices no longer selected for local execution."""
    coordinator: AlarmCoordinator = hass.data[DOMAIN][entry.entry_id]
    new_local = set()
    if entry.options.get(CONF_ALARM_MODE) == ALARM_MODE_LOCAL_PREFERRED:
        new_local = set(entry.options.get(CONF_LOCAL_SATELLITES, []))
    old_local = coordinator.local
    removed = old_local - new_local
    for sat in removed:
        try:
            await coordinator.async_native(sat, "voice_alarms_stop")
        except Exception:
            pass
        coordinator.pending_clear.add(sat)
    coordinator.satellites = list(entry.options.get(CONF_SATELLITE_DEVICES, []))
    coordinator.local = new_local
    coordinator.max_minutes = int(entry.options.get(CONF_MAX_RING_TIME, DEFAULT_MAX_RING_TIME))
    coordinator.sound = entry.options.get(CONF_SOUND, DEFAULT_SOUND)
    await coordinator.async_save()
    await coordinator.async_sync()
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Release listeners and HA ring tasks; keep device schedules for HA outages."""
    coordinator: AlarmCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    for task in coordinator.ringing.values():
        task.cancel()
    await asyncio.gather(*coordinator.ringing.values(), return_exceptions=True)
    return True


class VoiceAlarmSetIntent(intent.IntentHandler):
    """Handle 'set alarm' voice commands natively."""

    intent_type = "VoiceAlarmSet"

    def __init__(self, coordinator: AlarmCoordinator) -> None:
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = intent_obj.slots
        _LOGGER.warning("VoiceAlarmSetIntent: slots=%s, intent_obj.text=%r", slots, getattr(intent_obj, "text", None))
        satellite = self._resolve_satellite(intent_obj.context)
        if not satellite:
            return self._error_response("Could not identify which satellite you're speaking from.")

        # Try slot first, then fallback to full text extraction
        time_text = slots.get("time", {}).get("value", "")
        repeat_text = slots.get("repeat", {}).get("value", "once")

        # Also extract from full text in case slot parsing failed
        # The full text might be in a "text" slot or we can get it from the intent input
        full_text = slots.get("text", {}).get("value", "")
        if not full_text:
            # Try to get raw text from intent object
            full_text = getattr(intent_obj, "text", "") or str(slots)
        _LOGGER.warning("VoiceAlarmSetIntent: full_text=%r, time_text=%r", full_text, time_text)

        def extract_time(text: str) -> str:
            """Extract time from text using multiple patterns."""
            import re
            # Pattern 1: "12:55 AM" or "12.55 AM" or "12 55 AM"
            m = re.search(r'(\d{1,2})[.:]?(\d{2})?\s*(am|pm|a\.m\.|p\.m\.)', text, re.IGNORECASE)
            if m:
                h, m, ap = m.groups()
                m = m or "00"
                return f"{h}:{m} {ap.upper().replace('.', '')}"
            # Pattern 1b: "12 55 AM" (space between hour and minute)
            m = re.search(r'(\d{1,2})\s+(\d{2})\s*(am|pm|a\.m\.|p\.m\.)', text, re.IGNORECASE)
            if m:
                h, m, ap = m.groups()
                return f"{h}:{m} {ap.upper().replace('.', '')}"
            # Pattern 2: "at 12:55 AM"
            m = re.search(r'at\s+(\d{1,2})[.:]?(\d{2})?\s*(am|pm)', text, re.IGNORECASE)
            if m:
                h, m, ap = m.groups()
                m = m or "00"
                return f"{h}:{m} {ap.upper()}"
            # Pattern 3: STT run-together like "103 AM" -> treat as 1:03 AM or 10:03 AM
            m = re.search(r'(\d{3,4})\s*(am|pm)', text, re.IGNORECASE)
            if m:
                digits, ap = m.groups()
                if len(digits) == 3:
                    # "103" -> 1:03 (prefer 1-digit hour)
                    h, m = digits[0], digits[1:]
                else:
                    # "1255" -> 12:55
                    h, m = digits[:2], digits[2:]
                return f"{h}:{m} {ap.upper()}"
            return ""

        # Try slot time first
        if time_text:
            time_text = self._normalize_time(time_text)

        # Always try full text extraction as backup/override
        extracted = extract_time(full_text)
        _LOGGER.warning("VoiceAlarmSetIntent: extracted=%r, final time_text=%r", extracted, time_text)
        if extracted:
            time_text = extracted

        repeat = self._parse_repeat(repeat_text)

        try:
            result = await self.coordinator.async_command({
                "satellite": satellite,
                "operation": "set",
                "time": time_text,
                "repeat": repeat,
            })
            response = intent_obj.create_response()
            response.async_set_speech(result["response"])
            return response
        except ValueError:
            response = intent_obj.create_response()
            response.async_set_speech("Use a time such as 7 AM or 19:30.")
            return response

    def _resolve_satellite(self, context: intent.IntentContext) -> str | None:
        """Find satellite by device_id from context."""
        device_id = getattr(context, "device_id", None)
        if not device_id:
            return None
        for sat in self.coordinator.satellites:
            registry = er.async_get(self.coordinator.hass)
            entry = registry.async_get(sat)
            if entry and entry.device_id == device_id:
                return sat
        return None

    def _normalize_time(self, text: str) -> str:
        """Normalize STT time formats to HA-compatible format."""
        import re
        result = text
        result = result.replace('p.m.', 'PM').replace('a.m.', 'AM')
        result = result.replace('p.m', 'PM').replace('a.m', 'AM')
        result = result.replace('pm', 'PM').replace('am', 'AM')
        # Remove trailing period and normalize dots to colons
        result = result.rstrip('.').replace('.', ':')
        # Handle "11 31 PM" -> "11:31 PM" (space between hour/minute)
        result = re.sub(r'\s+(\d\d)\s*(AM|PM)', r':\1 \2', result)
        result = re.sub(r'\s+(\d)\s*(AM|PM)', r':0\1 \2', result)
        # Handle "11:31PM" -> "11:31 PM" (no space before AM/PM)
        result = re.sub(r'(\d):(\d\d)(AM|PM)', r'\1:\2 \3', result)
        return result.strip()

    def _parse_repeat(self, text: str) -> str:
        text = text.lower().strip()
        if text in ("daily", "every day", "everyday", "recurring", "repeating"):
            return "daily"
        if text in ("weekday", "weekdays", "work days", "workdays"):
            return "weekdays"
        if text in ("weekend", "weekends"):
            return "weekends"
        return "once"

    def _error_response(self, msg: str) -> intent.IntentResponse:
        response = intent.IntentResponse()
        response.async_set_speech(msg)
        return response


class VoiceAlarmStopIntent(intent.IntentHandler):
    """Handle 'stop alarm' voice commands natively."""

    intent_type = "VoiceAlarmStop"

    def __init__(self, coordinator: AlarmCoordinator) -> None:
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        satellite = self._resolve_satellite(intent_obj.context)
        if not satellite:
            return self._error_response("Could not identify which satellite you're speaking from.")

        result = await self.coordinator.async_command({
            "satellite": satellite,
            "operation": "stop",
        })
        response = intent_obj.create_response()
        response.async_set_speech(result["response"])
        return response

    def _resolve_satellite(self, context: intent.IntentContext) -> str | None:
        device_id = getattr(context, "device_id", None)
        if not device_id:
            return None
        for sat in self.coordinator.satellites:
            registry = er.async_get(self.coordinator.hass)
            entry = registry.async_get(sat)
            if entry and entry.device_id == device_id:
                return sat
        return None

    def _error_response(self, msg: str) -> intent.IntentResponse:
        response = intent.IntentResponse()
        response.async_set_speech(msg)
        return response


class VoiceAlarmCancelIntent(intent.IntentHandler):
    """Handle 'cancel alarm' voice commands natively."""

    intent_type = "VoiceAlarmCancel"

    def __init__(self, coordinator: AlarmCoordinator) -> None:
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = intent_obj.slots
        satellite = self._resolve_satellite(intent_obj.context)
        if not satellite:
            return self._error_response("Could not identify which satellite you're speaking from.")

        time_text = slots.get("time", {}).get("value", "")

        if time_text:
            time_text = self._normalize_time(time_text)
            try:
                hour, minute = parse_time(time_text)
                time_text = f"{hour:02}:{minute:02}"
            except ValueError:
                response = intent_obj.create_response()
                response.async_set_speech("Use a time such as 7 AM or 19:30.")
                return response
        else:
            # No time specified - cancel the only alarm or ask which
            active = [
                a for a in self.coordinator.alarms
                if a.satellite == satellite
                and (a.once_at is None or a.once_at > dt_util.utcnow().timestamp())
            ]
            if len(active) == 1:
                time_text = f"{active[0].hour:02}:{active[0].minute:02}"
            elif len(active) > 1:
                response = intent_obj.create_response()
                response.async_set_speech("Say which alarm time to cancel, or cancel all alarms.")
                return response
            else:
                response = intent_obj.create_response()
                response.async_set_speech("No alarms are set.")
                return response

        result = await self.coordinator.async_command({
            "satellite": satellite,
            "operation": "cancel",
            "time": time_text,
        })
        response = intent_obj.create_response()
        response.async_set_speech(result["response"])
        return response

    def _resolve_satellite(self, context: intent.IntentContext) -> str | None:
        device_id = getattr(context, "device_id", None)
        if not device_id:
            return None
        for sat in self.coordinator.satellites:
            registry = er.async_get(self.coordinator.hass)
            entry = registry.async_get(sat)
            if entry and entry.device_id == device_id:
                return sat
        return None

    def _normalize_time(self, text: str) -> str:
        import re
        result = text
        result = result.replace('p.m.', 'PM').replace('a.m.', 'AM')
        result = result.replace('p.m', 'PM').replace('a.m', 'AM')
        result = result.replace('pm', 'PM').replace('am', 'AM')
        # Remove trailing period and normalize dots to colons
        result = result.rstrip('.').replace('.', ':')
        # Handle "11 31 PM" -> "11:31 PM" (space between hour/minute)
        result = re.sub(r'\s+(\d\d)\s*(AM|PM)', r':\1 \2', result)
        result = re.sub(r'\s+(\d)\s*(AM|PM)', r':0\1 \2', result)
        # Handle "11:31PM" -> "11:31 PM" (no space before AM/PM)
        result = re.sub(r'(\d):(\d\d)(AM|PM)', r'\1:\2 \3', result)
        return result.strip()

    def _error_response(self, msg: str) -> intent.IntentResponse:
        response = intent.IntentResponse()
        response.async_set_speech(msg)
        return response


class VoiceAlarmListIntent(intent.IntentHandler):
    """Handle 'list alarms' voice commands natively."""

    intent_type = "VoiceAlarmList"

    def __init__(self, coordinator: AlarmCoordinator) -> None:
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        satellite = self._resolve_satellite(intent_obj.context)
        if not satellite:
            return self._error_response("Could not identify which satellite you're speaking from.")

        result = await self.coordinator.async_command({
            "satellite": satellite,
            "operation": "list",
        })
        response = intent_obj.create_response()
        response.async_set_speech(result["response"])
        return response

    def _resolve_satellite(self, context: intent.IntentContext) -> str | None:
        device_id = getattr(context, "device_id", None)
        if not device_id:
            return None
        for sat in self.coordinator.satellites:
            registry = er.async_get(self.coordinator.hass)
            entry = registry.async_get(sat)
            if entry and entry.device_id == device_id:
                return sat
        return None

    def _error_response(self, msg: str) -> intent.IntentResponse:
        response = intent.IntentResponse()
        response.async_set_speech(msg)
        return response