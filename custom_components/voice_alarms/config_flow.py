"""Config flow for Voice Alarms."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import async_discover_prefixes, async_friendly_name
from .const import CONF_PREFIX, DOMAIN


class VoiceAlarmsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick the ESPHome device that runs the Voice Alarms package."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which discovered device to control."""
        prefixes = async_discover_prefixes(self.hass)
        if not prefixes:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            prefix = user_input[CONF_PREFIX]
            await self.async_set_unique_id(prefix)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=async_friendly_name(self.hass, prefix),
                data={CONF_PREFIX: prefix},
            )

        options = [
            SelectOptionDict(value=prefix, label=async_friendly_name(self.hass, prefix))
            for prefix in prefixes
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PREFIX): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )
