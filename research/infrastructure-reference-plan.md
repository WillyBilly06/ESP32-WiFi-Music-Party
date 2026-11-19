# Optional infrastructure reference

This optional reference is implemented and does not redefine the final goal.
The router/no-router preference question is unanswered. A temporary AP test
may isolate coexistence behavior; it cannot qualify100 receivers or silently
make an external AP mandatory.

The pinned SDK labels connected STA coexistence stable but connected SoftAP
and sniffer RX C1. The reference separates music leadership from Wi-Fi role: both admitted
boards stay connected STAs while group source/follower roles change.

## Transport and clocks

- Keep raw Public Action4/9 frames with our OUI and original four-frame SBC
  aggregation. Audio need not traverse the AP's Ethernet forwarding path.
- Register the audited generic Action handler on source and follower. Source
  control reception must not overwrite its TX copy trace with an RX trace.
- Both boards use the same AP's hardware STA TSF. Preserve association,
  handler registration and the STA clock sampler across group role changes.
- Continue bounded packet ownership, copy suppression, common presentation
  timestamps, clock correction and continuous silence markers. No ESP-NOW/FEC.
- Keep the source renderer24 and follower21 with RX22; current reserve500ms
  is a stability diagnostic, not measured end-to-end latency.

## Provisioning reference

The owned Windows2.4GHz WPA2 test AP is active, maximum eight clients. It has Ethernet internet and an available Wi-Fi adapter.
Use a reversible test-owned2.4GHz hotspot through native WinRT APIs, with
generated credentials kept only in private operational configuration. Avoid
printing credentials or packaging them in the development ZIP. Verify the
existing Linux VM SSH/music path remains reachable after any host change.
No hotspot has been started or configured yet.

Add a bounded application configuration command that joins the test AP before
Bluetooth load. Do not infer association from an accepted connect call or a
transient AP-info result: require the actual connected event as well as current
link state. Keep diagnostics without auto-reconnect inside measured windows.

## Acceptance

Compare against the preserved source119/receiver123 failures using actual
REDMAGIC music, actual Linux standby A2DP, both GATT clients and unchanged
logical audio load. Keep association separate from group leadership, then test
both takeover directions, source continuity on late join and pause/resume.
Pass a strict finite pilot before attempting the mandatory hour. Preserve
failed receipts. Hardware clock estimates do not qualify acoustic output or
100 devices;100-receiver design still requires an explicit topology decision.

## Measured transport comparison, 2026-11-02

Native Ethernet forwarding through the temporary AP works with WPA2. Its
completion callback supplies a protected802.11 wrapper: parse QoS/CCMP before
binding the physical tag to outstanding metadata. The SDK explicitly copies
`esp_wifi_internal_tx` input before return, so native sends retain metadata
and reuse one scratch frame. Raw sends retain each full frame until completion.

At100 groups/s,476-byte payloads and three copies, native windows8/16/24
all delivered only about46-47 unique groups/s with Bluetooth disabled. A deeper
queue did not resolve sustained capacity. Receipt:
`evidence/native-window-1793636118912034100/receipt.json`.

Keeping both stations associated but sending our direct Public Action frames
delivered868 unique groups across868 sequence advances over8.675seconds,
with zero invalid payloads or RX queue drops in that interval. TX and RX
reported PHY code9,24Mbps. This short Bluetooth-disabled observation is not an
audio or coexistence qualification. Receipt:
`evidence/infra130-direct-air-1793636495985783000/receipt.json`.

The native sniff sample observed rate codes0 and11; it did not separately
classify AP downlink and source uplink, so the exact slow hop is unproven.
The AP forwarding path is excluded from the next live audio candidate.

Candidate131 preserves the associated STA hardware clock when switching
from native forwarding to direct packets.500ms is the current initial
stability reserve; it is not a measured phone-to-DAC delay. The Windows AP
reference supports eight associated clients and does not establish100-node
capacity or an AP-free product.
