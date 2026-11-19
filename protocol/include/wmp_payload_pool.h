#ifndef WMP_PAYLOAD_POOL_H
#define WMP_PAYLOAD_POOL_H
#include <stdint.h>
#define WMP_PAYLOAD_BYTES 24576u
#define WMP_PAYLOAD_EXTENTS 41u
/* Caller serializes. Fixed storage, contiguous byte ranges, no heap calls. */
typedef struct {
    uint16_t offset[WMP_PAYLOAD_EXTENTS],length[WMP_PAYLOAD_EXTENTS];
    uint16_t count,available;
} wmp_payload_pool;
void wmp_payload_pool_init(wmp_payload_pool *pool);
int wmp_payload_alloc(wmp_payload_pool *pool,unsigned bytes);
int wmp_payload_free(wmp_payload_pool *pool,unsigned offset,unsigned bytes);
#endif
