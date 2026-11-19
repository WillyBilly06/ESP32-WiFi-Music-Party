# Historical status snapshot - superseded

Current AGENTS.md and CURRENT_STATUS.md take precedence. Permissions, phone states and parameters below are historical.

# Current development status - 2026-11-03

The goal is active and incomplete. Samsung remains reserved and excluded from
ADB, media, volume, app and Bluetooth control. The user authorized REDMAGIC
plus the identity-admitted Linux source as its replacement fixture. No passing
full-workload hour, clean automatic handover, acoustic latency/alignment or
100-node qualification exists.

## Current installed candidate

Both original admitted boards run `3901207d2e53c04e`,
`build/app-1793713062091472300`. Guarded921600-baud installations passed
exact readback, protected-region checks and two boots:

- COM6: `evidence/install-1793713221241151900-COM6/receipt.json`
- COM7: `evidence/install-1793713221224893600-COM7/receipt.json`

Original backups and every failed receipt remain. Latest images stay installed.
Console115200. Pinned clean ESP-IDF6.1 and BTstack1.8.3 remain unchanged.
PCM5102A: WS25/DOUT26/BCLK27. ESP-NOW, CSB and FEC are excluded.
125 portable protocol tests pass:
`build/protocol-1793713007749531300/receipt.json`.
Three host delivery-parser integrity checks also pass.
Offline proof:
`evidence/repair-pacing-queue-offline-1793713220425991100/receipt.json`.
Live pair proof:
`evidence/repair-pacing-queue-pair-ready-1793713466968835300/receipt.json`.

## Implemented source recovery fixes

The source's four-reference repair cap rejected the last selected group in
two of four deliberately missing seven-group bursts before any repair API
submission. Eight references reuse the existing immutable payload pool and
fit the current32-slot scheduler with approximately17 original groups.

A separate global8ms guard returned without retaining requests. It incremented
`deferred` but produced no repair decision or transmission. The current
implementation queues a paced repair with an absolute ready time, subtracts
queue delay from its deadline budget, and permits urgent deadline bypass.
No extra audio allocation, per-packet logging or blocking radio callback was
introduced. Source group/session identity and the authoritative cached header
govern repairs, instead of receiver codec hints.

Repairs spread up to three copies over available headroom; requests inside
the normal80ms guard may get one urgent copy above20ms. Four original copies
and all original SBC bytes/sample counts remain unchanged. Selected-identity
capture is optional,1632B per armed board, and retains CRC-protected metadata
and physical carrier tags through submission, completion, reception, handoff
and decoding. The instrumentation unloads after each owned episode.

## Current hardware evidence

`evidence/delivery-path-1793713694168151500/receipt.json` recovered7/8 selected
targets in deliberately missing seven-group bursts. Every tagged valid NACK
received a recorded decision; paced requests were queued. The remaining
failure had three accepted/driver-completed repairs but no receiver
acceptance. This is diagnostic loss injection, not a stability pass.

Unchanged full REDMAGIC-source/Linux-standby/A2DP/Wi-Fi/BLE measurement:
`evidence/pipeline-timing-1793713834918336000/receipt.json`:
61.3s,20 receiver concealed codec frames, no source conceal/RTP gaps.

With promiscuous observation alongside DirectRX:
`evidence/pipeline-timing-1793713927892362200/receipt.json`:
61.3s,12 receiver concealed frames. Promiscuous and normal receive counts
both13654, with no repeated MAC sequence numbers. This does not support
loss between those observation points; it does not prove over-air success.

The receiver-only isolation matrix is in
`research/receiver-coexistence-20261103.json`:

- Full receiver Classic A2DP plus BLE:61-63% acceptance relative to source
  native submissions; role-aware preference did not eliminate concealment.
- Receiver Bluetooth stopped:87.1%, no concealment in40.7s.
- Receiver BLE only with real transactions:85.6%, no concealment in40.6s.
- Source REDMAGIC A2DP and BLE remained active in both isolation cases.

These finite observations implicate receiver coexistence/availability when
its ignored Classic stream is active. Bluetooth-off and BLE-only cases
cannot qualify delivery. RF aborts, PHY losses and filtering before the
observation points are not distinguished.

## Restored full fixture

