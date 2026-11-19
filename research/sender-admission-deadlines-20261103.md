# Sender admission and deadline experiment

Candidate934dd9ee1d7e1ea8, build/app-1793746373524522500, changes only
source queue admission and ready-slot ordering in the MCU path. MCS7/HT20,
four original copies, native window6, repair loan limit8,24ms copy spacing,
6ms dither, original SBC and the500ms diagnostic reserve stay fixed.

Before: each repair entered the shared source queue at its front. Every ready
repair outranked every ready original, independent of their presentation times.
After: repairs enter the same FIFO as originals. Ready slots use the common
presentation deadline, with originals winning ties. Future slots remain ordered
by local due time; common-clock presentation values are never compared with the
local scheduling clock. The dispatch-time repair expiry guard remains in place.
Originals are retained even when late.

The preceding one-repair-loan diagnostic removed source queue pressure and
original lateness for35s, but did not qualify follower playback. This supports
investigating repair interference; it does not establish the new scheduler works.
Reference: evidence/repair-loan1-1793744768991553200/receipt.json.

195 portable host tests passed, including ready/future ordering, earlier repair
deadlines, original tie precedence and independent clock domains:
evidence/scheduler-host-tests-1793746366982810000/receipt.json.
The full candidate build and current-source offline binding also passed.

Hardware results must bind both exact installations and include both actual
phone streams plus BLE. Observe original_late_submit/done, source byte queue,
blocked codec frames, renderer faults and per-copy receipt separately. A clean
short window, API acceptance or TX completion cannot establish an hour of audio
stability or physical speaker synchronization.
