#include "wmp_pcm_blocks.h"
uint32_t wmp_pcm_blocks_guard_us(const wmp_pcm_blocks *s,uint32_t rate,uint32_t guard) {
    if(!s || !rate)return guard;
    return guard+(uint32_t)(((uint64_t)s->count*1000000u+rate-1u)/rate);
}
int wmp_pcm_blocks_init(wmp_pcm_blocks *s,unsigned frames) {
    if(!s)return 0;
    s->count=0;s->frames=frames && frames<=WMP_PCM_DMA_MAX_FRAMES?frames:0;return s->frames!=0;
}
int wmp_pcm_blocks_push(wmp_pcm_blocks *s,const int16_t *data,unsigned frames,wmp_pcm_writer writer,void *context) {
    if(!s || !data || !writer || !s->frames || s->frames>WMP_PCM_DMA_MAX_FRAMES || s->count>s->frames)return 0;
    unsigned offset=0;
    if(s->count) {
        unsigned n=s->frames-s->count;if(n>frames)n=frames;
        for(unsigned i=0;i<n*2u;i++)s->tail[s->count*2u+i]=data[i];
        s->count+=n;offset=n;
        if(s->count==s->frames) { if(!writer(context,s->tail,s->frames))return 0;s->count=0; }
        else return 1;
    }
    while(frames-offset>=s->frames) {
        if(!writer(context,data+offset*2u,s->frames))return 0;
        offset+=s->frames;
    }
    s->count=frames-offset;
    for(unsigned i=0;i<s->count*2u;i++)s->tail[i]=data[offset*2u+i];
    return 1;
}