All three activities are restored; no isolation mode remains selected.
Latest restoration:
`evidence/pipeline-timing-1793714916305926700/receipt.json`:
30.7s,8 receiver conceals, zero late/underrun, zero upstream RTP gaps on both
boards. Linux delivered374.8 SBCframes/s at48kHz,119B per frame. Both boards
advanced actual GATT commands and notifications. This finite restored window
does not prove either-role or sustained stability.

COM7 is source; REDMAGIC plays its existing YouTube stream. Linux real music
feeds COM6 as ignored standby at20% sink volume. REDMAGIC BLE runs:
`115dd470`, `e85a6b04`. Both associated stations use the unchanged owned
reference AP on channel11 and hardware AP TSF. Profile:4 originals,64ms
absolute spacing,8ms dither,54Mbps (callback rate code12 agrees), TXwindow24,
pacing0, NACK delay120ms, repair limit8, registered wake window65535 with
default interval0, A2DPCOEX2, balanced preference0, DirectRX1, sniffer off.

Linux's admitted Realtek adapter E8:47:3A:EC:51:9A is hci0 after reboot.
The fixture now uses documented Adapter1.ConnectDevice via a temporary
BlueZ `--experimental` service override to bypass failed discovery.
Only that controller was admitted; no unrelated ACL link was present during
the service change. USB power policy is temporarily `on` (previous `auto`)
for the admitted0bda:8922 device. No OS/kernel/controller firmware was replaced.
The guest and source bridge remain running; credentials/session keys are not
bundled in the source archive.

Source minimum observed current free heap was32372B in the last30s window.
Earlier full runs reached below30000B; no lifetime heap-floor claim exists.
No physical end-to-end latency measurement exists. The500ms common reserve
is an experiment allowed for stability, not measured phone-to-output delay.

## Next required work

Implement and test a bounded connectionless receive window with a matching
interval and common-AP wake-event timing under actual full A2DP/Wi-Fi/BLE.
An older20ms interval with maximum window harmed Bluetooth, so do not repeat
an all-on interval change blindly. Espressif documents additional Wi-Fi
priority requests for customized window/interval combinations; measure both
Classic and LE effects before selecting a policy.

Keep the full goal: stable receiver delivery, both automatic role directions,
silence/resume, late join, measured heap, unchanged continuous hour and
physical timing. The current reference AP supports only8 clients;100-node
design/qualification remains incomplete. Exact image/readback/live-INFO
bindings remain mandatory. See `research/delivery-path-20261103.json` for
specific source rejection cases and the pacing fix.

## Corrections and implemented work

The old cold override forced3 copies despite a4-copy start request. Earlier
status prose overstated the count. Original receipts are preserved with
copy-count reviews. Memory/lifecycle results from those runs remain valid
for their measured3-copy workload, not a four-copy qualification.

Core session epoch telemetry is independent of optional history and has a
CRC. Copy trace rows have canonical-record CRCs. Optional FAULTLOG and
labelled DROPRANGE diagnostics distinguish planned suppression from actual
radio submission and record sample/presentation identity.

AVDTP transport START/SUSPEND/RELEASE now drives coexistence state separately
from recent RTP activity. Losing packets for500ms cannot by itself demote
an already started A2DP stream. Workload tests still require actual packets.
Election jitter is bounded20..60ms; COM6's former246ms could exceed the
8KiB staging window.119 portable protocol tests,10 trace-parser tests and
2 host-connection regressions pass; hardware full qualification remains open.

Major audio/source/codec/clock resources allocate on demand. Decoder state
unloads during sustained party silence; playback pools stay warm for resume
and release when the party is actually idle. Detailed history is opt-in via
COPYLOG, including the previously boot-resident repair-history ring. The
1232-byte source assembler is now part of the lazy11704-byte source runtime.
Stream start publishes transport state before draining, and transport lifetime
keeps the prepared queue warm across short RTP gaps. Per-packet application
callbacks reuse pools. Vendor driver allocation remains vendor managed.
ELF flash-placement audit passed in
`evidence/lazy-layout-1793664547165286300/receipt.json`.
The earlier545a candidate's cold free heap was113288B on each board.
Earlier observed source low-water values include optional diagnostic memory;
no guaranteed30KiB lifetime floor is claimed.

