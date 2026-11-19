#include "wmp_frame_pool.h"
void wmp_frame_pool_init(wmp_frame_pool *s) {
    if(!s)return;
    s->free=WMP_FRAME_MASK;s->pending=s->ready=s->held=s->borrowed=s->retired=s->repair_pending=0;s->head=s->count=0;
    for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)s->references[i]=0;
}
int wmp_frame_pool_acquire(wmp_frame_pool *s) {
    if(!s || !s->free)return -1;
    for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if(s->free&(UINT64_C(1)<<i)) { s->free&=~(UINT64_C(1)<<i);s->pending|=UINT64_C(1)<<i;return (int)i; }
    return -1;
}
int wmp_frame_pool_publish(wmp_frame_pool *s,unsigned i) {
    if(!s || i>=WMP_FRAME_SLOTS || !(s->pending&(UINT64_C(1)<<i)) || s->count>=WMP_FRAME_SLOTS)return 0;
    s->fifo[(s->head+s->count)%WMP_FRAME_SLOTS]=(uint8_t)i;++s->count;
    s->pending&=~(UINT64_C(1)<<i);s->ready|=UINT64_C(1)<<i;return 1;
}
int wmp_frame_pool_take(wmp_frame_pool *s) {
    if(!s || !s->count)return -1;
    unsigned i=s->fifo[s->head];s->head=(uint8_t)((s->head+1u)%WMP_FRAME_SLOTS);--s->count;
    s->ready&=~(UINT64_C(1)<<i);s->held|=UINT64_C(1)<<i;return (int)i;
}
int wmp_frame_pool_release(wmp_frame_pool *s,unsigned i) {
    if(!s || i>=WMP_FRAME_SLOTS || !(s->held&(UINT64_C(1)<<i)))return 0;
    s->held&=~(UINT64_C(1)<<i);
    if(s->borrowed&(UINT64_C(1)<<i))s->retired|=UINT64_C(1)<<i;else s->free|=UINT64_C(1)<<i;
    return 1;
}
int wmp_frame_pool_pin(wmp_frame_pool *s,unsigned i) {
    if(!s || i>=WMP_FRAME_SLOTS || !((s->ready|s->held|s->retired)&(UINT64_C(1)<<i)) || s->references[i]==255u)return 0;
    ++s->references[i];s->borrowed|=UINT64_C(1)<<i;return 1;
}
int wmp_frame_pool_unpin(wmp_frame_pool *s,unsigned i) {
    if(!s || i>=WMP_FRAME_SLOTS || !(s->borrowed&(UINT64_C(1)<<i)))return 0;
    if(!--s->references[i])s->borrowed&=~(UINT64_C(1)<<i);else return 1;
    if(s->retired&(UINT64_C(1)<<i)) { s->retired&=~(UINT64_C(1)<<i);s->free|=UINT64_C(1)<<i; }
    return 1;
}
int wmp_frame_pool_cancel(wmp_frame_pool *s,unsigned i) {
    if(!s || i>=WMP_FRAME_SLOTS || !(s->pending&(UINT64_C(1)<<i)))return 0;
    s->pending&=~(UINT64_C(1)<<i);s->free|=UINT64_C(1)<<i;return 1;
}
int wmp_frame_pool_pin_repair(wmp_frame_pool *s,unsigned i) {
    if(!s || i>=WMP_FRAME_SLOTS)return 0;
    uint64_t bit=UINT64_C(1)<<i;
    if(s->repair_pending&bit)return -1;
    if(!wmp_frame_pool_pin(s,i))return 0;
    s->repair_pending|=bit;return 1;
}
int wmp_frame_pool_pin_repair_limit(wmp_frame_pool *s,unsigned i,unsigned limit) {
    if(!s || i>=WMP_FRAME_SLOTS || !limit || limit>WMP_FRAME_SLOTS)return 0;
    if(s->repair_pending&(UINT64_C(1)<<i))return -1;
    if((unsigned)__builtin_popcountll(s->repair_pending)>=limit)return -2;
    return wmp_frame_pool_pin_repair(s,i);
}
int wmp_frame_pool_unpin_repair(wmp_frame_pool *s,unsigned i) {
    if(!s || i>=WMP_FRAME_SLOTS || !(s->repair_pending&(UINT64_C(1)<<i)))return 0;
    if(!wmp_frame_pool_unpin(s,i))return 0;
    s->repair_pending&=~(UINT64_C(1)<<i);return 1;
}

int wmp_frame_pool_quiescent(const wmp_frame_pool *s) {
    if(!s || s->pending || s->ready || s->held || s->borrowed || s->retired || s->repair_pending || s->count)return 0;
    for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if(s->references[i])return 0;
    return s->free==WMP_FRAME_MASK;
}
