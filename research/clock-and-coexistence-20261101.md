# Clock and coexistence investigation,2026-11-01

These are development measurements, not acoustic or release qualification.

- Build1cfd4e60a0a94d87 corrected an invalid combined coexistence-status clear.
  The controller then returned status0, but receiver clock beacons still
  disappeared with the second Classic phone connected. Fixing that API call
  alone did not fix reception.
- Earlier receiver code left clock_valid set after its15-second mapping
  expired. It then classified repeated startup rejection as late packets.
  Expiration/preparation counters now expose this state explicitly.
- Build520b611d89ca85d2 disables legacy11b rates through the supported public
  API before Wi-Fi start. Native source clock beacons changed from1Mbps
  (native rate0) to6Mbps (rate11). With both phones sending A2DP and both
  BLE connections exchanging actual data, matching beacons continued,
  measured clock residual was1us, and no mapping expiration occurred in
  the finite comparison. This does not measure physical speaker skew.
- Source-only control collected3224.44seconds without a source fault, then
  deliberately stopped for the source image update. It is not a one-hour
  party pass: evidence/source-long-1793588959642089700.
- At24Mbps data frames and32ms completion pacing, the paired72s-class window
  still concealed audio. Switching the unchanged SBC packets to our own
  vendor action carrier reduced loss in a finite45-second comparison but
  did not eliminate it. No esp_now API or ESP-NOW OUI is used.
- A6Mbps audio-data trial overflowed the source, unlike6Mbps clock beacons.
  It was rejected and24Mbps data restored. Preserve the failed trace:
  evidence/radio-delivery-1793592722396299500.
- With the paired phone connecting, core0 receiver dispatch stalled321849us
  while Wi-Fi callbacks on core1 continued, overflowing the8-entry queue.
  Steady-state losses also occurred with no additional application queue
  overflow, so that initial stall does not explain every missing group.
- The300ms candidate moves the receiver consumer to core1,priority19 below
  audio21 and transmitter20. It uses30 shared slots,6 native TX buffers,
  and measured-stack reductions. The896-byte live payload bound covers
  four224-byte dual-channel SBC frames at the unchanged advertised maximum
  bitpool53.16 blocks/8 subbands fix normal group duration at512 samples;
  encoded SBC bytes are forwarded unchanged. All13312 header/bitpool cases
  pass the actual C length parser against this bound.

Primary platform constraints: ESP-IDF6.1 calls sniffer receive coexistence
supported with unstable performance (C1). This is a platform constraint, not
proof of the cause of each drop:
https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32/api-guides/coexist.html
The exact public rate API is documented in the pinned unmodified header:
third_party/esp-idf/components/esp_wifi/include/esp_wifi.h.

Full two-phone/continuous-BLE/one-hour testing of the300ms candidate is pending.

## Later hardware and handover findings

- Quiet-UART comparison kept raw readers open, with no periodic status queries
  for90seconds. Both actual YouTube streams, BLE write traffic and roles stayed
  active. COM6 still added74 concealments and11 late packets; COM7 added none.
  Logging therefore is not the sole cause. Receipt:
  evidence/quiet-uart-1793595918647469500/receipt.json.
- A firmware84 handover took8.094seconds to report both renderers ready and
  added957 concealments on the new follower. The return phase was interrupted
  by a malformed telemetry field stopping the old monitor. It is not a pass.
- Firmware84 adds a unique24-bit physical TX tag in the existing vendor-action
  carrier bytes, and a separate32-bit slot owner. Overlapping replay trains no
  longer match completions solely by logical audio group/copy. That fixes an
  ownership ambiguity, but did not eliminate steady receiver loss.
- Code review found the old transition branch setmode0 before its old TX work
  drained. The next loop changed the AP beacon interval/TSF while old-generation
  packets remained in flight, and the eventual previous_mode check missed the
  source-to-follower preparation. Firmware85 uses an explicit producer barrier,
  preserves the old mode/clock while draining, and applies the new role only
  after accepted native buffers complete. Source publication/repair admission
  shares the short barrier lock; no Wi-Fi/VHCI send occurs inside that lock.
