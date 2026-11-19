# Current development status - 2026-11-03

Both boards now run934dd9ee1d7e1ea8 from build/app-1793746373524522500.
MCS7/HT20 remains fixed:65Mbps long GI, bandwidth code1 and MCS7 native TX
completions. Both921600-baud installations passed exact readback, protected
flash audits and two verified boots:
- evidence/install-1793746481878949000-COM6/receipt.json
- evidence/install-1793746729341499800-COM7/receipt.json

The new sender admits repairs through FIFO and selects ready packets by common
presentation deadline, with originals winning ties. Originals remain retained
when late.195 host tests and the complete build passed; this is not audio
qualification. Both real phone streams and BLE progressed during the tests.
- research/sender-admission-deadlines-20261103.md
- evidence/scheduler-host-tests-1793746366982810000/receipt.json

The initial source6 gate failed at7.782s with8 receiver conceals. A66.344s copy
diagnostic had0 source faults/late originals, but748 receiver conceals and8 late
groups;693/5651 receiver groups had no original copy (12.26%). That follower had
BALANCE preference instead of the intended Wi-Fi preference.
- evidence/party-long-1793746835637082700/receipt.json
- evidence/copy-loss-1793746956212964000/receipt.json

Preparation incorrectly sent manual COEX0 after A2DPCOEX1, overwriting the
automatic follower preference while the firmware policy cache considered it
applied. A real role change restored the intended preference. The preparation
tool no longer sends that override; qualification now checks both role policies.
This corrects a concrete setup inconsistency, not the whole loss problem:
the next gate, with the intended policy, failed at56.047s with156 receiver
conceals and3 late groups while the source remained clean.
- evidence/party-long-1793747212097232100/receipt.json

Both automatic handovers passed role/readiness checks at9.203s and8.985s.
Each becoming source added4 concealed codec frames, so audio did not pass.
No Bluetooth disconnection, source RTP gap or timing-profile overwrite occurred.
- evidence/two-phone-handover-1793747045688005500/receipt.json

A wider64ms/16ms-dither/220ms-NACK profile failed in61.593s. Although original
absence decreased to231/5278 groups (4.38%),4018 originals were submitted late,
99 input frames were blocked, and receiver faults reached4288 conceals/959 late.
Better raw receipt did not yield useful audio. These windows differ in roles,
history and diagnostic allocation; they are not exclusive causal comparisons.
The24ms spacing/6ms dither/40ms NACK profile was restored without restarting
Wi-Fi, the session or DAC. Native window6, repair loan limit8 and the500ms
diagnostic common reserve remain. COM6 is source, COM7 follower.
- evidence/copy-loss-1793747357807826700/receipt.json
- evidence/copy-profile-1793747445686927500/receipt.json

Current-image natural group14038 is covered by both retained copy windows.
All4 originals completed22..26ms apart; receiver raw/valid/repaired masks were0.
Source audio/queue/RTP fault deltas were0. Loss preceded the recorded RX callback;
RF/coexistence and lower-driver loss remain indistinguishable. The repair trace
rolled during printing; absent target entries do not prove no NACK or replay.
- research/natural-copy-absence-934d-20261103.json

Full stability remains unqualified. No passing hour, acoustic timing or100-node
proof exists. Original SBC, four consecutive frames/group, four copies, no FEC,
BTstack1.8.3, clean IDF6.1 and WS25/DOUT26/BCLK27 remain unchanged. Samsung remains
released at20% media volume. The original Python Serial Studio GUI is still open.

## Historical61e5 snapshot below - superseded by the current results above

The full goal remains active and incomplete. Both admitted boards now run
61e5c1a110b3a5af from build/app-1793742157175532000. The user-selected current PHY
is MCS7/HT20:65Mbps with long GI. Both interfaces report bandwidth code1; received
frame metadata has HT20/MCS7 samples. This setting remains fixed for investigation.
- COM6: evidence/install-1793742282126629700-COM6/receipt.json
- COM7: evidence/install-1793743099594063300-COM7/receipt.json
- Explicit no-reset readback: evidence/user-mcs7-ht20-1793745739311163200/receipt.json

Original SBC, four contiguous frames/group, four original copies, no delivered FEC,
BTstack1.8.3, clean IDF6.1 and WS25/DOUT26/BCLK27 remain unchanged. Samsung remains
released at20% media volume. The original Python Serial Studio GUI remains open.
There is no passing full-workload hour, acoustic timing or100-node qualification.

## Measured four-copy delivery and current failure

At the older HT40 baseline, copy receipt was28.1/39.2/44.6/38.6%;662 of5270 retired
receiver groups had no original copy (12.6%). The product-of-marginals comparison
was14.9%, so this sample does not prove excess correlated loss. Poor individual
receipt already explains frequent four-copy absence. Sender history recorded all
four for its5277 retired entries, with0 API failure/late/injected-copy deltas.

The dual callback observer counted5196 matching WMP frames in both promiscuous
and native callbacks. That provides no evidence of loss between those callbacks;
RF and lower-driver loss remain indistinguishable there. Three deliberately
suppressed groups recovered through intact full-packet replay.
- research/four-copy-delivery-audit-20261103.json

The new image rechecks queued repairs before dispatch and counts original/repair
lateness separately. It also explicitly sets HT20 before start; older scalar
RATE65 used the SDK's default HT40, verified by received-frame metadata. The guide
actually documents HT40 as default; do not attribute this specifically to NVS.

The first full HT20 window (COM6 source) improved original receipt to62.6..64.9%,
with76 of4911 groups missing all originals, but FAILED badly:1993 source blocked
codec frames,1875 source conceals,20393 receiver conceals and4522 receiver late.
New counters identified19434 original late submissions,0 repair late submissions.
- evidence/copy-loss-1793744047471079000/receipt.json

A runtime native-window12 test failed: source late submissions8595, source
conceals2384, receiver10782, minimum sampled source heap27272B. Window6 restored.
A one-pending-repair test eliminated new source lateness/queue pressure/conceals in
its35s window, but follower still added12 conceals and2931 underrun checks. The
limit8 was restored. These are diagnostic failures, not release evidence.
- evidence/native-window12-1793744463006275900/receipt.json
- evidence/repair-loan1-1793744768991553200/receipt.json

Source7 is currently leader after an unplanned media/ownership transition. Source6
with REDMAGIC's larger original SBC packets remains the failing direction. Both
roles remain mandatory; a lighter naturally negotiated phone stream is not a
substitute for qualifying the larger source. Candidate dispatch repairs are still
pushed to the source queue's front and all due repairs outrank originals; fair
queue admission/actual-deadline ordering remain the next sender investigation.

The latest3900s attempt failed at its initial check because REDMAGIC actual music
was STOPPED. It observed0 qualification seconds; do not call this a radio/hour
result. Both boards remain configured MCS7/HT20, with temporary follower locks0.
- evidence/party-long-1793745739726187700/receipt.json

## Validation limits

191 host checks passed after the current source changes. Exact readback and two
boots passed on both boards. Native TSF is obtained from the common AP and mapped
to the local playback clock; no1-3us physical synchronization claim exists.
The reference AP supports8 associated clients, only two boards have been tested,
and500ms is a temporary diagnostic reserve rather than measured end-to-end delay.
Lower latency, robust continuous input fixtures, both clean handovers, pause/late
join, actual hour, standalone startup and physical output timing remain required.

