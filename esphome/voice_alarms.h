// Voice Alarms ESPHome support
// Add this to your ESPHome config:
// esphome:
//   includes:
//     - voice_alarms.h
//   platformio_options:
//     build_flags: -std=gnu++17

#pragma once

#include <vector>

struct VaAlarm {
  uint8_t hour;           // 0-23
  uint8_t minute;         // 0-59
  uint8_t repeat_mask;    // bit0=Mon ... bit6=Sun, 0=once
  uint8_t tone;           // index into tone table
};