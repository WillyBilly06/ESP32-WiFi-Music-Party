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

## 2026-11-20 — Karaoke effects, RAM trims, effects below the Wi-Fi task (build app-1795163168373401100, all boards)

- Karaoke effects on the music (`fx.c`, integer only, IRAM): vocal remover, echo (250 ms), reverb
  (4 combs + 2 allpasses at fs/2), chorus (20 ms). Rings are allocated only while an effect is on
  (14.3 KB with everything on) and only if 10 KB of heap stays free. Effects characteristic
  `aefb000b`, control opcode 7; a party master sends its settings in a 44-byte announce.
- Worst case (every effect on, every value at max): solo aptX HD 48 kHz 24.5 % of one core, 0 lost,
  heap 61.6 KB free. 3-speaker party: master heap 24.9 KB free (18.5 KB minimum).
- RAM: Wi-Fi WPA3/Enterprise/NVS off, 12 management buffers, BLE scan cache 10, host ACL buffers 6,
  esp_timer 2048 / event 1536 stacks, party receive queue 8 -> 6. `STACKS` prints every task's
  unused stack plus the heap held by the radio, audio and source.
- Stutter found and fixed: the Wi-Fi driver task runs on core 1 at priority 23; the EQ/effects stage
  ran at the render priority 24 and held it off (up to ~3 ms per block). The master's TX completions
  slowed (5.7-6.1 ms, 45-57 credit waits/s vs 4.0-4.2 ms, 19-23/s at 22, same session, 3/3), and when
  the channel was also busy its send queue overflowed (qfull +72..+317) and every speaker concealed
  (master up to 311 frames, followers up to ~1350). The EQ/effects stage now runs at 22 like decoding.
  After the fix: party with every effect at max, 0 concealed/late/underrun on all three speakers;
  master TX completion 4.1 ms with effects at max (4.3 ms off); app connect + 20 volume taps clean.
- Same-session A/B after the fix, 3 x 120 s effects off vs max (master + COM6): master TX completion
  4.26-4.35 ms off, 4.10-4.54 ms max, send queue never full, 0 concealed/late. Open: COM6 had one DMA
  underrun in the last max window, with decodes over 10 ms rising (2-10 per window); under investigation.
- `dsp_us` in `WMP_SIGNAL` now includes time preempted by Wi-Fi, so it is wall time, not CPU load.

## 2026-11-20 (night) — Follower underrun work; COM6/COM4 USB serial lost (build app-1795166743380470200, all boards)

- Remaining effects-max blemish after the priority fix: one-block (5 ms) DMA underruns on a follower,
  about one per 9-10 min with every effect at max (COM6, 2 in ~22 min; none with effects off).
  Decode and the EQ/effects stage both run below Wi-Fi on a follower; measured wall times up to
  14.6 ms decode and 13 ms DSP against a 30 ms ring (6 x 5 ms).
- Changes: (1) the output stage processes and pushes one DMA block at a time instead of a whole
  group (build app-1795164855844239300: DSP wall max fell from 9-13 ms to 5-8 ms; one underrun
  remained in 9 min of max); (2) with effects on, the player gives a missing group up 6 ms earlier
  (refill guard 2 ms + 6 ms + one block), and `WMP_DECODE` now reports `slack_min_us`, `slack_tight`
  (group starts with under 8 ms in the ring) and `underrun_slack_us`.
- Verification of (2) on a follower is still open: at 01:34:36 COM6 and COM4 stopped sending any
  serial data at the same instant, mid-reply, while both kept playing their part on the air (the
  master still heard their presence reports and repair requests). A later esptool connect and a
  plain EN reset of COM6 produced no bytes at all (not even the ROM boot log); the reset did reach
  the chip (it left the party). No Windows USB event was logged. Both USB-UART links need a replug.
  COM6 is now idle after that reset; COM4 still follows. COM7 (master) was clean in every window:
  5 x 300 s off/max A/B, TX completion 4.1-4.5 ms, 0 concealed/late/underrun, send queue never full.

### Goal soaks, COM7 solo (same build, 1 % volume, measured over direct serial)

