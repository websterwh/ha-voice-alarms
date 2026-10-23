# Voice Alarms for Home Assistant

Per-device voice alarms for Home Assistant Assist using Wyoming faster-whisper, Kokoro TTS, and the built-in conversation agent with `prefer_local_intents`. Each alarm belongs to the satellite it was set on and rings only there.

## Features

- **Per-device isolation**: Alarms set on Voice PE ring on Voice PE only; alarms set on Bedroom Echo ring on Bedroom Echo only
- **Multiple alarms per device**: Set as many alarms as needed (up to 30 per device)
- **Flexible repeat patterns**: Once (default), daily, weekdays, weekends
- **Configurable ring duration**: Default 30 minutes, adjustable from 1-120 minutes
- **No snooze, no reminders**: Clean, simple alarm behavior
- **One-time alarms auto-delete**: Automatically removed after firing
- **Native HA integrations**: Uses `input_text`, `input_boolean`, blueprints, and conversation triggers
- **Dual-mode operation**: HA fallback (blueprint rings) or Local preferred (device rings locally)
- **Local alarm support on ESPHome devices**: Works fully offline on Home Assistant Voice PE and similar ESPHome satellites

## Requirements

- Home Assistant 2026.10+
- Wyoming faster-whisper (for STT)
- Kokoro TTS (for TTS)
- Built-in conversation agent with `prefer_local_intents: true`
- Two `assist_satellite` devices (e.g., Voice PE, Echo)

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click "Integrations" → three dots → "Custom repositories"
3. Add `https://github.com/websterwh/ha-voice-alarms` as category "Integration"
4. Search for "Voice Alarms" and install
5. Restart Home Assistant

### Manual

1. Copy `custom_components/voice_alarms` to your HA config directory
2. Copy `blueprints/automation/` to your HA config directory
3. Restart Home Assistant

## Configuration

After installation, go to **Settings → Devices & Services → Add Integration** and search for "Voice Alarms".

Configure:
- **Max Ring Time**: How long an alarm rings before auto-stop (1-120 minutes, default: 30)
- **Alarm Mode**: Choose **HA Fallback** (blueprint rings) or **Local Preferred** (device rings locally)
- **Satellite Devices**: Select all `assist_satellite` entities you want alarms on
- **Local Satellites** (when Local Preferred): Tick only the satellites that have the ESPHome snippet flashed
- **Tone Base URL** (optional): Leave empty for baked-in tones, or set HTTP base URL for external tones

Find device IDs in **Settings → Devices & Services → Devices** → click device → copy "Device ID".

## Blueprints

The integration provides three blueprints (installed automatically):

### Voice Alarm: Set
Triggers on conversation commands:
- "Set alarm for 7 AM"
- "Set daily alarm for 6:30"
- "Wake me up at 8 AM on weekdays"
- "Set weekend alarm for 9 AM"

Stores alarms as `HH:MM<repeat><device_index>` in `input_text.voice_alarms`:
- Repeat codes: `o`=once, `d`=daily, `w`=weekdays, `e`=weekends
- Device index: 0, 1, 2… corresponding to configured satellites order
- Example: `07:00w0` = 7 AM weekdays on first satellite

### Voice Alarm: Ring
Runs every minute via `time_pattern`. Checks for due alarms, announces on the correct satellite only, loops until "stop alarm" or max time reached. One-time alarms (`o`) are removed when they fire.

### Voice Alarm: Stop, Cancel, List
Handles all query/stop commands scoped to the asking device:
- "Stop alarm" - stops ringing on this device only
- "Cancel my alarm" - cancels one alarm (prompts if multiple)
- "Cancel the 7 AM alarm" - cancels specific time
- "Cancel all alarms" - removes all alarms for this device
- "What alarms do I have" - lists only this device's alarms

## Voice Commands

| Command | Action |
|---------|--------|
| "Set alarm for 7 AM" | Sets one-time alarm |
| "Set daily alarm for 6:30" | Repeats daily |
| "Wake me up at 8 AM on weekdays" | Weekdays only |
| "Set weekend alarm for 9 AM" | Weekends only |
| "Stop alarm" | Stops ringing on this device |
| "Cancel my alarm" | Removes one alarm |
| "Cancel the 7 AM alarm" | Removes specific time |
| "Cancel all alarms" | Clears all for this device |
| "What alarms do I have" | Lists alarms for this device |

