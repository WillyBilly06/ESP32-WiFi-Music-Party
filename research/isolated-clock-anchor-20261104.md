# Isolated ESP32 clock network

The user reported Ethernet interruptions during PC-hotspot experiments. The
owned Windows hotspot was stopped and its previous configuration restored.
The cause is unconfirmed. Do not resume PC Internet sharing or modify adapters
to satisfy an audio test. This candidate creates the reference BSS entirely on
the admitted ESP32s; the PC does not join or provide its uplink.

Candidatea584df778b0e3daf is frozen in build/app-1793749436022613200.
COM7 will provide an AP clock anchor, while COM6 joins it as a station. Raw WMP
audio, all original SBC bytes, four-frame groups and four copies are retained.
MCS7/HT20 is now also the boot default. Audio source/follower ownership remains
independent of this fixed AP/station assignment, so either phone may take over
without moving the BSS or restarting its clock domain.

The AP and station sample the existing native TSF getter in the background.
Clock domain3 explicitly identifies the local AP anchor; domain2 remains an
associated station. An AP is never reported as an associated station merely to
pass old gates. The validator checks the respective radio role, clock validity,
freshness, sampling span and shared-domain metadata. The SDK documents the
interface-specific getter and the station's need to have received a beacon:
[Espressif Wi-Fi API](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32/api-reference/network/esp_wifi.html).
This is not a physical timing-accuracy claim.

202 host checks pass, including rejection of a fake AP association, missing AP
clock-domain evidence, missing native samples and stale/wide clock reads:
evidence/isolated-clock-host-tests-1793749604240808400/receipt.json.
The complete candidate build and current-source binding passed. Its hardware
clock, direct AP RX, actual dual-phone/BLE audio and heap remain to be measured.

tools/prepare_isolated_pair.py uses local UART RPC only. It creates private group
credentials in hardware/isolated-network.json; no credentials are printed,
passed on a process command line or included by the source-package hardware
allowlist. It calls neither the PC-hotspot helper nor adapter configuration.
The two boards must both have exact verified candidate installations first.

This intermediate network is limited to8 associated stations. It does not
fulfil the100-receiver design requirement. Non-associated clock acquisition,
anchor failure/election, persistent boot provisioning, low measured latency,
clean role switching and an actual hour remain incomplete. No release or audio
qualification follows from these host/build checks.
