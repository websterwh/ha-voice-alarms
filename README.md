# Voice Alarms for Home Assistant Voice PE

On-device alarms for the [Home Assistant Voice PE](https://github.com/esphome/home-assistant-voice-pe), packaged as a single ESPHome package. There is no Home Assistant custom integration, no HACS repository, no helper entities, and no cloud dependency.

Alarms are stored in the device's flash, evaluated against the device clock, and ring locally. Ringing uses Voice PE's own timer machinery, so the stop wake word, center button, LED ring, ducking and automatic stop all keep working, including while Home Assistant is offline.

## Install

Requires ESPHome 2026.10 or newer and the Voice PE package. Add this package next to your existing device configuration and set the substitutions you want:

```yaml
substitutions:
  va_timezone: America/Los_Angeles
  va_default_sound: Nightstand
  va_max_ring_time: "10"

packages:
  Nabu Casa.Home Assistant Voice PE: github://esphome/home-assistant-voice-pe/home-assistant-voice.yaml@d4e6fa43d6a1a7d342b7d4d403567e06516719de
  voice_alarms: github://websterwh/ha-voice-alarms/esphome/voice_alarms.yaml@master
```

Keep exactly one copy of the upstream Voice PE package and merge these keys into your existing `substitutions:` and `packages:` mappings rather than adding duplicate top-level keys. Pin `voice_alarms` to a release tag instead of `@master` once you have one. Compile and flash the device.

If you use a local copy of this repository, replace the second package line with `voice_alarms: !include esphome/voice_alarms.yaml`.

## Package options

| Substitution | Default | Purpose |
| --- | --- | --- |
| `va_timezone` | `America/Los_Angeles` | Timezone used for alarm evaluation. Must match your local time. |
| `va_default_sound` | `Simple Alarm` | Sound selected on a fresh device. Use any name from the list below. |
| `va_max_ring_time` | `10` | Minutes before the alarm stops itself. Voice PE also caps ringing at 15 minutes. |
| `va_sounds_url` | This repository's `sounds/` folder | Base URL for embedded sounds. Override to use a local or mirrored copy. |

Changing a substitution requires rebuilding and flashing the firmware. The sound can also be changed at runtime from the **Alarm Sound** select entity and survives a reboot.

## Sounds

Seventeen sounds are bundled in `sounds/` and embedded at build time:

`Adrift`, `Clangy`, `Clarity`, `Countertop`, `Focus`, `Glimmer`, `Grand Tour`, `Grand Tour Season 2`, `Nightstand`, `Nimble`, `Orcas`, `Porchlight`, `Pulsar`, `Rainier`, `Simple Alarm`, `Simple Timer`, `Squared Waves`.

The files are mono 22.05 kHz, 64 kbps MP3 versions of the originals, about 1.9 MB total, so the firmware stays within the Voice PE application partition. They are downloaded at build time, not at ring time.

## Managing alarms

With the device added to Home Assistant over the ESPHome integration, each action is available as an `esphome.<device_name>_<action>` service. The same actions are exposed on the native API, so any ESPHome API client can use them.

### Add an alarm

```yaml
action: esphome.voice_kit_alarm_set
data:
  at: "07:00"
  days: weekdays
  label: Wake up
```

| Field | Required | Description |
| --- | --- | --- |
| `at` | yes | `HH:MM` in 24-hour time. `7 AM`, `7:30 pm`, `430 PM` and a bare `7` are also accepted. |
| `days` | no | `once`, `daily` (default), `weekdays` or `weekends`. `weekday`/`weekend` are accepted too. |
| `label` | no | Optional name, up to 32 characters, returned by `alarm_list`. |

Alarms are limited to 30 per device. Adding the same time and repeat again updates the existing alarm instead of duplicating it. A `once` alarm targets the next occurrence of that time; if you add it during its own minute, it targets tomorrow.

### Delete, list, stop and test

```yaml
# Delete by time, by id, or everything at once.
action: esphome.voice_kit_alarm_delete
data:
  at: "07:00"
```

```yaml
action: esphome.voice_kit_alarm_delete
data:
  alarm_id: alarm-1
```

Omit both fields to delete all alarms.

```yaml
action: esphome.voice_kit_alarm_list
response_variable: alarms
```

The response contains `count`, `accepted` and an `alarms` array of stored alarm objects.

```yaml
action: esphome.voice_kit_alarm_stop
```

```yaml
action: esphome.voice_kit_alarm_test
```

`alarm_test` rings the selected sound without storing an alarm. The **Test Alarm** button does the same from the device page, and the **Alarm Ringing** binary sensor shows when an alarm is ringing.

### Voice control

The package does not register Assist sentences. To set alarms by voice, create a Home Assistant intent or automation that parses the spoken time and calls the `alarm_set` action above. Keep the parsing in Home Assistant; the device only receives a concrete `HH:MM` value.

## Behaviour and limits

- Alarms are persisted in flash and can ring without Home Assistant, but the device must first synchronize its clock over SNTP. There is no battery-backed RTC, so alarms cannot run before a clock sync after a cold boot.
- Missed one-time alarms are not replayed. A one-time alarm is removed after its firing minute.
- Ringing reuses Voice PE's `timer_ringing` state. Stopping an alarm also stops a timer ringing at the same moment, and an alarm that comes due while a timer is already ringing is marked as fired without replacing that timer's sound.
- The stop wake word, any detected wake word, the center button, and the `alarm_stop` action all dismiss a ringing alarm. The LED ring uses Voice PE's timer-ring animation.
- The package is intended for Voice PE, because it depends on that firmware's `timer_ringing`, `play_sound`, `external_media_player` and `audio_file` definitions. It is not a generic alarm component for other ESPHome devices.
- Do not rely on this as a safety-critical alarm clock. Power loss before a flash write completes can lose the most recent change.

## Validation

```bash
python -m pytest tests
```

`tests/test_package.py` checks the package structure, the sound bundle and the option-to-file mapping. `tests/test_alarms.py` compiles the actual scheduler, add, delete and list lambdas for the host with ArduinoJson and exercises them with a stub clock. Set `ARDUINOJSON_INCLUDE` to the `src` directory of an ArduinoJson 7 checkout before running it, for example:

```bash
git clone --depth 1 --branch v7.4.3 https://github.com/bblanchon/ArduinoJson.git /tmp/ArduinoJson
ARDUINOJSON_INCLUDE=/tmp/ArduinoJson/src python -m pytest tests
```

To validate the full firmware configuration, use ESPHome in a separate environment from the test dependencies:

```bash
esphome config tests/voice_pe_validation.yaml
esphome compile tests/voice_pe_validation.yaml
```

The fixture uses the local `sounds/` directory instead of downloading the published files. Neither command flashes a device. Physical audio, microphone interaction, wake-word stopping and flash recovery still need a check on real hardware.

## License

MIT