| Mode | Effects | Minutes | Concealed / late / underrun | Phone packets lost | Playout phase |
|---|---|---|---|---|---|
| Solo SBC 44.1 kHz | off | 60 | 0 / 0 / 0 | 0 | 790-791 us, flat |
| Solo SBC 44.1 kHz | all at max | 60 | 0 / 0 / 0 | 0 | 790-791 us, flat |
| Solo aptX HD 48 kHz | off | 60 | 0 / 0 / 0 | 0 (>= 6302 packets every minute) | 617-622 us, flat |
| Solo aptX HD 48 kHz | all at max | 60 | 0 / 0 / 0 | 0 | 620 us, flat |
| Solo aptX HD, app connected | off | 60 | 0 / 0 / 0 | 0 | 619-620 us, flat |
| Solo AAC 44.1 kHz | off | 60 | 0 / 0 / 0 | 0 (>= 2530 packets every minute) | 378-442 us |
| Solo AAC 44.1 kHz | all at max | 60 | 0 / 0 / 0 | 0 | 378-442 us |
| Solo aptX 48 kHz | off | 60 | 0 / 0 / 0 | 0 (>= 4301 packets every minute) | 611-622 us |
| Solo aptX 48 kHz | all at max | 60 | 0 / 0 / 0 | 0 | 611-622 us |

- App on the master during aptX HD playback: 10 connects (force-stop, connect) each clean, then 50
  volume taps in 30 s clean, then the app stayed connected for the hour above (no reconnect).
- Master heap minimum since boot 18,356 B (from the party earlier); smallest free stack ipc0 324 B.
- The phone's per-device codec switch ("HD audio") only appears a while after a fresh connection.
- Not yet run: every party mode, follower-side BLE, party events (all need the
  COM6/COM4 USB serial links back).

## 2026-11-20 (afternoon) — 3-speaker party soak failed; congestion shedding (build app-1795217874161961900)

### Party SBC soak, 3 speakers (build app-1795166743380470200), FAILED

COM7 master + COM6 + COM4, volume 0, phone playing YouTube, 60 min effects off then 60 min all at max.
Both hours failed the 0-frame target; every minute outside these episodes was clean on all three.

