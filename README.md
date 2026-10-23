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
3. Add `https://github.com/william/ha-voice-alarms` as category "Integration"
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
- **Voice PE Device ID**: The device_id of your Voice PE satellite
- **Bedroom Echo Device ID**: The device_id of your Bedroom Echo satellite

Find device IDs in **Settings → Devices & Services → Devices** → click device → copy "Device ID".

## Blueprints

The integration provides three blueprints (installed automatically):

### Voice Alarm: Set
Triggers on conversation commands:
- "Set alarm for 7 AM"
- "Set daily alarm for 6:30"
- "Wake me up at 8 AM on weekdays"
- "Set weekend alarm for 9 AM"

Stores alarms as `HH:MM<repeat><device>` in `input_text.voice_alarms`:
- Repeat codes: `o`=once, `d`=daily, `w`=weekdays, `e`=weekends
- Device codes: `V`=Voice PE, `E`=Bedroom Echo
- Example: `07:00wV` = 7 AM weekdays on Voice PE

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

## Architecture

```
input_text.voice_alarms  →  Comma-separated HH:MM<repeat><device> entries
input_boolean.voice_pe_ringing     →  Voice PE currently ringing
input_boolean.bedroom_echo_ringing →  Bedroom Echo currently ringing
```

All logic runs in blueprints → no custom Python code at runtime → fully inspectable in HA UI.

## Compatibility

- Home Assistant 2026.10+
- Works with any `assist_satellite` device
- No LLM, no cloud dependencies
- Local Wyoming + Kokoro pipeline

## License

MIT