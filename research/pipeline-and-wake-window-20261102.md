# Pipeline timing and connectionless wake registration

Both boards run `e24b52020896d8aa`, `build/app-1793675411909678900`.
Exact readback/two-boot receipts are
`evidence/install-1793675603033879900-COM6/receipt.json` and
`evidence/install-1793675603030838700-COM7/receipt.json` (921600 baud).
The image adds aggregate decoder wall time, native completion dwell, late
sender wakeups and original-reference age from audio admission. No per-packet
printing or allocation was added to callbacks.64-bit sums have short locks.

`evidence/pipeline-timing-1793675992287084700/receipt.json`,62.36s, both real
phone streams plus BLE, original SBC, four originals, no FEC:

- Source344.51 input frames/s versus278.30 original completions/s.
- Decoder mean342us,9.52% wall budget; send API mean345us.
- Native original dwell mean78.39ms, including callback scheduling.
-2273 completions exceeded100ms; no new sender wakes exceeded5ms.
- Oldest sequence11519 to15881; maximum observed admission age937ms.
- Source blocked4204 codec frames and concealed4232 missing chunks.

This separates delay after API submission from decode and timer cost; it
does not separately measure driver, RF and callback-dispatch delays. Startup
lifetime maxima must not be presented as measurement-window maxima.

The source's single ROLECENTRAL request produced status0 with role1; its
actual role stayed peripheral. ROLEPOLICY1 was restored. This was not a
successful central-role comparison:
`evidence/classic-central-review-1793676313992402100/receipt.json`.

Completion-based copy spacing did not fix delivery. Absolute64ms /
completion-paced16ms / absolute64ms had source conceals1903/2410/3074 and
receiver conceals10967/9543/10780. Both phone streams/BLE remained active.
See `evidence/completion-spacing-1793676359186678200/receipt.json`.
Absolute64ms and8ms dither were restored.

The audited generic PM registration (`research/receive-window-v61/audit.json`)
was compiled but disabled. Its argument is a persistent uint16_t pointer.
No SDK source/binary or ESP-NOW initialization is involved.

Off/on/off on both endpoints, same continuous two-phone/BLE session:
`evidence/receive-window-1793676534229530900/receipt.json`.

| Registration | Original completions/s | Source conceals | Blocked frames | Receiver conceals | Receiver late |
|---|---:|---:|---:|---:|---:|
| off |289.54|1903|1836|10533|1208|
| on |346.05|0|2|629|43|
| off |247.51|3095|3150|10658|1013|

Input RTP gaps were0 throughout. Native mean dwell was75.55/45.89/87.98ms.
Ordinary WIFI_PS_NONE was already configured. Registration materially changes
service in this fixture; this does not identify an exclusive internal cause.

Both registrations were subsequently enabled for60.57s:
`evidence/pipeline-timing-1793676715565209400/receipt.json`. Original rate
333.15/s remained below344.51 input, with507 source conceals,450 source
blocked frames,5307 receiver conceals and997 late groups. Native dwell
averaged41.37ms; source current free heap stayed at least34584B.
Repair submissions consumed25.49 packets/s. This is a partial fix, not
stable audio, a one-hour pass, an acoustic result or100-node qualification.

Primary references:
[ESP-IDF6.1 power management](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32/api-guides/wifi-driver/wifi-performance-and-power-save.html)
describes connectionless modules' separate Window/Interval allocation under
coexistence. It does not specify the private generic registration ABI or
guarantee this transport's performance.
[ESP-IDF6.1 RF coexistence](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32/api-guides/coexist.html)
describes shared RF arbitration. Requested copy cadence is not an RF timestamp.

## Follow-up and automatic registration

With both windows registered, receiver policy2/1/2/1 still failed:
source conceals0/692/0/874, receiver3724/5660/1555/8183, standby RTP gaps0:
`evidence/standby-priority-1793676830663887500/receipt.json`. Profile2 was
restored on both boards. Changing receiver policy also changed a variable
source load; these observations are not an isolated RF-cause proof.

NACK delay60/120/60ms retained four originals and enabled repair. Source
conceals267/0/0 and receiver3190/1044/1786; receiver requests2129/1117/1981:
`evidence/nack-delay-awake-1793677017932456200/receipt.json`.
Source blocked266/59/7 frames, so no case is an intact-stream pass. Runtime
120ms was selected for follow-up, with no claim of stability. The60ms initial
request is earlier than the192ms requested original-copy span, which motivates
later-request tests; actual native delay and presentation headroom must count.

Build30f786f65659c29e, `build/app-1793677288512137400`, adds automatic generic
window registration on DIRECTRX activation, rollback on setup failure and
paired unregister when the owning path closes. The prior auto-only build
`build/app-1793677077860143400` built successfully but was never flashed.
The current image additionally exposes the public SDK wake interval setter
as RXINTERVAL (0 is default; bounded10..1000ms alternatives), and expands
NACKDELAY to20..300ms. Defaults remain the SDK default interval and60ms NACK.

Both exact installs passed:
`evidence/install-1793677411492040700-COM6/receipt.json` and
`evidence/install-1793677411641616300-COM7/receipt.json`.
Automatic registration off-to-on passed without manual RXWINDOW commands:
`evidence/auto-wake-provision-1793677631898486000-COM6/receipt.json` and
`evidence/auto-wake-provision-1793677525303891100-COM7/receipt.json`.
Both actual phones and both BLE clients were restored after firmware reboots.
Reboots are deployment interruptions, never evidence of continuous stability.