| Hour | Minute | COM7 (master) | COM6 | COM4 | Cause (from the boards' counters) |
|---|---|---|---|---|---|
| effects off | 48-49 | 686 concealed, 7 timeline recoveries | 670 concealed, 10 late | 661 concealed, 9 late | Phone stopped sending for 18.4 s without suspending the stream (`WMP_MEDIA_GAP ms=18418`, then AVDTP start): YouTube between videos. The master publishes silence only when the stream is not "streaming", so the timeline restarted 7 times. Not fixed yet. |
| all at max | 21-22 | 2297 concealed, send queue full 2074, 23 recoveries | 6137 concealed, 954 late, 2 underruns | 6137 concealed, 959 late, 1 underrun | Busy 2.4 GHz air at the master for ~40 s: mean TX completion 11.3 ms (normal ~4.2), up to 90 ms. The scheduler kept sending repeat copies after their play time (3828 late copies), the send queue filled, the phone-input queue then dropped groups for every speaker including the master. Phone Wi-Fi is on 5 GHz (ch 100), so not the phone's own traffic. |
| all at max | 55 | clean (510 repair requests unserved) | clean | 2084 concealed, 5 underruns | COM4 only: it received 1.8 copies per group instead of ~3.7 and missed 25 % of groups for ~18 s (RX gaps up to 370 ms) while the master's TX timing was normal and COM6 received everything. Local to COM4's reception. |

Heap minimum on the master in that run: 20,016 B (effects max); smallest free stack ipc0 324 B.

### Fix: congestion shedding on the master (TXSHED, default on)

- Change (`radio.c`, `wmp_time.c`): (1) an original copy whose presentation time is within 20 ms
  (the existing repair guard) by the shared clock is retired instead of transmitted; without a clock
  it stays deliverable; (2) among ready originals, a lower copy index goes first, so under a backlog
  every group gets its first copy before any group gets a repeat. 3 or 4 copies are still scheduled.
  `TXSHED 0|1` switches it live; `WMP_TX_LIFETIME` reports `shed`, `original_expired_trains/copies/unsent`.
  Host tests for both helpers (179 protocol tests pass).
- Same-session A/B, master COM7 playing the phone, COM4 and COM6 both sending full-size probe load
  (`LOAD 1 500 4 24000 1392`, ~330-390 frames/s each, the most one board reaches), TXSHED toggled
  live in 3 interleaved rounds; totals from the dashboard's STAT blocks grouped by `shed`:

| Master under load | TXSHED=0 (810 s) | TXSHED=1 (1286 s) |
|---|---|---|
| Mean TX completion | 14.9 ms | 13.1 ms |
| Master concealed frames | 1685 | 0 |
| Send-queue full (groups dropped) | 12,079 | 0 |
| Phone-input queue full | 2,968 | 0 |
| Copies sent after play time | 82,596 | 0 |
| Copies retired (groups never sent) | 0 (0) | 4,564 (0) |
| Timeline recoveries | 19 | 0 |

  A one-jammer run (~470 frames/s) only raised TX completion to ~7.3 ms; neither setting dropped or
  shed anything there (inconclusive, kept as such).
- Field episode on the new build at 16:08: busy air for ~2 min; the master retired 2676 late copies,
  no group went unsent, no send-queue overflow, master clean; followers lost 150 (COM6) and 280
  (COM4) frames. Compare minute 21 above (thousands per speaker). Not an A/B.
- Followers are not measured by this A/B (they were the load).

### Found during the A/B (open)

- After the load ended, COM6's stop command was never read: a board sending this probe load starves
  its own console and IDLE1 (task-watchdog warnings, `wmp_tx` on CPU 0, `wmp_rx`/`wmp_audio` on CPU 1;
  only on boards in `LOAD 1`, never on the master or a normal follower). COM6 kept loading the
  channel for ~15 min until an EN reset (esptool read-mac, flash untouched). Test-tool limitation.
- While that load continued, the master underran ~2000 frames/min (decode up to 58 ms, phone RTP
  arriving ~2.8x faster than normal after the stall) and its heap minimum fell to 10,524 B (goal: 15 KB).
  It recovered by itself once the load stopped.
- COM4 stayed stuck afterwards: phase 12.8 ms off the timeline (normal ~1 ms), concealing nearly every
  frame for >5 min with the air clean, until a leave/rejoin (BROADCAST 0/1), after which it was clean.
  A follower that does not realign by itself is a stability bug; root cause not found yet.
- `BROADCAST 0` switches Wi-Fi off, so the probe load only works on a board that stays in the party;
  `LOAD 0` keeps the load's copies/spacing on the follower (reset to `LOAD 0 90 4 33000 476`).
- The master's STAT dump now takes ~10 s (13.8 KB at ~1.4 KB/s) against ~5 s on the previous build,
  with TXSHED 0 or 1 alike (9.6-10.3 s); followers take ~3 s. Cause not found. Its effect: the serial
  dashboard's Party view polled STAT every 3 s regardless and queued requests on the master without
  end, so commands timed out. The dashboard (outside this repo) now waits for each reply (30 s cap).
- COM4 reception: in the first minutes after this build's bring-up COM4 again lost ~25-50 % of
  copies in bursts while COM6 received all 4 copies of every group. Physical placement not checked.

### Fix: stale-cursor guard on followers (CURSORGUARD, default on; build app-1795230842126952000, all boards)

- Root cause of COM4 staying stuck after the load test: a follower re-anchors its sample-to-time
  mapping only when it plays a group. After a long stretch of concealment while the shared timeline
  moved later (master phase grew ~590 ms under the load), its cursor was ahead of the stream by more
  than the buffered lead. Every arriving group was then behind the cursor although its own
  presentation time was still ahead (`BUFFER.queued_on_time_late` 70,831), each was dropped as late,
  none was played, so the mapping was never refreshed. Phase still read ~1-13 ms, so the phase-based
  realign never fired. Only leaving the party cleared it.
- Change (`audio.c`): three arrivals in a row that are behind the cursor but more than 50 ms before
  their own presentation time reopen playout from the buffered groups (one faded gap, like a rejoin).
  `WMP_CURSOR` reports `guard`, `resyncs`, `behind_run`. Diagnostic `ANCHORSHIFT <ms>` (followers only)
  puts the cursor ms ahead of the stream at an unchanged play time, reproducing the state.
- Same-session A/B on COM4 in the 3-speaker party (phone playing, volume 0), 71 s after each injection:

| COM4 after ANCHORSHIFT 700 | guard off (2 rounds) | guard on (2 rounds) |
|---|---|---|
| Concealed frames | 23,976 / 23,974, no recovery | 8 / 8 (one gap) |
| On-time arrivals dropped as late | 6,019 / 6,017 | 2 / 4 |
| Resyncs | 0 / 0 | 1 / 1 |

  Switching the guard on while stuck recovered with one resync (938 / 940 frames, then clean).
  ANCHORSHIFT 200 with the guard on: 1 resync, 8 frames. During normal party play the guard fired
  0 times (COM6 and COM7, ~1 h each; COM4 between injections).
- Command-parser bug found on the way: `sscanf("ANCHOR %32s %63s")` also matched "ANCHORSHIFT 700"
  (a format space matches zero characters), so the first ANCHORSHIFT runs were parsed as an ANCHOR
  request and rejected (key shorter than 8). ANCHORSHIFT is now parsed first. Those runs are void.

## 2026-11-20 (evening) — 2-hour 3-speaker party soak on build app-1795230842126952000

COM7 master + COM6 + COM4, volume 0, phone (REDMAGIC) playing YouTube over SBC, measured every minute
over direct serial. Includes the TXSHED and CURSORGUARD fixes above.

| Hour | COM7 (master) | COM6 | COM4 | Result |
|---|---|---|---|---|
| effects off, 60 min | 0 concealed / 0 late / 0 underrun | 0 / 0 / 0 | 0 / 0 / 0 | **pass** (0 phone RTP gaps, 0 send-queue full, 0 recoveries, 0 resyncs, 0 groups unsent) |
| every effect at max, 60 min | 0 / 0 / 0 | 4 / 0 / 0 (one event, minute 17) | 44 / 0 / 0 (minutes 26, 30, 42, 47) | **FAILED** on both followers |

- Each failed event is 1-6 whole groups (4 SBC frames each) that never reached that one follower: all
  4 copies were lost (`RX.highest - RX.unique` = 1-6), and every repair request the master heard for them
  was a `REPLAY.cache_miss` (5 events, 39 misses, 1 `too_late`): the master's repair cache is its own
  playout pool (40 slots), and the group was no longer in it. Why it was gone (already played, or
  released early) is not established yet. The events on COM6 and COM4 happened at different times,
  so the master did not skip a group (0 retired unsent).
- Repair requests heard by the master: ~90/min with effects off, ~125/min with effects at max. That
  difference alone did not show whether the effects cause loss, so a same-session A/B followed.

### A/B: effects off vs every effect at max (same party, same session)

Effects switched on the master (followers follow) every 5 min, order alternating per round, 10 s
settle before each window. 5 of 6 planned rounds completed (25 min per condition); the PC then hung
at 22:52 (unexpected shutdown, no crash dump), which also reset COM7 and COM6. A first attempt is
void: a variable clash in the test script made each window ~15 s long.

| 25 min per condition | effects off | effects at max |
|---|---|---|
| COM6 concealed | 71 (one window) | 8 (two windows) |
| COM4 concealed | 4 | 132 (two windows) |
| COM7 (master) concealed | 0 | 0 |
| Repair requests heard by the master | 2,981 | 3,244 (+9 %) |
| Repair requests the master could not serve (`cache_miss`) | 22 | 51 |
| Groups retired unsent / phone RTP gaps | 0 / 0 | 0 / 0 |

- Result: the effects are **not** the cause. Unrepaired losses happen with the effects off as well
  (COM6 lost the most with them off), they move between followers from window to window, and the
  master is clean in both conditions. The clean first hour of the soak was the air, not the setting.
- So the open defect is: a follower occasionally loses every copy of some groups, and the master
  answers the follower's repair requests with `cache_miss`. The next step is to record when each
  missed request arrives relative to the group's play time and its copies, so the fix can be chosen
  on evidence (longer shared lead vs. keeping groups for repair longer).
- The playout correction stepped to about -1200 ppm for one minute on all three boards together at
  minutes 8, 38 and 58 of the first hour (phase stayed within 1.3 ms; nothing concealed). The master's
  timeline slew, followed by both followers as designed.
- No drift between speakers: phase 0.8-1.3 ms on every board in every minute.
- Phone RTP rate fell from ~4,130 to ~2,580 packets/min at minute 49 of the first hour (the phone's
  choice; 0 gaps).
- Heap minimum since boot on the master: 34,172 B (effects off), 19,148 B (effects max), goal 15 KB.
  Followers 41-42 KB / 26-27 KB.
- Smallest free stack: `ipc0` 324 B on COM7 and COM6, **244 B on COM4** (goal 256 B). The IPC task
  uses ESP-IDF's default 1024-byte stack. Not fixed yet.

## 2026-11-21 — IPC stack fix and a clean 60-minute effects-max party on build app-1795245400742070900

Build changes (all three boards flashed at 921600, COM4 ROM MAC rechecked before the write):
- `CONFIG_ESP_IPC_TASK_STACK_SIZE=1280` (was the 1024 default). `ipc0` free stack on the master is
  now 580 B (was 244-324 B; goal 256 B). Heap cost ~512 B; master heap minimum after bring-up 34,056 B.
- New `WMP_REPAIR_MISS` line in the master's STAT: for every repair request the master cannot serve
  (`cache_miss` or `too_late`) it bins how far the group was from its play time and records how long
  ago its copies went out. This is to establish why missed groups are gone from the master's pool.

60 min, 3-speaker party (COM7 master, COM6 + COM4 followers), SBC, every karaoke effect at max,
volume 0, after the PC hang and a fresh bring-up:

| 60 min | concealed | late | underrun | phone RTP gaps | queue full | resyncs | heap min | smallest stack |
|---|---|---|---|---|---|---|---|---|
| COM7 (master) | 0 | 0 | 0 | 0 | 0 | 0 | 18,972 B | `ipc0` 580 B |
| COM6 | 0 | 0 | 0 | 0 | 0 | 0 | 25,396 B | `wmp_rx` 496 B |
| COM4 | 0 | 0 | 0 | 0 | 0 | 0 | 25,656 B | `wmp_rx` 504 B |

- Pass for this one hour: party SBC with effects at max, 0 frames lost on every speaker. No drift:
  phase 0.75-1.12 ms on every board every minute.
- Repair requests heard by the master: 5,011 (~84/min); all served (`cache_miss` 0, `too_late` 0).
  So the new miss record stayed empty and the cache-miss cause is still **not established**. This
  hour's clean result does not show the defect is gone: the same build lost frames in the second hour
  of the previous soak, and losses come and go with the air. More soaks with the record are needed.
- The playout correction stepped to about -1200 ppm for one minute on all three boards together at
  minutes 20 and 52 (as in the previous soak; nothing concealed).
- Phone RTP rate fell from ~4,000 to ~2,600 packets/min at minute 9 (the phone's choice; 0 gaps).
- The master's accumulated phase correction is 60.9 ms after this boot (it was 164 ms in the
  previous boot). It is not the latency: the shared lead stays `requested_us=300000`.

### 60 min, 3-speaker party, SBC, effects off, same build and boot — FAILED (COM4 4 concealed)

| 60 min | concealed | late | underrun | phone RTP gaps | resyncs | heap min |
|---|---|---|---|---|---|---|
| COM7 (master) | 0 | 0 | 0 | 0 | 0 | 18,972 B (since boot, includes the effects-max hour) |
| COM6 | 0 | 0 | 0 | 0 | 0 | 25,396 B |
| COM4 | **4** (one group, minute 14) | 0 | 0 | 0 | 0 | 25,656 B |

- The master heard 9,273 repair requests since boot and served all of them (`cache_miss` 0,
  `too_late` 0, `WMP_REPAIR_MISS` empty). So this loss is **not** the cache-miss defect.
- COM4's STAT window (uptime 4857-4887 s) holding the loss: `CONCEAL missing+4` (the group was not
  there at its play time; not a decode error), `late+0`, `RX stale/conflict/qfull+0`, no new receive
  gap (`max_gap_us` stays 97 ms), and `NACK_DEADLINE peak_attempts` rose from 3 to **7** (one repair
  request needed up to 7 tries, 40 ms apart). The master served them, so the replies, or the requests,
  did not get through in time. COM4 also showed `DMA queue_overflow+1` and `DECODE slack_tight+5` in
  that window.
