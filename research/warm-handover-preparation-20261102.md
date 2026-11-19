# Separate stream preparation from AP clock preparation

## Problem and historical evidence

The prior wmp_audio_prepare_peer always blocked peer packet admission until
now+2000000us, including a leader change between already playing associated
STAs. The source can assign its opening packet a500ms future presentation
time, so this gate can exclude that packet even when the receiver's AP clock
is already available. This is distinct from RF packet loss.

Historical role receipts show a new source playing while the old source is
still preparing or silent, with increasing preparation skips. Extract:
`evidence/warm-handover-offline-before-1793680766404278600/receipt.json`,
from `evidence/two-phone-handover-1793669308246401400/receipt.json`.
These are the older image and REDMAGIC/Linux fixture. They do not isolate
every dropout or qualify the current Samsung fixture.

## Candidate policy

A running renderer records its local associated-STA clock epoch. A warm
transition can retain a fresh shared AP clock when the epoch still matches.
Repeated role/announcement preparation calls reuse this proof even after
the first request has stopped the old DAC. Cold resources, invalid/stale
clock data or a changed AP clock epoch keep the two-second preparation rule.

STA connect/disconnect events immediately invalidate the cached clock epoch
and request cold preparation, without making a synchronous TSF ioctl in the
event handler. Clock learning uses the hardware STA clock for either music
role. Epoch numbers are LOCAL invalidation tokens; comparing their numbers
between boards does not prove clock synchronization. The transport still
requires source and followers to share the configured AP clock domain.

Every audio item records its preparation version. If preparation changes
during payload copy, the producer cancels its unpublished reservation and
allows a retry. When the renderer notices a reset after taking new items,
it releases old admissions while retaining packets admitted under the new
version. A second version check before PCM output avoids knowingly writing
superseded work. SBC decoder history resets at a new playback stream; the
encoded SBC bytes, sample counts and common presentation timestamps remain
unchanged. One already submitted DMA/API operation cannot be retracted.

WMP_PREP_POLICY reports warm/cold requests, admission retries and clock epoch
tokens. WMP_SOURCE_CLOCK adds its local domain epoch. These are normal STAT
reports, not per-packet serial output or radio callback allocations.

## Verification and limits

All114 host protocol tests passed in
`build/protocol-1793680528674329600/receipt.json`, including warm first-packet
admission, repeated preparation, cold/new-AP fallback, resource teardown and
two reset/admission races using the actual C preparation and frame-pool code.
The existing four handover readiness gate tests also passed. These verify
policy and queue behavior, not ESP32 ISR timing, audible switching or RF.

The earlier recovery candidate's NACK ownership and64-slot tracking changes
are retained. Combined candidate54afb0020b26e439 in
`build/app-1793680757020576200` built successfully with unchanged SDK/BTstack
pins. Offline source hashes, image checksums, admitted flash extents and ELF
type sizes passed:
`evidence/warm-handover-candidate-offline-1793680931106005800/receipt.json`.
Audio items grow80 to88 bytes,400B across50 slots, separately from the earlier
NACK growth1536B. No new per-packet allocation is added. The initial warm
candidate `app-1793680621860484500` also built but was superseded before any
installation. Neither board nor Samsung was accessed. Latest verified
installations remain30f. Live
two-phone tests, both role directions, heap margin, cold startup/late join,
one-hour stability and acoustic synchronization remain unqualified.

In particular, this warm-handover change does not coordinate a completely
cold receiver's two-second preparation with a new source's initial timeline,
or establish safe reanchoring of an active source after AP TSF reset. Those
remain separate requirements; no success claim is made for them here.
