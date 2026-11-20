# Current development status - 2026-11-17 UTC

The goal is active and incomplete. Both admitted boards now run 8302c439bdd535b5
from build/app-1794912209001652200; no measurement on it is recorded here yet:
- COM7: evidence/install-1794912310818485700-COM7/receipt.json (guarded installer)
- COM6: evidence/esptool-flash-1794912291967489000-COM6/receipt.json (esptool)

The latest measurements used 87cd192f56692b3e from build/app-1793760567700722900:
- COM7: evidence/install-1794909330333266700-COM7/receipt.json (guarded installer)
- COM6: evidence/esptool-flash-1794910420964728400-COM6/receipt.json
COM6 was written with esptool write-flash and per-image hash verification
because the guarded installer's readback kept failing on COM6; INFO and PING
were verified afterwards. Its receipt records that the guarded installer's full
readback, outside-extent and NVS audits were not performed.

The PC hotspot stays off following the Ethernet complaint. The isolated ESP32
pair is active: COM7 is the anchor AP, COM6 its associated station, both on
MCS7/HT20 65 Mbps long GI. Bluetooth audio leadership is independent of these
Wi-Fi roles. Samsung is released for testing at 20% media volume.

Results on 87cd192f56692b3e with REDMAGIC plus BLE (digital counters only):
- 7 minutes at a 200 ms playout reserve: 3 groups dropped (11.6 ms each), 0 late.
- 500 ms reserve: a 64 s window had 0 drops. The current protocol source caps
  the common reserve at 300 ms, so this setting is no longer accepted.
- REDMAGIC's Classic link to COM7 dropped once with HCI reason 8 (connection
  timeout).

Runtime profile: 4 original consecutive SBC frames per group, 4 original copies,
NACK replay repair, no FEC. Original SBC bytes and PCM5102A WS25/DOUT26/BCLK27
remain unchanged.

There is no passing hour, physical output-timing, acoustic or 100-receiver
proof. Non-associated clock acquisition, anchor failure/election, persistent
startup, lower measured latency, clean handover audio and sustained
full-workload qualification remain required. Earlier failed receipts and
snapshots remain under evidence/ and research/.

## 2026-11-17 follower coexistence table (build app-1794921804915796300)

- `esp_coex_preference_set()` is a stub in the pinned v6.1 libcoexist (returns 0, does nothing).
  The real scheduler uses fixed two-phase tables; COM6 (follower) ran
  `coex_schm_ble_default_bt_default_wifi_conn` = 40% Wi-Fi / 60% BT phase, and 209 of 267 lost
  copies in the RX timeline fell in the BT phase.
- New: a follower automatically replaces the active table with a 90/10 table (`FOLLOWERCOEX <pct>`,
  0 disables); a source restores the library table. Diagnostics: `COEXSTATE`, `COEXTABLE <pct> <type>`.
- Interleaved A/B (same session, 60 s windows, REDMAGIC A2DP+BLE on COM7, Linux BLE + suspended A2DP on COM6):
  native 44/60/92 concealed frames per minute vs 90% 0/4/16. Copies received per group 2.5 -> 3.1.
- Automatic soak, 5 x 60 s at 200 ms latency: 4, 4, 12, 8, 0 concealed frames per minute; zero late; links stayed up.
- Cost: COM6 BLE writes applied drop from ~317 to ~170 per minute.
- One earlier window had 3796 conceals while COM7 transmitted ~14% fewer copies and COM6 received every group sent;
  cause on the source/phone input side not yet identified.
- Not yet proven: one-hour stability, acoustic quality, role switch with the new table.

## 2026-11-17 (later) build app-1794955436935757900

Installed on COM6/COM7 with `install_app.py` exact readback.

Fixed / added
- Installer readback: COM6's CP210x link drops bytes while receiving long stub reads at 921600, after
  which the stub stays blocked. `read_checked` now verifies every 16 KiB chunk against the device MD5 and,
  on failure, re-enters the ROM loader, re-admits the MAC, reloads the stub and continues (460800 after the
  first retry). Receipts record `read_retries` / `read_recoveries`. Both boards now install with exact readback.