- The same window holds a master timeline retarget: at uptime 4864 s the master's slack monitor moved
  the shared phase target by +36.0 ms (phone input arrived late; `input_behind_max_us` 188 ms since
  boot), and 60 s later by -30.7 ms. Each retarget is a 1200 ppm common slew that every speaker
  follows for ~30 s. This boot had 6 retargets (30.1, 30.8, 36.0, -30.7, 32.8, 32.4 ms) and this was
  the only loss, so one coincidence (about a 2 % chance per loss) does not establish a link.
- Next: the followers' fault log (`FAULTLOG 1`, 16 records, off by default) is now on for COM6 and
  COM4, so the next missing group gets its play-time lead and pool state recorded, to compare with the
  master's retarget times and the repair attempts.

### Source switched to the Linux VM (user needed the phone), 2026-11-21 ~01:50

The user needed the REDMAGIC, so the source became the identity-admitted Linux adapter
(E8:47:3A:EC:51:9A, Realtek 0bda:8922, hci0 on the Ubuntu VM), playing SomaFM Groove Salad through
`tools/linux_music_source.py` to COM7 over SBC, now at 48 kHz. **These are Linux-source measurements,
not phone measurements.** COM7 does not offer inquiry scan during a party, so `PARTY 0` was sent for
the pairing and `PARTY 1` afterwards (this flag only gates Classic visibility; party auto mode stays off
until COM7 reboots).

