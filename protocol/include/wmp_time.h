#ifndef WMP_TIME_H
#define WMP_TIME_H
#include <stdint.h>
/* Stream preparation is independent of a stable associated-AP clock domain.
 * Callers serialize this state together with playback-queue admission. */
typedef struct { uint64_t ready_us;uint32_t version,clock_epoch;uint8_t warm; } wmp_preparation;
int wmp_preparation_begin(wmp_preparation *state,uint64_t now,int shared_clock,uint32_t clock_epoch,int renderer_running_shared,uint32_t renderer_clock_epoch);
/* qualified_ready_us is a local clock-acquisition deadline, not packet PTS.
 * Passing0 retains the existing running-renderer/latch policy. */
int wmp_preparation_begin_at(wmp_preparation *state,uint64_t now,int shared_clock,uint32_t clock_epoch,int renderer_running_shared,uint32_t renderer_clock_epoch,uint64_t qualified_ready_us);
int wmp_preparation_allows(const wmp_preparation *state,uint64_t now,int clock_valid);
int wmp_preparation_keeps(const wmp_preparation *state,uint32_t admitted_version);
void wmp_preparation_forget(wmp_preparation *state);
uint32_t wmp_election_delay(const uint8_t address[6]);
/* Original copies retain an absolute origin and unchanged first-copy time.
 * Later copies may shift within +/-jitter_us; jitter <= spacing/4 preserves
 * order. This is a host submission plan, not an RF reservation. */
int wmp_copy_due(uint64_t ready_us,uint64_t identity,unsigned copy,uint32_t spacing_us,uint32_t jitter_us,uint64_t *due_us);
typedef struct { uint64_t extended;uint32_t previous;uint8_t valid; } wmp_mac_unwrap;
void wmp_mac_reset(wmp_mac_unwrap *state);
/* Input must be the MAC RX clock, not esp_timer or a TSF value. Consecutive
 * captures must be less than2^31us apart. Reset explicitly on driver restart. */
int wmp_mac_extend(wmp_mac_unwrap *state,uint32_t raw,uint64_t *extended);
/* Absolute sample math: repeated copies retain the same presentation time.
 * This neither maps source time to a receiver clock nor starts the DAC. */
int wmp_sample_time(uint64_t anchor_us,uint64_t anchor_sample,uint32_t rate,uint64_t sample,uint64_t *time_us);
/* Advance an absolute TX cadence after one accepted packet. A stalled task
 * never catches up by submitting an overdue train back-to-back. */
uint64_t wmp_tx_next(uint64_t previous_us,uint64_t accepted_us,uint32_t period_us);
/* The raw-send call can itself consume most of the pacing period. Account for
 * that time inside the cadence, rather than adding a fresh delay afterward. */
uint64_t wmp_tx_after_call(uint64_t previous_us,uint64_t start_us,uint64_t end_us,uint32_t period_us);
/* Map original local ingress to the source clock. Queue residence must not
 * become an apparent producer-clock drift and feed latency back into itself. */
uint64_t wmp_ingress_clock(uint64_t source_now,uint64_t local_now,uint64_t enqueued_local);
/* A short remaining window may still carry a useful full-packet replay. */
unsigned wmp_replay_copies(uint64_t remaining_us,uint32_t spacing_us,uint32_t guard_us);
/* Schedule up to three repairs at the requested spacing, retaining unused
 * headroom for a fresh request after the native buffers complete. A late
 * request gets one final opportunity above urgent_guard_us. */
unsigned wmp_replay_plan(uint64_t remaining_us,uint32_t minimum_spacing_us,uint32_t guard_us,uint32_t urgent_guard_us,uint32_t *spacing_us);
/* Keep a paced repair queued rather than rejecting the incoming request.
 * An urgent deadline may bypass the future pacing tail. */
uint64_t wmp_replay_start(uint64_t now_us,uint64_t next_us,uint64_t remaining_us,uint32_t urgent_guard_us);
/* Compute the next application submission time from a recent native wake
 * event. Stale/missing events cannot silently become a free-running gate. */
int wmp_wake_due(uint64_t now_us,uint64_t event_us,uint32_t interval_us,uint32_t span_us,uint64_t *due_us);
/* Generate deliberate silence only when its source-owned timestamp reaches
 * the normal publishing reserve. Return its ideal ingress time without
 * feeding timer polling jitter into the shared latency controller. */
int wmp_silence_due(uint64_t presentation,uint64_t source_now,uint64_t local_now,uint32_t reserve,uint64_t *ingress);
/* Ready packets precede future ones. Among ready packets use the common
 * presentation deadline; originals win ties with redundant repairs. */
int wmp_tx_precedes(uint64_t candidate_due,int candidate_repair,uint64_t candidate_deadline,
                    uint64_t selected_due,int selected_repair,uint64_t selected_deadline,uint64_t now);
enum { WMP_TX_EXPIRED=-1,WMP_TX_WAIT_CLOCK=0,WMP_TX_SEND=1 };
/* Revalidate repairs at dispatch after queue/credit waits. Originals remain
 * deliverable; an absent shared clock is not proof a repair has expired. */
int wmp_tx_deadline(int repair,uint64_t presentation_us,uint64_t source_now_us,uint32_t guard_us);
/* Congested air: an original copy that cannot reach a receiver before guard_us ahead of its
 * presentation time can no longer play anywhere. With a known shared clock it is retired instead of
 * spending airtime that a newer group's first copy needs. Without a clock it stays deliverable. */
int wmp_tx_original_expired(uint64_t presentation_us,uint64_t source_now_us,uint32_t guard_us);
/* wmp_tx_precedes plus breadth-first copies: among ready originals, a lower copy index goes first, so
 * under a backlog every group gets its first copy before any group gets a repeat. */
int wmp_tx_precedes_copy(uint64_t candidate_due,int candidate_repair,unsigned candidate_copy,uint64_t candidate_deadline,
                         uint64_t selected_due,int selected_repair,unsigned selected_copy,uint64_t selected_deadline,uint64_t now);
int wmp_feedback_window(uint32_t last_rx,uint32_t now,uint32_t window_us);
/* A zero gate explicitly disables the RX-age heuristic. Native TX credits,
 * request lifetime and generation fencing still apply at the caller. */
int wmp_feedback_blocked(uint32_t last_rx,uint32_t now,uint32_t gate_us);
#endif
