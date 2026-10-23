"""Config flow for Voice Alarms."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_MAX_RING_TIME,
    CONF_VOICE_PE_DEVICE_ID,
    CONF_BEDROOM_ECHO_DEVICE_ID,
    DEFAULT_MAX_RING_TIME,
    DEFAULT_VOICE_PE_DEVICE_ID,
    DEFAULT_BEDROOM_ECHO_DEVICE_ID,
)


class VoiceAlarmsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Alarms."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="Voice Alarms", data=user_input)

        # Get list of assist_satellite entities
        satellites = self.hass.states.async_entity_ids("assist_satellite")
        satellite_options = [
            selector.SelectOptionDict(value=entity_id, label=entity_id)
            for entity_id in satellites
        ]

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_MAX_RING_TIME,
                    default=DEFAULT_MAX_RING_TIME,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=120, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_VOICE_PE_DEVICE_ID,
                    default=DEFAULT_VOICE_PE_DEVICE_ID,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=satellite_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_BEDROOM_ECHO_DEVICE_ID,
                    default=DEFAULT_BEDROOM_ECHO_DEVICE_ID,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=satellite_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return VoiceAlarmsOptionsFlow(config_entry)


class VoiceAlarmsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Voice Alarms."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get list of assist_satellite entities
        satellites = self.hass.states.async_entity_ids("assist_satellite")
        satellite_options = [
            selector.SelectOptionDict(value=entity_id, label=entity_id)
            for entity_id in satellites
        ]

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_MAX_RING_TIME,
                    default=self.config_entry.data.get(CONF_MAX_RING_TIME, DEFAULT_MAX_RING_TIME),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=120, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_VOICE_PE_DEVICE_ID,
                    default=self.config_entry.data.get(CONF_VOICE_PE_DEVICE_ID, DEFAULT_VOICE_PE_DEVICE_ID),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=satellite_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_BEDROOM_ECHO_DEVICE_ID,
                    default=self.config_entry.data.get(CONF_BEDROOM_ECHO_DEVICE_ID, DEFAULT_BEDROOM_ECHO_DEVICE_ID),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=satellite_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=data_schema
        )