- An effects-off phone soak started 01:31 was stopped at ~10 min when the phone was released (0 frames
  lost up to then). Its `soak60d.py` child kept running after the stop, so from 01:51 two soaks polled
  the boards at once ("Access is denied" on COM7).
- **Void run** (Linux source, first attempt): during that double polling, at minute 13 the master let
  33 groups expire unsent (`original_expired_unsent+33`, 38 repairs unserved) and COM6 concealed 69 and
  underran 4. At minute 25 the Linux link was dropped by accident. The run is void, but the minute-13
  event is kept: two back-to-back STAT dumps on the master (about 14 KB each, ~1.2 s of UART time at
  115200 each) coincided with the master failing to send. Possible mechanism (not proven): any
  non-console task that prints while STAT fills the 1 KB UART buffer blocks; on the master the A2DP
  media handler prints `WMP_MEDIA_GAP` on input gaps over 100 ms. Single polling every 30 s gave
  clean hours, so serial output is not shown to cause the routine losses.

### 60 min, 3-speaker party, SBC 48 kHz, effects off, Linux source — FAILED (COM4 8 concealed)

| 60 min | concealed | late | underrun | source RTP gaps | resyncs | heap min | smallest stack |
|---|---|---|---|---|---|---|---|
| COM7 (master) | 0 | 0 | 0 | 0 | 0 | 18,972 B (since boot) | `ipc0` 580 B |
| COM6 | 0 | 0 | 0 | 0 | 0 | 25,396 B | `wmp_rx` 432 B |
| COM4 | **8** (one group at minute 7, one at minute 19) | 0 | 0 | 0 | 0 | 25,656 B | `wmp_rx` 440 B |

