#include "wmp_sbc_queue.h"
void wmp_sbc_queue_init(wmp_sbc_queue *s) {
    if(s)s->read=s->write=s->used=s->head=s->count=0;
}
int wmp_sbc_queue_push(wmp_sbc_queue *s,const wmp_audio_group *g,uint32_t rate,uint64_t now) {
    if(!s || !g || !rate || !g->frames || g->frames>WMP_MAX_GROUP_FRAMES || !g->bytes || g->bytes>WMP_MAX_PAYLOAD ||
       !g->sample_count || g->sample_count>WMP_MAX_GROUP_SAMPLES || g->first_sample>UINT64_MAX-g->sample_count ||
       s->count>=WMP_SBC_QUEUE_GROUPS || g->bytes>WMP_SBC_QUEUE_BYTES-s->used)return 0;
    wmp_sbc_entry *e=&s->entries[(s->head+s->count)%WMP_SBC_QUEUE_GROUPS];
    *e=(wmp_sbc_entry){.first_sample=g->first_sample,.enqueued_us=now,.samples=g->sample_count,
        .rate=rate,.bytes=g->bytes,.frames=g->frames,.codec=g->codec,.channels=g->channels};
    for(unsigned i=0;i<g->bytes;i++)s->data[(s->write+i)%WMP_SBC_QUEUE_BYTES]=g->payload[i];
    s->write=(s->write+g->bytes)%WMP_SBC_QUEUE_BYTES;s->used+=g->bytes;++s->count;return 1;
}
int wmp_sbc_queue_peek(const wmp_sbc_queue *s,wmp_audio_group *g,uint32_t *rate,uint64_t *enqueued) {
    if(!s || !g || !rate || !enqueued || !s->count)return 0;
    const wmp_sbc_entry *e=&s->entries[s->head];
    for(unsigned i=0;i<e->bytes;i++)g->payload[i]=s->data[(s->read+i)%WMP_SBC_QUEUE_BYTES];
    g->first_sample=e->first_sample;g->next_sample=e->first_sample+e->samples;
    g->sample_count=e->samples;g->bytes=e->bytes;g->frames=e->frames;g->target=WMP_MAX_GROUP_FRAMES;g->codec=e->codec;g->channels=e->channels;
    *rate=e->rate;*enqueued=e->enqueued_us;return 1;
}
int wmp_sbc_queue_pop(wmp_sbc_queue *s) {
    if(!s || !s->count)return 0;
    unsigned bytes=s->entries[s->head].bytes;s->read=(s->read+bytes)%WMP_SBC_QUEUE_BYTES;
    s->used-=bytes;s->head=(s->head+1u)%WMP_SBC_QUEUE_GROUPS;--s->count;return 1;
}
