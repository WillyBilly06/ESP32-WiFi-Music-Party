#ifndef WMP_COPY_TRACE_H
#define WMP_COPY_TRACE_H
#include <stdint.h>
enum { WMP_COPY_SUBMIT,WMP_COPY_ACCEPT,WMP_COPY_COMPLETE,WMP_COPY_RX,WMP_COPY_VALID,WMP_COPY_REPAIR,WMP_COPY_TX_FAIL,WMP_COPY_INJECT };
enum { WMP_ROUTE_QUEUED=1,WMP_ROUTE_DEFERRED=2,WMP_ROUTE_DUPLICATE=4,WMP_ROUTE_DECODED=8,WMP_ROUTE_LATE=16,WMP_ROUTE_CODEC_BAD=32,WMP_ROUTE_QUEUE_FAILED=64,WMP_ROUTE_PURGED=128 };
typedef struct {
    uint64_t sequence,presentation_us;
    uint32_t a[4],b[4];
    uint8_t seen,accepted,completed,failed,repaired,valid,injected,route;
} wmp_copy_record;
typedef struct {
    wmp_copy_record records[64];
    uint64_t generation,first,highest;
    uint32_t counts[3][4],retired[16],repeated;
    uint32_t gap_first[4],gap_last[4],gap_max[4],gap_events[4];
    uint8_t gap_valid;
    uint8_t kind,valid;
} wmp_copy_trace;
/* Caller serializes writers/snapshots. No allocation, UART or clock reads. */
void wmp_copy_trace_event(wmp_copy_trace *s,uint64_t generation,uint64_t sequence,uint64_t pts,unsigned stage,unsigned copy,uint32_t a,uint32_t b);
void wmp_copy_trace_reset_gaps(wmp_copy_trace *s);
/* Routing may update only an existing receiver row, never advance/seed one. */
int wmp_copy_trace_route(wmp_copy_trace *s,uint64_t generation,uint64_t sequence,unsigned bits);
#endif