- Timeline steps: late-report growth is applied as one common forward step (`wmp_playout.steps`), and every
  sink realigns once when its output error exceeds -20 ms (faded concealment fill) or +30 ms (drop one group
  with a faded bridge): `WMP_AUDIO realign_pad / realign_pad_ms / realign_skip`. The source drift
  deadband is now +/-30 ms (the 15 ms band retargeted every few seconds under 20 ms BLE notifications and
  kept sinks resampling at +/-1200 ppm). Previously a 100 ms growth took ~40-80 s to reach the output, with
  every group late meanwhile. Unit-tested; not yet exercised by a live growth event.
- Phone input timing: `WMP_RTP_TIMING input_gap_max_us / input_gaps_50ms / input_gaps_200ms /
  input_ahead_max_us / input_behind_max_us / input_rebases`.
- Diagnostics: `RXFILTER <1|3>`, `COEXBIT <type> <hexbits> <0|1>`.
- Tests: fixed a use-after-free in `test_eight_frame_group_round_trips_wire_and_parser`; all suites pass.
- `restore_owned_fixture.py` lets a zero-join listener wait for its beacon clock (it locks only once audio flows).

Measured
- Cold start (fresh boot, provision, REDMAGIC local 65-min file, then Linux BLE+A2DP): 0 late,
  12 concealed frames in ~140 s. The earlier minutes of late frames were not reproduced; they coincided
  with YouTube input bursts (source queue full 4083x, groups up to 1.4 s old).
- Role handover: owner phone stops -> other board takes the party in ~4 s (4 concealed frames);
  second phone plays while the other board owns -> follower suspends it within ~2 s.
- Zero-join broadcast (no association, beacon clock), full load: 36-90 concealed frames/min with the 90%
  follower table (native table 150-156; 95%/type 2: 520). Joined pair: 0-16. Not explained by:
  sniffer data-frame filtering, the Wi-Fi "connected" coex bit (worse), direct action RX (receives nothing
  without association), or copy spacing 24/33/45 ms. Loss is in the air (RX queue never full).

## 2026-11-17 zero-join loss investigation (build app-1794960383570300400)

- Zero-join (passive listener, beacon clock): COM6 receives ~2.5 of 4 copies (~37% copy loss) vs
  ~3.1 (~22%) when joined. Ruled out, each by live A/B on COM6:
  coex phase (45% loss inside the 90% Wi-Fi phase), time blackouts (other copies are received inside
  loss runs), RF power save (`RFAWAKE 1` no effect), receiver overload (TX power 20 -> 2 dBm, RSSI -15
  -> -25 dBm, no change), sniffer data-frame filter, Wi-Fi "connected" coex bit (worse), copy spacing,
  and the receive path itself (joined pair with sniffer path keeps 3.1 copies/group).
- Power saving: BT controller modem sleep compiled out (`CONFIG_BTDM_CTRL_MODEM_SLEEP=n`); Wi-Fi uses
  `WIFI_PS_NONE`. `ESP_WIFI_STA_DISCONNECTED_PM_ENABLE` is force-selected by
  `ESP_COEX_SW_COEXIST_ENABLE` and cannot be disabled while software coexistence is on.
- New diagnostics: `TXPOWER <8..84>`, `RFAWAKE <0|1>`, `COEXBIT <type> <hex> <0|1>`,
  `RXFILTER <classes, 4=FCS-fail>` (driver delivered no FCS-failed frames), passive RX trace records
  with RSSI/noise floor (kind 6, a|=0x40), kind 10 rx_bad.
- Working hypothesis: the difference is on the source side (soft-AP with no associated station).

## 2026-11-17 pure broadcast + follower sniff (build app-1794963566501156500)

- Pure broadcast (user instruction: no SoftAP, ESP-NOW-style raw broadcast without ESP-NOW): both
  boards are unconnected stations (`tools/prepare_pure_broadcast.py`, zero join/SoftAP commands).
  The source timeline is its own esp_timer (source clock mode 4); ANNOUNCE is 24 bytes with the source
  clock stamped just before encode/TX; followers fit `wmp_beacon_clock` to the current leader's stamps.
  Leadership precedence across independent clocks compares broadcast age. Roles change without any
  Wi-Fi mode switch. `WMP_PURE_BROADCAST` reports stamps and clock feed.
