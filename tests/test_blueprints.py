from pathlib import Path

import hassil
import pytest
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    PLATFORM_SCHEMA,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import load_yaml


@pytest.mark.parametrize(
    "filename", ["voice_alarm_set.yaml", "voice_alarm_stop_and_query.yaml"]
)
@pytest.mark.asyncio
async def test_real_home_assistant_blueprint_and_action_schema(filename, tmp_path):
    HomeAssistant(str(tmp_path))
    data = load_yaml(str(Path("blueprints/automation") / filename))
    blueprint = Blueprint(
        data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    inputs = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": filename,
                "input": {"satellite_devices": ["assist_satellite.bedroom"]},
            }
        },
    )
    inputs.validate()
    config = PLATFORM_SCHEMA(inputs.async_substitute())
    assert (
        config["actions"][-1]["set_conversation_response"].template
        == "{{ result.response }}"
    )


@pytest.mark.parametrize(
    "filename,phrase,expected,slot",
    [
        ("voice_alarm_set.yaml", "set alarm for 7 AM", "once", "7 AM"),
        ("voice_alarm_set.yaml", "set daily alarm for 6:30 AM", "daily", "6:30 AM"),
        ("voice_alarm_set.yaml", "set weekday alarm for 8 AM", "weekdays", "8 AM"),
        ("voice_alarm_set.yaml", "set weekend alarm for 9 AM", "weekends", "9 AM"),
        ("voice_alarm_stop_and_query.yaml", "stop alarm", "stop", None),
        ("voice_alarm_stop_and_query.yaml", "cancel my alarm", "cancel_one", None),
        (
            "voice_alarm_stop_and_query.yaml",
            "cancel the 7 AM alarm",
            "cancel_time",
            "7 AM",
        ),
        ("voice_alarm_stop_and_query.yaml", "cancel all alarms", "cancel_all", None),
        ("voice_alarm_stop_and_query.yaml", "what alarms do I have", "list", None),
    ],
)
def test_real_sentence_matcher(filename, phrase, expected, slot):
    data = load_yaml(str(Path("blueprints/automation") / filename))
    intents = hassil.Intents.from_dict(
        {
            "language": "en",
            "intents": {
                trigger["id"]: {"data": [{"sentences": trigger["command"]}]}
                for trigger in data["trigger"]
            },
        }
    )
    result = hassil.recognize(
        phrase, intents, slot_lists={"time": hassil.WildcardSlotList(name="time")}
    )
    assert result is not None
    assert result.intent.name == expected
    if slot is not None:
        assert result.entities["time"].value.lower() == slot.lower()
