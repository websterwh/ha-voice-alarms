from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.voice_alarms.config_flow import (
    _build_schema,
    _discover_sounds,
    _validate,
)


@pytest.mark.parametrize(
    "data,sounds,error",
    [
        ({"satellite_devices": []}, {}, "satellite_devices"),
        (
            {
                "satellite_devices": ["assist_satellite.a"],
                "local_satellites": ["assist_satellite.b"],
            },
            {},
            "local_satellites",
        ),
        (
            {
                "satellite_devices": ["assist_satellite.a"],
                "local_satellites": ["assist_satellite.a"],
            },
            {},
            "sound",
        ),
        (
            {
                "satellite_devices": ["assist_satellite.a"],
                "local_satellites": ["assist_satellite.a"],
                "sound": "Bell.wav",
            },
            {"assist_satellite.a": ["Chime.wav"]},
            "sound",
        ),
        (
            {
                "satellite_devices": ["assist_satellite.a"],
                "local_satellites": ["assist_satellite.a"],
                "sound": "Bell.wav",
            },
            {"assist_satellite.a": ["Bell.wav"]},
            None,
        ),
    ],
)
def test_config_rejects_unavailable_sounds_and_invalid_scope(data, sounds, error):
    errors = _validate(data, sounds)
    assert (error in errors) if error else errors == {}


@pytest.mark.asyncio
async def test_sound_options_discovered_from_device_registry(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(
        "select.renamed", "Bell.wav", {"options": ["Bell.wav", "Chime.wav"]}
    )
    registry = SimpleNamespace(
        async_get=lambda entity: SimpleNamespace(device_id="device-a")
    )
    select = SimpleNamespace(
        domain="select",
        unique_id="mac-select-voice_alarms_sound",
        entity_id="select.renamed",
    )
    with (
        patch(
            "custom_components.voice_alarms.config_flow.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.voice_alarms.config_flow.er.async_entries_for_device",
            return_value=[select],
        ),
    ):
        sounds, devices = await _discover_sounds(hass, ["assist_satellite.a"])
    assert sounds == ["Bell.wav", "Chime.wav"]
    assert devices == {"assist_satellite.a": ["Bell.wav", "Chime.wav"]}


def test_schema_sound_dropdown_preserves_filenames():
    schema = _build_schema([], ["My Bell.wav"], {})
    sound = next(
        validator for key, validator in schema.schema.items() if str(key) == "sound"
    )
    assert sound("My Bell.wav") == "My Bell.wav"
