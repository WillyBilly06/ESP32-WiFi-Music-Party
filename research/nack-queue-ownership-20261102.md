# Repair request ownership and retry accounting

## Evidence and limits

The bounded capture on installed30f786f65659c29e matched371 logical groups.
26 had all four TX completions and no original RX callback recorded;
25 of those had an integrity-valid repair observed. This does not prove all
25 were queued or played before their deadlines. One group,64501, had no
repair recorded. Its last TX completion had326894us presentation headroom.
These callbacks are not RF timestamps, and an absent RX callback cannot
distinguish RF unavailability from loss inside the receiver driver.

Receipt: `evidence/awake-copy-diagnosis-1793678342904088400/receipt.json`.
Optional histories were released; source current heap returned to34872B.
The temporary history/snapshot allocations lowered source lifetime minimum
to27084B. That diagnostic run does not establish a permanent30KiB heap floor.

## Reproduced defect

The receiver called wmp_nack_commit immediately after enqueueing a request.
The transmit task might then wait for native credit or its8ms receive-based
feedback window, discard it after another receiver's request, or change roles.
Retries were consumed before API submission, and the tracker could enqueue
the same logical request again while the first copy was still pending.

`evidence/nack-queue-before-1793679199882578100/receipt.json` executes the old
portable C implementation with five queued/current request positions during
a blocked transmit period. It queued the same missing sequence five times
and consumed five retries with zero submissions. Its source hash matches the
preserved installed30f frozen source. This is a host queue reproduction,
not a reproduction of the RF failure or proof that it caused group64501.

## Candidate implementation

One pending flag reserves the request before queue handoff. Planning skips
that pending entry so other missing groups can progress. Queue rejection,
peer-request suppression and abandoned feedback release the reservation.
Only successful TX API submission consumes a retry and starts its32ms retry
interval. That still does not assert a successful RF transmission or ACK.

The application serializes tracker accesses across the receive and transmit
tasks with a short lock, released before any queue wait or SDK send call.
Generation checks fence old queued submissions/cancellations from a new
stream. A late original/repair invalidates queued feedback before submission.
The receiver also refreshes processing time after its blocking queue receive,
so request suppression is measured from processing rather than a stale loop
timestamp. No original SBC byte, four-frame aggregation, four-copy count,
presentation timestamp or source-payload reference lifetime changed.

WMP_NACK_TRACKER reports active/pending entries, retry reservations, accepted
submissions and cancellations. It is a normal STAT report, not per-packet
printing. No new queue or per-packet heap allocation was added.

The matching new host sequence remains one queued request with zero retries
until its API acceptance, then one retry:
`evidence/nack-queue-after-1793679598248292800/receipt.json`.
The initial106 native protocol tests passed, including seven new pending/epoch/
suppression/expiry/retry-budget regressions:
`build/protocol-1793679382195701000/receipt.json`.
Xtensa ELF DWARF confirms unchanged sizes:32-byte entry and536-byte tracker,
using existing padding for the pending flag:
`evidence/nack-candidate-abi-1793679385203882600/receipt.json`.

The initial `build/app-1793679385203882600` compiled successfully against the unchanged
pinned SDK and BTstack. It has not been flashed. Samsung is reserved, both
boards retain installed30f, and no live audio, radio, handover or hour pass
is claimed for this candidate.

## Remaining investigation

The earlier16-slot tracker silently omitted four eligible identities in a
20-group burst regression. The final candidate expands it to64 entries and
keeps the400ms lifetime. That raises tracker allocation by1536B; live memory
margin and actual burst occupancy still need measurement. It is recovery
bookkeeping, not increased RF throughput. The saved traces do not
show enough per-ID NACK history to assign group64501 to one failure stage.

The planned transmit-burst comparison (24 credits/unpaced versus8 credits/
1ms pacing) stopped before its treatment when Samsung became unavailable.
It remains untested. Six static SDK RX buffers and application task priority
are a receive-burst hypothesis, not a verified cause. Future hardware tests
must preserve both real phone/BLE workloads and four original copies once
Samsung is explicitly released, or report a different fixture honestly.

## Capacity regression

`evidence/nack-capacity-before-1793679884748227100/receipt.json` executes the
16-slot tracker on a20-group gap and records missing101..104 as untracked.
At44.1kHz with512 samples/group, observing group121 after244ms leaves the
earliest missing group about267ms of the500ms reserve. A120ms request
delay,40ms nominal transmit budget and80ms guard still fit in that model.
The new64-slot tracker retains all20 and also preserves two overlapping
20-group bursts. These model assumptions do not prove physical RF timing.
All108 tests passed in `build/protocol-1793679885158686900/receipt.json`.
The initial unchanged536-byte ABI receipt applies to the pending-flag change
only. Final candidate8689ef35a1911dc2,
`build/app-1793679903768346800`, confirms a2072-byte tracker and32-byte entry
in Xtensa ELF DWARF. Offline ESP image checksums, source hash correspondence
and flash extents passed in
`evidence/nack-candidate-offline-1793680054596544900/receipt.json`.
The final native108-test receipt is
`build/protocol-1793679963879723900/receipt.json`.

The intermediate builds `app-1793679385203882600` and
`app-1793679774587344200` also compiled, but none of these recovery candidates
was flashed. Latest verified installed images remain30f on both boards.

## Reservation preflight

Thirteen two-source diagnostic/qualification harnesses now check local phone
admission before UART/ADB work. Mocked checks exercised all thirteen without
opening either interface. A legacy comparator's unconditional finally-block
restore was also caught and fixed; it now restores observed prior state only.
The failed and corrected host-only receipts are respectively
`evidence/repair-load-1793680075206410900/receipt.json` and
`evidence/repair-load-1793680100377992700/receipt.json`.
Other checks are recorded in
`evidence/phone-reservation-preflight-1793679736576711200/receipt.json` and
`evidence/phone-preflight-remaining-1793680012439524800/receipt.json`.
These expected rejections are host permission tests, not failed RF sessions.