- Firmware85 contains an OFF-by-default addressed-repair experiment. The four
  original broadcasts and their SBC bytes stay unchanged. Addressed repairs
  use the requesting peer MAC through the existing raw Wi-Fi API, without
  esp_now. Native completion counters are separate from playback evidence.
  No100-receiver capacity or lossless-delivery claim follows from this test.
- A receiver-beacon100TU comparison was invalidated because phone playback
  stopped and automatic roles changed. Do not use that window as an isolated
  timing comparison.
- Samsung showed a Classic ACL while its audio profile was disconnected and
  YouTube was routed to the phone speaker. The profile has been reconnected;
  actual incoming SBC, not ACL presence alone, gates the current comparison.

Firmware85 source/build: build/app-1793596379345990100,build7f47222eda9a4d6c.
The one-hour full-system qualification is still pending and no release is ready.

## Linux replacement and native-action receive comparison

The Samsung is user-reserved. The replacement source is the existing Ubuntu VM
`E:\Linux 2\Ubuntu 64-bit.vmx`, `billy-pc`, `billydakidz@192.168.63.129`.
Normal SSH authentication succeeded. Credentials are not written to project
files, process arguments, or source bundles. COM6/COM7 remain Windows-owned;
only the identity-matched Realtek Bluetooth adapter is passed to VMware.

BlueZ5.85 initially treated a Classic-connected dual-mode device as connected
without establishing its LE bearer. That produced an empty GATT service list
and could disconnect A2DP when the GATT client closed. The bounded replacement
enables BlueZ experimental APIs using a runtime `/run/systemd` drop-in, sets
PreferredBearer=le, establishes the real GATT client first, then switches the
preference to bredr and connects the A2DP profile. Both links, four services,
real control writes/notifications, and incoming SBC have been observed together.
This is REDMAGIC+Linux testing, not two-phone qualification.

Linux Firefox/YouTube trials failed after brief playback with the site's
playback error. Those trials remain failed. Linux now uses an observable
GStreamer player streaming SomaFM Groove Salad directly to the exact COM6
Bluetooth sink; REDMAGIC remains on YouTube. Pausing a live HTTP source must
close/reopen its pipeline, because resuming the old connection caused a
non-seekable HTTP Range request failure. No music is saved in the source bundle.

Firmware89 changes the outer carrier to Public Vendor Action category4/action9,
local OUI02:57:4d. WMP logical CRC, codec bytes, four-frame aggregation and four
original copies are unchanged. Both boards had exact verified89 installations:

- COM6: evidence/install-1793603369983928600-COM6/receipt.json
- COM7: evidence/install-1793603369982411100-COM7/receipt.json

The supported `esp_wifi_remain_on_channel` receiver API returned success in AP
context but delivered zero callbacks. In STA context it delivered valid WMP
packets and completed/rearmed its one-second requests. This capability result
does not establish better audio coexistence. Its per-copy timestamp is a MAC
read in the callback, not a hardware RF capture timestamp; native AP beacon
capture remains the clock-pair source.

Actual same-image, same STA context comparison with both Classic music inputs
and both BLE clients active:
`evidence/native-rx-comparison-1793603925696408300/receipt.json`.

|30-second receive path|COM6 concealments|COM6 late|COM7 concealments|COM7 late|
|---|---:|---:|---:|---:|
|Promiscuous|120|0|0|0|
|Native action/ROC|159|0|0|0|

Both media sources advanced, actual BLE writes continued, no application RX
queue overflow or clock expiration was added, and COM7's incoming A2DP had no
RTP gap delta. Linux standby input had26 RTP gaps during the first phase and0
during the second. Neither receive path passed. Native RX remains OFF by default.
Observed free heap was about34KB; minimum was29.4KB, below the requested30KB
minimum. A concealment counter increment represents a missing render group,
not one SBC codec frame.

The source also reported51/67 and77/84 repair cache misses/request counts in
these windows. That records repair failure; it does not prove whether source
retention, delayed feedback, RF availability, or their combination caused each
miss. Do not describe those misses as natural interference without more evidence.

