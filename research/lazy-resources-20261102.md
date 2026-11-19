# Lazy runtime resources

Mutable codec and audio state lives in RAM only while its owning operation
needs it. Read-only code and lookup tables remain mapped from flash. This
is a resource-lifetime change; it is not an audio transport qualification.

| Resource | Allocating context | Lifetime |
| --- | --- | --- |
| SBC decoder, 4836 bytes | Renderer task | Preload on audio intake; release after two seconds without SBC work |
| PCM/resampler state, 4144 bytes | Resource service | Active playback/party session, including continuous silence |
| Compressed packet arena and DMA block state, 29608 bytes | Resource service | Active playback; release after five seconds without offers and no stream/party/peer owner |
| Renderer task, 2048-byte stack | Resource service | Same lifetime as playback resources |
| Source SBC queue, scratch group and assembler, 11704 bytes | BTstack host event/timer | Source preparation and source role; keep warm for seamless source silence/resume; release on standby or explicit stop |
| Transmit slot metadata, 4096 bytes | Transmitter task | Source/probe role; release only after outstanding work finishes |
| Detailed copy and repair histories | Explicit COPYLOG command | Off by default; allocate together on request and free together on disable; total reported as history_bytes |
| Clock sampler, 2048-byte stack | Clock service | Enabled clock domain; self-delete when disabled |

Bluetooth/Wi-Fi connection services, receive queues, small control metadata
and listeners remain resident to accept phone connections and late joins.
Our ordinary radio callbacks do not allocate; application audio packets reuse
activated pools. Vendor driver internals retain their own allocation policy.
First activation runs in normal task context. Pending source packets are
retained for retry while playback resources are being created.

The source assembler formerly reserved1232 bytes of static RAM even on a
standby receiver. It now belongs to the lazy source runtime. The packet
handler never allocates it; stream events and the host timer prepare it.
The stream-start event publishes the active state before draining the source
queue, avoiding an immediate allocate/free cycle at first startup. Detailed
repair records previously allocated at boot now share COPYLOG's lifetime.
Ordinary repair operation and counters do not require either history ring.

Inactive mutable working buffers are freed, rather than written to flash.
Read-only codec instructions and tables stay in the mapped flash image.
The flash-layout audit binds those assertions to the built ELF hash; it
cannot establish runtime or acoustic performance.

Pool teardown blocks new producers and waits for the producer, renderer and
borrowed/retired payload references to become quiescent. I2S is disabled and
deleted before playback storage is freed. A worker deletes its own task;
external code does not operate on a task handle during teardown.

Allocation failure rolls back partial playback allocation and retries with
backoff. Source queue allocation also backs off for500ms. A failed lazy
transmit-slot allocation clears the source-role request latch and permits a
later retry. Forced out-of-memory behavior has not been hardware-qualified.

## Diagnostic ownership

`COPYLOG 1` enables detailed history; `COPYLOG 0` releases it. `TRACE <nonce>`
still reads history. The former resource spelling `TRACE 0/1` collided with
the hexadecimal query parser and is replaced by the distinct COPYLOG verb.
`capture_copies.py` temporarily enables missing histories, allows the ring
to populate, captures matching traces and restores resources it enabled.
Existing explicitly enabled history remains enabled.

Detailed history is not required by audio or repair delivery. It supplies
optional packet/callback timing and diagnostic repair annotations. Regular
audio fault counters continue without allocating a detailed history ring.

## Regressions caught during hardware testing

An early candidate repeatedly unloaded COM6 during intentional silence.
Its service read the clock before another core refreshed the activity
timestamp. Unsigned subtraction could then turn a fresh offer into an
apparent idle interval. The original endpoint-only lifecycle test did not
reject this churn: COM6 had147 starts and146 stops before explicit stop.
Its receipt is preserved with a disqualifying `review.json` beside it:
`evidence/lazy-lifecycle-1793650851482106200/`.

The fix reads time/activity under the same lock, rejects half-range/future
ages and allows a fresh offer to cancel a pending teardown. The renderer
commits shutdown under that lock. The strengthened hardware test requires
eight seconds of advancing silence output without any playback or codec
resource restart, plus exactly one pool unload for the explicit stop.

