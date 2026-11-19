# Repair delivery experiment

A controlled known-group loss on the actual two-A2DP/two-BLE workload
recorded45 receiver requests,22 heard at the sender,12 queued repairs,
7 repair packets observed,4 repaired groups delivered/played and9 conceals.
This includes natural losses as well as the injected group. It does not
prove that the injected group alone failed. Receipt:
`evidence/infra135-repair-fault-1793641780898318200/receipt.json`.

The receiver validates replay flags before deduplication, so observed repairs
are counted even if an original copy eventually arrives. Repetition timing
and feedback delivery remain material issues; no unconditional reception
guarantee is claimed.

The next candidate retains regular three-copy audio and adds:
- An8ms callback-relative receive window for sending pending requests on the
  associated direct profile. A received source frame is evidence of a recent
  usable receive opportunity; its callback time is not an exact RF timestamp.
- One application transmit credit reserved from ordinary audio/announcements
  for control/repair. Due repairs win app scheduling, while future repairs
  cannot block already-ready audio. This does not preempt the vendor FIFO.
- Up to two repair copies with40ms completion-relative spacing and80ms guard,
  within the unchanged80ms admission budget.
- Separate attempted/accepted/completed/failed repair counters.

The queue waits for notifications while the feedback receive window is closed;
it does not spin on an overdue deadline.82 portable tests pass, including
future-repair ordering, receive-window expiry and32-bit timer wrap. Hardware
validation of this candidate remains pending.

## Subsequent findings

The body-length sweep did not establish the proposed alignment bug: every
observed direct callback was queued for payloads473-480, with zero decoded
probe corruption. One473-byte counter interval crossed a receive-session
change and is invalid; the original receipt is preserved with a separate
analysis.

The repair candidate still added conceals on the full workload. The next
change waits120-140ms before requesting a missing group, instead of racing
the scheduled original copies at20-40ms. Requests retain up to eight32ms
retry opportunities within400ms. The same sixteen fixed records now hold
sparse missing sequences, so losses16 apart cannot overwrite one another.
Up to three repair copies use40ms completion spacing, with a conservative
110ms per-opportunity planning budget and80ms guard.83 portable tests pass;
hardware evidence remains pending.

The first repair candidate is installed as`app-1793643303357668500` with
separate repair submission/completion counters. It still failed the strict
full-workload conceal gate. Its packet-size sweep accepted every observed
callback at473-480 bytes; an alignment fix was therefore not adopted.
The extended-grace/sparse-tracker candidate is`app-1793645073378648800`,
built successfully and currently being installed on both admitted boards.