- Root cause of most zero-join loss: the follower's idle (A2DP-suspended) phone ACL stayed in active
  mode and was polled every few tens of ms. Link policy now allows sniff; a follower enters sniff
  (640-800 slots) after the peer has owned the party for 2 s and exits before streaming or leadership
  (`SNIFF 0|1`, `WMP_SNIFF`). A/B: sniff on 3.06-3.09 copies/group, 4-12 concealed frames/min;
  sniff off 2.46, 100. BLE throughput also rises (~360 vs ~240 writes/min).
- Coex Wi-Fi phase type: 3 is best (types 1/2: ~2.0 copies/group, 516-831 concealed/min).
- Repair: pure-broadcast followers use deadline-bounded NACKs (fitted clock -> local deadline):
  source cache misses fell from ~20% of heard requests to 1/651.
- Soak (full load: REDMAGIC A2DP+BLE on COM7, Linux BLE + suspended A2DP on COM6, 20 ms notifications),
  5 x 60 s at 200 ms: 16, 4, 0, 8, 16 concealed frames/min (~2 lost groups/min), 0 late.
- Concealment now continues the detected period, fades over ~46 ms and crossfades back (128 frames).
- Installer: write path also recovers (re-enter ROM loader, rewrite image, exact readback decides).
- Rejected: clearing the Wi-Fi 0x40 coex status bit (worse), source 80% Wi-Fi table (no gain, starves
  phone A2DP by ~31%).

## 2026-11-18 - Broadcast loss root cause: MCS7 frame corruption (rate A/B)

Build app-1795047293462246300 adds `HWSTAT` (MAC hardware RX/TX counters at 0x3ff73094/0x3ff7328c..0x3ff73d70,
map recovered from libpp `dbg_lmac_hw_statis_dump`), `WIFISTAT <mask>` (driver dump) and `TXRATE <hex code>`
(live raw-broadcast PHY rate; applied rate verified from TX completions, `WMP_PHY tx_rate`).
`rxcckerr`/`ofdmerr` are NOT error counts (they are higher on idle channels); use `rx_fcs`.

- TX power sweep 2..19 dBm (RSSI -22 -> -32 dBm, noise floor -96): delivery 70-82%, no trend. Not SNR, not saturation.
- Live rate A/B, phone A2DP streaming, COM7 source -> COM6, 40 s windows, alternated twice:

| rate | copies received | concealed frames /40 s | COM6 rx_fcs /s |
|---|---|---|---|
| MCS7 | 70.0%, 71.2% | 36, 32 | 83, 79 |
| MCS3 | 84.2%, 83.3% | 0, 0 | 33, 57 |
| MCS1 | 80.1%, 79.1% | 8, 12 | 32, 37 |