Firmware90 adds an OFF-by-default, no-IP association diagnostic. It binds only
to the current group's known leader BSSID/derived WMP-SYNC SSID, makes one
connection attempt, and reports actual station association separately from
API acceptance. The source SoftAP currently permits one station; this diagnostic
is explicitly a two-board test and cannot qualify a100-receiver architecture.

Firmware90 was installed only on COM6 for the association comparison, retaining
COM7's verified89 image as a running reference. Exact COM6 receipt:
`evidence/install-1793604341216607600-COM6/receipt.json`.
The WMP wire format is identical. The association actually completed in one
attempt, with both Classic streams and BLE clients active. Receipt:
`evidence/native-rx-comparison-1793604688119277100/receipt.json`.
The30-second associated windows still added177 concealments with promiscuous
RX and147 with native action RX; neither passed. COM7 added none in those
windows. Runtime association/native receive were disabled after the experiment;
the latest diagnostic firmware remains installed. This test does not evaluate
an Ethernet/IP data receive path, because audio still used Public Action frames.

The REDMAGIC/Linux handover run completed both role changes in8.953 and5.234
seconds respectively, with both Classic/LE connections retained. Linux's live
HTTP pause/play control worked without the earlier Range request error.
`evidence/two-phone-handover-1793604816388899900/receipt.json` records role_pass=true
but audio_counter_pass=false: both transitions added concealments/late packets,
and each new DMA session reported a queue overflow. Linux-source operation also
added blocked source frames during its steady portion. Those are unresolved
audio failures, not successful Music Party qualification.

## Repair backpressure and native data-path candidate

The goal was explicitly resumed. Samsung remains user-reserved; current inputs
are REDMAGIC YouTube and Linux direct music, both with actual BLE transactions.

`evidence/retention-investigation-1793605165683162600` showed source input age
up to837ms and all28 admitted source slabs pinned. Several recorded repair
requests arrived after their recorded PTS, so a cache miss does not by itself
justify extending retention. A source-repair ON/OFF/ON comparison kept original
four-copy scheduling unchanged:
`evidence/repair-backpressure-1793605248486493100/receipt.json`.
The source OFF window drained its encoded input queue to zero and added zero
late transmissions; ON windows added4527 and4627 late transmissions, with the
last ON window also adding184 blocked codec frames and50 source concealments.
This identifies repair load as a contributor to source congestion. It does not
explain all underlying receive loss: source-only OFF still left receiver loss.

`evidence/broadcast-repair-compare-1793605391485260900/receipt.json` disabled
feedback at both ends, then compared broadcast feedback/repairs. Both-OFF added
590 receiver concealments; broadcast repairs added911 and74 late packets, plus
516 source late transmissions. Broadcast MAC mode alone is insufficient.

Candidate92 bounds repair admissions to40 groups/second, at most two extra
copies per repair, with one broadcast NACK rather than three. The four original
copies remain unchanged. Repairs may be deferred explicitly; this prevents
unbounded repair work from consuming capacity required by the original stream.

Code inspection also found that the896-byte live bound excluded two transport
length bytes for each codec frame. Four224-byte dual-channel SBC frames require
904 bytes. The bound now derives from the negotiated bitpool cap and includes
all prefixes. A byte-preserving maximum-group regression and the exhaustive
mode check pass;68 portable tests passed in
`build/protocol-1793606081413097300`. This defect affects a supported codec
boundary; it does not explain the current smaller joint-stereo packet losses.

Candidate92 adds an OFF-by-default native Ethernet data path over the unmodified
Wi-Fi driver, with the same WMP header/CRC/original SBC. It registers the normal
RX callback, frees each driver-owned RX buffer, and uses driver TX-completion
notifications with the existing physical tag/owner matching. Enabling it disables
promiscuous receive. The associated receiver uses hardware-synchronized STA TSF
through the bounded background sampler; its callback copy timestamps are not
hardware RF captures and its generic data TX callback does not report PHY rate.
The strict qualifier checks association, STA clock identity, freshness and getter
span for this mode instead of requiring disabled promiscuous beacon callbacks.

