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


def test_package_does_not_depend_on_the_home_assistant_integration():
    """The firmware must keep scheduling and ringing with Home Assistant offline."""
    text = PACKAGE.read_text()
    for forbidden in (
        "custom_components",
        "voice_alarms_sync",
        "voice_alarms_stop",
        "voice_alarms.command",
        "assist_satellite",
        "payload:",
    ):
        assert forbidden not in text


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
    # ESPHome only declares the response JsonObject `root` inside api.respond's data lambda.
    response = actions["alarm_list"]["then"][0]["api.respond"]["data"]
    assert 'root["alarms"]' in response and 'root["count"]' in response

    assert config["binary_sensor"][0]["id"] == "va_alarm_ringing"
    assert config["button"][0]["id"] == "va_test_alarm_button"
    assert config["number"][0]["id"] == "va_alarm_volume"
    assert config["number"][1]["id"] == "va_alarm_timeout_minutes"
    assert config["select"][0]["id"] == "voice_alarms_sound"
    assert "va_max_ring_time" not in config["substitutions"]
    assert config["substitutions"]["va_alarm_volume"] == "0.85"
    assert any(script["id"] == "va_boost_alarm_volume" for script in config["script"])


def test_alarm_entities_are_grouped_on_the_main_device():
    config = load_package()
    assert "devices" not in config.get("esphome", {})

    alarm_entities = [
        entity
        for section in ("number", "select", "binary_sensor", "button")
        for entity in config[section]
        if entity["name"].startswith("Alarm")
    ]
    assert len(alarm_entities) == 5
    for entity in alarm_entities:
        assert "device_id" not in entity, entity
        assert entity["entity_category"] == "config", entity


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


def test_alarm_volume_is_boosted_and_restored():
    config = load_package()
    volume = config["number"][0]
    assert volume["min_value"] == 0.4
    assert volume["max_value"] == 0.85


def test_alarm_timeout_is_a_runtime_number():
    config = load_package()
    timeout = config["number"][1]
    assert timeout["name"] == "Alarm Timeout"
    assert timeout["min_value"] == 1
    assert timeout["max_value"] == 30
    assert timeout["step"] == 1
    assert timeout["unit_of_measurement"] == "min"
    assert timeout["restore_value"] is True
    assert timeout["initial_value"] == 10

    script = next(script for script in config["script"] if script["id"] == "va_alarm_timeout")
    assert "va_alarm_timeout_minutes" in script["then"][0]["delay"]


def test_alarm_sound_cannot_restart_after_stop():
    config = load_package()
    script = next(script for script in config["script"] if script["id"] == "va_play_alarm_sound")
    assert "!id(timer_ringing).state" in script["then"][0]["lambda"]

    ring = next(script for script in config["script"] if script["id"] == "va_ring_alarm")
    actions = ring["then"][0]["if"]["then"]
    assert {"script.execute": "va_boost_alarm_volume"} in actions
    # Ringing must not go through the switch action, which would start Voice
    # PE's own timer chime and its hard 15 minute stop.
    assert {"switch.turn_on": "timer_ringing"} not in actions
    assert any("publish_state(true)" in (action.get("lambda") or "") for action in actions)
    assert {"script.execute": "enable_repeat_one"} in actions
    assert {"script.execute": "va_alarm_timeout"} in actions
    assert all("delay" not in action for action in actions)
    assert sum(action == {"script.execute": "va_play_alarm_sound"} for action in actions) == 1

    boost = next(
        script for script in config["script"] if script["id"] == "va_boost_alarm_volume"
    )["then"][0]["if"]["then"]
    assert "va_previous_volume" in boost[0]["lambda"]
    assert "set_volume(target)" in boost[0]["lambda"]
    assert boost[1]["wait_until"]["condition"] == {"switch.is_off": "timer_ringing"}
    assert "set_volume(id(va_previous_volume))" in boost[2]["lambda"]


def test_validation_fixture_uses_the_package_and_local_sounds():
    fixture = yaml.load(
        (ROOT / "tests" / "voice_pe_validation.yaml").read_text(), Loader=PackageLoader
    )
    assert fixture["packages"]["voice_alarms"] == "../esphome/voice_alarms.yaml"
    assert fixture["substitutions"]["va_sounds_url"] == "../sounds"
