"""Match the registered trigger sentences with real hassil, as the default agent does."""

import pytest
from hassil import Intents, recognize_all
from hassil.intents import WildcardSlotList

from custom_components.voice_alarms import voice

GROUPS = {
    **{f"set_{repeat}": s for repeat, s in voice.SET_SENTENCES.items()},
    "stop": voice.STOP_SENTENCES,
    "cancel_all": voice.CANCEL_ALL_SENTENCES,
    "cancel": voice.CANCEL_SENTENCES,
    "list": voice.LIST_SENTENCES,
}
INTENTS = Intents.from_dict(
    {
        "language": "en",
        "intents": {k: {"data": [{"sentences": s}]} for k, s in GROUPS.items()},
    }
)
INTENTS.slot_lists["time"] = WildcardSlotList("time")


def matches(text):
    found = {}
    for result in recognize_all(text, INTENTS):
        if result.intent.name in found:
            break
        found[result.intent.name] = {k: e.value for k, e in result.entities.items()}
    return found


@pytest.mark.parametrize(
    ("text", "group", "time", "repeat"),
    [
        ("Set alarm for 4.30 AM.", "set_once", "4:30 AM", "once"),
        ("set an alarm for 4:30 a.m.", "set_once", "4:30 AM", "once"),
        ("Set alarm for 103 AM", "set_once", "1:03 AM", "once"),
        ("Set alarm 2 20 AM", "set_once", "2:20 AM", "once"),
        ("wake me up at 7", "set_once", "7", "once"),
        ("set a daily alarm for 7 AM", "set_daily", "7 AM", "daily"),
        ("set a weekday alarm at 6.15 a.m.", "set_weekdays", "6:15 AM", "weekdays"),
        ("set an alarm for 7:00 every day", "set_once", "7:00", "daily"),
        ("set an alarm for 6 AM on weekends", "set_once", "6 AM", "weekends"),
    ],
)
def test_set_sentences(text, group, time, repeat):
    found = matches(text)
    assert list(found) == [group]
    spoken, parsed_repeat = voice.split_repeat(found[group]["time"], group[4:])
    assert voice.normalize_time(spoken) == time
    assert parsed_repeat == repeat


@pytest.mark.parametrize(
    ("text", "group", "time"),
    [
        ("Stop the alarm.", "stop", None),
        ("cancel the alarm", "cancel", None),
        ("cancel all alarms", "cancel_all", None),
        ("cancel all my alarms", "cancel_all", None),
        ("cancel the 7 AM alarm", "cancel", "7 AM"),
        ("cancel alarm for 7.15 PM", "cancel", "7:15 PM"),
        ("list my alarms", "list", None),
        ("what alarms do I have", "list", None),
    ],
)
def test_other_sentences_match_exactly_one_trigger(text, group, time):
    found = matches(text)
    assert list(found) == [group]
    assert voice.normalize_time(found[group].get("time", "")) == (time or "")


def test_unrelated_sentences_do_not_match():
    assert matches("stop") == {}
    assert matches("set a timer for 5 minutes") == {}
