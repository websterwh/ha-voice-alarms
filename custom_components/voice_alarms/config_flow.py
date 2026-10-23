"""Config flow for Voice Alarms."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    ALARM_MODE_HA_FALLBACK,
    ALARM_MODE_LOCAL_PREFERRED,
    CONF_ALARM_MODE,
    CONF_LOCAL_SATELLITES,
    CONF_MAX_RING_TIME,
    CONF_SATELLITE_DEVICES,
    CONF_SOUND,
    DEFAULT_ALARM_MODE,
    DEFAULT_MAX_RING_TIME,
    DEFAULT_SOUND,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _discover_sounds(
    hass: HomeAssistant,
    satellite_entity_ids: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """Discover available sound options from ESPHome select entities.

    For each satellite entity, finds a select entity on the same HA device whose
    unique_id or object_id (the part after the domain dot) contains the literal
    string 'voice_alarms_sound'.  The options attribute of that select's current
    state becomes the sound list for that satellite.

    Returns:
        common_sounds  -- intersection of discovered option lists across all
                          satellites that expose a matching select; falls back to
                          [DEFAULT_SOUND] when nothing is found.
        per_satellite  -- mapping of satellite_entity_id -> option list for
                          downstream validation.
    """
    ent_reg = er.async_get(hass)
    per_satellite: dict[str, list[str]] = {}

    for sat_id in satellite_entity_ids:
        sat_entry = ent_reg.async_get(sat_id)
        if not sat_entry or not sat_entry.device_id:
            continue

        device_entries = er.async_entries_for_device(ent_reg, sat_entry.device_id)
        for entry in device_entries:
            if entry.domain != "select":
                continue
            uid_lower = (entry.unique_id or "").lower()
            oid_lower = entry.entity_id.rsplit(".", 1)[-1].lower()
            if "voice_alarms_sound" in uid_lower or "voice_alarms_sound" in oid_lower:
                state = hass.states.get(entry.entity_id)
                if state:
                    options: list[str] = state.attributes.get("options") or []
                    if options:
                        per_satellite[sat_id] = list(options)
                        break  # one sound select per satellite device

    if not per_satellite:
        return [DEFAULT_SOUND], {}

    option_sets = [set(opts) for opts in per_satellite.values()]
    common = sorted(option_sets[0].intersection(*option_sets[1:]))
    return (common if common else [DEFAULT_SOUND]), per_satellite


def _build_schema(
    satellite_options: list[selector.SelectOptionDict],
    sound_options: list[str],
    defaults: dict[str, Any],
) -> vol.Schema:
    """Build the shared config/options voluptuous schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_MAX_RING_TIME,
                default=defaults.get(CONF_MAX_RING_TIME, DEFAULT_MAX_RING_TIME),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=120,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SATELLITE_DEVICES,
                default=defaults.get(CONF_SATELLITE_DEVICES, []),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=satellite_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_LOCAL_SATELLITES,
                default=defaults.get(CONF_LOCAL_SATELLITES, []),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=satellite_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=True,
                )
            ),
            vol.Required(
                CONF_ALARM_MODE,
                default=defaults.get(CONF_ALARM_MODE, DEFAULT_ALARM_MODE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=ALARM_MODE_HA_FALLBACK,
                            label="HA Fallback (HA announces)",
                        ),
                        selector.SelectOptionDict(
                            value=ALARM_MODE_LOCAL_PREFERRED,
                            label="Local Preferred (device rings locally)",
                        ),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_SOUND,
                default=defaults.get(CONF_SOUND, DEFAULT_SOUND),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=s, label=s)
                        for s in sound_options
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _validate(
    user_input: dict[str, Any],
    per_sat_sounds: dict[str, list[str]],
) -> dict[str, str]:
    """Return a dict of field -> error_key for any constraint violations."""
    errors: dict[str, str] = {}

    satellites: list[str] = user_input.get(CONF_SATELLITE_DEVICES, [])
    local_sats: list[str] = user_input.get(CONF_LOCAL_SATELLITES, [])
    sound: str = user_input.get(CONF_SOUND, DEFAULT_SOUND)

    if not satellites:
        errors[CONF_SATELLITE_DEVICES] = "satellites_required"
        return errors

    if not set(local_sats).issubset(set(satellites)):
        errors[CONF_LOCAL_SATELLITES] = "local_not_subset"
        return errors

    for sat in local_sats:
        sat_opts = per_sat_sounds.get(sat)
        if sat_opts is None or sound not in sat_opts:
            errors[CONF_SOUND] = "sound_not_available"
            break

    return errors


