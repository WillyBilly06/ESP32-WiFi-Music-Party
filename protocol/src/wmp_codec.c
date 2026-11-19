#include "wmp_codec.h"
#include "wmp_wire.h"
/* Group payload bound leaves room for the 16-byte clock trailer inside WMP_MAX_PAYLOAD. */
#define GROUP_BOUND (WMP_MAX_PAYLOAD-WMP_CLOCK_TRAILER)
static const wmp_codec_profile profiles[WMP_CODEC_COUNT]={
    /* SBC: 4 frames (512 samples, 11.6 ms at 44.1 kHz) per group; about 476 bytes at bitpool 53. */
    {WMP_CODEC_SBC,"SBC",4,0,512,WMP_MAX_PAYLOAD-2u,WMP_MAX_PAYLOAD,128,32,4096},
    /* AAC-LC: one 1024-sample access unit per group (23.2 ms); up to ~1.4 KB at high VBR peaks. */
    {WMP_CODEC_AAC,"AAC",1,0,1024,GROUP_BOUND-2u,GROUP_BOUND,1024,16,28672},
    /* aptX: 4 bytes per 4 stereo samples; 512-sample groups are 512 bytes (352 kbps at 44.1 kHz). */
    {WMP_CODEC_APTX,"aptX",0,4,512,512,514,512,32,4096},
    /* aptX HD: 6 bytes per 4 stereo samples; 512-sample groups are 768 bytes. */
    {WMP_CODEC_APTX_HD,"aptX HD",0,6,512,768,770,512,32,4096},
};
const wmp_codec_profile *wmp_codec_profile_get(unsigned codec) {
    return codec<WMP_CODEC_COUNT?&profiles[codec]:0;
}
uint32_t wmp_codec_payload_bytes(const wmp_codec_profile *p) {
    if(!p)return 0;
    /* Typical group size: SBC ~480, AAC ~1000 (320 kbps), aptX 514/770. Bounded by the 16-bit pool. */
    uint32_t typical=p->codec==WMP_CODEC_SBC?520u:p->codec==WMP_CODEC_AAC?1100u:p->max_group_bytes;
    uint32_t bytes=((uint32_t)p->queue_groups*typical+3u)&~3u;
    return bytes>65532u?65532u:bytes;
}
uint32_t wmp_codec_stream_samples(const wmp_codec_profile *p,uint32_t bytes) {
    if(!p || !p->stream_quantum || bytes%p->stream_quantum)return 0;
    return bytes/p->stream_quantum*4u;
}
int wmp_rtp_read(const uint8_t *p,uint32_t n,wmp_rtp_header *out,const uint8_t **payload,uint32_t *payload_bytes) {
    if(!p || !out || !payload || !payload_bytes || n<12u || (p[0]>>6)!=2u)return 0;
    uint32_t at=12u+4u*(p[0]&15u);
    if(at>n)return 0;
    if(p[0]&0x10u) { if(at+4u>n)return 0;at+=4u+4u*(((uint32_t)p[at+2u]<<8)|p[at+3u]);if(at>n)return 0; }
    uint32_t end=n;
    if(p[0]&0x20u) { uint32_t pad=p[n-1u];if(!pad || pad>end-at)return 0;end-=pad; }
    if(end<=at)return 0;
    out->marker=(uint8_t)(p[1]>>7);out->payload_type=(uint8_t)(p[1]&127u);
    out->sequence=(uint16_t)(((uint16_t)p[2]<<8)|p[3]);
    out->timestamp=((uint32_t)p[4]<<24)|((uint32_t)p[5]<<16)|((uint32_t)p[6]<<8)|p[7];
    *payload=p+at;*payload_bytes=end-at;return 1;
}