Conclusion: at MCS7 about 14 points of copy loss are frames that arrive but fail CRC (64-QAM 5/6 has no
margin for the board's distortion/interference even at 60 dB SNR). MCS3 removes that part; CRC errors fall to
background (~33/s, also seen with no source on the channel). The remaining ~16-20% loss leaves no CRC trace
(receiver not listening), and it grows with airtime (MCS1 worse than MCS3), so it is time-based: coex/BT slots or
other receiver-busy periods. Next: attribute that residual to receiver coex phases at MCS3.
Source restored to MCS7 after the test (AGENTS.md profile); switching the default needs user approval.

### Full rate sweep and third receiver (2026-11-18, later)

19 rates, two 35 s windows each, phone A2DP streaming, COM7 -> COM6 (evidence/rate-ab-20261118/sweep-all.txt).
Zero concealment in both passes: MCS4 87.8%, MCS3 87.4%, MCS3 short GI 86.6%, legacy 36M 85.8%, legacy 24M 83.9%,
MCS2 83.5%, 11M CCK short preamble 89.8% (about twice MCS3 airtime). Worse: MCS7 76.1% (12 concealed),
MCS5/MCS6/54M (8 each), MCS0/MCS1 (28-56; long frames collide with BT slots). Pass 2 had 2-3x background CRC
errors and one 6M window collapsed; differences of a few points within the top group are not significant.

COM29 admitted on user instruction: ESP32-D0WD-V3 rev3.1, MAC b4:bf:e9:6a:f8:48, 4 MB flash (c4/6016).
esptool 5.4 needs `--spi-connection SPI` for this board (auto attach reads 0xFF); stub read-flash fails above
115200 on its link. User declined a backup of its Arduino firmware; flashed app-1795047293462246300 at 921600
(all hashes verified). Driven directly over serial (`evidence/rate-ab-20261118/com29.py`), not dashboard-managed.
Prepared as a third pure-broadcast receiver (channel 6, same settings as COM6, BT on without a phone).

Three-receiver trace (COM7 TX done vs COM6 and COM29 RX, 2.5 s windows, `three_rx.py`), with a
common-plus-independent loss model:

| rate | runs | lost at both (source/air) | COM6 only | COM29 only |
|---|---|---|---|---|
| MCS7 | 3 | 12-17% | 19-26% | 30-35% |
| MCS3 | 2 | ~4% | ~18% | 19-30% |

At MCS7 most shared loss is transmitter-side corruption, removed by MCS3. What remains at MCS3 is mostly
per-receiver (independent): moments a given receiver is not listening. Independent loss is what copies and
repair handle well. Next target: receiver-side not-listening periods (follower coex/BLE).
Source restored to MCS7 (AGENTS.md profile) pending the user's decision on the default rate.

## 2026-11-18 - Slave stutter fixed: per-copy clock trailer, 300 ms timeline, follower refuses own phone

Build app-1795054734744155900 (COM6/COM7; MCS3 default).

- Follower phone: with `ENABLE_AVDTP_ACCEPTOR_EXPLICIT_START_STREAM_CONFIRMATION`, a follower refuses its own
  phone's AVDTP START (BAD_STATE) while another board leads and party is ON (`STARTREJECT 0|1`, counters in
  `WMP_STANDBY`). The 2 s standby suspend stays as a backstop for streams started before a peer took over.
  Not yet exercised live (the Linux player did not restart its stream); needs a real phone pressing play.
- Clock trailer (wire flag `WMP_FLAG_CLOCK`, 16 bytes after the payload, excluded from the logical CRC):
  send stamp, time to the group's next copy, TX serial, quiet flag. Followers feed the least-delayed copy of
  each 100 ms window to the clock fit (about 10/s from about 285/s seen) and stop using ANNOUNCE stamps while
  audio stamps flow (`CLOCKCOPY 0|1|2`). 166 protocol tests pass (4 new).
  Quiet-only ANNOUNCE stamping alone was not enough: fit still swung 0.26-4.9 ms.
- Remaining bursts traced to the source: COM7 `original_late_submit` jumped exactly when COM6 concealed.
  Phone A2DP input arrived up to 179 ms behind its timeline (1,760 gaps over 50 ms; source queue full 28x),
  leaving too little of the 200 ms budget for four spaced copies.
- LATENCY 300 (source-owned, both boards): 212 s with phone streaming, COM6 0 concealed, 0 late, fit 130-270 us,
  COM7 0 late submits. Before (200 ms): bursts of 56-490 concealed frames every 20-60 s.
  `prepare_pure_broadcast.py` now sets 300 ms. Phone-to-speaker latency = phone A2DP buffering + 300 ms (not measured).

### Third receiver COM4 (2026-11-18)

User replaced COM29 with a new board on COM4: ESP32-D0WD-V3 rev3.1, MAC 88:13:bf:a9:b4:e0, 8 MB flash (normal
flash detection). Flashed app-1795054734744155900 at 921600 (hashes verified), prepared as a pure-broadcast
receiver at MCS3/300 ms. First 50 s after joining: 2.8-3.1 copies/group and 136 concealed frames (startup).
Then 2 minutes side by side: COM4 3.47-3.50 copies/group, COM6 3.20-3.25, both 0 concealed and 0 late, clock
fits 155-266 us and within about 10 us of each other in every window. Bluetooth off/on A/B on COM4 showed no
lasting difference once settled.

