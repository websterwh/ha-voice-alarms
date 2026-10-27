"""Execute the package's actual C++ lambdas with ArduinoJson, not a Python model.

Set ARDUINOJSON_INCLUDE to ArduinoJson 7.4.3's src directory.
"""

import os
import subprocess
from pathlib import Path

import yaml


class FirmwareLoader(yaml.SafeLoader):
    pass


def tagged(loader, tag, node):
    return loader.construct_scalar(node)


FirmwareLoader.add_multi_constructor("!", tagged)


def test_alarm_led_feedback_tracks_ringing():
    config = yaml.load(
        Path("esphome/voice_alarms_snippet.yaml").read_text(), Loader=FirmwareLoader
    )
    ringing = next(s for s in config["switch"] if s["id"] == "va_alarm_ringing")
    assert {"switch.turn_on": "timer_ringing"} in ringing["on_turn_on"]
    assert ringing["on_turn_off"][-1] == {"switch.turn_off": "timer_ringing"}

    leds = next(s for s in config["script"] if s["id"] == "control_leds")
    assert leds["then"] == [
        {
            "if": {
                "condition": {"switch.is_on": "va_alarm_ringing"},
                "then": [{"script.execute": "control_leds_timer_ringing"}],
            }
        }
    ]


def test_actual_firmware_sync_and_scheduler(tmp_path):
    include = os.environ.get("ARDUINOJSON_INCLUDE")
    assert include and (Path(include) / "ArduinoJson.h").is_file(), (
        "Set ARDUINOJSON_INCLUDE to ArduinoJson 7.4.3's src directory"
    )
    config = yaml.load(
        Path("esphome/voice_alarms_snippet.yaml").read_text(), Loader=FirmwareLoader
    )
    actions = {item["action"]: item for item in config["api"]["actions"]}
    sync = actions["voice_alarms_sync"]["then"][0]["lambda"]
    schedule = config["interval"][0]["then"][0]["lambda"]
    restore = config["time"][0]["on_time_sync"][0]["lambda"]
    source = (
        r"""
#include <ArduinoJson.h>
#include <algorithm>
#include <array>
#include <cassert>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <functional>
#include <string>
#include <vector>
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGD(...) ((void)0)
#define id(x) x
namespace json {
  bool parse_json(const std::string &raw, const std::function<bool(JsonObject)> &f) {
    JsonDocument doc;
    if (deserializeJson(doc, raw)) return false;
    if (!doc.is<JsonObject>()) return false;
    return f(doc.as<JsonObject>());
  }
  std::string build_json(const std::function<void(JsonObject)> &f) {
    JsonDocument doc;
    f(doc.to<JsonObject>());
    std::string result;
    serializeJson(doc, result);
    return result;
  }
}
struct ClockTime {
  int year, month, day_of_month, hour, minute, second, day_of_week;
  time_t timestamp;
  bool is_valid() const {return year >= 2026;}
};
struct Clock {
  ClockTime value;
  ClockTime now() const {return value;}
  void set(time_t stamp) {
    auto t = *localtime(&stamp);
    value = {t.tm_year+1900,t.tm_mon+1,t.tm_mday,t.tm_hour,t.tm_min,t.tm_sec,t.tm_wday+1,stamp};
  }
} va_clock;
std::array<char,4096> va_alarms_json{};
std::array<char,2048> va_fired_tokens{};
int va_max_ring_secs = 1800;
std::string va_sound_key = "timer_finished_sound";
bool va_sync_ok = false;
std::string va_sync_error;
struct Ring {
  bool state = false;
  int count = 0;
  void turn_on() {state = true; ++count;}
  void turn_off() {state = false;}
} va_alarm_ringing;
struct Sound {
  std::string option;
  Sound &make_call() {return *this;}
  Sound &set_option(const std::string &value) {option=value; return *this;}
  void perform() {}
} voice_alarms_sound;
void synchronize(std::string payload) {
"""
        + sync
        + r"""
}
void schedule_tick() {
"""
        + schedule
        + r"""
}
void restore_settings() {
"""
        + restore
        + r"""
}
std::string payload(std::string alarms, int max_minutes=30, std::string sound="Timer finished") {
  return "{\"alarms\":["+alarms+"],\"sound\":\""+sound+"\",\"max_ring_time\":"+std::to_string(max_minutes)+"}";
}
std::string alarm(std::string name, std::string repeat="daily", std::string once="null", int hour=7, int minute=0) {
  return "{\"id\":\""+name+"\",\"hour\":"+std::to_string(hour)+",\"minute\":"+std::to_string(minute)+",\"repeat\":\""+repeat+"\",\"once_at\":"+once+"}";
}
void reset() {
  va_alarms_json.fill(0); va_fired_tokens.fill(0);
  va_alarm_ringing.state=false; va_alarm_ringing.count=0;
}
int main() {
  setenv("TZ","America/Los_Angeles",1); tzset();
  synchronize("not JSON"); assert(!va_sync_ok);
  synchronize(payload(alarm("bad", "daily", "null", 25))); assert(!va_sync_ok);
  synchronize(payload(alarm("bad", "daily", "null", 7,60))); assert(!va_sync_ok);
  synchronize(payload(alarm("bad", "unknown"))); assert(!va_sync_ok);
  synchronize(payload(alarm("bad"),121)); assert(!va_sync_ok);
  synchronize(payload(alarm("bad"),30,"missing.wav")); assert(!va_sync_ok);
  std::string too_many;
  for (int i=0; i<31; ++i) {if(i) too_many+=","; too_many+=alarm("id-"+std::to_string(i));}
  synchronize(payload(too_many)); assert(!va_sync_ok);
  reset();
  va_clock.set(1792764012); // Friday 2026-10-23 07:00:12 PDT
  assert(va_clock.value.hour==7 && va_clock.value.day_of_week==6);
  synchronize(payload(alarm("weekday","weekdays"),2,"alarm_bell.wav")); assert(va_sync_ok);
  assert(va_max_ring_secs==120);
  schedule_tick(); assert(va_alarm_ringing.count==1);
  va_alarm_ringing.turn_off();
  schedule_tick(); assert(va_alarm_ringing.count==1);
  synchronize(payload(alarm("weekday","weekdays"),2,"alarm_bell.wav"));
  schedule_tick(); assert(va_alarm_ringing.count==1);
  va_clock.set(1792850412); // Saturday, same local time
  schedule_tick(); assert(va_alarm_ringing.count==1);
  synchronize(payload(alarm("weekend","weekends")));
  schedule_tick(); assert(va_alarm_ringing.count==2);
  reset(); va_clock.set(1792764012);
  synchronize(payload(alarm("once","once","1792764000")));
  schedule_tick(); assert(va_alarm_ringing.count==1);
  JsonDocument stored;
  assert(!deserializeJson(stored,va_alarms_json.data()));
  assert(stored["alarms"].as<JsonArray>().size()==0);
  va_alarm_ringing.turn_off();
  synchronize(payload(alarm("once","once","1792764000")));
  schedule_tick(); assert(va_alarm_ringing.count==1);
  va_clock.set(1792850412); schedule_tick(); assert(va_alarm_ringing.count==1);
  synchronize(payload("",120,"alarm_chime.wav")); assert(va_sync_ok);
  va_max_ring_secs=1800; restore_settings(); assert(va_max_ring_secs==7200);
  reset();
  // Two distinct alarms around a DST clock rewind must each fire only once.
  va_clock.set(1793520000); // 2026-11-01 01:00 PDT
  assert(va_clock.value.hour==1 && va_clock.value.minute==0);
  synchronize(payload(alarm("a","daily","null",1,0)+","+alarm("b","daily","null",1,30)));
  schedule_tick(); assert(va_alarm_ringing.count==1);
  va_alarm_ringing.turn_off(); va_clock.set(1793521800);
  schedule_tick(); assert(va_alarm_ringing.count==2);
  va_alarm_ringing.turn_off(); va_clock.set(1793523600); // 01:00 again, PST
  schedule_tick(); assert(va_alarm_ringing.count==2);
}
"""
    )
    cpp = tmp_path / "firmware_test.cpp"
    cpp.write_text(source)
    binary = tmp_path / "firmware_test"
    subprocess.run(
        ["g++", "-std=c++20", "-I", include, str(cpp), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)