This is initially a two-board SoftAP/STA diagnostic, not a100-node topology.
Espressif marks both connected SoftAP and sniffer coexistence C1 (unstable),
while connected STA is Y. A scalable normal-data deployment would need a
suitable AP/network design with the audio nodes operating as stations.
Primary source:
https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32/api-guides/coexist.html

Candidate91's4KB main stack was rejected before deployment: disassembly showed
the trace function alone reserves3712 bytes, before printf/caller overhead.
Candidate92 retains the6KB main stack; memory savings cannot remove required
diagnostic stack headroom.

Native data-path follow-up receipts remain failed, not one-hour evidence.
Candidate92's completion callback received802.11 data rather than the input
Ethernet layout; six unmatched buffers stalled the sender. Candidate93 also
rejected short discovery completions. Candidate94 validates only a bounded
carrier/tag and binds it to the retained canonical header. It ran thousands of
source completions with zero tx_unknown, but source throughput still failed.
Receipts include `evidence/native-ethernet-first-1793606396021633100` and
`evidence/native-ethernet-short-frame-fix-1793607009379262500`.

Candidate95 disables Wi-Fi TX AMPDU and sets the supported internal data-rate
control to24Mbps, separately from the raw-frame rate control. Driver errors are
reported; the observed error257 is ESP_ERR_NO_MEM, despite roughly35KB free
heap. This is TX queue/allocation pressure, not evidence of enough service
capacity. The fixed-rate trial still filled the source input ring and discarded
new incoming frames; it failed. Linux music had also ended when an asynchronous
pause raced a firmware flash and its sink disappeared. That early window was
not the full dual-source workload. The helper was explicitly restarted, and
`linux_control` now waits for matching applied control/state before a flash.
The later actual dual-input snapshot is retained with
`evidence/native-fixed-rate-1793607671858924700`.

Candidate96 adds read-only power-save state and a bounded PHY observer.
`evidence/native-phy-sniffer-only-1793608699075283400/receipt.json` captured416
valid source data frames in about2seconds: rate enum9 is24Mbps, RSSI-13dBm,
and get_ps returned WIFI_PS_NONE. The observer intentionally used promiscuous
receive and an idle receiver Classic stream; it is a PHY diagnostic, not full
workload qualification. The configured data PHY was therefore observed on air;
low PHY and modem power saving do not explain that particular bottleneck.

`evidence/native-data-coex-1793608131774627800` kept both real music inputs and
BLE active while selecting source Wi-Fi preference. It did not drain the source
queue. Both96 installations passed exact readback:
`evidence/install-1793608249265818000-COM6/receipt.json` and
`evidence/install-1793608249124011100-COM7/receipt.json`.

Candidate97 changes the transmitter task from core1 to core0 and records
aggregate API duration/calls and long-call counts. Wi-Fi/audio/RX remain core1;
controller and BTstack host remain core0. This is a measured submission-contention
experiment; no successful throughput or audio result is established by its build.

## Later buffer, copy-load and role findings

The core0 experiment reached about318 native completions/s.500us and unthrottled
submission reached about331 and329/s, still below the approximately355/s needed
for four copies of44.1kHz four-frame groups plus discovery. Original-copy trains
were observed at244..300ms despite a96ms configured span. Absolute due-time
scheduling did not make that overloaded four-copy window pass.

Three copies are explicitly allowed by the project requirements. With four
unchanged SBC frames/group, three copies, an eight-buffer application window,
and16 dynamic driver TX buffers, a finite native window kept the source encoded
queue empty with no new source concealment, blocked input or underrun. Receiver
queue overflows remained. Moving RX service ahead of playback stopped those
overflows; bounded repairs recovered10 groups in a later window but5 concealments
and1 late packet remained. See:
`evidence/native-three-copies-1793611785228349000`,
`evidence/native-three-rx-priority-1793611952116465400`, and
`evidence/native-three-repair-1793612044288851200`.

The full trace function left only24 console-stack bytes before the fix. It now
uses temporary task-context storage, not radio-callback allocation. The permanent
probe payload is generated in its final owned TX buffer. A real full trace on100
left1256 console-stack bytes with a smaller stack. Other task stacks were trimmed
only against observed margins. That did not establish a global30KB minimum:
transient/diagnostic minima below that threshold remain recorded.

