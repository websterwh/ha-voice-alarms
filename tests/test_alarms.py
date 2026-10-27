"""Regression tests against real Home Assistant types, without a live installation."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.voice_alarms import (
    AlarmCoordinator,
    async_setup_entry,
    async_unload_entry,
    async_update_options,
)
from custom_components.voice_alarms.models import Alarm, new_alarm, parse_time

ZONE = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 23, 7, 0, 12, tzinfo=ZONE)
BED = "assist_satellite.bedroom"
DESK = "assist_satellite.desk"


@pytest_asyncio.fixture
async def coordinator(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="test",
        data={"satellite_devices": [BED, DESK], "max_ring_time": 30},
        options={},
    )
    result = AlarmCoordinator(hass, entry)
    result.async_save = AsyncMock()
    return result


@pytest.mark.parametrize(
    "text,expected",
    [
        ("7 AM", (7, 0)),
        ("12 am", (0, 0)),
        ("12 PM", (12, 0)),
        ("6:30 pm", (18, 30)),
        ("23:59", (23, 59)),
        ("7", (7, 0)),
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize(
    "text", ["", "24:00", "13 PM", "7:60", "-1:00", "tomorrow", "7 AM garbage"]
)
def test_invalid_times_fail(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_once_rolls_forward_and_round_trips():
    alarm = new_alarm(BED, "7 AM", "once", NOW)
    assert alarm.once_at == NOW.replace(day=24, second=0).timestamp()
    assert Alarm.from_dict(alarm.to_dict()) == alarm
    assert not alarm.due(NOW)
    assert alarm.due(NOW.replace(day=24))


@pytest.mark.parametrize(
    "repeat,day,expected",
    [
        ("daily", 24, True),
        ("weekdays", 23, True),
        ("weekdays", 24, False),
        ("weekends", 24, True),
        ("weekends", 23, False),
    ],
)
def test_repeat_day_boundaries(repeat, day, expected):
    alarm = new_alarm(BED, "7 AM", repeat, NOW - timedelta(hours=1))
    assert alarm.due(NOW.replace(day=day)) is expected
    assert not alarm.due(NOW.replace(day=day, minute=1))
    alarm.last_fired = NOW.replace(day=day).strftime("%Y-%m-%dT%H:%M")
    assert not alarm.due(NOW.replace(day=day))


@pytest.mark.asyncio
async def test_set_and_cancel_are_satellite_isolated(coordinator):
    with patch("custom_components.voice_alarms.dt_util.now", return_value=NOW):
        for satellite in (BED, DESK):
            result = await coordinator.async_command(
                {"satellite": satellite, "operation": "set", "time": "8 AM"}
            )
            assert result["response"] == "Alarm set for 08:00."
        await coordinator.async_command({"satellite": BED, "operation": "cancel_all"})
    assert [alarm.satellite for alarm in coordinator.alarms] == [DESK]


@pytest.mark.asyncio
async def test_duplicate_and_ambiguous_cancel_do_not_mutate(coordinator):
    with patch("custom_components.voice_alarms.dt_util.now", return_value=NOW):
        for time in ("8 AM", "9 AM"):
            await coordinator.async_command(
                {"satellite": BED, "operation": "set", "time": time}
            )
        result = await coordinator.async_command(
            {"satellite": BED, "operation": "set", "time": "8 AM"}
        )
        assert "already" in result["response"]
        result = await coordinator.async_command(
            {"satellite": BED, "operation": "cancel"}
        )
        assert "which" in result["response"]
    assert len(coordinator.alarms) == 2


@pytest.mark.asyncio
async def test_failed_local_sync_rolls_back_set(coordinator):
    coordinator.local = {BED}
    coordinator.async_native = AsyncMock(side_effect=HomeAssistantError("offline"))
    with (
        patch("custom_components.voice_alarms.dt_util.now", return_value=NOW),
        pytest.raises(HomeAssistantError),
    ):
        await coordinator.async_command(
            {"satellite": BED, "operation": "set", "time": "8 AM"}
        )
    assert not coordinator.alarms
    assert coordinator.async_save.await_count == 2


@pytest.mark.asyncio
async def test_local_payload_and_retry_after_failure(coordinator):
    coordinator.local = {BED}
    coordinator.alarms = [new_alarm(sat, "8 AM", "once", NOW) for sat in (BED, DESK)]
    coordinator.async_native = AsyncMock(
        side_effect=[HomeAssistantError("offline"), None]
    )
    with pytest.raises(HomeAssistantError):
        await coordinator.async_sync(BED)
    assert BED not in coordinator.last_payload
    await coordinator.async_sync(BED)
    payload = json.loads(coordinator.async_native.call_args.args[2]["payload"])
    assert len(payload["alarms"]) == 1
    assert payload["alarms"][0]["id"] == coordinator.alarms[0].id
    assert "last_fired" not in payload["alarms"][0]
    assert payload["sound"] == "Timer finished"
    assert payload["max_ring_time"] == 30
    await coordinator.async_sync(BED)
    assert coordinator.async_native.await_count == 2


@pytest.mark.asyncio
async def test_due_dispatch_once_and_local_never_double_rings(coordinator):
    coordinator.local = {BED}
    coordinator.async_native = AsyncMock()
    from unittest.mock import Mock

    coordinator.start_ringing = Mock()
    coordinator.alarms = [
        new_alarm(sat, "7 AM", "once", NOW - timedelta(hours=1)) for sat in (BED, DESK)
    ]
    with patch("custom_components.voice_alarms.dt_util.now", return_value=NOW):
        await coordinator.async_tick(NOW)
        await coordinator.async_tick(NOW)
    coordinator.start_ringing.assert_called_once_with(DESK)
    assert len(coordinator.alarms) == 2
    with patch(
        "custom_components.voice_alarms.dt_util.now",
        return_value=NOW + timedelta(seconds=90),
    ):
        await coordinator.async_tick(NOW)
    assert not coordinator.alarms
    assert (
        json.loads(coordinator.async_native.call_args.args[2]["payload"])["alarms"]
        == []
    )


@pytest.mark.asyncio
async def test_options_override_initial_data(tmp_path):
    entry = SimpleNamespace(
        entry_id="test",
        data={"sound": "Timer finished", "max_ring_time": 30},
        options={"sound": "alarm_bell.wav", "max_ring_time": 2},
    )
    coordinator = AlarmCoordinator(HomeAssistant(str(tmp_path)), entry)
    assert coordinator.sound == "alarm_bell.wav"
    assert coordinator.max_minutes == 2


@pytest.mark.asyncio
async def test_native_service_uses_registry_and_real_node_name(coordinator):
    entry = SimpleNamespace(
        domain="esphome",
        runtime_data=SimpleNamespace(device_info=SimpleNamespace(name="real-node")),
    )
    registered = SimpleNamespace(config_entry_id="esphome_entry")
    coordinator.hass.config_entries = SimpleNamespace(async_get_entry=lambda key: entry)
    with patch.object(
        er,
        "async_get",
        return_value=SimpleNamespace(async_get=lambda entity: registered),
    ):
        with pytest.raises(ServiceValidationError):
            coordinator.native_name(BED, "voice_alarms_sync")
        coordinator.hass.services.async_register(
            "esphome", "real_node_voice_alarms_sync", AsyncMock()
        )
        assert (
            coordinator.native_name(BED, "voice_alarms_sync")
            == "real_node_voice_alarms_sync"
        )


@pytest.mark.asyncio
async def test_unknown_satellite_rejected(coordinator):
    with pytest.raises(ServiceValidationError):
        await coordinator.async_command(
            {"satellite": "assist_satellite.stranger", "operation": "list"}
        )


@pytest.mark.asyncio
async def test_sync_requests_response_from_native_action(coordinator):
    handler = AsyncMock(return_value={"synced": True})
    coordinator.hass.services.async_register(
        "esphome",
        "real_node_voice_alarms_sync",
        handler,
        supports_response=SupportsResponse.ONLY,
    )
    with patch.object(
        coordinator, "native_name", return_value="real_node_voice_alarms_sync"
    ):
        await coordinator.async_native(BED, "voice_alarms_sync", {"payload": "{}"})
    assert handler.call_args.args[0].data == {"payload": "{}"}
    assert handler.call_args.args[0].return_response


@pytest.mark.asyncio
async def test_ring_timeout_cleans_up(coordinator):
    coordinator.max_minutes = 0.0001
    coordinator.async_stop_media = AsyncMock()
    coordinator.hass.services.async_register(
        "assist_satellite", "announce", AsyncMock()
    )
    task = asyncio.create_task(coordinator.async_ring(BED))
    coordinator.ringing[BED] = task
    await asyncio.wait_for(task, 1)
    assert BED not in coordinator.ringing
    coordinator.async_stop_media.assert_awaited_once_with(BED)


@pytest.mark.asyncio
async def test_real_storage_survives_coordinator_recreation(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="persist", data={"satellite_devices": [BED]}, options={}
    )
    first = AlarmCoordinator(hass, entry)
    first.alarms = [new_alarm(BED, "8 AM", "daily", NOW)]
    first.alarms[0].last_fired = NOW.strftime("%Y-%m-%dT%H:%M")
    await first.async_save()
    second = AlarmCoordinator(hass, entry)
    await second.async_load()
    assert second.alarms == first.alarms


@pytest.mark.asyncio
async def test_entry_setup_and_unload_without_nonexistent_platform(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
    )
    cleanups, tasks = [], []

    def background(hass, coro, name):
        task = asyncio.create_task(coro, name=name)
        tasks.append(task)
        return task

    entry = SimpleNamespace(
        entry_id="setup",
        data={"satellite_devices": [BED]},
        options={},
        async_on_unload=cleanups.append,
        add_update_listener=lambda listener: lambda: None,
        async_create_background_task=background,
    )
    assert await async_setup_entry(hass, entry)
    await asyncio.gather(*tasks)
    assert hass.services.has_service("voice_alarms", "command")
    response = await hass.services.async_call(
        "voice_alarms",
        "command",
        {"satellite": BED, "operation": "list"},
        blocking=True,
        return_response=True,
    )
    assert response == {"response": "No alarms are set."}
    for cleanup in cleanups:
        cleanup()
    assert await async_unload_entry(hass, entry)
    assert not hass.services.has_service("voice_alarms", "command")


@pytest.mark.asyncio
async def test_offline_local_removal_reloads_and_defers_cleanup(coordinator):
    coordinator.local = {BED}
    coordinator.async_native = AsyncMock(side_effect=HomeAssistantError("offline"))
    coordinator.hass.data["voice_alarms"] = {"test": coordinator}
    reload = AsyncMock()
    coordinator.hass.config_entries = SimpleNamespace(async_reload=reload)
    await async_update_options(coordinator.hass, coordinator.entry)
    reload.assert_awaited_once_with("test")
    assert coordinator.pending_clear == {BED}
    coordinator.async_native = AsyncMock()
    coordinator.local = set()
    await coordinator.async_sync_pending()
    assert not coordinator.pending_clear
    assert coordinator.async_native.call_args_list[0].args[1] == "voice_alarms_sync"
    assert (
        json.loads(coordinator.async_native.call_args_list[0].args[2]["payload"])[
            "alarms"
        ]
        == []
    )
    assert coordinator.async_native.call_args_list[1].args[1] == "voice_alarms_stop"
