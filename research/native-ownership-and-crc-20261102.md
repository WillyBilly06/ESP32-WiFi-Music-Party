# Native ownership and receive CPU work

The pinned SDK header `esp_private/wifi.h` documents that
`esp_wifi_internal_tx` copies its input before returning. Native mode retains
32 immutable metadata entries and one scratch frame; its24-bit completion
tag remains owned until completion, including timeout quarantine. Raw frames
still have eight retained buffers: no raw-copy guarantee is assumed. Native
mode frees the raw pool only after quiescing every outstanding owner.

A low-rate hardware probe had116 accepted/completed sends, no TX failures,
timeouts or invalid received payloads. Enlarging the native window8->16->24
did not improve sustained useful throughput; see
`evidence/native-window-1793636118912034100/receipt.json`.

Direct packets bypassing AP forwarding delivered100 groups/s with904-byte
payloads, three copies and an associated hardware clock on both boards, with
no sequence gaps in the short direct-handler interval. That is a Bluetooth-off
transport measurement.

With two actual A2DP inputs and two BLE clients, removing the extra1ms TX
pacing eliminated publication retries in a6.7s observation. It did not qualify
audio: the receiver still added two conceals and one late group. Subsequent
strict pilots failed on DMA starvation at priority21 and RX queue overflow
at higher playback priorities. These failed receipts remain preserved.

The receive path verified the wire CRC and logical CRC using eight bit steps
per byte. Candidate132 replaces this with a256-entry read-only flash table
and separates header masking from the payload loop. Independent zlib CRC
references cover266 payload lengths plus the canonical check vector and
wire/logical checks; all80 portable protocol tests pass. The wire format and
CRC polynomial are unchanged. Hardware improvement remains to be measured.

Linux BlueZ5.85 exposed `PreferredBearer=bredr` on the dual-mode board, which
made the BLE probe open Classic. Selecting LE for this one test device fixed
BLE; an explicit A2DP profile connection then established Classic alongside
it. The four GATT services and ongoing writes/notifications were observed.

## Raw path audit and next candidate

The raw API wrapper allocates through `ic_ebuf_alloc` before posting the
packet to HMAC. Its `esf_buf_alloc` static path and `esf_buf_alloc_dynamic`
path both copy input with `memcpy` before returning. The assembly, archive
hashes and scope are recorded in `research/raw-tx-copy-v61/audit.json`. This
is a pinned-implementation finding, not a promised public API contract. The
build now rejects different SDK/archive hashes until that audit is renewed.

Candidate133 reuses one serialized scratch frame in both native/raw paths,
retains immutable metadata until each completion, and defaults to16 outstanding
packets. It removes the8x1008-byte raw payload pool. Input audio references
remain pinned through final submission, and timed-out metadata is quarantined
until an actual completion. Hardware validation is still pending.

Four copies with the old eight-entry window sustained only about295 TX
completions/s and overloaded source publication. That failed case was stopped
and three copies restored. The deeper queue experiment must demonstrate
useful delivery and memory reserve before four-copy audio is adopted.
