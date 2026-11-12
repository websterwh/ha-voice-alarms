"""Client for the Voice Alarms ESPHome package actions."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

ALARM_SET = "alarm_set"


def prefix_for_name(name: str) -> str:
    """Return the ESPHome service prefix for a device or node name."""
    return name.replace("-", "_").lower()


@callback
def async_discover_prefixes(hass: HomeAssistant) -> list[str]:
    """Return every ESPHome node that exposes the alarm package."""
    services = hass.services.async_services().get("esphome", {})
    return sorted(
        {name[: -len(f"_{ALARM_SET}")] for name in services if name.endswith(f"_{ALARM_SET}")}
    )


@callback
def async_friendly_name(hass: HomeAssistant, prefix: str) -> str:
    """Return the Home Assistant device name for an ESPHome service prefix."""
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries("esphome"):
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            for name in (device.name_by_user, device.name):
                if name and prefix_for_name(name) == prefix:
                    return name
    return prefix.replace("_", " ").title()


class AlarmApi:
    """Call the on-device alarm actions for a single ESPHome node."""

    def __init__(self, hass: HomeAssistant, prefix: str) -> None:
        self.hass = hass
        self.prefix = prefix

    @property
    def name(self) -> str:
        return async_friendly_name(self.hass, self.prefix)

    def service_name(self, action: str) -> str:
        return f"{self.prefix}_{action}"

    async def async_call(
        self,
        action: str,
        data: dict[str, Any] | None = None,
        *,
        response: bool = False,
    ) -> Any:
        """Call one of the package's native API actions."""
        name = self.service_name(action)
        if not self.hass.services.has_service("esphome", name):
            raise ServiceValidationError(
                f"Missing esphome.{name}. Flash the Voice Alarms package and reload the device."
            )
        return await self.hass.services.async_call(
            "esphome",
            name,
            data or {},
            blocking=True,
            return_response=response,
        )

    async def async_alarms(self) -> list[dict[str, Any]]:
        """Return the schedule stored on the device."""
        # ESPHome wraps an action's response object under the action name, but
        # older versions returned the object directly, so accept both.
        result = await self.async_call("alarm_list", response=True)
        payload = result if isinstance(result, dict) else {}
        candidates: list[Any] = [payload, *payload.values()]
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("alarms"), list):
                return [alarm for alarm in candidate["alarms"] if isinstance(alarm, dict)]
        return []

    async def async_has_alarm(self, hour: int, minute: int, repeat: str) -> bool:
        """Return whether the device stored an alarm after it accepted one."""
        for alarm in await self.async_alarms():
            if (
                alarm.get("hour") == hour
                and alarm.get("minute") == minute
                and (alarm.get("repeat") or "daily") == repeat
            ):
                return True
        return False


@callback
def async_resolve_api(hass: HomeAssistant, satellite_id: str | None) -> AlarmApi | None:
    """Pick the alarm device for a satellite, falling back to a single entry."""
    entries: dict[str, AlarmApi] = hass.data.get(DOMAIN, {}).get("entries", {})
    apis = list(entries.values())
    if len(apis) == 1:
        return apis[0]
    if not satellite_id:
        return None

    entity = er.async_get(hass).async_get(satellite_id)
    device_id = entity.device_id if entity else None
    devices = dr.async_get(hass)
    while device_id:
        device = devices.async_get(device_id)
        if device is None:
            break
        for name in (device.name_by_user, device.name):
            if not name:
                continue
            prefix = prefix_for_name(name)
            for api in apis:
                if api.prefix == prefix:
                    return api
        # Alarm entities live on an ESPHome sub-device, so keep walking up to
        # the Voice PE device that owns the satellite.
        device_id = device.parent_device_id
    return None
