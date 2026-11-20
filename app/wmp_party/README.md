# Music Party (wmp_party)

Android Flutter app that controls ESP32 "Wi-Fi Music Party" speakers over BLE.
Protocol contract: `../../docs/ble-control-protocol.md` (firmware 2026-11-18).

## Build / test

```
flutter analyze
flutter test
flutter build apk --debug         # build/app/outputs/flutter-apk/app-debug.apk
flutter run --dart-define=WMP_DEMO=true   # simulated speakers, no hardware
```

The identity key lives in one committed file, `lib/ble/identity_key.dart`:
key id 0, the public ASCII key "WMP development identity key v01". It keeps
other apps and devices apart; it is not a secret. Anyone building their own
speakers puts their own key and a new key id there and in
`firmware/app/main/identity_key.h`, then rebuilds both.

Pinned: flutter_blue_plus 2.3.15, permission_handler 12.0.3 (android 13.0.1;
14.x needs compileSdk 37), provider 6.1.5+1, shared_preferences 2.5.6,
crypto 3.0.8, fake_async 1.3.3 (dev). `android/gradle.properties` sets
`kotlin.incremental=false` because the project (D:) and the pub cache (C:) are on
different drives.

## Layout

- `lib/protocol/` pure Dart, no Flutter: UUIDs (documented and byte-reversed form
  are both accepted), Status/Channel/Broadcast decode, volume/latency/EQ encode,
  control opcodes, EQ presets + response curve, `DebouncedWriter`.
- `lib/ble/` `BleBackend` interface, flutter_blue_plus implementation, permission
  gate (Android 12+ BLUETOOTH_SCAN/CONNECT, older: location), demo backend.
- `lib/state/` `SpeakerController` (one per speaker) and `AppState`.
- `lib/storage/` known speakers + custom EQ presets (shared_preferences).
- `lib/ui/` screens.

## Behaviour notes

- Only Music Party speakers are listed, never by name: the scan has a platform
  filter on the control service UUID, and `parseWmpAdvert` then requires
  manufacturer data with company id 0xFFFF and payload
  `['W', 'M', version 1, known product, status]` (product 1 = ESP32 + PCM5102A).
  The status byte (streaming, broadcast, party host, follower, phone connected,
  another app connected) is shown on nearby cards.
- Identity handshake on `aefb0009` right after service discovery, before any
  other write or CCCD: write `[1, app_nonce(8)]`, read
  `[state, key_id, speaker_nonce(8), speaker_tag(10)]`, check the HMAC-SHA256 tag
  in constant time, write `[2, app_tag(10)]` (exactly 1 write + 1 read + 1
  write). A wrong tag disconnects with "Not a Music Party speaker"; another key
  id or a speaker without `aefb0009` shows "Speaker needs a firmware update";
  neither is retried automatically.
- Scanning is high rate in short bursts: Android low-latency mode with
  continuous updates (live RSSI), so speakers advertising every 200 ms appear
  within a few hundred ms. A continuous low-latency scan starves the phone's own
  A2DP stream (measured 124 lost RTP packets per 30 s), so every burst is capped
  at 6 s (`kScanBurst`) and stops early once every saved speaker has been heard,
  or the moment the user opens/connects a speaker (before `connect()`). No burst
  starts while a connection is being set up. Bursts run at start-up, when
  Bluetooth comes on, on the scan button, pull to refresh, and whenever the home
  screen comes back (back from a speaker page, or app resumed on home).
- Writes go to the dedicated characteristics (write with response) so ATT 0x13
  rejections surface. Sliders are debounced (250 ms), at most one write in
  flight, then the characteristic is read back; the UI keeps the read-back value
  and shows a message when the speaker rejected or changed it.
- No periodic GATT reads. Live state comes from Status notifications (~1/s).
  Channel/broadcast/latency are read on connect, when a speaker page opens
  (unless read in the last 5 s), after a write, on pull to refresh, and once
  when Status shows the party role changing. After a broadcast toggle the app
  reads Broadcast on Status notifications (when the link state changes, else
  every other one) until `wifi_ready` is set and `handover` is 0, for at most
  20 notifications.
- Connect sequence (keeps air time free for the phone's A2DP stream; Android
  bursts to a 7.5 ms interval during service discovery): connect without MTU
  exchange -> request Android low-power connection priority -> one MTU
  request (517, the ATT maximum; failure ignored) -> exactly one
  `discoverServices` (no Service Changed subscription) -> identity handshake ->
  read Status ->
  subscribe Status -> request low power again -> read volume, EQ, channel,
  broadcast -> firmware revision. (The identity handshake sits between
  discovery and the Status read.) Party delay comes from Status, not a read.
  flutter_blue_plus itself never discovers or clears the GATT cache; a second
  7.5 ms burst seen on the speaker is Android's own. Timeouts: connect 15 s,
  discovery 20 s, GATT op 6 s.
- Lost links reconnect automatically after 3 s, 6 s, 12 s (minimum 3 s so a
  dropping link does not keep loading the radio); speakers that dropped
  because Bluetooth was switched off reconnect when it comes back.
- Party delay (latency) is shown only on the party host.
- EQ is 7 bands (63 Hz low shelf; 160/400/1k/2.5k/6.3k Hz peaking, Q 1.1; 16 kHz
  high shelf). A 5-byte EQ read (older firmware) and custom presets saved by the
  5-band app are migrated like the speakers did: old bands go to 0/1/3/4/6, new
  bands 2 and 5 are the average of their neighbours (truncated toward zero; the
  firmware's rounding is assumed to match C integer division).
- Broadcast reads accept the 3-byte legacy and 5-byte form
  `[on, wifi_ready, party, aac_allowed, handover]`. The "Party codec: SBC / AAC-LC"
  picker (Control opcode 0x06, read back from byte 3) is hidden on 3-byte firmware.
- Codec handover: while `handover` != 0 the speaker page and card show
  "Switching to party mode...". Afterwards, if the phone is not streaming, a
  "Press Play on your phone" prompt appears until Status reports streaming. After
  switching broadcast on, at least 3 Status notifications are watched so a
  short handover is not missed; a pause that started with music playing also
  triggers the prompt.
- flutter_blue_plus is used with `License.nonprofit` (personal project). A
  commercial product would need its commercial license.
