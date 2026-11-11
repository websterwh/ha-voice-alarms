"""Structural checks for the standalone ESPHome package."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "esphome" / "voice_alarms.yaml"


class PackageLoader(yaml.SafeLoader):
    """Load ESPHome's custom tags as plain scalars."""


PackageLoader.add_multi_constructor(
    "!", lambda loader, tag, node: loader.construct_scalar(node)
)


def load_package() -> dict:
    return yaml.load(PACKAGE.read_text(), Loader=PackageLoader)


def slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def test_package_has_no_home_assistant_integration_dependencies():
    text = PACKAGE.read_text()
    for forbidden in (
        "custom_components",
        "voice_alarms_sync",
        "voice_alarms_stop",
        "assist_satellite",
        "payload:",
    ):
        assert forbidden not in text
    assert not (ROOT / "custom_components").exists()
    assert not (ROOT / "blueprints").exists()
    assert not (ROOT / "hacs.json").exists()


def test_every_sound_option_is_bundled_and_mapped():
    config = load_package()
    options = config["select"][0]["options"]
    audio_files = {entry["id"]: entry["file"] for entry in config["audio_file"]}

    assert config["select"][0]["initial_option"] == "${va_default_sound}"
    assert config["substitutions"]["va_default_sound"] in options
    assert len(options) == 17
    assert len(audio_files) == 17

    for option in options:
        filename = f"{slug(option)}.mp3"
        assert (ROOT / "sounds" / filename).is_file(), filename
        assert f"${{va_sounds_url}}/{filename}" in audio_files.values()

    mapping = next(
        script for script in config["script"] if script["id"] == "va_play_alarm_sound"
    )["then"][0]["lambda"]
    cases = [
        (int(index), key) for index, key in re.findall(r'case (\d+): key = "([^"]+)"', mapping)
    ]
    assert [index for index, _ in cases] == list(range(len(options)))
    for (_, key), option in zip(cases, options, strict=True):
        assert key == f"va_sound_{slug(option)}"
        assert f"${{va_sounds_url}}/{slug(option)}.mp3" == audio_files[key]


def test_alarm_actions_and_entities_are_present():
    config = load_package()
    actions = {entry["action"]: entry for entry in config["api"]["actions"]}

    assert set(actions) == {
        "alarm_set",
        "alarm_delete",
        "alarm_list",
        "alarm_stop",
        "alarm_test",
    }
    assert set(actions["alarm_set"]["variables"]) == {"at", "days", "label"}
    assert set(actions["alarm_delete"]["variables"]) == {"alarm_id", "at"}
    assert actions["alarm_list"]["supports_response"] == "only"

    assert config["binary_sensor"][0]["id"] == "va_alarm_ringing"
    assert config["button"][0]["id"] == "va_test_alarm_button"
    assert config["select"][0]["id"] == "voice_alarms_sound"
    assert config["substitutions"]["va_max_ring_time"] == "10"


def test_scheduler_is_on_device_and_persisted():
    config = load_package()
    assert config["time"][0]["platform"] == "sntp"
    assert config["time"][0]["timezone"] == "${va_timezone}"

    globals_by_id = {entry["id"]: entry for entry in config["globals"]}
    assert globals_by_id["va_alarms_json"]["restore_value"] is True
    assert globals_by_id["va_fired_json"]["restore_value"] is True

    scheduler = config["interval"][0]["then"][0]["lambda"]
    assert "va_alarms_json" in scheduler
    assert "va_fired_json" in scheduler
    assert "va_ring_alarm" in scheduler


def test_validation_fixture_uses_the_package_and_local_sounds():
    fixture = yaml.load(
        (ROOT / "tests" / "voice_pe_validation.yaml").read_text(), Loader=PackageLoader
    )
    assert fixture["packages"]["voice_alarms"] == "../esphome/voice_alarms.yaml"
    assert fixture["substitutions"]["va_sounds_url"] == "../sounds"
