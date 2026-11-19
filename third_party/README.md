# third_party

The firmware builds against three upstream projects, included here as **git submodules pinned
to exact commits**. They are **unmodified official checkouts**: no file inside them is edited.
`tools/build_app.py` refuses to build if any of them is on a different commit or has local
changes.

| Path | Upstream | Pin |
|---|---|---|
| `third_party/esp-idf` | https://github.com/espressif/esp-idf | tag `v6.1`, commit `fff9895c82d744c7237be8847347bdd1b07c6643` (with its own submodules) |
| `third_party/btstack` | https://github.com/bluekitchen/btstack | tag `v1.8.3`, commit `51e11ce3692abfe31b4ee3d66cffdeb1ae1626f2` |
| `third_party/esp32-hifi-a2dp-sink` | https://github.com/WillyBilly06/esp32-hifi-a2dp-sink | commit `7dcb71a0c3ffea973322fcec1f8f46abecd6b079` (only its decoders are used) |

The ESP-IDF and BTstack pins are also recorded in `../dependencies.lock.json`, which the build
and install tools compare against every build manifest.

## Fetching them

```bash
git clone --recursive https://github.com/WillyBilly06/ESP32-WiFi-Music-Party.git
```

or, in an existing clone:

```bash
git submodule update --init --recursive
```

That reproduces the identical SDK, Bluetooth host and decoder sources. Check with
`git submodule status --recursive`: no line should start with `+` or `-`.

## Where the project's changes live

All project code is in `firmware/` and `protocol/`. Where the firmware needs to observe or
adjust SDK or BTstack behaviour, it does so from the outside at build or link time:

- `firmware/app/main/coex_trace.c` observes the coexistence scheduler by interposing
  `coex_schm_register_callback`, `coex_wifi_request` / `coex_wifi_release` and
  `coex_bt_request` / `coex_bt_release` with linker `--wrap` (see `firmware/app/main/CMakeLists.txt`).
- BTstack's ESP32 VHCI transport is compiled with a force-included header
  (`firmware/app/main/vhci_probe.h`) and an extra source (`vhci_space.c`) added from
  `firmware/app/CMakeLists.txt`; BTstack's mesh sources are left out of the build.
- `firmware/components/btstack_config` provides BTstack's configuration header.
- `firmware/components/wmp_codecs` compiles the AAC-LC and aptX decoders straight from the
  pinned `esp32-hifi-a2dp-sink` checkout.
- `research/raw-tx-copy-v61/audit.json` records the hashes of the ESP-IDF Wi-Fi libraries
  whose raw-TX copy behaviour was audited; the build checks them.

## What each one provides

- **ESP-IDF v6.1**: Espressif's SDK, Wi-Fi and Bluetooth controller, PHY and toolchain integration.
- **BTstack v1.8.3**: the Bluetooth host (A2DP sink, AVDTP, AVRCP, BLE GATT) running on the
  ESP32 controller over VHCI. Bluedroid is not used.
- **esp32-hifi-a2dp-sink**: Helix AAC (AAC-LC only, SBR sources excluded; RealNetworks RPSL)
  and libfreeaptx (aptX / aptX HD; LGPL-2.1-or-later). Their licences apply to those files.
  Locally it can be a sparse checkout of just these paths; a full checkout works the same:
  `esp-idf/components/bt/host/bluedroid/external/libaac-lc/`,
  `esp-idf/components/bt/host/bluedroid/external/libfreeaptx/`, `native_bt/codecs/`.