102's strict pilot stopped after28.235s on a receiver underrun increment, with no
new concealments, late packets or RX queue overflow. Equal RX/output priority
also failed.103 retains RX priority, uses six5ms receiver DMA descriptors, and
reserves one descriptor interval plus the retained PCM tail before a missing
group's presentation deadline. It requests repair earlier while retaining the
three later retry opportunities.104 bounds repairs to one additional copy and
at most12.5 groups/s. Final accepted TX copies no longer retain the source audio
slab while a separate owned TX buffer waits for physical completion.

The source pool also had an arithmetic deficit:28 admitted groups cover only
298.7ms at48kHz, before input-burst margin against a300ms reserve.105 widens the
pool bitmaps to64 bits and allocates36 slots,34 admitted to source publication.
Actual source queue/admission deferrals stayed at zero after startup in that
candidate. Tests exercise allocation and reference retirement above slot31.
However, the unassociated receive path still failed under the full workload:
`evidence/standalone36-audio-window-1793615573755448300` added655 receiver
concealments in roughly half a minute with no RX queue overflow. A raw data
carrier comparison also failed. Do not describe this as a stable100-node path.

The connectionless native-listen comparison
`evidence/connectionless-listen36-1793616086740689600` is invalid as an audio
continuity window: COM6 repeatedly promoted itself, changed generations and
stopped its DAC. A zero concealment delta in that case is not a pass.

107 introduces a peer-owner lease before generation selection. A receive gap
under1.5s cannot release an active owner; an explicit idle announcement releases
it immediately. A foreign idle owner cannot reset active playback. The initial
one-second deterministic lower-ID tie-break remains bounded to startup.71
portable tests pass including these state transitions. The role fix still needs
hardware validation. BLE-first REDMAGIC setup reached a continuous first Classic
attempt beyond20s without disconnect, unlike several earlier cold-start timeout
runs; that is setup evidence, not a general pairing/reconnect qualification.

The filtered passive-listen diagnostic109 did enter80ms scans on the sender's
channel/BSSID, with20ms gaps. During the second Classic connection the receiver
briefly promoted itself, then returned as an AP: transition count advanced and
the listen counter stopped at194. That is not proof that scanning could not
receive audio; the role transition had cancelled the configured station path.

110 keeps a selected, explicitly active owner alive when its matched hardware
beacons are fresh. An idle announcement/silence clears the playing state, so
those same beacons cannot revive an idle owner. A new-generation binding clears
the old matched-beacon timestamp. Tests cover delayed-beacon monotonicity as
well as lease expiry, explicit idle, foreign-idle rejection and startup ties.
72 portable tests pass in `build/protocol-1793618156884760600`.
The passive-listen hardware rerun is still pending; do not label it qualified.

Current packet-pool allocation is36 immutable slabs with64-bit ownership masks;
source admission leaves two slots for receiver skew. This covers approximately
363ms at48kHz for34 source groups, rather than299ms for28, against the selected
300ms reserve. Real source deferrals/full events stopped in105 after startup.
The standalone raw carrier still suffered receive loss, and a raw data-carrier
trial failed too. Router/access-point suitability remains an explicit open
product constraint; an external AP has not been configured or used in these runs.


111 held the receiver in STA/follower mode while the filtered scan start/done
counters kept advancing. After standby Classic music began, receiver callbacks
stayed56371, unique22187 and matched beacons2263 for over300s, while the source
continued fresh TX completions. Disabling our A2DP streaming hint and choosing
Wi-Fi preference did not resume reception. That intervention does not establish
our hint policy as the root cause. The fixed-role evidence is diagnostic only:
`evidence/fixed-follower-listen111-1793619270212963500/receipt.json`.

Two association requests were accepted but no actual association followed.112
adds the driver-reported primary/secondary channel and station event/reason
counters. Accepted requests and software-selected channel6 cannot establish
actual association or the hardware channel. No restart counts as continuity.