Current four-copy lifecycle verification passed on both boards in
`evidence/lazy-lifecycle-1793664792939530300/receipt.json`, including the
single source allocation on resume. Idle teardown reclaimed65376B on COM7
and47332B on COM6. Resumed current free heap was46364/65856B; diagnostic
global minima were34216/51876B. Both BLE clients remained active. This is
one-active-source allocation evidence, with Linux paused, not a continuous
two-source audio pass. See research/lazy-resources-20261102.md.

Both actual source streams and BLE transactions were restored afterward.
The short progress receipt is
`evidence/post-lazy-fixture-1793665011867104500/receipt.json`: the Linux
sink had verified routing/render progress, both boards received RTP and
applied BLE commands, and the source current free heap was35264B with a
34216B global minimum. This restoration check is not a long audio pass.

## Current evidence

The latest image adds decoder wall time, native completion dwell, sender
wake delay and source-loan admission age. The62s full-workload diagnostic
found342us decoding,345us send API calls and78.39ms mean native dwell.
Borrowed packet identities advanced; live backpressure remains severe.
See research/pipeline-and-wake-window-20261102.md.

Registering a generic connectionless wake window materially improved the
off/on/off comparison: original completions289.5/346.1/247.5 packets/s,
source conceals1903/0/3095 and receiver conceals10533/629/10658, with no
input RTP gaps. Both registrations are now enabled experimentally. The
60s follow-up still failed with507 source and5307 receiver conceals.
This is a partial fix, not stable audio. The current30f image registers the
window automatically when DIRECTRX is enabled, rolls back failed setup and
releases its registration when the path is disabled. Both off-to-on hardware
provisioning checks passed without an RXWINDOW command:
`evidence/auto-wake-provision-1793677525303891100-COM7/receipt.json` and
`evidence/auto-wake-provision-1793677631898486000-COM6/receipt.json`.
SDK/BTstack sources remain unchanged. Both actual phone streams and BLE
clients were restored; Samsung volume is20%. Runtime NACK delay is120ms;
the compiled default remains60ms. RXINTERVAL exposes the supported SDK wake
interval with default0; nondefault timing is under diagnostic comparison.
NACKDELAY now permits20..300ms for bounded later-request tests.
The following older sections preserve their original image/workload scope.

The user reauthorized Samsung SM-S948U1/PHONE-SECOND and requested20% media
volume. ADB identity is verified; volume index3/15 was applied and read back,
including after the earlier Bluetooth connection. Linux is paused and its
COM6 Classic connection was explicitly disconnected. Samsung is connected
to COM6 and supplies real YouTube audio at verified20% media volume; REDMAGIC
supplies the simultaneous standby stream on COM7. Audio remains unqualified.

Before the sample-clock fix, checksum-verified Samsung snapshots showed3303
group discontinuities in3513 RTP packets with only2 RTP sequence gaps, plus
source queue pressure and severe receiver concealment:
`evidence/samsung-before-sample-fix-1793671085759044300/receipt.json`.
The new assembler uses samples counted from validated SBC frames to join
contiguous RTP packets; timestamp disagreement is diagnostic, not a false
packet-boundary gap. New WMP_RTP_TIMING exposes the raw/expected timestamp
steps. SBC bytes remain unchanged. Missing playback advances by one codec
frame and clips to the next known boundary. Live Samsung verification
confirmed removal of false packet-boundary breaks, but did not solve delivery.
`evidence/live-audio-1793672366571447800/receipt.json` records the remaining
severe source transmission backlog, receiver late input and concealment.

The current repair budget is4 queued/in-flight loans. The4/16/4/16 comparison
in `evidence/repair-admission-1793673569656056300/receipt.json` showed repair
pending maxima0/0/1/0 while original transmission references stayed near41.
Repair concurrency was not the observed bottleneck; no fix is claimed.

The hardware ownership-reclamation test passed after confirmed A2DP
suspension and actual party stop on both boards:
`evidence/buffer-quiescence-confirmed-1793673989118574700/receipt.json`.
All borrowed/retired/repair refs reached0 and both audio/source pools unloaded.
The earlier stop attempt raced phone suspension, automatically re-entered
silence mode, and is not a buffer-leak diagnosis.
The live41-reference plateau also matches the ownership capacity:32 copy
jobs plus8 queued messages plus1 pending publisher. Playback release can
retire a still-pinned payload; the last transmission owner makes it reusable.
This count is consistent with backlog, not a per-reference lifetime audit.

