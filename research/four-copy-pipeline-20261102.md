# Four-copy pipeline diagnosis

At window18, four-copy operation saturated source staging:24 groups pending,
oldest age about490ms against the configured500ms reserve. This was a
measured sender backlog, not proof of random natural RF loss.

At window24, source publication/late/conceal counters stopped increasing in
a short full-workload interval, but source heap could fall below30KB.
Receiver DMA starvation remained with playback priority21.

With TX window24, RX worker24 and receiver playback24, the next full-workload
interval delivered500 unique groups across500 sequence advances over5.82s.
Source and receiver added zero conceals, late, DMA underruns and RX queue
drops. Two groups were recovered and played via repairs. Both actual A2DP
inputs and both Android BLE links remained active. This is a finite digital
observation, not an acoustic, two-minute or hour qualification. Receipt:
`evidence/four-copy-tx24-rx24-audio24-1793647395884858100/receipt.json`.

The memory candidate reduces only the BTstack input staging ring12KB->8KB,
which is still17 ordinary476-byte groups. The separate24KB compressed
playback arena and500ms initial reserve are unchanged. Saved4KB funds the
deep transmit window. Queue tests still verify no overwrite on rejection and
wrap behavior;83 protocol groups pass. Playback priority24 now applies after
both source and follower configuration, including role changes.

A BR/EDR central request succeeded before music, but REDMAGIC later changed
ESP32 back to peripheral role. The clean interval therefore cannot be
attributed to holding source central role. Role preference returns to the
standard peripheral setup, with actual-role telemetry retained.
