#include "wmp_sbc.h"
int wmp_sbc_info_read(const uint8_t *p,size_t n,wmp_sbc_info *out) {
    if(!p || !out || n<4u || p[0]!=0x9cu)return 0;
    static const uint32_t rates[4]={16000u,32000u,44100u,48000u};
    unsigned subbands=(p[1]&1u)?8u:4u,blocks=4u*(1u+((p[1]>>4)&3u));
    unsigned mode=(p[1]>>2)&3u,channels=mode?2u:1u,pool=p[2];
    if(pool<2u || pool>(mode<2u?16u:32u)*subbands)return 0;
    unsigned bits=blocks*pool*(mode<2u?channels:1u)+(mode==3u?subbands:0u);
    unsigned bytes=4u+subbands*channels/2u+(bits+7u)/8u;
    if(bytes>n)return 0;
    *out=(wmp_sbc_info){.rate=rates[p[1]>>6],.bytes=(uint16_t)bytes,.samples=(uint16_t)(blocks*subbands),.channels=(uint8_t)channels};return 1;
}
int wmp_rtp_sbc_read(const uint8_t *p,size_t n,wmp_rtp_sbc *out,const uint8_t **frames,size_t *bytes) {
    if(!p || !out || !frames || !bytes || n<13u || (p[0]>>6)!=2u)return 0;
    size_t at=12u+4u*(p[0]&15u);if(at>=n)return 0;
    if(p[0]&16u) {
        if(n-at<4u)return 0;
        size_t extension=4u+4u*(((size_t)p[at+2u]<<8)|p[at+3u]);
        if(extension>n-at)return 0;
        at+=extension;if(at>=n)return 0;
    }
    if(p[0]&32u) { unsigned padding=p[n-1u];if(!padding || padding>=n-at)return 0;n-=padding; }
    if(at>=n)return 0;
    uint8_t media=p[at++];if(media&0x10u)return 0;
    if(media&0x80u)return -1;
    if(media&0x60u || !(media&15u) || at>=n)return 0;
    wmp_rtp_sbc h={.timestamp=((uint32_t)p[4]<<24)|((uint32_t)p[5]<<16)|((uint32_t)p[6]<<8)|p[7],.sequence=(uint16_t)(((uint16_t)p[2]<<8)|p[3]),.frames=media&15u};
    size_t cursor=at;
    for(unsigned i=0;i<h.frames;i++) { wmp_sbc_info frame;if(!wmp_sbc_info_read(p+cursor,n-cursor,&frame))return 0;cursor+=frame.bytes;h.samples+=frame.samples; }
    if(cursor!=n)return 0;
    *out=h;*frames=p+at;*bytes=n-at;return 1;
}
void wmp_sbc_stream_reset(wmp_sbc_stream *s) {
    if(s) { uint32_t mismatches=s->timestamp_mismatches;*s=(wmp_sbc_stream){.timestamp_mismatches=mismatches}; }
}
int wmp_sbc_stream_accept(wmp_sbc_stream *s,const wmp_rtp_sbc *p,uint64_t *first,uint16_t *distance) {
    if(!s || !p || !first || !distance || !p->samples || !p->frames || s->next_sample>UINT64_MAX-p->samples)return -1;
    uint16_t step=s->valid?(uint16_t)(p->sequence-s->sequence):0u;
    if(s->valid && !step)return 0;
    if(s->valid) {
        s->last_delta=p->timestamp-s->timestamp;s->last_expected=s->packet_samples;
        if(step==1u && s->last_delta!=s->last_expected)++s->timestamp_mismatches;
    }
    *first=s->next_sample;*distance=step;s->next_sample+=p->samples;
    s->timestamp=p->timestamp;s->packet_samples=p->samples;s->sequence=p->sequence;s->valid=1;return 1;
}
