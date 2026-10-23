"""Voice Alarms integration for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    CONF_MAX_RING_TIME,
    CONF_SATELLITE_DEVICES,
    CONF_ALARM_MODE,
    CONF_TONE_BASE_URL,
    CONF_LOCAL_SATELLITES,
    ALARM_MODE_HA_FALLBACK,
    ALARM_MODE_LOCAL_PREFERRED,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Service schemas
SYNC_SERVICE_SCHEMA = vol.Schema({
    vol.Optional("satellite"): cv.string,
})

TEST_SERVICE_SCHEMA = vol.Schema({
    vol.Required("satellite"): cv.string,
    vol.Optional("tone", default=0): vol.Coerce(int),
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Voice Alarms component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Voice Alarms from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Register services
    async def handle_sync(call: ServiceCall) -> None:
        """Sync alarms to satellites."""
        satellite_entity = call.data.get("satellite")
        await _sync_alarms(hass, entry, satellite_entity)

    async def handle_test(call: ServiceCall) -> None:
        """Test alarm on a satellite."""
        satellite_entity = call.data["satellite"]
        tone = call.data.get("tone", 0)
        await _test_alarm(hass, entry, satellite_entity, tone)

    hass.services.async_register(
        DOMAIN, "sync", handle_sync, schema=SYNC_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "test_alarm", handle_test, schema=TEST_SERVICE_SCHEMA
    )

    # Forward entry setup to automations (blueprints)
    await hass.config_entries.async_forward_entry_setups(entry, ["automation"])

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def _sync_alarms(hass: HomeAssistant, entry: ConfigEntry, target_satellite: str | None) -> None:
    """Push the current alarm list to the selected satellites."""
    data = entry.data
    satellites = data.get(CONF_SATELLITE_DEVICES, [])
    alarm_mode = data.get(CONF_ALARM_MODE, ALARM_MODE_HA_FALLBACK)
    tone_base_url = data.get(CONF_TONE_BASE_URL, "")
    local_satellites = set(data.get(CONF_LOCAL_SATELLITES, []))

    # Get the alarm list from the input_text helper (created by blueprints)
    # The helper entity_id is fixed by the blueprints: input_text.voice_alarms
    alarm_state = hass.states.get("input_text.voice_alarms")
    alarm_list = alarm_state.state if alarm_state else ""

    # If targeting a specific satellite, filter
    if target_satellite:
        if target_satellite not in satellites:
            _LOGGER.warning("Satellite %s not in configured list", target_satellite)
            return
        targets = [target_satellite]
    else:
        targets = satellites

    for sat_entity in targets:
        # Get device_id from the assist_satellite entity
        sat_state = hass.states.get(sat_entity)
        if not sat_state:
            _LOGGER.warning("Satellite entity %s not found", sat_entity)
            continue
        device_id = sat_state.attributes.get("device_id")
        if not device_id:
            _LOGGER.warning("Satellite %s has no device_id", sat_entity)
            continue

        # Check if local mode is preferred and this satellite is marked as local-capable
        use_local = (
            alarm_mode == ALARM_MODE_LOCAL_PREFERRED
            and sat_entity in local_satellites
        )

        if use_local:
            # Call ESPHome custom service to push alarms
            await _push_to_esphome(hass, device_id, alarm_list, tone_base_url)
        else:
            # HA fallback mode: nothing to push; blueprints will handle ringing
            _LOGGER.debug("HA fallback mode for %s", sat_entity)


async def _test_alarm(hass: HomeAssistant, entry: ConfigEntry, satellite_entity: str, tone: int) -> None:
    """Fire a test alarm on a specific satellite."""
    data = entry.data
    satellites = data.get(CONF_SATELLITE_DEVICES, [])
    tone_base_url = data.get(CONF_TONE_BASE_URL, "")

    if satellite_entity not in satellites:
        _LOGGER.warning("Satellite %s not in configured list", satellite_entity)
        return

    sat_state = hass.states.get(satellite_entity)
    if not sat_state:
        _LOGGER.warning("Satellite entity %s not found", satellite_entity)
        return
    device_id = sat_state.attributes.get("device_id")
    if not device_id:
        _LOGGER.warning("Satellite %s has no device_id", satellite_entity)
        return

    # Send a one-shot test tone via ESPHome
    await _push_test_tone(hass, device_id, tone, tone_base_url)


async def _push_to_esphome(hass: HomeAssistant, device_id: str, alarm_list: str, tone_base_url: str) -> None:
    """Send alarm list to ESPHome device via its API."""
    # Use the esphome integration's send_command service if available
    # The service is: esphome.send_command
    # It expects device_id and a JSON payload.
    try:
        await hass.services.async_call(
            "esphome",
            "send_command",
            {
                "device_id": device_id,
                "payload": {
                    "type": "voice_alarms/sync",
                    "alarms": alarm_list,
                    "tone_base_url": tone_base_url,
                },
            },
            blocking=True,
        )
        _LOGGER.debug("Sent alarm sync to ESPHome device %s", device_id)
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.error("Failed to send alarm sync to %s: %s", device_id, err)


async def _push_test_tone(hass: HomeAssistant, device_id: str, tone: int, tone_base_url: str) -> None:
    """Send a test tone command to ESPHome device."""
    try:
        await hass.services.async_call(
            "esphome",
            "send_command",
            {
                "device_id": device_id,
                "payload": {
                    "type": "voice_alarms/test_tone",
                    "tone": tone,
                    "tone_base_url": tone_base_url,
                },
            },
            blocking=True,
        )
        _LOGGER.debug("Sent test tone to ESPHome device %s", device_id)
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.error("Failed to send test tone to %s: %s", device_id, err)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["automation"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok