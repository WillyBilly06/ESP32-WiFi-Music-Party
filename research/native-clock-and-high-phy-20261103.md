# Native clock and higher-PHY decisions - 2026-11-03

The current design uses AP-synchronized TSF, common sample/presentation times,
immutable logical packet identity and spaced original copies. It does not use
ESP-NOW time synchronization, action-frame clock injection or arrival-triggered
playback. Native action RX currently samples the MAC clock in its callback;
that must not be relabelled a preamble-end hardware capture. A1MHz timer's
resolution does not establish1us cross-device playback accuracy.

Primary sources checked:
- Pinned esp_wifi.h: esp_wifi_get_tsf_time requires association and at least one
  received beacon in STA mode; power-saving can affect accuracy.
- Espressif coexistence guide: RF resources are shared by Wi-Fi, BT and BLE;
  priorities and grants are dynamic. It does not promise all copies sent while
  Bluetooth owns RF are discarded, nor document the proposed MAC-fatigue claim.
  https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/coexist.html
- Espressif raw80211 guide permits rate configuration through the public API:
  https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/wifi-driver/wifi-vendor-features.html
- Pinned esp_wifi.h requires raw rate configuration after init and before start.
  No active-stream retune is introduced.
- Pinned wifi_phy_rate_t defines MCS7_LGI as65Mbps at HT20; rates depend on
  bandwidth and guard interval. Existing54Mbps OFDM is unchanged by default.
- ESP-NOW time module is for time distribution and deep-sleep recovery. Its page
  does not establish microsecond speaker synchronization; it also violates the
  project's no-ESP-NOW boundary.
  https://docs.espressif.com/projects/esp-now/en/latest/esp32/api-reference/time/espnow_time.html

Candidatebc29 adds RATE65 and per-WMP raw completion rate counts. SDK acceptance,
driver-reported rate, unique integrity-checked receive throughput, deadline
coverage and DAC output are separate evidence. No on-air rate or improved audio
claim exists merely from compilation/configuration. Higher nominal rate can
reduce payload airtime but changes sensitivity and preamble overhead; measurement
on the actual packet sizes must decide the useful profile.

100 receivers: four broadcast originals and broadcast repair do not multiply by
receiver count, and receivers suppress duplicate logical groups. Clock association
capacity is a separate constraint: this Windows reference AP supports8 clients.
A100-client AP or a separately validated passive native timestamp distribution
path is needed before this fixture architecture can claim100 receivers. One
shared presentation time is the requirement; identical packet receipt time or
literal zero physical output skew is not established by either broadcast or TSF.
