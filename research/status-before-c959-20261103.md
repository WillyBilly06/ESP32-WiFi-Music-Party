# Current development status - 2026-11-03

The goal is active and incomplete. Samsung is reserved and excluded from
ADB, UI, media, volume, Bluetooth and GATT operations. The replacement fixture
uses REDMAGIC and the existing identity-admitted Ubuntu Bluetooth adapter.
No clean full-workload hour, current clean takeover, acoustic timing or
100-speaker qualification exists.

## Current exact image

Both original COM6/COM7 boards run `f7a4e6aef788600b`,
`build/app-1793729487724019100`. Both921600-baud installations passed exact
readback, protected-region/NVS checks and two identity-verified boots:

- COM6: `evidence/install-1793729658145646100-COM6/receipt.json`
- COM7: `evidence/install-1793729658161780600-COM7/receipt.json`
- Offline image/source proof: `evidence/preparation-lock-offline-1793729649303727200/receipt.json`
- Fresh real-stream pair: `evidence/preparation-lock-pair-ready-1793729989798383600/receipt.json`
- 135 current portable protocol checks: `build/protocol-1793728007155231300/receipt.json`

Fresh pinned ESP-IDF6.1 and BTstack1.8.3 remain unchanged. PCM5102A uses
WS25/DOUT26/BCLK27. ESP-NOW, CSB and delivered FEC remain excluded.
Historical FEC host tests do not mean parity is linked into the firmware.
Latest images, original backups and failed evidence are retained.

## Changes and actual remaining failure

The NACK tracker uses the common presentation deadline, with at most16
accepted requests inside that bounded lifetime. Entry size remains32B;
the old eight-attempt path remains only for unshared legacy timestamps.
The source checks the missing group's actual cached deadline before repair.
Eight immutable repair references remain bounded within the audio pool.
Disabling pending-repair deduplication was tested and rejected after source
queue/audio faults; it remains enabled.

The audio pool now has64 frame slots and32KiB payload storage. A regression
covers500ms at48kHz plus15 incoming groups and two receive reserves. It does
not prove all codec modes, repair overlaps or arbitrary bursts fit.
The source metadata staging queue is16 entries/1664B, increased from eight:
eight admitted repair messages could previously occupy the entire queue.
BT host stack is3072B. Receive task stack is3584B, after the prior3072B task
left only396B reserve under the full workload. The active native TX window
is eight; maximum allocated metadata capacity is16 instead of32. Pruning
unused metadata/queue entries saves approximately832B while the RX stack
adds512B. Payload size, SBC bytes, four originals and active window are unchanged.

| Exact observation | Source faults | Receiver fault | Resource/coverage result |
| --- | --- | --- | --- |
| Prior queue16 image4330,181.875s |0 blocked/input gaps/conceals/queue overflow |4 concealed frames;0 late/underrun | Minimum sampled source heap30296B; receiver stack396B failed strict admission |
| Prior5850 strict pilot,49.531s |0 blocked/input gaps/conceals/queue overflow |4 concealed frames;0 late/underrun | Source heap at least32076B, receiver49348B; receive stack at least1216/1024B on source/follower |

Receipts: `evidence/pipeline-timing-1793727252773979000/receipt.json` and
`evidence/party-long-1793727894561115400/receipt.json`. That5850 strict
pilot stopped on its first fault and FAILED. Both actual input streams and
continuous BLE advanced, with no Classic/BLE disconnect, RX queue overflow,
invalid codec, API error or DMA underrun in that measured interval.
These counters separate the receiver's missing group from earlier source
staging failures; they do not identify RF loss versus receive/repair handling.
Post-fault copy snapshots cannot reconstruct an unarmed historical group.
The prospective observation at
`evidence/replay-watch-1793727989990356600/receipt.json` reproduced four
consecutive missing frames at171.735s. Both input gap counts and all source
audio/staging faults remained zero; both BLE clients applied277 commands.
For matching group27099, source repair-decision metadata recorded all four
originals accepted/completed, a one-copy repair admitted with59363us left,
and a subsequent request rejected with11543us left. These masks describe
original submissions/completions, not actual repair delivery. Both rolling
copy windows had already advanced beyond27099 when retrieved. Earlier
requests may have been overwritten in the eight-entry repair history.
No receiver-copy absence is inferred outside retained coverage. All optional
histories were unloaded after acquisition. See
research/natural-receiver-fault-5850-20261103.json.

Selected intentional loss tests recovered all eight selected targets at
`evidence/delivery-path-1793723033750818800/receipt.json`. This does not
qualify all groups or natural loss. Physical tags showed some accepted and
driver-completed repairs without receiver callbacks. Completion is not
proof of RF delivery. See research/queue16-stack-budget-20261103.json,
research/source-staging-depth-20261103.json and the preserved failed receipts.

## Latest takeover and observer evidence

