# WMP speaker BLE control protocol (firmware 2026-11-18)

Each speaker is a BLE peripheral. No pairing or bonding: every attribute uses ATT security none.

- BLE advertising name: `WMP-Control-XXXX` (XXXX = last 2 bytes of the base MAC, hex).
- Bluetooth Classic (A2DP sink) name of the same speaker: `WMP-Sink-XXXX`.
- Advertising interval about 1 s. Once connected the speaker requests a 60-75 ms connection
  interval (no peripheral latency), so a GATT round trip takes about 100-150 ms. Keep phone-side
  scanning short and low-duty: a continuous low-latency scan starves the phone's A2DP stream.

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

(UUIDs as written by BTstack `att_db_util_add_*_uuid128`, big-endian byte order
`ae fb 00 0N 2c 51 4b 0d b1 00 ec 5b 00 00 00 01`. Verify the exact string against a real
scan with nRF Connect before hard-coding; if the byte order is reversed the service shows up as
`01000000-5bec-00b1-0d4b-512c0N00fbae`.)

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
Generic Access (device name), Generic Attribute (Service Changed, indicate), Device Information:
manufacturer "WMP development", model "ESP32 Wi-Fi Music Party", serial = base MAC hex,
firmware revision = build id, hardware "ESP32 / PCM5102A".
