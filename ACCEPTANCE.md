# Full-goal acceptance audit

The objective was resumed on2026-11-03 and is unachieved. Samsung is now explicitly released for Music Party tests at20% volume.
Current hardware tests use REDMAGIC/Samsung; Linux remains an optional labelled fixture. The functional scope is
unchanged. A development archive is not a release.
User amendments remove FEC and permit additional common latency for stability.

| Requirement | Current evidence | State |
| --- | --- | --- |
| Fresh project, pinned clean IDF6.1/BTstack1.8.3, admitted COM6/7 only | Current build manifest, dependency cleanliness guards and exact installation receipts in CURRENT_STATUS.md | Implemented and checked |
| Original backups/failed evidence retained; latest image kept | Identity/MAC/extent/readback/two-boot installer receipts; failed observations preserved | Checked for current installs |
| Original SBC bytes/sample counts; four contiguous frames; four originals | Portable wire/group/cache tests, actual copy admission and injection masks; corrected cold override | Implemented; sustained delivery not qualified |
| A2DP plus BLE on both, standalone source and incoming standby handling | Both61e5 installations; both real phone/BLE streams active; audio counters still fail | Finite admission observed; sustained and promoted Linux-source quality unqualified |
| No ESP-NOW/CSB/FEC or copied controller patches | Current source/component lists and pinned vendor hashes | Implemented boundary |
| Duplicate suppression and no per-receiver audio ACKs | Logical CRC/identity tests and shared broadcast repair protocol | Implemented;100-node feedback behavior not qualified |
| Hardware common clock and shared presentation timeline | Two associated stations' native AP TSF and CRC-protected session epochs | Finite digital evidence; acoustics unmeasured |
| Seamless pause/silence/resume | Earlier lifetime/pause tests and current code | Current four-copy full-workload repeat pending |
| Either board leads; standby takeover both directions | Current61e5 rolling PHY handoff passed; full two-direction clean audio remains pending | Roles pass; clean handover audio and sustained output remain unqualified |
| Late join does not restart master | Running-source generation unchanged in finite late-join receipt | Digital join proven; sustained/acoustic qualification incomplete |
| Raw transport useful throughput and coexistence matrix | Historical controlled BT-off/idle/A2DP/BLE cases; current actual full-workload failures | Current end-to-end coexistence unqualified |
| Constant gap-free audio; at least one real hour | Current role-dependent coexistence pilot failed at7.016s; no passing hour | Failed; no completed passing full-workload hour |
| Low latency and synchronized physical PCM5102A output | Correct wiring and digital DMA/phase counters;150ms requested candidate failed;500ms prior stability candidate also unqualified | Physical end-to-end delay/alignment not measured |
| Design toward100 receivers, validate only tested hardware | Broadcast IDs/timeline/repair suppression; current AP reference limited8 clients, only2 boards |100-node architecture/qualification incomplete |
| Organized reproducible source, tools, evidence and package | Current pinned build, scripts and development archive | Development deliverable; release gate closed |

SDK raw-rate lifecycle is now corrected and hardware-checked. Next required evidence is a stable delivery policy,
passing current full-workload pilot, both automatic handover directions,
current pause/late-join regression, then the unchanged continuous hour.
Physical output and scale claims require their own matching evidence.
Receiver loss recovery remains unfinished; host-side source gaps are a
separate issue and must not be used to explain away all Wi-Fi losses.
