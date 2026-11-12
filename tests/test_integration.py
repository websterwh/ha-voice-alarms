"""Structural checks for the Voice Alarms Home Assistant integration.

Home Assistant is not installed in the test environment, so these tests parse
the integration sources instead of importing them.
"""

import ast
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "voice_alarms"
INTENTS = COMPONENT / "intents.py"
FALLBACK = "Sorry, I didn't get that."


class BlueprintLoader(yaml.SafeLoader):
    """Load blueprint inputs as plain scalars."""


BlueprintLoader.add_multi_constructor(
    "!", lambda loader, tag, node: loader.construct_scalar(node)
)


def test_manifest_matches_the_component():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == COMPONENT.name == "voice_alarms"
    assert manifest["config_flow"] is True
    assert manifest["version"]
    assert manifest["requirements"] == []
    assert "conversation" in manifest["dependencies"]
    assert "esphome" in manifest["after_dependencies"]


def test_every_python_file_parses():
    files = sorted(COMPONENT.rglob("*.py"))
    assert files
    for path in files:
        ast.parse(path.read_text(), filename=str(path))


def test_config_flow_targets_an_esphome_alarm_device():
    source = (COMPONENT / "config_flow.py").read_text()
    assert "async_discover_prefixes" in source
    assert "CONF_PREFIX" in source
    assert "async_set_unique_id" in source
    assert "_abort_if_unique_id_configured" in source
    assert 'reason="already_configured" if prefixes else "no_devices"' in source
    assert "_async_current_entries" in source
    assert "prefix not in configured" in source

    api = (COMPONENT / "api.py").read_text()
    assert 'endswith(f"_{ALARM_SET}")' in api
    assert "ALARM_ACTIONS" in api
    assert "parent_device_id" in api


def test_assist_phrases_cover_every_operation():
    source = INTENTS.read_text()
    assert "register_trigger" in source
    assert "set [a|an|my] alarm" in source
    assert "weekly" not in source
    for repeat in ("once", "daily", "weekdays", "weekends"):
        assert f'"{repeat}": [' in source
    for phrase in (
        "wake me up",
        "list [my] alarms",
        "cancel|delete|remove|clear",
        "(stop|dismiss|silence|turn off|shut off)",
        "test [the|my] alarm[s]",
    ):
        assert phrase in source


def test_commands_map_to_the_package_actions():
    source = INTENTS.read_text()
    assert '"alarm_set"' in source
    assert '"alarm_delete"' in source
    assert '"alarm_stop"' in source
    assert '"alarm_test"' in source
    assert '"alarm_list"' in (COMPONENT / "api.py").read_text()
    assert '"days": repeat_value' in source
    # The generated ESPHome actions mark every variable as required, so the
    # calls must send all of them even when the value is empty.
    assert '"label": ""' in source
    assert '"alarm_id": ""' in source
    assert "async_has_alarm" in source
    # A stored alarm is verified with the device before claiming success.
    assert "if not await api.async_has_alarm(*parsed, repeat_value)" in source


def test_unparsed_or_failed_commands_use_the_short_fallback():
    assert FALLBACK in (COMPONENT / "const.py").read_text()
    source = INTENTS.read_text()
    assert source.count("return FALLBACK_RESPONSE") >= 4
    assert "except HomeAssistantError" in source
    assert "return FALLBACK_RESPONSE" in source.split("except HomeAssistantError", 1)[1]


def test_times_are_parsed_and_normalized():
    source = INTENTS.read_text()
    assert "def normalize_time" in source
    assert "p.m." in source or "a.m." in source
    assert "def parse_time" in source
    assert "hour += 12" in source
    assert "hour = 0" in source


def test_command_service_and_strings_are_documented():
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    command = services["command"]
    assert set(command["fields"]) == {"satellite", "operation", "time", "repeat"}
    assert command["fields"]["operation"]["required"] is True
    assert command["fields"]["operation"]["selector"]["select"]["options"] == [
        "set",
        "stop",
        "cancel",
        "cancel_all",
        "list",
        "test",
    ]

    strings = json.loads((COMPONENT / "strings.json").read_text())
    translations = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert strings == translations
    assert "user" in strings["config"]["step"]
    assert "no_devices" in strings["config"]["abort"]


def test_integration_serves_the_restored_blueprints():
    source = (COMPONENT / "__init__.py").read_text()
    assert "SERVICE_COMMAND" in source
    assert "SupportsResponse.OPTIONAL" in source
    assert 'return {"response": response}' in source

    blueprints = sorted((ROOT / "blueprints" / "automation").glob("*.yaml"))
    assert len(blueprints) == 2
    for path in blueprints:
        blueprint = yaml.load(path.read_text(), Loader=BlueprintLoader)
        assert blueprint["blueprint"]["domain"] == "automation"
        assert "voice_alarms.command" in path.read_text()


def test_hacs_metadata_is_present():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"] == "Voice Alarms"
    assert hacs["homeassistant"]