## ESPHome Local Alarms (Home Assistant Voice PE)

For satellites running ESPHome (e.g., Home Assistant Voice PE), you can enable **fully local alarms** that work even when Home Assistant is offline.

### 1. Add the ESPHome snippet

Paste the following into your device's ESPHome YAML (after the `packages:` line).  
Place the accompanying `voice_alarms.h` file in the same directory.

```yaml
# Voice Alarms – ESPHome snippet for Home Assistant Voice PE
# Paste after the `packages:` line; place `voice_alarms.h` next to this YAML

esphome:
  includes:
    - voice_alarms.h

time:
  - platform: sntp
    id: va_clock
    servers:
      - pool.ntp.org
    timezone: "America/Los_Angeles"

globals:
  - id: va_alarms
    type: "std::vector<VaAlarm>"
    restore_value: false

script:
  - id: va_play_tone
    parameters:
      tone_id: int
    then:
      - speaker.play:
          id: speaker
          media_url: "tone_{{ tone_id }}.wav"
          volume: 0.8

interval:
  - interval: 60s
    then:
      - lambda: |
          auto now = id(va_clock).now();
          for (auto &a : id(va_alarms)) {
            bool match = (a.hour == now.hour && a.minute == now.minute);
            if (!match) continue;
            if (a.repeat_mask == 0) {
              match = true;
            } else {
              uint8_t wday = now.day_of_week;
              match = (a.repeat_mask & (1 << wday)) != 0;
            }
            if (match) {
              id(va_play_tone).execute(a.tone);
              if (a.repeat_mask == 0) {
                a = id(va_alarms).back();
                id(va_alarms).pop_back();
              }
            }
          }

voice_assistant:
  on_alarm_set:
    - lambda: |
        VaAlarm a;
        a.hour         = /* hour slot from intent */;
        a.minute       = /* minute slot */;
        a.repeat_mask  = /* compute from repeat slot (once|daily|weekdays|weekends) */;
        a.tone         = /* tone slot (0,1,2…) */;
        id(va_alarms).push_back(a);

binary_sensor:
  - platform: template
    name: "Voice Alarms Local Supported"
    id: voice_alarms_local_supported
    lambda: "return true;"
    entity_category: "diagnostic"
```

### 2. Add the C++ header

Create a file named **`voice_alarms.h`** in the same folder as your ESPHome YAML:

```cpp
// voice_alarms.h – place next to the .yaml file
#pragma once

struct VaAlarm {
  uint8_t hour;           // 0-23
  uint8_t minute;         // 0-59
  uint8_t repeat_mask;    // bit0=Mon … bit6=Sun, 0 = one-shot
  uint8_t tone;           // index into tone_<n>.wav
};
```

### 3. Add alarm tone files

Put your `.wav` files in the ESPHome `data/` directory:

```
esphome/
  data/
    tone_0.wav
    tone_1.wav
    tone_2.wav
    …
```

The script uses `media_url: "tone_{{ tone_id }}.wav"` – the `tone_id` stored in the alarm (0, 1, 2…) selects the file.

### 4. Enable in Home Assistant

1. Re-compile & flash the Voice PE.
2. In HA, update the HACS repo, reload **Voice Alarms**.
3. Choose **Alarm Mode → Local Preferred**.
4. Under **Local Satellites**, tick the Voice PE.
5. Test: “Hey <satellite>, set alarm for 7 am”. The device stores the alarm locally and rings at 07:00 even if HA is offline.

## Architecture

```
input_text.voice_alarms  →  Comma-separated HH:MM<repeat><device_index> entries
input_boolean.<satellite>_ringing  →  Per-satellite ringing flags (HA fallback)
```

- **HA Fallback mode**: Blueprints drive `assist_satellite.announce` from HA.
- **Local Preferred mode**: Integration pushes alarm list to ESPHome devices via `esphome.send_command`; devices ring locally using their own speaker.

All logic runs in blueprints → no custom Python code at runtime → fully inspectable in HA UI.

## Compatibility

- Home Assistant 2026.10+
- Works with any `assist_satellite` device
- Local alarms require ESPHome device with speaker (Voice PE, custom ESPHome builds)
- No LLM, no cloud dependencies
- Local Wyoming + Kokoro pipeline

## License

MIT