## 2026-11-18 — Codecs (AAC, aptX, aptX HD): decoders working, master budget is the limit
- Root cause of "decoders not working": the master's source queue (wmp_sbc_queue) dropped the
  group's codec id, so every AAC/aptX group went out labelled SBC and every speaker fed it to
  the SBC decoder. Fixed (codec + channels carried through the queue; protocol test added).
- Other fixes: classic aptX has no RTP header; codec switch flushes master group/queue/RTP and
  restarts PLC at the play point; SBC preload no longer evicts the AAC decoder per packet;
  BTstack host stack 3072 -> 4096 (overflowed on COM7, 72 bytes free on COM6).
- Quality: PCM path is 24-bit in int32; aptX HD plays over 32-bit I2S; SBC/AAC/aptX 16-bit
  output with rounding. aptX/aptX HD decode bit-exact vs ffmpeg (host harness, 260k samples).
- aptX: plain decode while in sync, one-codeword aptx_decode_sync only to recover (library
  step-size precedence bug); channels decoded in parallel (right channel on core 0).
- Measured cost (CODECBENCH, interrupts off): aptX 1.45 M cycles = 6.0 ms per 512-frame group
  (QMF 0.66 M, prediction 0.74 M). Split across cores: 3.1-3.9 ms wall.
- Priorities: Wi-Fi task is pinned to core 1 at 23. Decoding at 24 starved it (TX completion
  21 ms, late copies everywhere); decoding now runs at 22 on core 1.
- Still failing (not fixed): the MASTER collapses with AAC/aptX — its local playback pool and
  16 ms DMA ring (128-frame blocks) cannot absorb AAC's 21 ms groups / aptX decode, the pool
  fills, and the broadcast to followers stalls (source queue full). Master heap min 7 KB with
  AAC. aptX HD (~72 KB/s) also exceeds the fixed 24 KB arena at the 300 ms timeline.
  Next: per-codec playback buffers (DMA ring and arena sized per codec, master included) and
  never let the master's own playback pool block the broadcast.
- Fixture note: the Linux source card is currently on profile a2dp-sink-aac.

## 2026-11-19 — Sony/JBL-style modes, stereo pair, EQ, handover (build app-1795077957673426600)
All three boards (COM6, COM7, COM4) run this build. Measured on hardware, REDMAGIC phone, YouTube.

**Modes.** Broadcast is off at every boot (solo): Wi-Fi is fully de-initialised and all codecs are
offered (SBC, AAC, aptX, aptX HD). Solo idle heap 140-142 KB free. `BROADCAST 1` / BLE opcode 5
starts Wi-Fi; the speaker carrying the phone's audio becomes the party master, others join.
- Defaults are now the tuned party settings (copy spacing 33 ms, TX window 6, dither 6 ms, NACK delay
  40 ms, latency 300 ms, receive wake window on), so no bring-up script is needed.
- 60 s windows, 3 speakers, SBC 44.1 kHz: 0 concealed on master and both followers (with and
  without a non-flat EQ on every speaker). Followers join with 4-16 concealed groups at startup.
- Broadcast off on the master: it keeps playing solo with no gap; followers fade out. Broadcast on
  again: re-elected master, followers back, 0 concealed in the next 30 s.

**Codec handover.** In broadcast only SBC is offered (AAC-LC too with `BCODEC 1` / BLE opcode 6).
Non-party endpoints are unlinked from BTstack's endpoint list while broadcasting (reporting them
"in use" was not enough: with aptX pinned in Android's HD-audio dialog the phone released the
sink-configured SBC stream within 30 ms and dropped AVDTP, in a loop). Turning broadcast on during
aptX playback: the speaker releases A2DP, reconnects as initiator with SBC (~0.5 s), the phone
pauses, and on Play it streams SBC and the speaker becomes master. Election is blocked until the
stream is an allowed codec (previously the master could start on aptX, heap fell to 30 KB).
No auto-resume: there is no AVRCP controller (would need AVRCP play after handover).

