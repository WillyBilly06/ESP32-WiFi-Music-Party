# Current development status - 2026-11-03

The goal remains active and incomplete. Samsung is reserved and excluded
from ADB, UI, media, volume, Bluetooth and GATT operations. Only the original
admitted COM6/COM7 boards and REDMAGIC/Linux replacement fixture are used.
No passing full-workload hour, clean current takeover audio, acoustic timing,
standalone boot/100-node qualification or release exists.

## Exact current firmware

Both boards run `c959bab73818b0ed`, `build/app-1793730517524498700`.
Installations at921600 passed exact readback, protected/NVS-region audits
and two identity-verified boots. COM7 kept playing as the reference while
COM6 was updated, then COM7 was updated as well.

- COM6: evidence/install-1793730692779681700-COM6/receipt.json
- COM7: evidence/install-1793730952466576600-COM7/receipt.json
- Offline source/images: evidence/rate-lifecycle-offline-1793730690645877300/receipt.json
-135 portable checks: build/protocol-1793730514487343600/receipt.json

Clean pinned ESP-IDF6.1 and BTstack1.8.3 remain unchanged. Original SBC bytes,
frame/sample counts and PCM5102A WS25/DOUT26/BCLK27 are preserved. No delivered
ESP-NOW, CSB or FEC. Old failed receipts, backups and images remain intact.
The64-slot/32KiB payload pool,16-entry1664B source metadata queue, native
window8/capacity16, host stack3072B and RX stack3584B are unchanged.

## Rate configuration corrected and checked on hardware

Application-owned public raw-rate API calls are now confined to a stopped
Wi-Fi interface before start, as required by the pinned SDK. Startup configures
AP; cold network provisioning stops Wi-Fi, configures STA, starts it, then
associates. Actual music-role changes retain the established STA association.
The selected54Mbps profile is unchanged. Different-rate console requests are
allowed only in an idle AP context before phones/network/path registration.
An already configured identical rate is a no-op during playback.

Both boards accepted6/9/12/18/24/36/48/54Mbps configurations in the cold test:
evidence/rate-lifecycle-1793731024341883200/receipt.json. During two actual
music streams and BLE,54 was a no-op and24 was rejected on each board.
Wi-Fi start/stop/configuration counts, source generation, DMA identity and
hardware-clock domain did not change:
evidence/rate-lifecycle-1793731232539709400/receipt.json.

The old f7 driver callback already reported rate12, SDK WIFI_PHY_RATE_54M;
new callbacks also report12. This is a last reported frame, not an RF-rate
histogram or independent airtime capture. The documented call-order defect
was real but did not prove the old54 request was ineffective or cause loss.
See research/rate-lifecycle-hardware-20261103.json and
evidence/rate-order-baseline-1793730619585696100/receipt.json.

## Natural loss now covered before application reception

The current strict180s pilot FAILED at74.812s with four receiver concealed
frames. Both input RTP gap deltas and source queue/blocked/audio fault deltas
were zero; no Wi-Fi restart or late/underrun occurred.
Receipt: evidence/party-long-1793731275177687600/receipt.json.

The faster prospective observer caught another natural missing group:
evidence/replay-watch-1793731511030501300/receipt.json. Group35363 is inside
both CRC-checked histories' common settled range35362..35407. The source
submitted, accepted and completed all four originals, with0 failed/injected
copies; last completion was approximately336ms before its presentation.
The receiver's retained row records0 original callbacks,0 validated copies
and0 valid replay. Neighbouring groups were received. No input RTP gap,
source queue overflow, or source audio fault occurred in that window.

This rules out late decoder consumption as the cause of this particular
missing group. It does not distinguish RF erasure from filtering/dropping
inside the driver before the application callback. Source repair history
read after the copy dumps had rolled past this target; it does not prove
which requests or physical repair transmissions occurred.
See research/natural-copy-absence-c959-20261103.json.

## Earlier feedback candidate - experimental, longer test running

