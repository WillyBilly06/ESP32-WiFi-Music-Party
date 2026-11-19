#include "wmp_codecs.h"
#include "wmp_codec.h"
#include "aacdec.h"
#include "freeaptx.h"
#include "esp_heap_caps.h"
#include "wmp_aptx_backend.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

/* Bookkeeping only (static, a few bytes); the decoders' own state is the only heap held. */
static HAACDecoder aac;
static struct aptx_context *aptx;
/* aptX: the plain decoder runs while the stream is in sync. A parity failure (mid-stream
 * join, lost group) resets it and switches to one-codeword resynchronising decode until a
 * full decoder latency decodes cleanly, then back to the plain decoder. Output keeps all 24
 * bits. Batches of APTX_BATCH codewords yield the CPU between them so the radio receive task
 * on the same core is never held off for a whole group. */
#define APTX_BATCH 256u /* one parallel call per group */
static uint8_t aptx_quantum,aptx_recovering,aptx_run;
static uint32_t aptx_recoveries;
static uint32_t open_rate;
static uint8_t active,open_channels;
static uint32_t held_bytes,errors;
static wmp_codecs_open_stats stats;

void wmp_codecs_close(void) {
    wmp_crumb(12,active);
    if(aac) { AACFreeDecoder(aac);aac=NULL; }
    if(aptx) { wmp_aptx_parallel_stop();aptx_finish(aptx);aptx=NULL; }
    active=0;held_bytes=0;open_rate=0;open_channels=0;
}
static int aac_raw_params(void) {
    AACFrameInfo info;memset(&info,0,sizeof(info));
    info.nChans=open_channels;info.sampRateCore=(int)open_rate;info.profile=AAC_PROFILE_LC;
    return AACSetRawBlockParams(aac,0,&info)==ERR_AAC_NONE;
}
int wmp_codecs_open(uint8_t codec,uint32_t rate,uint8_t channels) {
    if(active && active==codec && open_rate==rate && open_channels==channels)return 1;
    wmp_codecs_close();++stats.opens;wmp_crumb(4,((uint32_t)codec<<20)|rate);
    stats.last_codec=codec;stats.last_rate=rate;stats.last_channels=channels;
    if(codec==WMP_CODEC_SBC || codec>=WMP_CODEC_COUNT || (channels!=1u && channels!=2u) || (rate!=44100u && rate!=48000u) ||
       ((codec==WMP_CODEC_APTX || codec==WMP_CODEC_APTX_HD) && channels!=2u)) { ++stats.bad_args;return 0; }
    size_t before=heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    open_rate=rate;open_channels=channels;
    if(codec==WMP_CODEC_AAC) {
        aac=AACInitDecoder();
        if(aac && !aac_raw_params()) { AACFreeDecoder(aac);aac=NULL;++stats.bad_args;open_rate=open_channels=0;return 0; }
    } else {
        aptx=aptx_init(codec==WMP_CODEC_APTX_HD);
        /* Both channels decode in the caller. aptX is a solo-only codec (no Wi-Fi), so the decode
         * task has core 1 to itself; the former core-0 helper task tripped the interrupt watchdog
         * (reset reason INT_WDT, last mark: helper handoff) and is no longer started. */
        aptx_quantum=codec==WMP_CODEC_APTX_HD?6u:4u;aptx_recovering=1;aptx_run=0; /* join anywhere: find sync first */
    }
    if(!aac && !aptx) {
        ++stats.no_memory;stats.largest_at_fail=(uint32_t)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
        open_rate=open_channels=0;return 0;
    }
    active=codec;wmp_crumb(5,codec);
    size_t after=heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    held_bytes=before>after?(uint32_t)(before-after):0u;
    return 1;
}
int wmp_codecs_decode(const uint8_t *unit,uint16_t bytes,void *out,uint32_t capacity,uint16_t *frames,uint8_t *channels,uint8_t *bits) {
    if(!active || !unit || !bytes || !out || !frames || !channels || !bits)return 0;
    if(active==WMP_CODEC_AAC) {
        /* Helix reads the access unit in place (it never writes input) and needs 2048 output samples. */
        if(capacity<(uint32_t)open_channels*AAC_MAX_NSAMPS*2u) { ++errors;return 0; }
        unsigned char *cursor=(unsigned char *)unit;int left=bytes;
        if(AACDecode(aac,&cursor,&left,(short *)out)!=ERR_AAC_NONE) { ++errors;AACFlushCodec(aac);aac_raw_params();return 0; }
        AACFrameInfo info;AACGetLastFrameInfo(aac,&info);
        if(info.nChans<1 || info.nChans>2 || info.bitsPerSample!=16 || info.outputSamps<=0) { ++errors;return 0; }
        *channels=(uint8_t)info.nChans;*frames=(uint16_t)(info.outputSamps/info.nChans);*bits=16;return 1;
    }
    int32_t *o=(int32_t *)out;size_t limit=capacity/4u,samples=0;int clean=1;wmp_crumb(6,((uint32_t)bytes<<16)|(capacity&0xffffu));
    for(uint16_t at=0;at+aptx_quantum<=bytes && samples+8u<=limit;) {
        size_t n=0;
        if(aptx_recovering) {
            int ready=0;
            size_t used=wmp_aptx_recover24(aptx,unit+at,aptx_quantum,o+samples,limit-samples,&n,&aptx_run,&ready);
            if(!used)break;
            at=(uint16_t)(at+used);samples+=n;clean=0;
            if(ready)aptx_recovering=0;
            continue;
        }
        uint16_t take=(uint16_t)(bytes-at);if(take>APTX_BATCH*aptx_quantum)take=(uint16_t)(APTX_BATCH*aptx_quantum);
        size_t used=wmp_aptx_decode24_parallel(aptx,unit+at,take,o+samples,limit-samples,&n);
        at=(uint16_t)(at+used);samples+=n;
        if(used<take && samples+8u<=limit) {
            /* Parity failure, not a full buffer: drop the failed codeword, resynchronise. */
            aptx_reset(aptx);aptx_recovering=1;aptx_run=0;++aptx_recoveries;clean=0;at=(uint16_t)(at+aptx_quantum);
        }
        if(at<bytes)taskYIELD();
    }
    if(!clean)++errors;
    wmp_crumb(7,(uint32_t)samples);
    samples&=~(size_t)1u;
    if(!samples)return 0;
    *channels=2;*frames=(uint16_t)(samples/2u);*bits=24;return 1;
}
uint8_t wmp_codecs_active(void) { return active; }
uint32_t wmp_codecs_bytes(void) { return held_bytes; }
uint32_t wmp_codecs_errors(void) { return errors; }
void wmp_codecs_stats(wmp_codecs_open_stats *out) { if(out)*out=stats; }
#ifdef ESP_PLATFORM
int wmp_codecs_bench(int hd,uint32_t result[4]) { return wmp_aptx_bench(hd,result); }
#endif