def _normalise(user_input: dict[str, Any]) -> dict[str, Any]:
    """Coerce selector output types before storage."""
    return {
        CONF_MAX_RING_TIME: int(user_input[CONF_MAX_RING_TIME]),
        CONF_SATELLITE_DEVICES: user_input.get(CONF_SATELLITE_DEVICES, []),
        CONF_LOCAL_SATELLITES: user_input.get(CONF_LOCAL_SATELLITES, []),
        CONF_ALARM_MODE: user_input[CONF_ALARM_MODE],
        CONF_SOUND: user_input[CONF_SOUND],
    }


class VoiceAlarmsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Alarms.

    Only a single config entry is allowed; a second attempt is aborted.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}

        # Sound discovery scope: use submitted satellite list after first
        # submission so the options update as the user changes their selection.
        # On the very first render use all known assist_satellite entities.
        all_satellites: list[str] = self.hass.states.async_entity_ids(
            "assist_satellite"
        )
        discover_from: list[str] = (
            user_input.get(CONF_SATELLITE_DEVICES, [])
            if user_input is not None
            else all_satellites
        )
        sound_options, per_sat_sounds = await _discover_sounds(self.hass, discover_from)

        satellite_options = [
            selector.SelectOptionDict(value=e, label=e) for e in all_satellites
        ]

        if user_input is not None:
            errors = _validate(user_input, per_sat_sounds)
            if not errors:
                return self.async_create_entry(
                    title="Voice Alarms",
                    data=_normalise(user_input),
                )

        defaults: dict[str, Any] = user_input or {}
        schema = _build_schema(satellite_options, sound_options, defaults)

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return VoiceAlarmsOptionsFlow()


class VoiceAlarmsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Voice Alarms.

    self.config_entry is injected by the framework before async_step_init is
    called.  Do not assign it in __init__; it is a read-only property.
    Options written here override the corresponding data keys at runtime.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        entry = self.config_entry

        def _cv(key: str, default: Any = None) -> Any:
            """Current effective value: options override data."""
            return entry.options.get(key, entry.data.get(key, default))

        all_satellites: list[str] = self.hass.states.async_entity_ids(
            "assist_satellite"
        )
        discover_from: list[str] = (
            user_input.get(CONF_SATELLITE_DEVICES, [])
            if user_input is not None
            else _cv(CONF_SATELLITE_DEVICES, [])
        )
        sound_options, per_sat_sounds = await _discover_sounds(self.hass, discover_from)

        satellite_options = [
            selector.SelectOptionDict(value=e, label=e) for e in all_satellites
        ]

        if user_input is not None:
            errors = _validate(user_input, per_sat_sounds)
            if not errors:
                return self.async_create_entry(data=_normalise(user_input))

        # Preserve submitted values on re-render after errors; use current
        # config on the initial render.
        defaults: dict[str, Any] = user_input or {
            CONF_MAX_RING_TIME: _cv(CONF_MAX_RING_TIME, DEFAULT_MAX_RING_TIME),
            CONF_SATELLITE_DEVICES: _cv(CONF_SATELLITE_DEVICES, []),
            CONF_LOCAL_SATELLITES: _cv(CONF_LOCAL_SATELLITES, []),
            CONF_ALARM_MODE: _cv(CONF_ALARM_MODE, DEFAULT_ALARM_MODE),
            CONF_SOUND: _cv(CONF_SOUND, DEFAULT_SOUND),
        }
        schema = _build_schema(satellite_options, sound_options, defaults)

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
