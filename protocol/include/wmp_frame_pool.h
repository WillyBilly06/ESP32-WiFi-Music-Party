#ifndef WMP_FRAME_POOL_H
#define WMP_FRAME_POOL_H
#include <stdint.h>
#define WMP_FRAME_SLOTS 40u
#define WMP_FRAME_MASK (UINT64_MAX>>(64u-WMP_FRAME_SLOTS))
/* Caller serializes operations. Payload ownership moves exactly once through
 * free -> producer -> ready -> renderer -> free. Taking ready indices never
 * depends on space in a separate reorder array. */
typedef struct {
    uint64_t free,pending,ready,held,borrowed,retired,repair_pending;
    uint8_t fifo[WMP_FRAME_SLOTS],head,count,references[WMP_FRAME_SLOTS];
} wmp_frame_pool;
void wmp_frame_pool_init(wmp_frame_pool *s);
int wmp_frame_pool_acquire(wmp_frame_pool *s);
int wmp_frame_pool_publish(wmp_frame_pool *s,unsigned index);
int wmp_frame_pool_take(wmp_frame_pool *s);
int wmp_frame_pool_release(wmp_frame_pool *s,unsigned index);
int wmp_frame_pool_cancel(wmp_frame_pool *s,unsigned index);
/* Network scheduling and repair readers share immutable ready/held/retired
 * payloads. Renderer release retires a pinned slot; the last unpin frees it. */
int wmp_frame_pool_pin(wmp_frame_pool *s,unsigned index);
int wmp_frame_pool_unpin(wmp_frame_pool *s,unsigned index);
/* A packet has at most one queued/in-flight repair train. -1 means busy. */
int wmp_frame_pool_pin_repair(wmp_frame_pool *s,unsigned index);
/* -2 means the bounded repair-loan budget is full; originals remain eligible. */
int wmp_frame_pool_pin_repair_limit(wmp_frame_pool *s,unsigned index,unsigned limit);
int wmp_frame_pool_unpin_repair(wmp_frame_pool *s,unsigned index);
/* True only when no producer, renderer or borrowed payload can still use storage. */
int wmp_frame_pool_quiescent(const wmp_frame_pool *s);
#endif