The current f7 image serializes the readiness flag and role publication with
publisher_lock. On5850, the first takeover really reached shared48kHz playback,
but the former leader retained source_wait=1 as a follower. Its readiness gate
failed after20seconds, and it also had16 real conceals. Both failures remain
preserved at evidence/two-phone-handover-1793729151457968000/receipt.json.

The current image passed both automatic role directions at
`evidence/two-phone-handover-1793729992872209800/receipt.json`. The gate was
unchanged. Readiness was first observed at6.937/6.297s, both Classic/BLE links
stayed connected, both input RTP gap deltas and source blocked-frame deltas
were zero, and standby input resumed without displacing the new leader.
Audio FAILED: followers concealed20/12 frames. Those increments were already
present at the first ready snapshot and did not grow during the following
approximately35seconds in each phase. Polling brackets this to startup;
it does not prove the exact packet-loss mechanism or physical latency.
See research/preparation-lock-hardware-20261103.json.

The lighter fault-first observer completed300s on5850 with no new audio/input
fault,125ms maximum diagnostic poll, and both BLE clients advancing. No new
fault was captured, so improved history retention still needs a real-fault
validation. The42.208s interval before the next test added four receiver
conceals; it is explicitly retained as failed/unmonitored coverage.
The40/20/40ms repair-spacing comparison then completed64.952/64.734/64.947s
including transitions with zero source/input/receiver faults. This does not
select20ms over40ms; the original40ms setting was restored and read back.
Receipts: evidence/replay-watch-1793728593095916100/receipt.json and
evidence/repair-release-1793728938258176500/receipt.json.

## SDK rate-configuration defect - unresolved

The pinned SDK header, components/esp_wifi/include/esp_wifi.h:1579, requires
esp_wifi_config_80211_tx_rate after init and before start. Current startup
and runtime RATE calls also invoke it after esp_wifi_start. This violates
the documented precondition. ESP_OK and selected-rate telemetry cannot
establish the physical transmit rate; this is not proof of the cause of
concealment. Fix configuration during a stopped/prestart interface lifecycle,
keep active-stream retuning prohibited, and re-measure before trusting RF
capacity calculations. See research/raw-rate-api-lifecycle-20261103.json.
The f7 readiness candidate intentionally did not bundle a rate change.

## Linux fixture and BLE reliability

VM: `E:\Linux 2\Ubuntu 64-bit.vmx`; guest192.168.63.129, userbillydakidz.
Only adapterE8:47:3A:EC:51:9A is admitted; its hci index must be verified.
Normal BlueZ direct BR/EDR connection reached COM6's actual audio sink
`bluez_output.6C_C8_40_42_BF_66.1`; media volume was read back at20%.
The owned player streams the existing SomaFM GrooveSalad source. REDMAGIC
plays its existing YouTube through COM7. COM6 discards Linux standby audio
while rendering the Wi-Fi source. Both BLE clients exchange commands and
notifications. No Samsung operation is part of this fixture.

A controlled Linux adapter assessment1/0/1 comparison yielded242/0/306
input RTP gaps over approximately one minute per case, but receiver conceals
were0/8/0. This fixture input issue cannot explain all receiver losses.
See research/linux-afh-20261103.json. Assessment mode0 is a finite diagnostic
configuration; AFH hopping remains enabled. The owned helper restores the
original mode1 at stop/timeout/link change, with independent readback.
No claim is made that this qualifies the normal Linux controller policy.
Task-owned runtime BlueZ experimental direct-connect and USB power settings
are recorded separately; no unrelated Linux audio service is stopped.

The Android BLE test service had reused stale GATT state on repeated starts.
It now closes its old owned client, resets session state, guards callback
identity and fails missing ACKs without replay. Two repeated-start sessions
passed both boards' writes and notifications at
`evidence/probe-session-repeat-1793724078350372600/receipt.json`.
The owned APK is installed on REDMAGIC only.

## Selected profile and outstanding gates

Four consecutive original SBC frames per group; four time-spaced originals,
64ms spacing plus8ms dither, native window8, repair deduplication enabled,
NACK delay120ms, repair cap8 and40ms repair spacing. Both stations associate
to the owned reference AP and use its TSF clock domain. Registered receive
window65535/interval0 and direct RX are enabled; optional TX wake gate is off.
The500ms shared presentation reserve is allowed for stability and is not a
measured phone-to-output latency. Neither pacing nor completion reserves RF.

The prior strict full-workload pilot failed, and current takeover audio fails.
Required next work is the SDK rate lifecycle correction, bounded measurements
of startup/steady receiver recovery, the unchanged strict pilot, clean takeover
audio, pause/silence, late join and at least one uninterrupted real hour. Physical PCM5102A timing
and100-node operation remain unmeasured; the reference AP is limited to eight
clients. No clean finite run or source package is a qualified release.
