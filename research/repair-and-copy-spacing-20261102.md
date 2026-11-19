# Four-copy configuration and recovery investigation

The goal remains incomplete. All receipts named below retain their raw data.
None is an acoustic,100-node or one-hour qualification.

## Configuration correction

The source-start command requested four copies, but the cold-boot
`original_copy_override` still forced three. Injection records exposed21
suppressed transmissions and mask7 for seven groups, rather than28/mask15.
The default is corrected to4; benchmark admission now checks actual count.
Earlier memory/lifecycle receipts retain their resource findings, with
copy-count reviews beside them. They do not qualify a four-copy workload.

Core session generation now has separate CRC-protected telemetry. It does
not become0 when optional copy history is disabled. The long-workload gate
checks the real session and four actual originals. Copy trace rows also
have a CRC over their canonical little-endian record; corrupted UART rows
must not be interpreted as delivery loss or successful delivery.

## Controlled seven-group loss

`DROPRANGE` suppresses explicitly selected original transmissions; it never
labels them as submitted to the radio. Repairs remain enabled. A retained
diagnostic records each target sequence, sample, presentation time and
suppression mask. Optional FAULTLOG records missing/late samples without
per-packet printing or allocation. Tests use real YouTube plus BLE, not a
tone, but initially only the master had incoming A2DP.

`evidence/burst-repair-ab-1793656657885007800/receipt.json` completed five
policies with4 actual originals and7 consecutive groups suppressed:

| Admission gap | Guard | Copy budget | Repair spacing | Wait for callback | Target conceals | Replays played, including other groups |
| ---: | ---: | ---: | ---: | --- | ---: | ---: |
|80ms|80ms|110ms|40ms|yes|7|2|
|8ms|80ms|110ms|40ms|yes|3|5|
|8ms|80ms|110ms|40ms|no|5|9|
|8ms|80ms|40ms|40ms|no|5|8|
|8ms|20ms|40ms|40ms|no|2|7|

Every case failed complete recovery. This is a finite comparison, not proof
of universal causal improvement. Each injected target was checked by its
actual sample/presentation identity; a replay counter alone cannot prove
that all targeted audio played. Source RTP gap delta was0 in these cases.
Maximum measured sender-loop gap was11.341ms and receiver dispatch3.095ms;
VHCI drops were0. A100ms application-task stall is not established by them.

The copy-budget helper admits one copy once remaining time exceeds its
guard. With guard80ms and budget110ms, a second copy becomes available at
190ms. An earlier commentary incorrectly described190ms as the minimum for
one copy; that was corrected after inspecting the helper.

## Original-copy spacing

`evidence/copy-diversity-1793656954389775500/receipt.json` kept source/session,
four originals, PHY54Mbps, window24, actual A2DP and both BLE clients. The
repair policy was fixed at8/20/40/40ms with absolute repair spacing.

| Original spacing | Completed observation | Added audio fault |
| ---: | ---: | --- |
|32ms|8.219s|receiver concealment|
|48ms|46.312s|none|
|64ms|45.046s|none|
|96ms|8.187s|receiver concealment|

BLE writes and notifications advanced on both boards in every window.
Source queue-full and TX-late deltas were0. A longer64ms test,
`evidence/music-window-1793657181799734800/receipt.json`, failed at93.422s
with one receiver concealment. It added no RTP/input discard, timeline
recovery, codec, queue or write fault. This does not qualify stability.

The new candidate makes64ms the default and allows request-delay comparison.
The former120ms first missing-group wait leaves less recovery time; the
new default60ms remains a candidate until tested on hardware. MAC-derived
election wait is bounded20..60ms: COM6's previous246ms could exceed the
8KiB staging capacity, about171..197ms for observed standard codec groups.
Portable tests additionally check the bound against maximum-size groups.

## Restoring actual second-source admission

The Linux hci1 adapter identity isE8:47:3A:EC:51:9A. Raw inquiry found the
admitted COM6 address and its speaker class. `hcitool scan` also resolves
remote names, so its timeout must not alone be called an inquiry/radio hang.
After fresh inquiry, normal BlueZ ConnectDevice/ConnectProfile admitted
COM6 and created its48kHz PipeWire sink. Real incoming SBC and intentional
standby-frame handling were recorded at
`evidence/dual-source-admission-1793658495054492300/receipt.json`.

The full two-source pilot then failed workload admission:
`evidence/party-long-1793658541763672700/receipt.json`. COM6 subsequently
reported Classic timeout8,5288 RTP gaps and zero VHCI host-buffer drops.
The Linux player had moved to a null sink while its GStreamer state still
said PLAYING. Its source tool now verifies target routing and rendered
buffer progress, and stops its own stream if fallback routing occurs.

