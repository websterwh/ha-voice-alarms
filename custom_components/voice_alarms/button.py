"""Button entities for Voice Alarms."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er

from . import AlarmCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Voice Alarms cancel buttons."""
    coordinator: AlarmCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        VoiceAlarmCancelButton(coordinator, entry, satellite, alarm, hass)
        for satellite in coordinator.satellites
        for alarm in coordinator.alarms
        if alarm.satellite == satellite
    ]
    async_add_entities(entities)


class VoiceAlarmCancelButton(ButtonEntity):
    """Button to cancel a specific alarm."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:alarm-off"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: AlarmCoordinator,
        entry: ConfigEntry,
        satellite: str,
        alarm,
        hass: HomeAssistant,
    ) -> None:
        self._coordinator = coordinator
        self._satellite = satellite
        self._alarm_id = alarm.id
        self._hass = hass
        self._attr_unique_id = f"{entry.entry_id}_{satellite}_cancel_{alarm.id}"
        self._attr_translation_key = "cancel_alarm"
        self._attr_translation_placeholders = {
            "time": f"{alarm.hour:02}:{alarm.minute:02}",
            "satellite": satellite,
        }

        # Link to the assist_satellite device
        registry = er.async_get(hass)
        origin = registry.async_get(satellite)
        if origin and origin.device_id:
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, origin.device_id)})

        self._remove_listener = coordinator.async_add_update_listener(
            self._schedule_update
        )

    @property
    def available(self) -> bool:
        """Return True if the alarm still exists."""
        return any(
            a.id == self._alarm_id for a in self._coordinator.alarms
        )

    async def async_press(self) -> None:
        """Cancel this alarm."""
        hour, minute = self._alarm_time()
        await self._coordinator.async_command({
            "satellite": self._satellite,
            "operation": "cancel",
            "time": f"{hour:02}:{minute:02}",
        })

    def _alarm_time(self) -> tuple[int, int]:
        for a in self._coordinator.alarms:
            if a.id == self._alarm_id:
                return a.hour, a.minute
        return 0, 0

    @callback
    def _schedule_update(self) -> None:
        if not self.available:
            self.hass.async_create_task(
                self.async_remove(force_remove=True)
            )
        else:
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listener."""
        self._remove_listener()