`evidence/source-drain-rate-1793673521207872600/receipt.json` measured344.66
incoming SBC frames/s versus271.40 total accepted radio transmissions/s on
the source, including controls, with0 accepted repairs/s. Four frames/group
and four originals require344.66 data transmissions/s plus controls. This
is a measured service deficit; deleting pinned buffers would discard unsent
audio. Source Wi-Fi preference and larger native windows did not resolve it:
`evidence/source-coex-1793674108301951300/receipt.json` and
`evidence/audio-tx-window-1793674301001049100/receipt.json`.
Both settings were restored to balanced and window24. The source manual
A2DP hint2/0/2/0 comparison is now complete:
`evidence/a2dp-hint-1793674644220080500/receipt.json`. Accepted total
transmissions/s were355.3/344.7/365.0/301.2, with source input RTP gaps0
in every window. Source conceals were175/183/170/1657, and receiver
conceals2824/6418/2629/8371. These include controls/repairs and are not
measurements of intact audio at the peer. Clearing the hint was not a fix.

The receiver ownership-aware policy2/1/2/1 comparison also failed audio:
`evidence/standby-priority-1793674918132207200/receipt.json`. Receiver
conceals were5643/5833/10641/10402; source conceals338/1193/2884/2051.
Both actual phones' RTP and both BLE clients progressed with no RTP gaps
in these windows. Source backpressure varied, so receiver policy alone is
not established as their cause. The earlier attempt
`evidence/standby-priority-1793674887320405400/receipt.json` stopped before
measurement because REDMAGIC's media session was absent; existing playback
was resumed before the complete comparison, without opening a new URL.

Both boards were restored to profile2, balanced coexistence, window24,
four originals,64ms spacing,8ms dither and repair limit4. Verified current
music/BLE state and Samsung volume3/15 are retained in
`evidence/post-profile-restore-1793675123708838500/receipt.json`.
The source still had41 references and22 queued SBC groups; the follower
had0 references and48/50 free slots. Audio remains badly impaired.
Next work must distinguish transmit-task scheduling/driver wait from codec
CPU cost and audit live ownership age; no extra-buffer or priority fix is
claimed from these comparisons. No firmware changed during these tests.

Full STAT bodies have byte-count/CRC envelopes. Buffered console output
passed24 consecutive live reads without retries. The four quiet/dense
logging windows on the preceding0e41 image had no new speaker concealment
or source queue-full events; that does not establish a general cause or cure.
See `evidence/logging-load-1793668802662373100/receipt.json`.

Both automatic role directions passed finite state/readiness checks on0e41
with REDMAGIC/Linux, but both failed audio counters:
`evidence/two-phone-handover-1793669308246401400/receipt.json`.
The current image also prepares source queues/audio resources before role
activation and wakes the transmitter when a role command is queued. Silence
cannot revoke the current transport-announced ownership lease.

The optional8ms copy dephasing had mixed results; its two windows avoided
source queue-full increments but did not eliminate receiver concealment.
`evidence/copy-dither-1793668126971505100/receipt.json` retains the full failed
four-case comparison. Firmware default is0; runtime dephasing is8000us on
both boards for the next experiments. No stability conclusion is drawn.

`evidence/burst-repair-ab-1793656657885007800/receipt.json` completed five
seven-group suppression policies with four originals. Every policy failed
complete recovery; target conceals ranged2..7. An early-request comparison
on the new candidate,
`evidence/burst-repair-ab-1793659887424364900/receipt.json`, also failed all
five cases. Source radio-budget tuning has not solved delivery.

`evidence/copy-diversity-1793656954389775500/receipt.json` measured four-copy
spacing on live REDMAGIC music and BLE on both boards.32/96ms cases added a
concealment near8s;48/64ms completed45s without new audio faults. The longer
64ms pilot `evidence/music-window-1793657181799734800/receipt.json` failed
at93.422s with one receiver concealment. It added no source RTP/input-loss,
codec, queue, write or timeline-recovery fault. These were one incoming
A2DP source, not the full two-source workload.

