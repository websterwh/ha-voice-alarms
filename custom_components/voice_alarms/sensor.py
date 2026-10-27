"""Sensor entities for Voice Alarms."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import AlarmCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Voice Alarms sensors."""
    coordinator: AlarmCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        VoiceAlarmsSensor(coordinator, entry, satellite, hass)
        for satellite in coordinator.satellites
    ]
    async_add_entities(entities)


class VoiceAlarmsSensor(SensorEntity):
    """Sensor showing alarms for a single satellite."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:alarm"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: AlarmCoordinator,
        entry: ConfigEntry,
        satellite: str,
        hass: HomeAssistant,
    ) -> None:
        self._coordinator = coordinator
        self._satellite = satellite
        self._hass = hass
        self._attr_unique_id = f"{entry.entry_id}_{satellite}_alarms"
        self._attr_translation_key = "voice_alarms"
        self._attr_translation_placeholders = {"satellite": satellite}

        # Link to the assist_satellite device
        registry = er.async_get(hass)
        origin = registry.async_get(satellite)
        if origin and origin.device_id:
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, origin.device_id)})

        self._remove_listener = coordinator.async_add_update_listener(
            self._schedule_update
        )

    @property
    def native_value(self) -> str:
        """Return the next alarm time or 'No alarms'."""
        alarms = self._alarms_for_satellite()
        if not alarms:
            return "No alarms"
        next_alarm = min(alarms, key=lambda a: (a.hour * 60 + a.minute))
        suffix = f" {next_alarm.repeat}" if next_alarm.repeat != "once" else ""
        return f"{next_alarm.hour:02}:{next_alarm.minute:02}{suffix}"

    @property
    def extra_state_attributes(self) -> dict:
        """Return all alarms as attributes."""
        alarms = self._alarms_for_satellite()
        return {
            "alarms": [
                {
                    "id": a.id,
                    "time": f"{a.hour:02}:{a.minute:02}",
                    "repeat": a.repeat,
                    "once_at": a.once_at,
                }
                for a in alarms
            ],
            "count": len(alarms),
            "satellite": self._satellite,
        }

    def _alarms_for_satellite(self):
        now = dt_util.utcnow()
        return [
            a
            for a in self._coordinator.alarms
            if a.satellite == self._satellite
            and (a.once_at is None or a.once_at > now.timestamp())
        ]

    @callback
    def _schedule_update(self) -> None:
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listener."""
        self._remove_listener()