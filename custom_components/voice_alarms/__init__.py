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

    # Load intent file (hass.helpers available here)
    import os
    intent_path = os.path.join(os.path.dirname(__file__), "intents", "en.yaml")
    if os.path.exists(intent_path):
        hass.helpers.intent.async_register_intents_from_file(intent_path)

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
        """Register a listener for alarm changes."""
        self._update_listeners.append(listener)
        return lambda: self._update_listeners.remove(listener)

    def _notify_update(self) -> None:
        for listener in self._update_listeners:
            listener()

    async def async_load(self) -> None:
        data = await self.store.async_load()
        if data:
            self.alarms = [Alarm.from_dict(item) for item in data["alarms"]]
            self.pending_clear = set(data.get("pending_clear", []))

    async def async_save(self) -> None:
        await self.store.async_save(
            {
                "alarms": [alarm.to_dict() for alarm in self.alarms],
                "pending_clear": sorted(self.pending_clear),
            }
        )
        self._notify_update()
    def validate_satellite(self, satellite: str) -> None:
        if satellite not in self.satellites:
            raise ServiceValidationError(
                "Select a satellite configured in Voice Alarms."
            )

    def is_local(self, satellite: str) -> bool:
        return satellite in self.local

    def native_name(self, satellite: str, action: str) -> str:
        """Use the actual ESPHome node name, never a friendly-name guess."""
        registry_entry = er.async_get(self.hass).async_get(satellite)
        if registry_entry is None or registry_entry.config_entry_id is None:
            raise ServiceValidationError(f"{satellite} has no registered integration.")
        config_entry = self.hass.config_entries.async_get_entry(
            registry_entry.config_entry_id
        )
        runtime = getattr(config_entry, "runtime_data", None)
        device_info = getattr(runtime, "device_info", None)
        if (
            config_entry is None
            or config_entry.domain != "esphome"
            or device_info is None
        ):
            raise ServiceValidationError(
                f"{satellite} must be a connected ESPHome satellite for local alarms."
            )
        name = f"{device_info.name.replace('-', '_')}_{action}"
        if not self.hass.services.has_service("esphome", name):
            raise ServiceValidationError(
                f"Missing esphome.{name}. Flash the Voice Alarms package and reconnect the device."
            )
        return name

    async def async_native(
        self, satellite: str, action: str, data: dict | None = None
    ) -> None:
        try:
            async with asyncio.timeout(3):
                await self.hass.services.async_call(
                    "esphome",
                    self.native_name(satellite, action),
                    data or {},
                    blocking=True,
                    return_response=action == "voice_alarms_sync",
                )
        except TimeoutError as err:
            raise HomeAssistantError(
                f"Alarm device {satellite} did not respond within three seconds."
            ) from err

    def payload(self, satellite: str, *, clear: bool = False) -> str:
        alarms = (
            []
            if clear
            else [
                {
                    key: value
                    for key, value in alarm.to_dict().items()
                    if key not in ("satellite", "last_fired")
                }
                for alarm in self.alarms
                if alarm.satellite == satellite
            ]
        )
        return json.dumps(
            {"alarms": alarms, "sound": self.sound, "max_ring_time": self.max_minutes},
            separators=(",", ":"),
        )

    async def async_sync(
        self, satellite: str | None = None, *, force: bool = False
    ) -> None:
        if satellite is not None:
            self.validate_satellite(satellite)
        for target in [satellite] if satellite else self.satellites:
            if not self.is_local(target):
                continue
            payload = self.payload(target)
            if force or self.last_payload.get(target) != payload:
                await self.async_native(
                    target, "voice_alarms_sync", {"payload": payload}
                )
                self.last_payload[target] = payload

    async def async_background_sync(self) -> None:
        async with self.lock:
            await self.async_sync_pending()

    async def async_sync_pending(self) -> None:
        """Run different devices concurrently, keeping commands serialized with sync."""

        async def sync_one(satellite: str) -> None:
            try:
                if satellite in self.pending_clear:
                    await self.async_native(
                        satellite,
                        "voice_alarms_sync",
                        {"payload": self.payload(satellite, clear=True)},
                    )
                    await self.async_native(satellite, "voice_alarms_stop")
                    self.pending_clear.remove(satellite)
                    await self.async_save()
                if satellite in self.local:
                    await self.async_sync(satellite)
            except HomeAssistantError as err:
                _LOGGER.debug("Alarm sync pending for %s: %s", satellite, err)

        await asyncio.gather(
            *(sync_one(satellite) for satellite in self.local | self.pending_clear)
        )

    async def async_command(self, data: dict[str, Any]) -> dict[str, str]:
        satellite, operation = data["satellite"], data["operation"]
        self.validate_satellite(satellite)
        async with self.lock:
            now = dt_util.now()
            if satellite in self.pending_clear and operation != "list":
                raise ServiceValidationError(
                    "Reconnect this satellite to clear its old local schedule before changing alarms."
                )
            active = [
                alarm
                for alarm in self.alarms
                if alarm.satellite == satellite
                and (alarm.once_at is None or alarm.once_at > now.timestamp())
            ]
            if operation == "stop":
                await self.async_stop(satellite)
                return {"response": "Alarm off."}
            if operation == "list":
                labels = [
                    f"{alarm.hour:02}:{alarm.minute:02}"
                    + (f" {alarm.repeat}" if alarm.repeat != "once" else "")
                    for alarm in active
                ]
                return {
                    "response": "Alarms: " + ", ".join(labels) + "."
                    if labels
                    else "No alarms are set."
                }
            previous = list(self.alarms)
            try:
                if operation == "set":
                    alarm = new_alarm(
                        satellite, data.get("time", ""), data.get("repeat", "once"), now
                    )
                    if any(
                        (item.hour, item.minute, item.repeat)
                        == (alarm.hour, alarm.minute, alarm.repeat)
                        for item in active
                    ):
                        return {"response": "That alarm is already set."}
                    if len(active) >= 30:
                        return {"response": "This device already has 30 alarms."}
                    self.alarms.append(alarm)
                    response = (
                        f"Alarm set for {alarm.hour:02}:{alarm.minute:02}"
                        + (f" {alarm.repeat}" if alarm.repeat != "once" else "")
                        + "."
                    )
                else:
                    if operation == "cancel_all":
                        removed = [
                            alarm
                            for alarm in self.alarms
                            if alarm.satellite == satellite
                        ]
                    elif data.get("time"):
                        hour, minute = parse_time(data["time"])
                        removed = [
                            alarm
                            for alarm in active
                            if (alarm.hour, alarm.minute) == (hour, minute)
                        ]
                    elif len(active) > 1:
                        return {
                            "response": "Say which alarm time to cancel, or cancel all alarms."
                        }
                    else:
                        removed = active
                    if not removed:
                        return {"response": "No matching alarms are set."}
                    self.alarms = [
                        alarm for alarm in self.alarms if alarm not in removed
                    ]
                    response = (
                        "All alarms cancelled."
                        if operation == "cancel_all"
                        else "Alarm cancelled."
                    )
                # A local set/cancel is not acknowledged until the device accepted the API call.
                await self.async_save()
                await self.async_sync(satellite, force=True)
            except ValueError:
                self.alarms = previous
                return {"response": "Use a time such as 7 AM or 19:30."}
            except HomeAssistantError:
                self.alarms = previous
                self.last_payload.pop(satellite, None)
                await self.async_save()
                raise
            except OSError:
                self.alarms = previous
                raise
            return {"response": response}

    async def async_tick(self, _now: datetime) -> None:
        async with self.lock:
            now = dt_util.now()
            changed = False
            for alarm in self.alarms:
                if alarm.satellite not in self.satellites or not alarm.due(now):
                    continue
                alarm.last_fired = now.strftime("%Y-%m-%dT%H:%M")
                changed = True
                if (
                    not self.is_local(alarm.satellite)
                    and alarm.satellite not in self.pending_clear
                ):
                    self.start_ringing(alarm.satellite)
            # Keep local one-shots through their firing minute to avoid racing device evaluation.
            remaining = [
                alarm
                for alarm in self.alarms
                if alarm.once_at is None or now.timestamp() < alarm.once_at + 90
            ]
            if len(remaining) != len(self.alarms):
                self.alarms = remaining
                changed = True
            if changed:
                await self.async_save()
            await self.async_sync_pending()

    def start_ringing(self, satellite: str) -> None:
        if satellite not in self.ringing:
            self.ringing[satellite] = self.entry.async_create_background_task(
                self.hass, self.async_ring(satellite), f"Voice alarm {satellite}"
            )

    async def async_ring(self, satellite: str) -> None:
        try:
            async with asyncio.timeout(self.max_minutes * 60):
                while True:
                    try:
                        await self.hass.services.async_call(
                            "assist_satellite",
                            "announce",
                            {
                                "entity_id": satellite,
                                "message": "Alarm. Say stop alarm to turn it off.",
                            },
                            blocking=True,
                        )
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Alarm announcement on %s failed: %s", satellite, err
                        )
                    await asyncio.sleep(8)
        except TimeoutError:
            pass
        finally:
            self.ringing.pop(satellite, None)
            await self.async_stop_media(satellite)

    async def async_stop_media(self, satellite: str) -> None:
        registry = er.async_get(self.hass)
        origin = registry.async_get(satellite)
        if origin is None or origin.device_id is None:
            return
        players = [
            entity.entity_id
            for entity in er.async_entries_for_device(registry, origin.device_id)
            if entity.domain == "media_player" and not entity.disabled
        ]
        if len(players) != 1:
            _LOGGER.warning(
                "Cannot identify a unique media player to stop on %s (%s found)",
                satellite,
                len(players),
            )
        if len(players) == 1:
            try:
                await self.hass.services.async_call(
                    "media_player",
                    "media_stop",
                    {"entity_id": players[0]},
                    blocking=True,
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Could not stop alarm audio on %s: %s", satellite, err)

    async def async_stop(self, satellite: str) -> None:
        if self.is_local(satellite):
            await self.async_native(satellite, "voice_alarms_stop")
        task = self.ringing.get(satellite)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def async_shutdown(self) -> None:
        tasks = list(self.ringing.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear schedules on devices no longer selected for local execution."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    config = {**entry.data, **entry.options}
    new_local = (
        set(config.get(CONF_LOCAL_SATELLITES, []))
        if config.get(CONF_ALARM_MODE) == ALARM_MODE_LOCAL_PREFERRED
        else set()
    )
    async with coordinator.lock:
        for satellite in coordinator.local - new_local:
            try:
                await coordinator.async_native(
                    satellite,
                    "voice_alarms_sync",
                    {"payload": coordinator.payload(satellite, clear=True)},
                )
                await coordinator.async_native(satellite, "voice_alarms_stop")
            except HomeAssistantError as err:
                coordinator.pending_clear.add(satellite)
                _LOGGER.warning(
                    "Device %s still has local alarms; HA announcements stay suspended until reconnect clears it: %s",
                    satellite,
                    err,
                )
        await coordinator.async_save()
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Release listeners and HA ring tasks; keep device schedules for HA outages."""
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_shutdown()
    for name in ("command", "sync", "test_alarm"):
        hass.services.async_remove(DOMAIN, name)
    await hass.config_entries.async_unload_platforms(entry, ["sensor", "button"])
    return True


class VoiceAlarmSetIntent(intent.IntentHandler):
    """Handle 'set alarm' voice commands natively."""

    intent_type = "VoiceAlarmSet"

    def __init__(self, coordinator: AlarmCoordinator) -> None:
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        slots = intent_obj.slots
        satellite = self._resolve_satellite(intent_obj.context)
        if not satellite:
            return self._error_response("Could not identify which satellite you're speaking from.")

        time_text = slots.get("time", {}).get("value", "")
        repeat_text = slots.get("repeat", {}).get("value", "once")

        # Normalize time (same logic as blueprint)
        time_text = self._normalize_time(time_text)

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
        result = result.replace('.', ':')
        result = re.sub(r'\s+(\d\d)\s*(AM|PM)', r':\1 \2', result)
        result = re.sub(r'\s+(\d)\s*(AM|PM)', r':0\1 \2', result)
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
        result = result.replace('.', ':')
        result = re.sub(r'\s+(\d\d)\s*(AM|PM)', r':\1 \2', result)
        result = re.sub(r'\s+(\d)\s*(AM|PM)', r':0\1 \2', result)
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