With identical500ms presentation reserve, four originals,64ms copy spacing,
8ms dither, eight repair references and40ms repair spacing, a120/40/120ms
initial NACK-delay comparison yielded8/0/4 receiver concealed frames.
Each case ran approximately90seconds; source/input/queue faults stayed zero.
Earlier requests increased repair traffic, and no source overflow was observed.
Receipt: evidence/repair-release-1793731752163060200/receipt.json.

The comparison restored120ms afterward. A separate explicit candidate then
set40ms on both boards, with readback:
evidence/earlier-nack-candidate-1793732102355672600/receipt.json.
This remains a runtime experiment. Compiled default is60ms; standard path
provisioning still selects120ms. No unverified default change is shipped.

The first strict600s experiment,
evidence/party-long-1793732117741411000/receipt.json, FAILED at23.281s:
receiver audio added0 conceal/late/underrun, but Linux input added one malformed
RTP packet and one gap, and source free heap reached29716B. Those failures
remain open. The malformed incoming packet has not been captured/classified.

The8/6/8 native outstanding-buffer comparison then completed with minimum
sampled source heap29716/31196/30456B, raw accepted rates approximately
403.0/401.6/404.7pps, source blocked/queue/late faults all zero, and receiver
conceals0/4/0. This supports six for memory headroom; it does not establish
better delivery. The comparison restored eight afterward.
Receipt: evidence/audio-tx-window-1793732314779199800/receipt.json.

A separate explicit candidate set native window6 and NACK delay40ms on both
boards, without changing SBC data, copies or common presentation reserve:
evidence/window6-nack40-candidate-1793732614505181800/receipt.json.
Compiled default window remains8; compiled NACK60ms and normal provisioning
120ms remain unchanged. This runtime candidate is experimental.

The new strict600s acquisition at
evidence/party-long-1793732618622715800/receipt.json FAILED at314.032s with
four receiver concealed frames. Both incoming streams added0 RTP gaps/bad
packets, source queue/blocked/audio faults stayed zero, no late/underrun was
added, and minimum sampled source heap was31196B (receiver41924B).
The resource change clears the observed memory failure; loss recovery is
still unqualified. No600s or one-hour pass exists.
A separate40/20/40ms repair-copy spacing comparison is underway with request
delay40ms/window6 otherwise fixed. See research/earlier-nack-window-budget-20261103.json.

## Retained takeover failure and remaining scope

The preceding f7 readiness publication fix passed both automatic role
directions with unchanged gates. Ready was first observed at6.937/6.297s;
new source input gaps and blocked frames were0/0. Followers still concealed
20/12 frames by their first ready sample. That is an audio failure:
evidence/two-phone-handover-1793729992872209800/receipt.json.
The current image retains that fix, but needs its own repeat after choosing
a delivery policy. Before-playback recovery and transition receive coverage
remain unresolved hypotheses, not established causes of every loss.

Linux uses only adapterE8:47:3A:EC:51:9A in the existing Ubuntu VM, with its
real SomaFM source at20% sink volume. REDMAGIC uses its existing YouTube.
Both incoming A2DP streams and both BLE clients remain part of tests; the
follower discards its phone stream while playing the group. Samsung is unused.
The Linux adapter assessment mode0 fixture diagnostic is finite and restores
original mode1 at stop/timeout/link change. The current helper is bounded to
1200s from18:55:17 UTC and expires19:15:17 UTC. It must be stopped/restored
after the running pilot (expected19:13:38 UTC) or verified terminal sooner. This
fixture setting is distinct from ESP32 firmware and normal Linux policy.

The owned reference AP supplies native TSF to both STAs and supports at most
eight clients.500ms is a shared presentation reserve allowed for stability,
not measured phone-to-output latency. Required gates remain: stable useful
transport, clean startup and both handovers, pause/silence, late join, current
strict pilot and uninterrupted hour, memory bounds, physical synchronization,
standalone deployment and a credible100-node design/validation boundary.
