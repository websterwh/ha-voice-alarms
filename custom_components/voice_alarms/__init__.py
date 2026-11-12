"""Voice Alarms: Assist voice control for on-device Voice PE alarms."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import AlarmApi, async_resolve_api
from .const import (
    ATTR_OPERATION,
    ATTR_REPEAT,
    ATTR_SATELLITE,
    ATTR_TIME,
    CONF_PREFIX,
    DOMAIN,
    OPERATIONS,
    REPEATS,
    SERVICE_COMMAND,
)
from .intents import async_execute, async_register_intents

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SATELLITE): cv.entity_id,
        vol.Required(ATTR_OPERATION): vol.In(OPERATIONS),
        vol.Optional(ATTR_TIME): cv.string,
        vol.Optional(ATTR_REPEAT): vol.In(REPEATS),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    hass.data.setdefault(DOMAIN, {"entries": {}, "intents": []})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register one Voice Alarms device."""
    data = hass.data.setdefault(DOMAIN, {"entries": {}, "intents": []})
    data["entries"][entry.entry_id] = AlarmApi(hass, entry.data[CONF_PREFIX])

    if not hass.services.has_service(DOMAIN, SERVICE_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_COMMAND,
            _async_command,
            schema=COMMAND_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not data["intents"]:
        data["intents"] = async_register_intents(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Forget one device, cleaning up after the last one."""
    data = hass.data.get(DOMAIN)
    if data is None:
        return True
    data["entries"].pop(entry.entry_id, None)
    if data["entries"]:
        return True
    for unregister in data.get("intents", []):
        unregister()
    data["intents"] = []
    if hass.services.has_service(DOMAIN, SERVICE_COMMAND):
        hass.services.async_remove(DOMAIN, SERVICE_COMMAND)
    return True


async def _async_command(call: ServiceCall) -> ServiceResponse:
    """Handle the scriptable alarm command, used by the bundled blueprints."""
    api = async_resolve_api(call.hass, call.data.get(ATTR_SATELLITE))
    if api is None:
        raise ServiceValidationError("No Voice Alarms device is configured.")
    response = await async_execute(
        api,
        call.data[ATTR_OPERATION],
        call.data.get(ATTR_TIME),
        call.data.get(ATTR_REPEAT),
    )
    return {"response": response}