- COM4's fault log: each lost group is 4 SBC frames that **never arrived** (`arrived_us=0`), concealed
  5-10 ms before their play deadline, with 25-30 of 40 pool slots held (not a full pool).
- The master's `cache_miss` rose by 4 and 5 in exactly the STAT windows of those two losses (and 0
  elsewhere in the hour). So here the losses **are** the cache-miss case: COM4 lost every copy and the
  master no longer had the group when COM4 asked for it.
- `WMP_REPAIR_MISS` stayed all zero despite those misses: the record sits behind the optional repair
  ring (`COPYLOG 1`), which is off by default. Diagnostic bug, not a firmware fault. `COPYLOG 1` is now
  on for the master for the next hour; the code will be fixed so the record does not depend on it.
- The master holds a group for repair only until it plays it itself (the pool releases the slot after
  decode). Hypothesis to test with the record: COM4's later repair tries arrive after the master has
  already decoded and released the group.
- With the Linux source the master retargeted its timeline 168 times in about 2 hours since the switch
  (6 times in the 2 hours on the phone in the same boot). Linux delivers A2DP in a different burst pattern; each retarget is a 1200 ppm common slew.
  Not linked to the losses yet.

### 60 min, 3-speaker party, SBC 48 kHz, effects off, Linux source, master `COPYLOG 1` — FAILED (master 6 underruns)

