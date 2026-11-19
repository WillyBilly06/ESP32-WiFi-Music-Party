#ifndef WMP_SBC_QUEUE_H
#define WMP_SBC_QUEUE_H
#include "wmp_audio_group.h"
#define WMP_SBC_QUEUE_BYTES 6144u
#define WMP_SBC_QUEUE_GROUPS 24u
/* One serialized BTstack publisher owns this queue. Variable encoded lengths
 * share a preallocated byte ring; no allocation, wait or payload overwrite. */
typedef struct {
    uint64_t first_sample,enqueued_us;
    uint32_t samples,rate;
    uint16_t bytes,frames;
    uint8_t codec,channels; /* the group's codec travels with its bytes */
} wmp_sbc_entry;
typedef struct {
    uint8_t data[WMP_SBC_QUEUE_BYTES];
    wmp_sbc_entry entries[WMP_SBC_QUEUE_GROUPS];
    uint32_t read,write,used,head,count;
} wmp_sbc_queue;
void wmp_sbc_queue_init(wmp_sbc_queue *queue);
int wmp_sbc_queue_push(wmp_sbc_queue *queue,const wmp_audio_group *group,uint32_t rate,uint64_t now_us);
int wmp_sbc_queue_peek(const wmp_sbc_queue *queue,wmp_audio_group *group,uint32_t *rate,uint64_t *enqueued_us);
int wmp_sbc_queue_pop(wmp_sbc_queue *queue);
#endif