**Stereo pair.** `SPEAKER 1/2` (BLE 0006 / opcode 3) sets left/right; it only applies when the party
has exactly 2 speakers. Verified: COM7=L, COM6=R with COM4 out (party=2 → outputs 1/2); COM4 back
(party=3) → both stereo.

**Memory.** Leak found with ESP-IDF heap tracing (diagnostic build only): 8 bytes per broadcast cycle
from the Wi-Fi wake-window registration (now unregistered before `esp_wifi_deinit`). Remaining
one-time 20 bytes is ESP-IDF's interrupt vector descriptor (reused, never freed by design). Release
build: settled solo heap identical over 3 follower join/leave cycles. The largest block after a
party session drops 110592 → 94208 (fragmentation; total free stays ≥140 KB).
Master heap while broadcasting SBC: 23-26 KB free, minimum 18.5 KB (arena 30 KB, pool 29 KB,
RX queue 12 KB, PCM 10 KB). Tight; not yet tested with a BLE app connected to the master.

**Solo aptX.** Interrupt-watchdog resets (reset reason 5, no panic print) on aptX stream start were
traced with RTC breadcrumbs (`WMP_CRUMB` lines at boot) to the core-0 aptX helper-task handoff.
The helper is no longer started (both channels decode on the decode task; aptX is solo-only). Solo
aptX 48 kHz: 60 s, 1 concealed (startup), decode 6 ms per call. BT host stack raised to 5632.

**Open.** AVRCP play after handover; AAC-LC broadcast test (`BCODEC 1`); master RAM headroom; LDAC in
solo; persist the broadcast codec setting; one-hour soak.

## 2026-11-19 (later) — AVRCP, volume, BLE responsiveness (build app-1795110539676746500, all boards)
- **AVRCP controller + target.** After a codec handover the speaker sends AVRCP Play if the phone was
  playing (measured: playback resumes ~4 s after `BROADCAST 1`, no user action). Phone volume ↔
  speaker volume sync (absolute volume). Fixed: BTstack answers the phone's volume-notification
  registration with its stored volume (initially 0); it is now seeded with the speaker's volume on
  connect. (A later drop to 0 was the phone's hardware volume keys, per Android's audio log.)
- **Volume curve** is now audio-taper (gain = (p/100)^2): 10 % = -40 dB, 50 % = -12 dB, 100 % = unity
  (bit-exact). It was linear (10 % = -20 dB, too loud).
- **Party volume:** the master's volume rides in the clock trailer (`sync_flags` bits 1-7 = volume+1;
  176 protocol tests pass). Followers play at the master's volume while it is fresh (2 s); their own
  setting returns in solo. Not yet confirmed by ear or telemetry (no effective-gain field yet).
- **BLE latency is dynamic:** 60-75 ms interval (≈150 ms round trip) while the app is in use (GATT
  activity in the last 8 s) or the speaker is not playing; 120-150 ms for idle solo playback;
  240-300 ms (≈600 ms) for an idle app during a party. No request for the first 5 s after a
  connection, so discovery/subscription run at the phone's fast default.
- **App** (Flutter session): the first build scanned with Android lowLatency for 12 s and polled GATT,
  which starved the phone's A2DP (124 RTP gaps, ~310 concealed/30 s on every speaker). The fixed build
  (low-power short scans, notifications instead of polling) is installed; COM4-master + COM6 pair then
  ran 30 s with 0 concealed.
- **Open:** aptX HD static report (decode now on one core; needs a measurement in solo), effective
  party gain telemetry, AAC-LC broadcast test.

## 2026-11-19 — "Low bitrate" sound fixed: APLL steering recalibrated the DAC clock (build app-1795115840725598100)
- Cause: `apll_steer` used `rtc_clk_apll_coeff_set()`, which stops/restarts the APLL sigma-delta
  modulator and recalibrates (spins until relock) on every call; it ran up to 100x/s (~151k calls in
  one session on COM7). The DAC master clock was disturbed continuously: grainy, low-bitrate sound
  with every counter clean (decoded aptX HD was verified full-band 24-bit by PCM capture + FFT).
