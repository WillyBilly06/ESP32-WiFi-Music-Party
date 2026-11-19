"""Identities of the physical test phones on this bench.

They are personal devices, so the real values are kept out of the repository in
tools/bench_ids.local.json (git-ignored), for example:

    {"phone_main": "<adb serial>", "phone_main_bt": "aa:bb:cc:dd:ee:ff",
     "phone_second": "<adb serial>", "phone_second_bt": "aa:bb:cc:dd:ee:ff"}

Without that file the placeholders below are used, and tools that need a real phone fail
at their own admission checks instead of driving an unknown device.
"""
import json
from pathlib import Path

_values = {
    'phone_main': 'PHONE-MAIN-SERIAL',
    'phone_main_bt': '00:00:00:00:00:01',
    'phone_second': 'PHONE-SECOND-SERIAL',
    'phone_second_bt': '00:00:00:00:00:02',
}
_local = Path(__file__).with_name('bench_ids.local.json')
if _local.exists():
    _values.update(json.loads(_local.read_text(encoding='utf-8')))

PHONE_MAIN = _values['phone_main']          # REDMAGIC test phone (adb serial)
PHONE_MAIN_BT = _values['phone_main_bt'].lower()
PHONE_SECOND = _values['phone_second']      # Samsung test phone (adb serial)
PHONE_SECOND_BT = _values['phone_second_bt'].lower()
