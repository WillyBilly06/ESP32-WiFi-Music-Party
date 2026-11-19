# Linux Bluetooth test source

The existing VMware Ubuntu VM is `E:\Linux 2\Ubuntu 64-bit.vmx`.
Verified normal SSH login: `billydakidz@192.168.63.129`, hostname `billy-pc`.
This NAT address is reachable from the Windows host; it is not a public URL.
The password is not stored in this project or its development archives.

The identity-matched Realtek USB Bluetooth adapter0bda:8922 is assigned to the
VM for testing. Windows Bluetooth is unavailable while VMware owns that adapter.
COM6/COM7 remain Windows-owned. No other USB device is assigned to the VM.
Samsung is user-reserved and is excluded from the current test-source map.

The Linux adapter is E8:47:3A:EC:51:9A; the admitted COM6 Bluetooth target is
6C:C8:40:42:BF:66. Only that target is paired/configured by the Linux helpers.
The active A2DP profile is standard SBC, not SBC-XQ.

BlueZ5.85 exposes the necessary PreferredBearer control with experimental APIs.
A test-owned runtime override at
`/run/systemd/system/bluetooth.service.d/wmp-test.conf` starts bluetoothd with
`--experimental`; the installed main configuration is unchanged. The override
does not survive reboot. Reverting it and restarting bluetoothd disconnects
active Bluetooth tests, so do that only after stopping the test clients.

For simultaneous Classic and LE to this dual-mode address, establish the LE
GATT client with PreferredBearer=le first, verify four services and actual
writes, then change PreferredBearer to bredr and connect the A2DP sink profile.
Attempting GATT through a Classic-only Device1 connection produced an empty
service list and could disconnect A2DP when the client closed.

Owned Linux files are under `/home/billydakidz/wmp-linux-source`:

- `linux_gatt_probe.py` uses Bleak for real control writes, acknowledgments and
  notifications. It records failures and exits on disconnect; no auto-reconnect.
- `linux_music_source.py` uses GStreamer/PipeWire to send direct SomaFM music to
  `bluez_output.6C_C8_40_42_BF_66.1`. It saves status, not the audio stream.
  Explicit pause closes the live HTTP pipeline; play opens a fresh stream.
  This preserves the Bluetooth ACL/GATT connections and avoids unsupported
  HTTP Range seeking after a long pause. Stream errors remain terminal.
- `linux_audio_bridge.py` exposes private status and bounded play/pause control
  at192.168.63.129:8766. Its token is in private operational configuration,
  excluded from source archives. It cannot execute arbitrary commands.

Windows uses the isolated Paramiko environment under `build/linux-client-env`
and `tools/linux_remote.py`. Credentials arrive through hidden stdin, never
command arguments/files. SSH host identity is recorded in
`hardware/admitted-linux-host.json` and `hardware/linux-known-hosts`.
The Linux Python test environment is an owned venv; GStreamer uses the existing
system GI modules. Firmware flashes end the BLE probe; reconnecting starts a
new run after preserving the old failed/ended status.

This replacement has demonstrated simultaneous A2DP input and real BLE
transactions. It has not established lossless Wi-Fi audio or a one-hour pass.