- Fix: write only the changed DSDM0/1/2 bytes over regi2c (no SDM stop, no calibration), at most
  every 100 ms and only for >= 2 ppm. Readback matches the written word; loop stays locked (phase
  < 0.6 ms, 0 drops, 0 underruns). User confirmed by ear: "Sounds clean now".
- Same session: integer-only audio path (no float): 7-band EQ (63 Hz shelf, 160/400/1k/2.5k/6.3k
  peaks Q1.1, 16 kHz shelf) from Q28 tables (`tools/gen_eq_tables.py`), branch-free split
  accumulator with error feedback (54 cycles per band per sample, DSPBENCH), fused gain / 24-bit
  saturation / L-R / pack pass, noise-shaped TPDF-dithered 16-bit output. Per-codec I2S width kept
  (16-bit except aptX HD 32-bit). Settings NVS v2 migrates the 5-band EQ.
- Not yet re-verified after this change: party sync on followers with the slower (100 ms) steering.

## 2026-11-19 (night) — Faster broadcast, connect stutter, speaker-only app (build app-1795121820896470600, all boards)

- Broadcast on: the master picks its channel before it becomes the source and starts Wi-Fi at
  once (deferred only while the phone still streams a solo-only codec such as aptX). Measured
  1.41 s to master and 1.51 s to the first follower, also when starting from aptX HD.
- Fixed the 8-byte leak per broadcast cycle (the Wi-Fi wake window was never unregistered) and a
  false "OUTPUT RAN DRY" count after a real underrun.
- Absolute volume survives the codec handover: AVRCP is disconnected with A2DP so Android
  negotiates it again, and the target volume is seeded on connect (no more interim volume 0).
- BLE connect stutter fixed (build app-1795138962869605600). Android holds a 7.5 ms interval while
  it discovers services. A radio trace showed A2DP packets reaching the speaker only every ~50 ms in
  that window, with the host acknowledging each within 21 ms: the controller, not our code, starved
  the Classic link.
  - Fixes: controller BLE auto latency (CONFIG_BTDM_CTRL_AUTO_LATENCY), a smaller GATT database
    (no Service Changed, Device Information = firmware revision only), and the app requesting MTU 517
    before discovery. The 7.5 ms window is now about 320 ms.
  - SBC on COM7: lost packets per app connect 45 → 0 in 4 of 4 connects (0 concealed, 0 underruns,
    no input gap over 50 ms; 15 s baselines also 0).
- PHONECONNECT no longer has phone addresses in the code: last A2DP peer, else the bonded list.
- Speaker-only app (JBL-style, see docs/ble-control-protocol.md):
  - The advert carries the service UUID and `WM` manufacturer data (version, product, status).
  - A mutual HMAC-SHA256 identity handshake runs on `aefb0009`.
  - Every control write is refused until the app has proven the key, and unproven links are dropped after 10 s.
  - The default key is public (identity_key.h / identity_key.dart); builders can swap in their own.
- Bench identities: the test phones' serials and Bluetooth addresses are read from the git-ignored
  `tools/bench_ids.local.json` (tools/bench_ids.py), not from the sources.

## 2026-11-19 (late) — App connect during a party (build app-1795149068348475400, all boards)

- On a party master the 7.5 ms discovery phase lasted ~1 s (each round trip ~60 ms: Wi-Fi takes
  40 % of the radio) and the phone dropped 6-8 A2DP packets per app connect.
  - Database cut to the control service plus Generic Access (firmware revision now `aefb000a`).
  - While the app connects to a party master, it sends 3 copies and Wi-Fi gets 30 % (`WMP_BLE_BOOST`).
- GATT Database Hash was tried and removed: Android on the test phone never reads it and always
  rediscovers. Android sometimes serves a stale cached database; the app clears it and rediscovers.
- Results (SBC/AAC party, app -> master 6/6, -> follower 5/6 clean on all three speakers; solo every
  codec 8/8 clean). One run with the app on COM4 had a party-wide gap: the master's Wi-Fi send queue
  overflowed (`qfull` +44, TX credit waits up) and the timeline recovered; under investigation.
- Underruns and slow decodes now carry `underrun_at_ms` / `slow_at_ms` in `WMP_DECODE`.
