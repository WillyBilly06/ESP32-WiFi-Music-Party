# Ownership, repeated-copy timing and measurement integrity

The complete goal remains active. This work does not qualify continuous
audio, acoustic synchronization, either handover direction or100 receivers.

## Verified priority comparison

`evidence/standby-priority-1793666274727501400/receipt.json` used two actual
A2DP sources, both BLE clients, four original copies and one unchanged shared
session. All accepted STAT snapshots passed whole-body CRC verification.
Twenty-four damaged read attempts were rejected and retained in the raw
UART journal; only fresh-nonce read requests were retried.

| Receiver policy | Seconds | Receiver conceals | Source conceals | Standby RTP gaps |
| --- | ---: | ---: | ---: | ---: |
|2, normal A2DP/balanced |32.922 |0 |0 |244 |
|1, Wi-Fi preference |32.469 |1 |0 |221 |
|2, normal A2DP/balanced |32.391 |1 |0 |135 |
|1, Wi-Fi preference |30.562 |0 |0 |427 |

Both real input streams and BLE commands progressed throughout. The normal
policy was restored. These finite cases do not establish a benefit from
demoting standby A2DP. The earlier comparison,
`evidence/standby-priority-1793665302809171900/receipt.json`, stopped when an
unprotected log row reported transmit window2; its baseline and a fresh row
reported24. The raw receipt remains a failed/incomplete observation.

## Telemetry integrity

Every STAT response now has a nonce-bound beginning and a final body length
and CRC32. Application formatting extends the checksum over the exact output
text; the reader normalizes UART line endings, reconstructs matching rows
and checks both the length and CRC before interpreting counters. A768-byte
formatting scratch allocation exists only during the request, in ordinary
task context. Non-STAT logs do not share that scratch memory.

`evidence/stat-integrity-live-1793666271659615500/receipt.json` verified live
responses from both boards and rejection of a changed24-to2 field in copies
of those responses. The eight host parser regressions cover dropped and
changed digits, stale rows, missing/duplicate envelopes, formatter/allocation
failure and console noise. Native streamed CRC matches zlib across arbitrary
chunk boundaries. A checksum authenticates no sender and does not make all
counters an atomic simultaneous RF measurement.

The default SDK console sends through a polling UART FIFO path. The next
candidate installs the supported buffered UART driver (256-byte RX,
1024-byte TX), keeping115200 console baud and921600 flashing. Its effect on
the observed missing UART bytes must be measured; adding CRC alone did not
eliminate the damaged output.

## Handover fault and correction

The strengthened reader requires a real nonzero shared epoch, matching
source/follower role bits, matching DAC sample rate and completed preparation.
Optional copy history is not an ownership oracle. Four host regressions cover
the former zero-equals-zero false pass, mismatched epochs/rates and preparing
or clock-invalid output.

In `evidence/two-phone-handover-1793666461158378800/receipt.json`, COM6 took
over and both outputs became48kHz on the same new epoch; readiness was first
observed after7.579s. Repeated rejected telemetry ended the observation before
both directions completed. The accepted snapshots already showed source
concealment, input discontinuities and transmit pressure. It is not an audio
or handover pass.

Code inspection then found that WMP_SILENCE unconditionally cleared the
peer's playing lease. Such a packet can be an old repeated copy after resume,
or a fill during an active transport's temporary input gap. The pending
correction makes the current ANNOUNCE transport state authoritative: silence
does not revoke that lease; ANNOUNCE uses actual AVDTP start/suspend state;
and an established source yields on real transport stop rather than a500ms
RTP quiet period. Genuine pause announcements still permit a standby takeover.
Recent-data telemetry remains separate and input gaps are not hidden.

## Bounded copy dephasing

An optional TXDITHER setting changes only later original audio-copy due times.
Copy0 keeps its origin. Each later due time is independently offset around
its absolute nominal ready+copy*spacing time. The limit spacing/4 preserves
order; the setting is capped at16ms and disabled by default. Repairs, silence,
payload bytes, logical sequence IDs and presentation timestamps are unchanged.
Three native regressions check the zero-setting identity, bounded monotonic
deterministic variation, and invalid/overflow rejection.

The fixed-load crossover compares0/8/0/8ms with60s windows and restores the
prior setting. A configured due time is a host submission plan, not a radio
reservation or an actual RF transmission timestamp.

## Upstream boundary remains distinct

The Linux standby has substantial missing RTP before it becomes the audible
source, and its earlier host HCI observation already contained sequence gaps.
The primary PipeWire source has a socket-EAGAIN path that reduces SBC bitpool
and skips an encoded packet:
https://github.com/PipeWire/pipewire/blob/master/spa/plugins/bluez5/media-sink.c#L1084
This is a plausible mechanism, not a captured EAGAIN diagnosis of this fixture.
It does not explain away separate broadcast losses with a healthy REDMAGIC
source. Espressif's documented shared-RF arbitration remains a constraint,
not proof of a particular packet's loss:
https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32/api-guides/coexist.html