112 associated on its first request after an instrumented fresh boot, before
reconnecting Linux. Actual channel was6, secondary ABOVE, not just cached6.
The short native-data full-workload comparison kept receiver callbacks and
STA TSF active. It still FAILED: over the recorded15s COM6 added2 conceals/1
late, COM7 added4 DMA underruns; source free heap26220B. See
`evidence/associated-native112-1793620579751792400/receipt.json`. Native TX
window4 backed up the sender; window8 drained it but consumed more heap.

113 adds diagnostic ETHMODE2: normal associated Ethernet RX plus existing raw
Action control TX. Its parser accepts both our tagged native Ethernet carrier
and the existing raw FromDS data format after driver LLC removal. Direct data
uses our bridge source address, so leadership uses the associated AP BSSID.
Source raw data TX needs no new frame representation or vendor patch.73
portable tests passed, including every truncated prefix of both encapsulations
for zero, short and maximum payloads. Hardware interoperability is pending.
This remains a two-board AP/STA diagnostic; it does not solve100 associations.


113 exact COM6 install passed: `evidence/install-1793621067174098000-COM6`.
Association getter transiently returned success before a connected event; that
attempt later disconnected with reason4. An explicit new attempt while source
used Action frames produced an actual connected event. This ordering result
does not prove why the first attempt failed. ETHMODE2 interoperated with the
raw FromDS source but suffered substantial concealment: it is not a solution.
The brief PHY/native comparison
`evidence/hybrid-phy-native-compare-1793621677624988400/receipt.json` saw781
additional valid PHY frames and781 native callbacks. Thus that interval does
not support duplicate filtering or our handoff as the missing-packet cause.
Sniffer enabling changed the diagnostic configuration; no acoustic claim.

Both boards were explicitly returned to ETHMODE1 for further diagnostics,
source TXwindow8. Source audio priority24 is a runtime experiment. Its short
window stopped DMA deficit increments but receiver concealments/lates remained
and source heap was26-28KB. See
`evidence/native-source-priority24-1793621935542529000/receipt.json`. No hour,
automatic-role, heap-minimum or100-receiver qualification has passed.


The completion-relative32ms spacing diagnostic failed badly: over20s receiver
added904 concealments/744 late and source104 concealments. Explicitly restored
absolute32ms scheduling. Evidence:
`evidence/native-completion-spaced-1793622240343100900/receipt.json`. Do not
select completion-relative pacing merely because it seems like better RF timing.

Read-only Linux controller measurement: identity-matched adapter is hci1, not
hci0. ACL handle7 returned AFH enabled with map `00080e00020078e0ff00`.
It already contains20 good channels, with only2435MHz inside the nominal
Wi-Fi6 channel span2426..2448MHz. No AFH map was written; the read does not
measure actual RF airtime or prove interference as the cause of loss.


114 adds selectable legacy OFDM rates to both raw TX and native TX. Source
exact installation: `evidence/install-1793623410082349100-COM7`. Actual dual
Classic streams and both BLE clients were restored. The bounded PHY sweep
`evidence/native-phy-sweep-1793623596143377700/receipt.json` failed all cases.
36Mbps produced no source output faults in23.8s but46 receiver conceals/41 late;
24/48/54 stopped on source faults. Rate alone is not the solution.

The corrected three/four-copy comparer also validates complete trace rows and
coverage, labels native RX as callback-clock sampling and computes source
submission headroom. Eight host evidence tests pass. Historical gap-seeded
receiver records often already represented missing sequences, but the old
intersection join did not handle genuinely absent rows. Do not reinterpret
old four-copy reports as valid three-copy evidence. The fresh52-group trace
`evidence/copies-1793623000470891300/receipt.json` showed15 first submissions
after PTS and four groups with all three TX completions but no RX callback.
Source clock mapping had14us sample span. This proves late application
submission for those15 groups, not exact RF delivery times or acoustic delay.

116 candidate replaces maximum-sized per-packet payload slabs with one24KiB
fixed payload arena and50 separate descriptors. A source leaves two descriptor
and payload reserves. Ranges remain pinned until the final network reference
ends; no radio callback allocates heap. Actual payloads remain unchanged,
including full904-byte groups. The byte arena holds50 maximal joint-stereo
groups484B or27 full904B groups; capacity depends on the codec.78 portable
tests pass, including randomized out-of-order allocation and reference
retirement. The initial common reserve becomes500ms for the current stream,
using the user-authorized stability tradeoff. This is not measured end-to-end
latency and is not a claim that every codec can sustain that reserve. Hardware
validation is pending.


