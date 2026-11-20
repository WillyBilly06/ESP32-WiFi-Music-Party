# WMP speaker BLE control protocol (firmware 2026-11-19)

Each speaker is a BLE peripheral. No pairing or bonding: every attribute uses ATT security none.
Instead, the app and the speaker prove to each other that they belong together (Identity, below),
and the speaker accepts no control write until that is done.

- BLE name (in the scan response): `WMP-Control-XXXX` (XXXX = last 2 bytes of the base MAC, hex).
- Bluetooth Classic (A2DP sink) name of the same speaker: `WMP-Sink-XXXX`.
- Advertising interval 200 ms. Once connected the speaker requests a 60-75 ms connection
  interval (no peripheral latency) and adapts it to 120-300 ms while the app is idle during playback.

## Recognising a speaker (advert)

The advert carries, in this order:

| AD type | Bytes | Meaning |
|---|---|---|
| 0x01 flags | `02` | General Discoverable, BR/EDR supported (dual mode) |
| 0x07 complete 128-bit UUIDs | control service `aefb0001-2c51-4b0d-b100-ec5b00000001` | lets the phone filter in its Bluetooth controller |
| 0xFF manufacturer data | `FF FF 57 4D <version> <product> <status>` | company 0xFFFF (no assigned id), `'W' 'M'`, version 1, product 1 = ESP32 + PCM5102A |

Status bits: 0 A2DP streaming, 1 broadcast on, 2 party master, 3 receiving as follower,
4 phone connected over Classic, 5 a BLE app is connected. The advert is refreshed when they change.

The app lists a device only if it has the service UUID and the `WM` manufacturer data with a
known version and product. It never matches on names. Anyone can copy an advert, so this only
selects candidates; the handshake decides.

## Identity (`aefb0009-2c51-4b0d-b100-ec5b00000001`, read + write)

Mutual HMAC-SHA256 challenge-response with a 32-byte key shared by the firmware and the app.
Three ATT operations, done right after service discovery:

1. App writes `[0x01, app_nonce(8)]`.
2. App reads `[state=1, key_id, speaker_nonce(8), speaker_tag(10)]`, where
   `speaker_tag = HMAC-SHA256(key, "WMP-S" || app_nonce || speaker_nonce)[0..9]`.
   The app checks it in constant time; on a mismatch it disconnects ("Not a Music Party speaker").
3. App writes `[0x02, app_tag(10)]`, where
   `app_tag = HMAC-SHA256(key, "WMP-A" || speaker_nonce || app_nonce)[0..9]`.
   A read now returns `[2, key_id]`.

Until step 3 succeeds every write, including CCCD subscriptions, fails with ATT error 0x08
(insufficient authorisation). Each challenge allows one proof attempt. A connection that has not
proven itself within 10 s is disconnected, so a foreign app cannot occupy the BLE link and its
airtime next to A2DP. Reads stay open. Serial telemetry: `WMP_IDENTITY result=proven|fail|dropped`,
and `identity/proven/failed/dropped/key` in `WMP_GATT`.

Key: a 32-byte key with a key id, in `firmware/app/main/identity_key.h` and
`app/wmp_party/lib/ble/identity_key.dart`. The default is the public key
`"WMP development identity key v01"`, key id 0. Anyone building their own speakers puts their own
key (and a new key id) in both files, so their app and speakers recognise only each other. An app
whose key id differs from the speaker's asks for a firmware update.

Test vector (app_nonce `0001020304050607`, speaker_nonce `08090a0b0c0d0e0f`):
speaker_tag `086bf272ac312b22ac54`, app_tag `34a37db566c82c07dc18`.

## Control service `aefb0001-2c51-4b0d-b100-ec5b00000001`

| UUID | Name | Properties | Format |
|---|---|---|---|
| `aefb0002-2c51-4b0d-b100-ec5b00000001` | Control | read, write, write-no-response | write: opcode + args (below); read: same 20 bytes as Status |
| `aefb0003-2c51-4b0d-b100-ec5b00000001` | Status | read, notify | 20 bytes (below), notified about once per second while subscribed |
| `aefb0004-2c51-4b0d-b100-ec5b00000001` | Volume | read, write | 1 byte, 0..100 % |
| `aefb0005-2c51-4b0d-b100-ec5b00000001` | Latency | read, write | u16 LE milliseconds, 120..300, writable only on the party master |
| `aefb0006-2c51-4b0d-b100-ec5b00000001` | Speaker channel | read, write | read 3 bytes `[setting, output, party]`; write 1 byte `setting` |
| `aefb0007-2c51-4b0d-b100-ec5b00000001` | EQ | read, write | 7 x int8 dB, each -12..+12 |
| `aefb0008-2c51-4b0d-b100-ec5b00000001` | Broadcast | read, write | read 5 bytes `[on, wifi_ready, party, aac_allowed, handover]`; write 1 byte 0/1 |
| `aefb0009-2c51-4b0d-b100-ec5b00000001` | Identity | read, write | handshake above; every other write needs it first |