| 60 min | concealed | late | underrun | source RTP gaps | resyncs |
|---|---|---|---|---|---|
| COM7 (master) | 0 | 0 | **6** (minutes 8, 23, 29, 47, 55, 59) | 0 | 0 |
| COM6 | 0 | 0 | 0 | 0 | 0 |
| COM4 | 0 | 0 | 0 | 0 | 0 |

- No repair misses this hour (`cache_miss` +0), so the miss record stayed empty again.
- The master's drift controller retargeted **149 times** in the hour. Every underrun minute shows the
  master slewing at about ±1130-1200 ppm. In the master's STAT window holding the underrun at uptime
  16349 s, the controller retargeted 3 times and its output correction swung from about -1200 to
  +1200 ppm (`correction_ppm +2426`), with `DECODE slack_tight +11`. The master's own output buffer is
  only about 1-5 ms (`DMA pending` 64-220 frames at 48 kHz), so a 2400 ppm reversal drains it.
- Root cause (controller side): every 1 s it takes the minimum ingress slack and, if that is outside
  `requested ±30 ms`, retargets the shared phase by the full error (up to 50 ms) at 1200 ppm. The Linux
  source delivers A2DP in bursts; one late burst moves the timeline later, the next clean second is then
  above the band and pulls it back: a reversal every few seconds to minutes. With the phone the same
  controller retargeted 6 times in 2 hours. A host model (one group in every 7 s arriving 40 ms late)
  reproduces it: 26 reversals in 10 simulated minutes (~156/h).
- Possible confound: `COPYLOG 1` was on for this hour only. The previous Linux hour (COPYLOG off, a
  similar ~150 retargets/h) had no master underruns, so the same-session A/B below keeps COPYLOG off.
- The COM4 loss at minute 14 of the phone effects-off hour (with `DMA queue_overflow+1`,
  `slack_tight+5`) also fell inside a +36 ms retarget. Not established as the same mechanism.

### Build app-1795264443378988700 (all three boards, flashed 921600, COM4 ROM MAC rechecked)

- Event lines from tasks other than the console (Bluetooth host, radio, audio) no longer wait for a
  STAT body to drain: one shared 256-byte line, written only if it fits in the UART transmit buffer now,
  else dropped and counted (`WMP_CONSOLE event_lines/event_dropped/event_truncated`). The console task
  still blocks for its own output. Residual wait: up to the console's current single write.
- `WMP_REPAIR_MISS` no longer needs `COPYLOG 1` to count (it still needs it for play time and copy ages).
- Drift controller made tunable at run time for an A/B, defaults unchanged:
  `DRIFT <window_ms> <slew_ppm> <hold_windows>`, reported in `WMP_LATENCY`. New option `hold_windows`:
  input arriving late still retargets at once, but the timeline is pulled earlier only after that many
  consecutive windows above the band (using their smallest minimum). Host tests (181 pass): the old
  setting reverses 26 times in the model, `DRIFT 1000 300 30` at most once, with under a quarter of the
  retargets.
- Same-session A/B now running on the Linux-source party: arm A `DRIFT 1000 1200 1` (old) vs arm B
  `DRIFT 1000 300 30`, alternating 15-minute windows, 4 rounds.

### Drift controller A/B (Linux source, 3-speaker party, SBC 48 kHz, effects off) — master PANIC in the last window

Same session, build app-1795264443378988700, COPYLOG off, alternating 15-minute windows, 4 rounds.
Per-window deltas: `scratchpad/abdrift1.txt` (kept).

| arm | clean windows | master underrun | follower concealed / underrun / dma_overflow | master retargets |
|---|---|---|---|---|
| A `DRIFT 1000 1200 1` (old) | 3 (45 min) | 0 | 0 / 0 / 0 | 93 (2.1/min) |
| B `DRIFT 1000 300 30` | 4 (60 min) | 0 | COM6 0 / **1** / **1** (round 3, `slack_tight` +6); COM4 0 | 38 (0.63/min) |
| A, round 3 (last window) | — | master stopped answering | COM6 152 / 2 / 3, COM4 154 / 1 / 4 | — |

