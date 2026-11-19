# Reuse clock preparation completed before stream arrival

## Candidate change

The prior warm-handover candidate required either a running renderer or a
warm transition latch to skip a new two-second preparation timer. An idle
receiver whose hardware AP clock had already been available for seconds
still started this wait at the new source's announcement. A source's500ms
opening presentation deadline could therefore fall inside the wait.

The background clock sampler now records a qualification start time. Its
existing accepted-sample span bound is1000us. A gap over1s between accepted
samples or a reference deviation over2000us restarts qualification. The
local preparation deadline is qualification start+2s. These are policy and
measurement bounds, not proof of physical synchronization accuracy.

For a new active source announcement, the receive task preloads the existing
lazy playback arena and uses this clock deadline. A clock already qualified
can admit the opening packet without another two-second wait, even if the
DAC was idle. If qualification is still underway, repeated announcements
retain its original deadline rather than moving it into the future.
Unknown clocks keep the conservative fallback. Idle control/setup messages
do not preload the arena; the decoder remains demand-loaded on SBC audio.
No radio callback or ISR gains allocation, printing or synchronous TSF reads.

The local clock epoch, stream generation and presentation timestamp remain
separate. No encoded SBC bytes, sample counts, aggregation, repetitions or
wire format changed. No A2DP start confirmation or phone playback behavior
was changed. Existing warm preparation and reset/admission fences remain.

## Verification

Native old/new C comparison:
`evidence/idle-clock-preparation-before-after-1793681579646584500/receipt.json`.
With assumed clock qualification at3s, an idle receiver's new stream at4s
previously waited until6s and rejected an opening packet arriving4.1s with
PTS4.5s. The new policy admits it. This is a host policy model, not RF, DAC
or audible evidence.

All119 protocol tests passed:
`build/protocol-1793681301790028000/receipt.json`. Five added tests cover the
idle settled-clock case, repeated qualification deadlines, invalid/new-clock
fallback, reloaded resources and a genuinely cold clock's early packet.

Candidate2546d3dc57039286, `build/app-1793681358499756400`, compiled with
unchanged dependency pins. Offline source hashes, ESP image checksums,
admitted extents and Xtensa ELF type sizes passed:
`evidence/clock-preparation-candidate-offline-1793681581031201600/receipt.json`.
Audio items remain88B, NACK state2072B and preparation state24B. There is no
additional heap reservation beyond the previous candidate, although the
existing arena may now be allocated earlier when actual stream demand arrives.
The sampler adds an8-byte static qualification timestamp.

## Open requirements

This does not make an actually unprepared receiver meet a packet deadline
that precedes qualification. For example, a clock ready at2.1s still cannot
play an opening packet due0.6s. Source/start coordination for that case and
active-source reanchoring after an AP TSF discontinuity remain incomplete.
An unknown clock at announcement retains the fallback timer; timing from
subsequent first acquisition needs hardware characterization.

No board, phone, ADB interface or serial port was accessed this turn. Samsung
remains reserved. Latest verified installed images remain30f; this candidate
is unflashed. All live full-workload, role, heap, hour, acoustic and scale
acceptance gates remain open.