(UUIDs as written by BTstack `att_db_util_add_*_uuid128`, big-endian byte order
`ae fb 00 0N 2c 51 4b 0d b1 00 ec 5b 00 00 00 01`; the advert carries the same UUID in the
little-endian AD order.)

### Control opcodes (write to `…0002`)
| Bytes | Meaning |
|---|---|
| `01 vv` | volume vv (0..100) |
| `02 lo hi` | latency ms (120..300), master only |
| `03 cc` | speaker channel: 0 stereo, 1 left, 2 right |
| `04 g0 g1 g2 g3 g4 g5 g6` | EQ gains, int8 dB -12..12 (7 bands) |
| `05 bb` | broadcast off (0) / on (1) |
| `06 aa` | broadcast codec: 0 = SBC only (default), 1 = the phone may also use AAC-LC |
| `11 lo hi` | status notify period ms (10..1000) |

Invalid values are rejected with ATT error 0x13 (value not allowed). Writes are queued (4 deep)
and applied on the speaker's Bluetooth host tick (~10 ms); read back to confirm.

### Status (20 bytes, `…0003`)
| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | format version (1) |
| 1 | 1 | role: 2 = party master (broadcasting), 1 = playing, 0 = idle |
| 2 | 1 | bit0 phone (A2DP) connected, bit1 BLE connected |
| 3 | 1 | bit0 phone streaming, bit1 following a party, bit2 preparing |
| 4 | 4 | sample rate Hz, u32 LE (0 = none) |
| 8 | 2 | broadcast latency ms, u16 LE |
| 10 | 1 | volume % |
| 11 | 1 | audio running / clock valid |
| 12 | 4 | concealed groups (stutter counter), u32 LE |
| 16 | 4 | late groups, u32 LE |

### Speaker channel (stereo pair, JBL-style)
`setting` is what the user chose (0 stereo, 1 left, 2 right). `output` is what the speaker plays
now. `party` is the party size: 1, 2, 3 (meaning 3 or more), or 0 (unknown/not in a party).
Left/Right only takes effect while the party has exactly 2 speakers; otherwise every speaker
plays full stereo. The setting is saved on the speaker.

### EQ
Seven bands: 63 Hz low shelf; 160 Hz, 400 Hz, 1 kHz, 2.5 kHz and 6.3 kHz peaking (Q 1.1); 16 kHz
high shelf. Each band is -12..+12 dB. Applied per speaker on its own output and saved on the speaker.
The speaker runs it in integer arithmetic only (Q28 biquads with error feedback, precomputed
coefficient tables for 16/32/44.1/48 kHz). Settings saved by the earlier 5-band firmware are migrated.

### Broadcast (party / Auracast-like)
Off at every power-on (like Sony speakers): the speaker plays solo, Wi-Fi is fully off and every
codec is offered to the phone (SBC, AAC, aptX, aptX HD). Turning broadcast on starts Wi-Fi: the
speaker that has the phone's audio becomes the party master, others nearby with broadcast on
join within a few seconds. In broadcast only SBC (preferred) and AAC-LC are allowed, so the phone
may briefly reconnect its audio when broadcast is switched on. `wifi_ready` reports whether the
Wi-Fi side has finished starting or stopping.

Codec handover: if the phone is streaming aptX / aptX HD (or AAC while `aac_allowed` is 0) when
broadcast turns on, the speaker releases the phone's A2DP stream, removes those codecs from what
it offers, and reconnects to the phone itself with SBC (about 1 s). Most phones pause the music
when this happens: the app should show "Switching to party mode…" while `handover` is nonzero and
then offer a Play prompt. The speaker becomes the party master once the phone plays again over
SBC. Turning broadcast off keeps the SBC stream (the phone may pick a better codec at its next
connection). Older 3-byte readers of this characteristic keep working: bytes 3-4 were appended.

## Standard services
Generic Access (device name, appearance), Generic Attribute (no characteristics), Device
Information (firmware revision = build id only). The database is kept small on purpose: nothing
bonds, so Android rediscovers it on every connection at a forced 7.5 ms interval, and every
discovery round trip takes airtime from the phone's A2DP stream to the same speaker. Apps should
request the maximum MTU (517) before discovering services, which cuts the round trips further.
The speaker's controller runs with BLE auto latency, so it skips empty LE events and A2DP keeps its
airtime during that 7.5 ms phase.
