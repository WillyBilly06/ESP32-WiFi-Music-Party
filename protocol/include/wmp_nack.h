#ifndef WMP_NACK_H
#define WMP_NACK_H
#include <stdint.h>
/* Cover the source's64-entry cache and bounded earlier requests. A20-group
 * burst is still recoverable inside a500ms presentation reserve, so16 slots
 * silently excluded eligible missing identities before any retry could run. */
#define WMP_NACK_SLOTS 64u
/* The explicit retry limit/lifetime occupy the old structure's padding. */
typedef struct { uint64_t sequence,first_us,next_us;uint8_t valid,attempts,pending,limit;uint32_t lifetime_us; } wmp_nack_entry;
typedef struct { uint64_t generation,highest;uint8_t valid;wmp_nack_entry entries[WMP_NACK_SLOTS]; } wmp_nack;
void wmp_nack_reset(wmp_nack *s);
/* Observe successful playback-queue handoff, not merely a raw copy. */
void wmp_nack_observe(wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now,uint32_t backoff_us);
void wmp_nack_observe_after(wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now,uint32_t backoff_us,uint32_t initial_delay_us);
/* Missing identities precede this received packet. Its source-owned
 * presentation deadline supplies a conservative useful-time ceiling, never
 * extended by subsequent packets. Caller subtracts transport/refill margin.
 * Allow up to16 accepted requests inside that finite window; no audio ACKs. */
void wmp_nack_observe_until(wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now,uint32_t backoff_us,uint32_t initial_delay_us,uint64_t useful_until_us);
unsigned wmp_nack_attempts(const wmp_nack *s,uint64_t generation,uint64_t sequence);
int wmp_nack_plan(wmp_nack *s,uint64_t now,uint64_t *sequence);
/* Low-level accepted-submission accounting. Queued/cross-core callers use
 * the generation-fenced reserve/submit API below. */
void wmp_nack_commit(wmp_nack *s,uint64_t sequence,uint64_t now);
/* Caller serializes tracker access. Reserve once before queue handoff;
 * only successful TX API submission consumes a retry. Epoch tags fence old
 * queued work after a reset, even if its logical sequence is reused. */
int wmp_nack_reserve(wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now);
int wmp_nack_pending(const wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now);
int wmp_nack_submit(wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now);
int wmp_nack_cancel(wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now,uint32_t defer_us);
void wmp_nack_heard(wmp_nack *s,uint64_t generation,uint64_t sequence,uint64_t now);
#endif