- **The last window is not a controller result.** The master panicked: `Guru Meditation Error: Core 1
  panic'ed (LoadProhibited)`, `EXCVADDR 0x00000ece`, CPU halted. The backtrace against the flashed ELF
  (hash 69ff50bac) is `record_repair` (radio.c) called from `replay` in the receiver task. Root cause: the
  previous build's change made `record_repair` keep going for unserved repairs (outcome 1/2) without
  COPYLOG. It then read `copy_trace.valid/generation/highest`, where `copy_trace` is `(*copy_history)`,
  NULL while COPYLOG is off. The first cache miss or too-late repair crashed the master. The other two
  `copy_trace` reads there were already guarded by the NULL record pointer. Fixed (NULL check) in build
  app-1795272742267898400. Both followers' concealment comes from losing the master.
- Result: arm B cuts retargets about 3.3x per minute, but neither arm reproduced the master underruns of
  the earlier Linux hour (6 with COPYLOG on), and arm B had the only follower loss. **Not enough to change
  the default; the controller stays at 1000 ms / 1200 ppm / hold 1.** The COPYLOG confound remains open.

### Build app-1795272742267898400 (all three boards, flashed 921600, COM4 ROM MAC rechecked)

- `record_repair` checks `copy_history` before reading the copy trace (above). Nothing else changed.

### 60 min, 3-speaker party, SBC 48 kHz, effects off, Linux source, build app-1795272742267898400 — FAILED (both followers, minute 18)

| 60 min | concealed | late | underrun | source RTP gaps | resyncs |
|---|---|---|---|---|---|
| COM7 (master) | 0 | 0 | 0 | 0 | 0 |
| COM6 | **4** (minute 18) | 0 | 0 | 0 | 0 |
| COM4 | **12** (minute 18) | **1** | 0 | 0 | 0 |

- The NULL fix holds: 11 cache misses this hour (3 in minute 18, 8 in minute 19), no panic.
- Both followers lost frames in the same minute, while the master could not serve their repairs
  (`cache_miss` 11, `too_late` 0, `original_expired_unsent` 0). The miss record says only `no_pts=11
  untracked=11`: on a cache miss the master has no play time for the group, so the record cannot say
  whether the group was already played, never published, or something else.
- Drift controller (default): 80 retargets/h, master and followers slewing together at about ±1200 ppm.
  Heap minimum 32276 B (master). Smallest stacks: master `ipc0` 580 B, followers `wmp_rx` 496 B.
- Next: build app-1795276925433908800 adds `WMP_REPAIR_MISS_POOL`. On each cache miss it reports the
  requested group's state in the master's playout pool (freed slots keep their header until reused, so
  "free" means played or discarded), and how far the request is from the oldest and newest groups
  still held. Diagnostics only, no behaviour change.

### 60 min, 3-speaker party, SBC 48 kHz, effects off, Linux source, build app-1795276925433908800 — clean (Linux source, not the phone)

| 60 min | concealed | late | underrun | source RTP gaps | resyncs |
|---|---|---|---|---|---|
| COM7 (master) | 0 | 0 | 0 | 0 | 0 |
| COM6 | 0 | 0 | 0 | 0 | 0 |
| COM4 | 0 | 0 | 0 | 0 | 0 |

- 0 cache misses, so the new `WMP_REPAIR_MISS_POOL` line stayed at zero (checked after the hour);
  the missed-repair mechanism still has no pool evidence. 153 drift retargets (default controller).
- Heap minimum 33152 B (master); smallest stack 464 B free (`wmp_rx`, followers).
- This bring-up first failed: COM7's Linux connect timed out once (the pairing is not bonded, so BlueZ
  forgets COM7 between connections), and the bench script's music restart killed its own shell
  (its `pkill -f` pattern matched its own command line). Neither is firmware; the void run was stopped
  before it counted.
- Of the last three Linux-source hours (effects off, COPYLOG off), one failed on missed repairs.