116 passed exact installs on both boards. Source free heap measured33728B
(minimum28288B) and receiver39760B (minimum34564B) before the standby stream
resumed. Initial source queue/deferred/full/timeline/late/DMA counters stayed
zero; receiver concealment remained. After actual second A2DP resumed, source
backpressure and late transmissions returned: the pool fixes memory and one
capacity deficit, not the full radio problem. No one-hour pass.

Pinned libnet80211 exposes `ieee80211_recv_action_register(category,action,cb)`.
The dispatcher passes `(node,hdr,body,end)` unchanged; the parser and vendor
handler subtract body from end, confirming the pointer-bound convention. ABI
disassembly and SDK archive hash are preserved in
`research/action-registration-v61`.117 adds OFF-by-default DIRECTRX to install
our bounded Public Action4/9 handler for our OUI. This bypasses the off-channel
lease callback and promiscuous data handoff. No ESP-NOW initialization, format
or runtime API is used; no SDK archive is patched. The receiving callback only
checks bounds/identity, copies into the existing queue and samples callback
MAC time. It does not decode, allocate heap, or block. Hardware validation is
pending; the API is private and tied to the pinned SDK.


119 mixed transport retained raw Public Action audio TX and normal driver
control RX on the source; the receiver used generic Action RX and normal
acknowledged control TX. In the same finite interval425 NACKs were sent and425
heard, versus337/162 with the earlier unacknowledged control path. This fixes
that measured control loss; it does not make broadcast audio reliable.
`evidence/mixed119-spacings-1793627600249690300/receipt.json` failed32ms and128ms
copy-spacing cases. Four copies did not qualify either. All failed evidence
remains; restored three copies/32ms afterward.

120 simultaneous PHY/native measurement:
`evidence/direct120-air-vs-stack-1793628509981528600/receipt.json` counted845
additional FCS-valid frames and845 handler callbacks, with zero recorded FCS
errors/repeated consecutive MAC sequences. Thus that interval does not support
native management duplicate filtering as the missing-copy cause. With receiver
Bluetooth disabled, physical reception rose to1374 frames in the corresponding
finite interval. Its concealment delta is NOT clean RF evidence: the old DAC
preparation bug was active, and79 groups were explicitly preparation-skipped.

123 fixes that bug: after a peer preparation request, the renderer drains old
queued frames and keeps the DAC stopped until the preparation period ends.
Previously it could start on an old queued frame while the ingress gate was
still skipping newly received frames. Missing-input and decode-mismatch conceal
counters are now separate. Continuous tests still reject preparation skips;
muting cannot turn a failed continuous window into a pass.

The generic Wi-Fi connectionless PM registry was audited as taking a persistent
uint16 pointer, NOT a numeric window value. The app uses static DRAM and matching
registration/unregistration. No ESP-NOW protocol or initialization is used.
ABI records are in `research/receive-window-v61`. The registered maximum window
was tested with both real A2DP streams and BLE clients; it did not solve loss.
`evidence/coex-window123-1793630486578232300/receipt.json` records the completed
matrix. The balanced actual-streaming hint was worse than the receiver Wi-Fi
preference policy. Every case still concealed. When disconnected from the AP,
callbacks remained frozen with the generic window both off and on while actual
Bluetooth frames/writes progressed. See
`evidence/unassociated123-window-1793630726177986300/receipt.json`.

No hour, acoustic, automatic-role or100-device qualification has passed. Both
boards were restored to the best measured associated diagnostic configuration,
with receiver follower lock enabled.124 increases main stack4096B after123's
STAT path left only488B high-water margin. The next unimplemented reference is
both boards as connected STAs on one AP, with radio association separate from
music leadership; ESP-IDF labels this coexistence scenario stable, whereas
SoftAP-connected and sniffer reception are C1. A temporary host AP comparison
cannot qualify100 clients or silently make an external router mandatory.
