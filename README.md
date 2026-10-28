# Voice Alarms for Home Assistant

Per-device alarms for Assist satellites, without an LLM. Alarms belong to the satellite that heard the command, not its position in a list. Up to 30 alarms per satellite; once, daily, weekdays, or weekends. No snooze or reminders.

## Requirements

- Home Assistant 2026.10 or newer.
- One or more `assist_satellite` entities with distinct devices.
- An Assist pipeline with the built-in conversation agent. Local Wyoming faster-whisper and Kokoro work; this integration does not install or depend on them. Keep `prefer_local_intents` enabled.
- For device-side ringing: Home Assistant Voice PE, ESPHome 2026.10 or newer, and the package below. Other hardware needs its own tested firmware adapter; this is not a universal speaker snippet.

## Install

In HACS, add `https://github.com/websterwh/ha-voice-alarms` as a custom **Integration** repository. Install Voice Alarms and restart HA. Alternatively, copy `custom_components/voice_alarms` into your HA configuration's `custom_components` directory and restart.

Add **Voice Alarms** in Settings → Devices & services. Select all participating satellite **entities**; no device IDs or helper entities are needed. Only one integration instance is supported.

Import these blueprints, create one automation from each, and select the same satellite entities:

- [Set alarms](https://github.com/websterwh/ha-voice-alarms/blob/master/blueprints/automation/voice_alarm_set.yaml)
- [Stop, cancel, and list](https://github.com/websterwh/ha-voice-alarms/blob/master/blueprints/automation/voice_alarm_stop_and_query.yaml)

HACS does not automatically create these automations. There is no ring blueprint: the integration schedules alarms itself.

### Modes

**HA Fallback:** HA loops a spoken alarm announcement through `assist_satellite.announce`. HA, the satellite connection, STT, and TTS must be available. This mode does not use the embedded sound selector.

**Local Preferred:** manually choose the satellites flashed with the Voice PE alarm package under **Local Satellites**. Those devices ring embedded audio themselves; remaining satellites use HA announcements. HA never starts an additional announcement on a local satellite, preventing double ringing. A failed device sync is an error, not a silent fallback; reconnecting retries synchronization. Setting or cancelling a local alarm requires an acknowledged sync from the device.

**Max Ring Time** is 1–120 minutes, default 30. **Sound** is a shared named option discovered from each selected local device's Voice Alarms Sound select. All selected local devices must offer it. Initial options include `Timer finished`, `alarm_chime.wav`, and `alarm_bell.wav`. Flash and reconnect devices before selecting them for local operation.

## Voice commands

- “Set alarm for 7 AM”
- “Set daily alarm for 6:30 AM”
- “Set weekday alarm for 8 AM”
- “Set weekend alarm for 9 AM”
- “Stop alarm”
- “Cancel my alarm” — cancels the only alarm; asks for a time if there are several.
- “Cancel the 7 AM alarm” — cancels all matching alarms at that time on this device.
- “Cancel all alarms” — this device only.
- “What alarms do I have”

Use numeric times such as `7 AM`, `6:30 pm`, or `19:00`; a bare `7` means 07:00. Repeating schedules use HA's local timezone, which must match `va_timezone` on each local device. One-time alarms target the next occurrence of the requested time. Past alarms are not replayed after an outage. Completed one-time records are removed automatically after a 90-second synchronization grace period.

Commands must originate on a configured satellite. Dashboard conversations without a device origin, or ambiguous device mappings, are rejected. A device with its own separate alarm implementation (for example EchoLocal) may intercept overlapping commands: disable that competing alarm handler before using these blueprints.

## Home Assistant Voice PE firmware

Remove the old alarm snippet and `voice_alarms.h` include. Retain your Wi-Fi, API encryption, device names, and custom wake-word configuration. Merge the following **package entries and substitution** into the existing YAML; do not create duplicate top-level `packages:` or `substitutions:` keys.

```yaml
substitutions:
  va_timezone: America/Los_Angeles

packages:
  Nabu Casa.Home Assistant Voice PE: github://esphome/home-assistant-voice-pe/home-assistant-voice.yaml@d4e6fa43d6a1a7d342b7d4d403567e06516719de
  voice_alarms: github://websterwh/ha-voice-alarms/esphome/voice_alarms_snippet.yaml@v2.1.1
```

The upstream pin identifies the Voice PE package used for validation. Don't add a second copy of that upstream package under another key. The complete alarm implementation is the [downloadable ESPHome package](https://raw.githubusercontent.com/websterwh/ha-voice-alarms/master/esphome/voice_alarms_snippet.yaml); the README imports that same file instead of maintaining another implementation. No external header, I²S pin replacements, `on_alarm_set`, or `data/` directory are needed.

The package registers WAVs with `audio_file` and plays them through Voice PE's existing announcement media player and audio-file source. Downloads occur at firmware build time, not ring time. New custom sounds require adding an `audio_file` entry, selector option and playback mapping in the package, then rebuilding; arbitrary files are not automatically embedded.

Compile and flash, reconnect the device in HA, then configure Local Preferred and select it. Use Developer tools → Actions → **Voice Alarms: Test alarm**, passing the satellite entity. Stop with “stop alarm”, the physical center button, or the local stop wake word. Stock Voice PE timers remain separate.

While a local alarm rings, the LED ring uses Voice PE's native timer-ringing animation. Stopping the alarm restores the normal LED state. A detected wake word (including the usual assistant wake word) dismisses the local alarm without opening a listening session; no listening light is expected. This LED behavior requires rebuilding and flashing the updated package.

### Offline boundaries

Device schedules and per-alarm firing markers are persisted in flash. Once a clock has synchronized, schedules can ring while HA is offline. This does **not** provide a battery-backed RTC: after a cold boot, SNTP must obtain time before alarms can run. The default public SNTP server needs network access for synchronization; a reachable LAN NTP server can replace it. Voice setting/cancelling/listing still needs HA. Power loss before flash persistence completes can lose a recent update; don't rely on this as a safety-critical alarm clock.

Local ringing uses embedded sounds and a monotonic ring-duration cap. HA fallback requires the network and TTS. A sound test or compile is not proof that physical audio, microphone interaction, wake-word stopping, or flash recovery works on your particular device.

## Services and storage

- `voice_alarms.command`: `satellite`, `operation` (`set`, `stop`, `cancel`, `cancel_all`, `list`), optional `time`, optional `repeat`. Returns `{"response": "..."}`; request response data.
- `voice_alarms.sync`: optional `satellite`; resends schedules to local devices.
- `voice_alarms.test_alarm`: required `satellite`; rings until stopped or capped.

HA owns durable storage through its storage API, keyed by UUID and stable satellite entity ID. ESPHome exposes device-specific `voice_alarms_sync`, `voice_alarms_stop`, and `voice_alarms_test` native API actions. There is no `esphome.send_command` service. Do not rename satellite entity IDs with active alarms: cancel them first and update the integration and blueprints.

Changing local-device selection clears schedules from deselected connected devices before reloading. An offline device cannot be cleared remotely: its pending cleanup is persisted and retried on reconnect, and HA announcements for it are suspended to prevent double ringing. Until it reconnects, its old local schedule can still ring. Removing the integration or disabling HA does not erase device-side schedules; cancel all local alarms while connected first.

## Upgrading from the prototype

The previous helper/index format and firmware were not functional. They are not migrated. Disable/delete the old set, ring, and stop automations; update the integration and import both replacement blueprints. Remove only old alarm helpers no other automation uses. Remove the old alarm C++ header/include and snippet, install the package above, and re-enter alarms. The old `tone_base_url` option is removed. No live HA configuration is changed by this repository update.

## Validation

The repository includes Python regression tests against Home Assistant, blueprint substitution/action-schema and Hassil phrase checks, execution tests for the actual firmware C++ lambdas, and a full Voice PE build fixture at `tests/voice_pe_validation.yaml`. The fixture uses local copies of the same WAVs so firmware can be validated before publishing a release. Run HA/Python tests and ESPHome in separate environments: installing both together can force an obsolete ESPHome version through dependency resolution.

The Python suite requires Home Assistant 2026.10.2, pytest, pytest-asyncio, PyYAML and Hassil 3.12.1 under Python 3.14. For `tests/test_firmware.py`, install `g++`, clone ArduinoJson tag `v7.4.3`, and set `ARDUINOJSON_INCLUDE` to that checkout's `src` directory before running `python -m pytest tests`. Validate/build the full device separately with ESPHome 2026.10.0: `esphome config tests/voice_pe_validation.yaml` and `esphome compile tests/voice_pe_validation.yaml`. Neither command flashes a device.

Physical-device acceptance still requires setting a near-term alarm, confirming it rings only on the source device, stopping it, checking a repeating alarm, and checking an already-synchronized device with HA unavailable. Firmware is never automatically flashed by this integration.

## License

MIT
