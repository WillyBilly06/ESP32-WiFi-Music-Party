# Host automatic-port failure

Windows repeatedly rejected local bind(port0) with10013, including the
standard-library asyncio socketpair used by IDF. Explicit available binds
outside the49152-65535 dynamic range worked. No global port, firewall or
unrelated application setting was changed. The exact system cause is unknown.

`tools/local_ipc.py` selects an unused task port18000-19999, skipping
occupied/denied ports. Control clients use it; `tools/idf_entry.py` replaces
only socketpair in the IDF CLI process and runs the unchanged pinned SDK.
The new socketpair passed a bidirectional byte check and build
`app-1793643303357668500` passed. Failed builds and telemetry receipts remain
preserved.

This repairs host tooling; it is not evidence of radio/audio stability.
