# Qualification must name and verify its firmware target

The existing timing/handover/hour harnesses recorded deployment metadata,
but did not require the boards to match the candidate under evaluation.
Installed30f could therefore be measured while the new source candidate
2546 remained unflashed. A receipt's recorded image is evidence only for
that image; it cannot qualify another source tree or binary.

The full two-phone hour and handover harnesses now require --app. The shorter
pipeline diagnostic accepts --app for the same checks; a run without it is
explicitly marked candidate_target_asserted=false.

The new offline binding checks the candidate's current own-source coverage,
dependency lock, raw image hashes and protected erase extents. For COM6 and
COM7 it requires matching deployed IDs, successful terminal installation,
admitted MAC, two verified boots, unchanged outside-extent audits and exact
raw/padded readback image identities. Fresh INFO responses from the existing
UART owner must match both board MACs and the selected build before the
measurement begins. Those are build bindings, not fresh ROM/USB admission
or proof of audible audio. The guarded installer still owns destructive
admission and writes.

Phone authorization is checked before these operations, and checked again
during long measurements. Samsung's testing_enabled remains false.
Mocked harness tests confirm rejection before candidate checking, UART or
phone access in
`evidence/target-guard-admission-1793702861199585100/receipt.json`.

The offline current-state check rejected candidate2546 because COM6 remains
installed30f:
`evidence/qualification-target-preflight-1793702801969378700/receipt.json`.
No serial, phone, reset, flash or media work occurred in that check.

## Protected image areas

The previous generic upper bound0x190000 did not constrain a partition-table
erase to0x8000..0x8fff. A4097-byte table would pad to8192 bytes and overlap
NVS at0x9000, even though the artifact areas did not overlap each other.
The host regression reproduced this acceptance in
`evidence/image-extent-before-1793702739766502400/receipt.json`.
This is a synthetic metadata/extent test, not evidence that a board was
written incorrectly; the actual candidate's table is3072 bytes.

Shared range checking now constrains bootloader erase below0x8000, table
erase below0x9000 and application erase below0x190000. Both standard and
historical low-baud installers use the same check, before acquiring a serial
lease. Existing NVS and otadata gaps remain protected. Routine flashing
continues to use921600 per the user's instruction.

Ten tests in tests/test_firmware_evidence.py exercise exact/mixed versions,
failed installations, padded readback mismatch, edited source/binary,
missing boot/extent evidence, live wrong-MAC/build responses and partial
two-board INFO completion. They use temporary local files and mock INFO,
without hardware. Four existing readiness tests also pass.

No firmware source or binary changed. Candidate2546 and its119 protocol
tests remain the software target. Hardware results, heap, roles, acoustics
and the unchanged hour remain unqualified until deployment and measurement.