#ifdef ESP_PLATFORM
#include "esp_attr.h"
#include "esp_timer.h"
#define CRUMB_MAGIC 0x57d1c0deu
static RTC_NOINIT_ATTR uint32_t crumb_magic,crumb_next,crumb_ring[16][2];
static uint32_t crumb_saved[16][2],crumb_saved_count,crumb_checked;
static void crumb_capture(void) {
    if(crumb_checked)return;
    crumb_checked=1;
    if(crumb_magic==CRUMB_MAGIC && crumb_next) {
        uint32_t n=crumb_next<16u?crumb_next:16u;
        for(uint32_t i=0;i<n;i++) { uint32_t k=(crumb_next-n+i)&15u;crumb_saved[i][0]=crumb_ring[k][0];crumb_saved[i][1]=crumb_ring[k][1]; }
        crumb_saved_count=n;
    }
    crumb_magic=CRUMB_MAGIC;crumb_next=0;
}
void wmp_crumb(uint32_t code,uint32_t value) {
    if(!crumb_checked)crumb_capture();
    uint32_t k=crumb_next&15u;crumb_ring[k][0]=(code<<24)|((uint32_t)(esp_timer_get_time()/1000)&0xffffffu);crumb_ring[k][1]=value;crumb_next++;
}
unsigned wmp_crumbs_previous(uint32_t out[16][2]) {
    crumb_capture();
    for(uint32_t i=0;i<crumb_saved_count;i++) { out[i][0]=crumb_saved[i][0];out[i][1]=crumb_saved[i][1]; }
    return (unsigned)crumb_saved_count;
}
#else
void wmp_crumb(uint32_t code,uint32_t value) { (void)code;(void)value; }
unsigned wmp_crumbs_previous(uint32_t out[16][2]) { (void)out;return 0; }
#endif
