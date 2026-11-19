# third_party

Nothing in this folder is committed except this note. The firmware builds against three
upstream checkouts at exact commits. `tools/build_app.py` refuses to build if any of them
is on a different commit or has local changes.

| Path | Upstream | Pin |
|---|---|---|
| `third_party/esp-idf` | https://github.com/espressif/esp-idf | tag `v6.1`, commit `fff9895c82d744c7237be8847347bdd1b07c6643` (recursive clone, unmodified) |
| `third_party/btstack` | https://github.com/bluekitchen/btstack | tag `v1.8.3`, commit `51e11ce3692abfe31b4ee3d66cffdeb1ae1626f2` |
| `third_party/esp32-hifi-a2dp-sink` | https://github.com/WillyBilly06/esp32-hifi-a2dp-sink | commit `7dcb71a0c3ffea973322fcec1f8f46abecd6b079` (sparse checkout, decoders only) |

The ESP-IDF and BTstack pins are also recorded in `../dependencies.lock.json`, which the build
and install tools compare against every build manifest.

## Fetching them

```bash
git clone --recursive --branch v6.1 https://github.com/espressif/esp-idf.git third_party/esp-idf
git -C third_party/esp-idf rev-parse HEAD   # must print fff9895c82d744c7237be8847347bdd1b07c6643

git clone https://github.com/bluekitchen/btstack.git third_party/btstack
git -C third_party/btstack checkout 51e11ce3692abfe31b4ee3d66cffdeb1ae1626f2

git clone --filter=blob:none --no-checkout https://github.com/WillyBilly06/esp32-hifi-a2dp-sink.git third_party/esp32-hifi-a2dp-sink
git -C third_party/esp32-hifi-a2dp-sink sparse-checkout set --no-cone /THIRD_PARTY.md /LICENSE /README.md \
    /esp-idf/components/bt/host/bluedroid/external/libfreeaptx/ \
    /esp-idf/components/bt/host/bluedroid/external/libaac-lc/ \
    /native_bt/codecs/aac/ /native_bt/codecs/aptx/ /native_bt/codecs/common/
git -C third_party/esp32-hifi-a2dp-sink checkout 7dcb71a0c3ffea973322fcec1f8f46abecd6b079
```

## What each one provides

- **ESP-IDF v6.1**: Espressif's SDK, Wi-Fi and Bluetooth controller, PHY and toolchain integration.
  `research/raw-tx-copy-v61/audit.json` pins the hashes of the SDK Wi-Fi libraries whose raw-TX
  copy behaviour was audited; the build checks them.
- **BTstack v1.8.3**: the Bluetooth host (A2DP sink, AVDTP, AVRCP, BLE GATT) running on the
  ESP32 controller over VHCI. Bluedroid is not used.
- **esp32-hifi-a2dp-sink**: only the non-SBC decoders are compiled from it, via
  `firmware/components/wmp_codecs`: Helix AAC (AAC-LC only, SBR sources excluded; RealNetworks
  RPSL) and libfreeaptx (aptX / aptX HD; LGPL-2.1-or-later). Their licences apply to those files.