The corrected intermediate image passed that gate, decoder reload, full
teardown and full reallocation on both boards:
`evidence/lazy-lifecycle-1793651853448572200/receipt.json`.
Its cold free heap was113360 bytes; measured source current heap included
34372 and36592 bytes, with an eventual low-water of29932 bytes while detailed
history was still automatically allocated. This prompted the explicit
copy-history lifetime in the final candidate. Measurements from this image
must not be relabeled as final-image measurements.

## Qualification boundaries

Portable protocol tests passed84 groups, including quiescent-pool checks
with references above index31. Current-image hardware tests and exact
installation receipts are recorded in CURRENT_STATUS.md.

Final image `app-1793652377021110700` passed the strengthened lifecycle gate
on both boards in `evidence/lazy-lifecycle-1793652896991493400/receipt.json`.
Cold free heap was113360 bytes on both. The subsequent music observation
ended with44168 source free bytes and a33896-byte source low-water; receiver
values were64340 and46532 bytes. Optional history stayed at0 bytes during
that observation. A receiver concealment after25.86 seconds failed its
audio gate; it added no resource restarts or codec/queue/write faults.

REDMAGIC supplies real YouTube/A2DP; both boards have BLE test clients.
Linux Classic has subsequently connected and supplied the standby stream.
Its host-side HCI observation also contains RTP gaps before ESP32 delivery;
the cause between the peer/controller/VM paths is not established. There is
no current passing dual-source, one-hour,
acoustic synchronization or100-speaker qualification. Receiver concealments
observed in earlier music remain failed evidence, not explained away as
natural RF loss or fixed by the memory refactor.

## Current four-copy image

Both boards now have candidate `7b3d93320f688736` from
`build/app-1793664270233146200`, installed at921600 baud with exact readback
and two verified boots per board. Flash placement passed in
`evidence/lazy-layout-1793664547165286300/receipt.json`: the SBC decoder
vtable, synthesis tables, sample-rate table, PLC coefficients and CRC table
are in mapped DROM. The source assembler has no static ELF allocation.

`evidence/lazy-lifecycle-1793664792939530300/receipt.json` passed the
current-image lifecycle checks: eight seconds of advancing silence without
resource churn,4176-byte optional copy/repair histories loaded and freed,
decoder reload for real music, decoder unloading on pause, full idle pool
teardown and resumed decoding on both boards. BLE stayed connected and
applied commands throughout. Source restart allocated once and did not
retire that allocation again; the subsequent three-second music check added
no playback, decoder or source-resource restarts. Allocation failures were0.

| Measurement | COM7 source | COM6 follower |
| --- | ---: | ---: |
| Heap recovered from music to idle, bytes |65376 |47332 |
| Idle free heap, bytes |113184 |113188 |
| Resumed music free heap, bytes |46364 |65856 |
| Global low-water through the diagnostic, bytes |34216 |51876 |

The source owns11704 bytes of queue/scratch/assembler storage; the follower
reported0 bytes of that storage. Both used4836 decoder bytes during SBC
playback and0 during sustained intentional silence. Essential Bluetooth,
Wi-Fi and receive services remain resident for connections and late joins.

An intermediate candidate passed the large-buffer transitions but failed
the strict single-source-allocation check on resume; that receipt remains
`evidence/lazy-lifecycle-1793664181610014000/receipt.json`. The source queue
now remains prepared for an actually started AVDTP transport even if recent
RTP activity temporarily expires. The final run passed the original single
activation check as well as the steady-music gate. This finite result does
not establish an hour without allocation churn or solve radio loss.

This lifecycle workload used one active REDMAGIC source and both BLE
clients, with the Linux source paused. Its memory figures do not qualify
the complete simultaneous two-source workload, end-to-end acoustic latency,
or a permanent heap floor. Failed telemetry reads and the corrected ELF
aggregate-section-flag audit remain preserved separately.

Afterward the Linux Classic connection was restored through normal BlueZ
APIs. Its paused HTTP music process ended on an upstream stream error;
the exited process and failed receipt were retained before starting a new
owned player. The final short restoration check,
`evidence/post-lazy-fixture-1793665011867104500/receipt.json`, verified its
exact Bluetooth sink and rendering, advancing incoming RTP and BLE commands
on both boards. Source current/minimum heap was35264/34216 bytes; follower
values were65856/46784 bytes. This does not extend the memory lifecycle
observation into a full simultaneous-source stability qualification.