Both actual incoming sources were admitted: REDMAGIC on COM7 and Linux
on COM6. COM6's standby counter advanced while its DAC followed COM7's
shared session. The latest strict two-source pilot,
`evidence/party-long-1793660637682943000/receipt.json`, failed at5.907s with
COM6 concealment and incoming RTP gaps. Both protocol stream/coexistence
states were active; this does not qualify full three-way coexistence.

The Linux fixture now verifies target Bluetooth routing and actual GStreamer
rendered-buffer progress, and stops its own stream on fallback. A previous
null-sink fallback and a separate DNS-start failure are preserved.

A Linux host-boundary observation already contained67 RTP sequence gaps in
8s of outgoing HCI traffic, with maximum controller-completion gap535ms:
`evidence/linux-host-path-1793660970234195200/host-hci-observation.json`.
Those gaps precede COM6 delivery. Adjacent MCU snapshots are not synchronized
to that exact interval. Raw Classic RSSI was-14 relative to the controller's
receive range, not-14dBm; reported link quality was0. The PC antenna question
was subsequently answered: the user confirmed it is attached. This does
not establish every failure's cause. Linux is now paused/disconnected;
Samsung is the current second real phone source.

## Latest full-workload diagnostics

`evidence/repair-load-1793661500448426200/receipt.json` completed a repair
on/off/on comparison without a pause, reset or role change. All four original
copies, both incoming A2DP streams and both BLE links stayed active. Receiver
concealments were5/10/7 over30.031/31.344/31.906s. Source concealments and source
queue-full deltas were0. BLE writes and notifications advanced on both.
Disabling repair did not eliminate loss; repair mode was restored on both.

`evidence/burst-repair-ab-1793662039717208100/receipt.json` tested reduced
repair-copy budgets. All four policies failed the strict recovery gate;
target conceals were2/4/5/5. Standby Linux RTP gaps also remain a full-workload
failure; target-only observations are not substitutes for that gate.

`evidence/linux-edr2-compare-1793661799588066900/receipt.json` records a
per-connection3Mbps-exclusion experiment. The controller accepted mask0xee1c.
An8s host probe still had72 outgoing HCI RTP gaps versus67 in the earlier
sample, so no improvement is claimed. Normal all-packet mask0xcc18 was then
restored with a successful packet-type-changed event. No codec or MCU image
was changed by this test.

`evidence/repair-load-1793662159195088700/receipt.json` compared54/24/12Mbps
with repairs enabled. Receiver conceals were15/261/2297 in about30s per case;
12Mbps also added213 source conceals. These rates were checked using callback
PHY enums, not just setter success. Both links were immediately restored to
54Mbps, and source decoding resumed with no new concealment in the bounded
recovery sample:
`evidence/phy54-recovery-progress-1793662356749678200/receipt.json`.
The helper now restores its prior rate on every terminal path. Lower rates
are not a stability fix. No one-hour or release gate passed.

## Fixture and qualification boundaries

Only REDMAGIC serialPHONE-MAIN and the admitted Linux adapter are used;
Samsung was reauthorized for testing at20% volume. Linux hci1
E8:47:3A:EC:51:9A is the Realtek0bda:8922
adapter in the existing Ubuntu VM. Private SSH credentials are not packaged.
Its USB power setting is temporarily on and page timeout was read back at
32768 units; previous settings were saved under its owned test folder.
Normal Classic SDP/inquiry refresh precedes reconnects; no bonds were erased.

UART owner was deliberately handed off after the previous handle terminated;
no ESP32 was reset by the handoff. Current capture is
`evidence/persistent-audio-1793671274950640800`, RPC48006. Tokens stay private.
Windows source-port retries occur only before any request bytes are sent;
no device mutation is replayed by that retry.

Both stations currently use an owned temporary AP's native TSF; custom data
bypasses AP forwarding. The AP's eight-client limit is a diagnostic limit.
AP-free/100-node operation and acoustic alignment remain unqualified.500ms
presentation reserve is a configured stability allowance, not a measured
phone-to-DAC delay. See research/repair-and-copy-spacing-20261102.md.
