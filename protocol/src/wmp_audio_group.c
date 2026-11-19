#include "wmp_audio_group.h"
void wmp_group_init(wmp_audio_group *g,uint8_t target) {
    if(!g)return;
    g->bytes=g->frames=0;g->sample_count=0;g->first_sample=g->next_sample=0;
    g->target=(target>=3u && target<=WMP_MAX_GROUP_FRAMES)?target:0;g->codec=WMP_CODEC_SBC;g->channels=0;
}
void wmp_group_init_codec(wmp_audio_group *g,uint8_t codec,uint8_t target) {
    const wmp_codec_profile *p=wmp_codec_profile_get(codec);
    if(!g)return;
    g->bytes=g->frames=0;g->sample_count=0;g->first_sample=g->next_sample=0;g->codec=codec;g->channels=0;g->target=0;
    if(!p)return;
    if(p->stream_quantum)g->target=1;
    else if(codec==WMP_CODEC_SBC)g->target=(target>=3u && target<=WMP_MAX_GROUP_FRAMES)?target:0;
    else g->target=(target>=1u && target<=WMP_MAX_GROUP_FRAMES)?target:0;
}
int wmp_group_push(wmp_audio_group *g,const uint8_t *sbc,uint16_t bytes,uint64_t first,uint16_t samples) {
    if(!g || g->codec!=WMP_CODEC_SBC)return -1;
    return wmp_group_push_unit(g,sbc,bytes,first,samples);
}
int wmp_group_push_unit(wmp_audio_group *g,const uint8_t *sbc,uint16_t bytes,uint64_t first,uint16_t samples) {
    const wmp_codec_profile *profile=g?wmp_codec_profile_get(g->codec):0;
    if(!g || !profile || profile->stream_quantum || !g->target || !sbc || !samples ||
       (g->codec==WMP_CODEC_SBC && (bytes<4u || sbc[0]!=0x9cu)) || (g->codec!=WMP_CODEC_SBC && !bytes) ||
       first>UINT64_MAX-samples)return -1;
    if(bytes>profile->max_unit_bytes || g->sample_count+samples>WMP_MAX_GROUP_SAMPLES)return -2;
    if(g->frames>=g->target || bytes>profile->max_group_bytes-2u || g->bytes>profile->max_group_bytes-2u-bytes)return -2;
    if(g->frames && first!=g->next_sample)return -3;
    if(!g->frames)g->first_sample=first;
    unsigned at=g->bytes;g->payload[at]=(uint8_t)bytes;g->payload[at+1u]=(uint8_t)(bytes>>8);
    for(unsigned i=0;i<bytes;i++)g->payload[at+2u+i]=sbc[i];
    g->bytes=(uint16_t)(g->bytes+2u+bytes);++g->frames;g->sample_count+=samples;g->next_sample=first+samples;
    return g->frames==g->target?1:0;
}
int wmp_group_push_stream(wmp_audio_group *g,const uint8_t *data,uint16_t bytes,uint64_t first,uint16_t *consumed) {
    const wmp_codec_profile *p=g?wmp_codec_profile_get(g->codec):0;
    if(consumed)*consumed=0;
    if(!g || !p || !p->stream_quantum || !g->target || !data || !consumed || bytes%p->stream_quantum)return -1;
    if(g->frames && first!=g->next_sample)return -3;
    if(!g->frames) { g->first_sample=first;g->next_sample=first;g->bytes=2u;g->frames=1;g->payload[0]=g->payload[1]=0; }
    uint32_t room=wmp_codec_stream_samples(p,(uint32_t)(p->group_samples-g->sample_count)/4u*p->stream_quantum);
    uint32_t take_bytes=(uint32_t)room/4u*p->stream_quantum;if(take_bytes>bytes)take_bytes=bytes;
    for(uint32_t i=0;i<take_bytes;i++)g->payload[g->bytes+i]=data[i];
    g->bytes=(uint16_t)(g->bytes+take_bytes);
    uint16_t unit=(uint16_t)(g->bytes-2u);g->payload[0]=(uint8_t)unit;g->payload[1]=(uint8_t)(unit>>8);
    uint32_t samples=wmp_codec_stream_samples(p,take_bytes);
    g->sample_count+=samples;g->next_sample+=samples;*consumed=(uint16_t)take_bytes;
    return g->sample_count>=p->group_samples?1:0;
}
int wmp_group_frame(const uint8_t *p,size_t n,uint16_t count,uint16_t index,const uint8_t **frame,uint16_t *frame_bytes) {
    return wmp_group_frame_codec(WMP_CODEC_SBC,p,n,count,index,frame,frame_bytes);
}
int wmp_group_frame_codec(uint8_t codec,const uint8_t *p,size_t n,uint16_t count,uint16_t index,const uint8_t **frame,uint16_t *frame_bytes) {
    if(!p || !frame || !frame_bytes || (!count || count>WMP_MAX_GROUP_FRAMES) || index>=count || n>WMP_MAX_PAYLOAD)return 0;
    size_t at=0;const uint8_t *selected=0;uint16_t selected_bytes=0;
    for(unsigned i=0;i<count;i++) {
        if(at+2u>n)return 0;
        uint16_t bytes=(uint16_t)(p[at]|((uint16_t)p[at+1u]<<8));at+=2u;
        if((codec==WMP_CODEC_SBC && (bytes<4u || bytes>n-at || p[at]!=0x9cu)) || (codec!=WMP_CODEC_SBC && (!bytes || bytes>n-at)))return 0;
        if(i==index) { selected=p+at;selected_bytes=bytes; }
        at+=bytes;
    }
    if(at!=n)return 0;
    *frame=selected;*frame_bytes=selected_bytes;return 1;
}
int wmp_copy_plan(uint64_t ready,uint64_t presentation,uint32_t spacing,uint8_t copies,uint64_t due[4]) {
    if(!due || (copies!=3u && copies!=4u) || !spacing || ready>=presentation)return 0;
    uint64_t horizon=(uint64_t)(copies-1u)*spacing;
    if(horizon>=presentation-ready)return 0;
    for(unsigned i=0;i<4u;i++)due[i]=i<copies?ready+(uint64_t)i*spacing:0;
    return 1;
}