The old coexistence hint used recent-packet activity and demoted A2DP after
500ms without RTP, even while AVDTP remained started. The new candidate
tracks transport START/SUSPEND/RELEASE separately for that hint. Recent
actual RTP still drives ownership and workload checks. This correction is
not yet proof of the timeout's cause or a validated RF fix.

The phone's saved-device reconnect also required a public Classic SDP
refresh; no bond database was erased. The upstream
[BTstack issue594](https://github.com/bluekitchen/btstack/issues/594) concerns
a different IDF4.x/BNEP case. Its Secure Connections workaround was already
present here and is not evidence identifying this failure.

Windows connect could fail10048 even after a successful explicit bind.
The task-local client now tries another source port only before any request
bytes are sent. No device action is replayed by this retry. Tests cover
collision retry and immediate propagation of a refused server.

## Latest candidate and host boundary

Both boards now run `app-1793658925170323700`, build545a608486ec363c,
with exact install receipts linked in CURRENT_STATUS.md. It retains4/64ms
defaults, configurable first NACK delay with60ms default, bounded election
jitter, and AVDTP-derived coexistence state.87 portable groups pass.

`evidence/burst-repair-ab-1793659887424364900/receipt.json` compared request
delays120/60/40/60/120ms on actual four-copy traffic. Target concealments were
5/3/5/5/4; no case passed. Reducing the first wait alone is not a solution.
The current strict two-source pilot at
`evidence/party-long-1793660637682943000/receipt.json` failed at5.907s.

The owned Linux host's8-second HCI TX metadata capture contained134 RTP
packets spanning8946..9146 with67 sequence gaps, before the ESP32 receive
path. Controller completion events had a maximum535ms gap. These are not
synchronized endpoint captures and do not quantify additional MCU-side loss.
The metadata-only probe is `tools/linux_hci_metadata_probe.py`.

The PC reported Classic RSSI-14 and link quality0. Classic RSSI is relative
to a controller-selected receive range rather than an absolute dBm reading;
negative values indicate reception below that range. See the primary
[Bluetooth HCI Read RSSI specification](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-54/out/en/host-controller-interface/host-controller-interface-functional-specification.html).
This supports checking the PC antenna, but does not establish every dropout's
cause. The physical antenna question remains pending.

A successful HCI command-complete arrived1.38s after a read command, beyond
the legacy utility's wait. Extended verified reads avoid falsely calling
that timeout a dead controller. Page timeout was read back as32768 units
after recording the previous8192 units. Normal BlueZ admission then created
the authenticated/encrypted COM6 link and its actual48kHz audio sink.

## Repair load and RF-rate controls

`evidence/repair-load-1793661500448426200/receipt.json` completed an on/off/on
repair comparison with both actual incoming streams, both BLE clients and
unchanged four-copy broadcast. Receiver concealments were5,10,7 in roughly
30s windows. Source concealments, source queue-full and late-audio deltas
were0. Both BLE writes and notifications advanced. Repairs helped in these
finite observations; the comparison does not prove absence of congestion.
Repair-off remained a diagnostic and was restored on.

Lighter repair budgets also failed full seven-target recovery; the four
target missing counts were2,4,5,5. These results are preserved separately in
`evidence/burst-repair-ab-1793662039717208100/receipt.json`.

The owned Linux link accepted0xee1c, which excludes3-DH1/3-DH3/3-DH5 while
allowing2Mbps EDR and BR formats. The bit interpretation comes from the
primary Bluetooth HCI Change Connection Packet Type table. An8-second
host probe showed72 sequence gaps, not an improvement over the earlier67.
The normal0xcc18 mask was restored with a successful terminal event. This
test does not establish which exact packet type was used on air.

The pinned SDK identifies PHY enum0x0c as54Mbps. Callback metadata matched
the requested54/24/12Mbps settings in the finite rate comparison. Receiver
conceal counts15/261/2297 and source counts0/0/213 ruled out simply lowering
the rate as a demonstrated fix. Both were restored to54Mbps and source
decoding progress was checked without resetting either board or counters.

## Pending-repair ownership guard

Candidate `be1dbe7d9ae4d20c` pins one repair transaction per packet until
all of its native completions arrive, rather than allowing a second train
after only the old20ms guard. Both boards passed exact installation and
the portable frame-pool suite increased to90 passing groups.

The fixed-workload off/on/off/on diagnostic is preserved in
`evidence/burst-repair-ab-1793663165193955300/receipt.json`. Each case
suppressed all four originals for seven groups (28 copies). Guard-off
target misses were3 and5; guard-on misses were3 and3, with12 and8 pending
requests suppressed. All four recovery gates failed. This establishes that
the new guard executes and bounds duplicate repair ownership; it does not
establish complete recovery or prove the remaining loss is natural RF loss.
