# Delivery dedup audit

The user requested investigation of conceals despite receiving later copies.
The concrete defect found was in handoff_audio: wmp_audio_offer returns0 for
failure,1 for an actual queue insertion and2 for preparation deferral. The old
handoff committed both1 and2 into the delivery dedup window. A later valid,
playable copy of a deferred packet could therefore be suppressed.

wmp_dedup_commit_delivery now commits only status1. The actual portable C
function is tested for deferred first copies, successful later copies, failed
queue retries and deferred future identities not advancing the delivery window.
This fixes a real warm-up/rejoin case. It does not prove the origin of all
steady-state conceals.

Candidate87cd192f56692b3e is frozen in build/app-1793760567700722900.
212 host tests and the full build passed. Both exact installations passed:
- evidence/install-1793760754226399000-COM7/receipt.json
- evidence/install-1793760911850486700-COM6/receipt.json
- evidence/dedup-route-journal-tests-1793760564304615300/receipt.json

Selected-identity events13/14 now retain raw and delivery dedup decisions with
physical copy tags. Event15 retains explicit application discard. The existing
64-group receiver trace also journals these bits:

| Bit | Meaning |
| --- | --- |
|1|Actual audio queue insertion|
|2|Preparation deferral; later copy remains eligible|
|4|Duplicate suppressed after prior queue insertion|
|8|Intact codec decode|
|16|Classified late|
|32|Codec/PCM mismatch|
|64|Queue insertion failed|
|128|Buffered item purged by application lifecycle|

The route byte occupies existing record padding: record size remains56bytes.
Its CRC includes the route byte. Host readers retain backward compatibility
with older55-byte CRC records. Journal updates cannot seed/advance the window,
rebind a source trace or overwrite a newer aliased record. No packet logging,
allocation or decoder operation was added to radio callbacks.

Source7 and receiver6 currently both use public MAC capture. The receiver is
unassociated; source7 has zero associated clients. Receiver clock recovery uses
hardware beacon/RX MAC timestamp pairs. Full Bluetooth inputs and BLE must be
present in any diagnosis. The first new natural-fault observation is:
evidence/replay-watch-1793761445046583600/receipt.json.

Cold passive-interface setup also exposed a driver channel reset to1 while
the cached setting stayed6. The setup tool now checks actual hardware channel
and corrects it with Bluetooth stopped. Current correction receipt:
evidence/passive-channel-fix-1793761327494044400/receipt.json.
An on-device persistent/default broadcast startup remains incomplete.

No clean hour, physical output-skew result or100-device performance is claimed.
