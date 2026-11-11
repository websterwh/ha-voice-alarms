"""Execute the package's real alarm lambdas with ArduinoJson and a stub clock.

Set ARDUINOJSON_INCLUDE to ArduinoJson 7's src directory to run this test.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class FirmwareLoader(yaml.SafeLoader):
    pass


FirmwareLoader.add_multi_constructor(
    "!", lambda loader, tag, node: loader.construct_scalar(node)
)

PREAMBLE = r"""
#include <ArduinoJson.h>
#include <array>
#include <cassert>
#include <cstdlib>
#include <ctime>
#include <string>

#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGD(...) ((void)0)
#define id(x) x

struct ESPTime {
  int year, month, day_of_month, hour, minute, second, day_of_week;
  time_t timestamp;
  bool is_valid() const { return year >= 2026; }
};
struct Clock {
  ESPTime value{};
  ESPTime now() const { return value; }
} va_clock;
struct Switch {
  bool state = false;
  void turn_on() { state = true; }
  void turn_off() { state = false; }
} timer_ringing;
struct Script {
  int count = 0;
  void execute() { ++count; }
} va_ring_alarm;

std::array<char, 4096> va_alarms_json{};
std::array<char, 2048> va_fired_json{};

JsonDocument response;
JsonObject root = response.to<JsonObject>();

void set_now(int year, int month, int day, int hour, int minute, int second) {
  std::tm value{};
  value.tm_year = year - 1900;
  value.tm_mon = month - 1;
  value.tm_mday = day;
  value.tm_hour = hour;
  value.tm_min = minute;
  value.tm_sec = second;
  value.tm_isdst = -1;
  std::time_t stamp = std::mktime(&value);
  va_clock.value = {year, month, day, hour, minute, second, value.tm_wday + 1, stamp};
}

void reset_state() {
  va_alarms_json.fill(0);
  va_fired_json.fill(0);
  va_ring_alarm.count = 0;
  timer_ringing.state = false;
}

size_t alarm_count() {
  JsonDocument doc;
  if (deserializeJson(doc, va_alarms_json.data()) || !doc["alarms"].is<JsonArray>()) return 0;
  return doc["alarms"].as<JsonArray>().size();
}

void alarm_set(const std::string &at, const std::string &days, const std::string &label) {
"""

EPILOGUE = r"""
}

int main() {
  setenv("TZ", "America/Los_Angeles", 1);
  tzset();
  reset_state();

  // Add, normalise and de-duplicate alarms.
  set_now(2026, 10, 23, 6, 0, 0);  // Friday
  alarm_set("7 AM", "", "");
  assert(alarm_count() == 1);
  alarm_set("07:00", "daily", "Wake");
  assert(alarm_count() == 1);
  alarm_set("6:30", "weekdays", "");
  assert(alarm_count() == 2);
  alarm_set("25:00", "daily", "");
  alarm_set("07:00", "sometimes", "");
  alarm_set("07:60", "daily", "");
  assert(alarm_count() == 2);

  // A daily alarm rings once in its minute, even across repeated ticks.
  set_now(2026, 10, 23, 7, 0, 5);
  schedule_tick();
  assert(va_ring_alarm.count == 1);
  schedule_tick();
  assert(va_ring_alarm.count == 1);

  // Weekday alarms stay quiet at the weekend, daily alarms still ring.
  set_now(2026, 10, 24, 7, 0, 5);  // Saturday
  schedule_tick();
  assert(va_ring_alarm.count == 2);

  // Weekday alarm rings on Friday at 06:30.
  set_now(2026, 10, 23, 6, 30, 5);
  schedule_tick();
  assert(va_ring_alarm.count == 3);

  // Weekend alarm rings on Saturday and not on Friday.
  set_now(2026, 10, 24, 8, 0, 0);
  alarm_set("08:00", "weekends", "");
  schedule_tick();
  assert(va_ring_alarm.count == 4);
  set_now(2026, 10, 23, 8, 0, 0);
  schedule_tick();
  assert(va_ring_alarm.count == 4);

  // One-time alarm: schedules the next occurrence, then removes itself.
  reset_state();
  set_now(2026, 10, 23, 6, 0, 0);
  alarm_set("07:00", "once", "");
  assert(alarm_count() == 1);
  schedule_tick();
  assert(va_ring_alarm.count == 0);
  set_now(2026, 10, 23, 7, 0, 10);
  schedule_tick();
  assert(va_ring_alarm.count == 1);
  assert(alarm_count() == 1);
  set_now(2026, 10, 23, 7, 1, 0);
  schedule_tick();
  assert(alarm_count() == 0);

  // A one-time alarm requested inside its own minute targets tomorrow.
  reset_state();
  set_now(2026, 10, 25, 7, 0, 20);
  alarm_set("07:00", "once", "");
  schedule_tick();
  assert(va_ring_alarm.count == 0);
  set_now(2026, 10, 26, 7, 0, 10);
  schedule_tick();
  assert(va_ring_alarm.count == 1);

  // Delete by time, by id and everything.
  reset_state();
  set_now(2026, 10, 27, 6, 0, 0);
  alarm_set("06:00", "daily", "");
  alarm_set("07:00", "daily", "");
  alarm_delete("", "07:00");
  assert(alarm_count() == 1);
  alarm_delete("alarm-999", "");
  assert(alarm_count() == 1);
  alarm_set("09:00", "weekdays", "Work");
  alarm_list();
  assert(response["count"] == 2);
  assert(response["alarms"].as<JsonArray>().size() == 2);
  alarm_delete("", "");
  assert(alarm_count() == 0);

  // A clock that has not synchronised cannot accept alarms.
  reset_state();
  va_clock.value = ESPTime{};
  alarm_set("07:00", "daily", "");
  assert(alarm_count() == 0);
}
"""


def test_alarm_lambdas_compile_and_run(tmp_path):
    include = os.environ.get("ARDUINOJSON_INCLUDE")
    if not include or not (Path(include) / "ArduinoJson.h").is_file():
        pytest.skip("Set ARDUINOJSON_INCLUDE to ArduinoJson 7's src directory")

    config = yaml.load(
        (ROOT / "esphome" / "voice_alarms.yaml").read_text(), Loader=FirmwareLoader
    )
    actions = {item["action"]: item for item in config["api"]["actions"]}
    alarm_set = actions["alarm_set"]["then"][0]["lambda"]
    alarm_delete = actions["alarm_delete"]["then"][0]["lambda"]
    alarm_list = actions["alarm_list"]["then"][0]["api.respond"]["data"]
    schedule = config["interval"][0]["then"][0]["lambda"]

    source = (
        PREAMBLE
        + alarm_set
        + "\n}\n\nvoid alarm_delete(const std::string &alarm_id, const std::string &at) {\n"
        + alarm_delete
        + "\n}\n\nvoid alarm_list() {\n"
        + alarm_list
        + "\n}\n\nvoid schedule_tick() {\n"
        + schedule
        + EPILOGUE
    )

    cpp = tmp_path / "alarm_test.cpp"
    cpp.write_text(source)
    binary = tmp_path / "alarm_test"
    build = subprocess.run(
        ["g++", "-std=c++20", "-I", include, str(cpp), "-o", str(binary)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
