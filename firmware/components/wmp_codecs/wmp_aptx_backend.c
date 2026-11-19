#include "wmp_codecs.h"
/* libfreeaptx (LGPL-2.1-or-later, pinned in third_party/esp32-hifi-a2dp-sink) compiled
 * through this file so the recovery helper below can read the two decoder fields it needs.
 * freeaptx.c is not compiled a second time anywhere else. */
#include "freeaptx.c"
#include "wmp_aptx_backend.h"

size_t wmp_aptx_decode24(struct aptx_context *ctx,const uint8_t *in,size_t bytes,int32_t *out,size_t capacity_samples,size_t *samples) {
    /* aptx_decode32 writes each sample as (24-bit value << 8); bring it back to a 24-bit value. */
    size_t written=0,used=aptx_decode32(ctx,in,bytes,(unsigned char *)out,capacity_samples*4u,&written);
    size_t n=written/4u;
    for(size_t i=0;i<n;i++)out[i]>>=8;
    *samples=n;return used;
}
/* One codeword per call through the resynchronising decoder: aptx_decode_sync sizes its
 * internal step with an operator-precedence slip, so given more input it can mistake a full
 * output buffer for a parity failure. One codeword in and at most two codewords out (it may
 * flush one cached codeword) can never hit that. *ready once a full decoder latency of
 * consecutive codewords decoded cleanly with nothing cached, after which the plain decoder is
 * safe to use again. */
size_t wmp_aptx_recover24(struct aptx_context *ctx,const uint8_t *in,size_t bytes,int32_t *out,size_t capacity_samples,size_t *samples,uint8_t *run,int *ready) {
    const size_t quantum=ctx->hd?6u:4u;
    uint8_t packed[48];
    if(bytes>quantum)bytes=quantum;
    size_t written=0,dropped=0;int synced=0;
    size_t used=aptx_decode_sync(ctx,in,bytes,packed,sizeof(packed),&written,&synced,&dropped);
    const uint8_t target=(LATENCY_SAMPLES+3)/4;
    if(!synced || ctx->decode_dropped)*run=0;
    else if(dropped)*run=target;
    else if(*run<target)++*run;
    *ready=*run>=target && !ctx->decode_sync_buffer_len && !ctx->decode_dropped;
    size_t n=written/3u;if(n>capacity_samples)n=capacity_samples;
    for(size_t i=0;i<n;i++) {
        uint32_t v=(uint32_t)packed[3*i]|((uint32_t)packed[3*i+1]<<8)|((uint32_t)packed[3*i+2]<<16);
        out[i]=(int32_t)(v^0x800000u)-0x800000;
    }
    *samples=n;return used;
}


/* Channel-parallel plain decode. aptX channels share no decoder state: per codeword each
 * channel runs dither, unpack, inverse quantisation/prediction and QMF synthesis on its own;
 * only the parity check combines them (left parity ^ right parity, against a sync index that
 * is 1 on every eighth codeword). So each core decodes one channel for the whole group,
 * recording that channel's parity bit per codeword, and the merge below replays the parity
 * sequence and the leading-latency skip exactly as aptx_decode32 does. Output is identical. */
#define WMP_APTX_MAX_CODEWORDS 256u
static uint8_t channel_parity[NB_CHANNELS][WMP_APTX_MAX_CODEWORDS];
static void decode_one_channel(struct aptx_context *ctx,unsigned c,const uint8_t *in,size_t n,int32_t *out) {
    struct aptx_channel *ch=&ctx->channels[c];int32_t s[4];
    const size_t q=ctx->hd?6u:4u;
    for(size_t k=0;k<n;k++) {
        const uint8_t *cw=in+k*q;
        aptx_generate_dither(ch);
        if(ctx->hd)aptxhd_unpack_codeword(ch,((uint32_t)cw[3*c]<<16)|((uint32_t)cw[3*c+1]<<8)|(uint32_t)cw[3*c+2]);
        else aptx_unpack_codeword(ch,(uint16_t)(((uint16_t)cw[2*c]<<8)|(uint16_t)cw[2*c+1]));
        aptx_invert_quantize_and_prediction(ch,ctx->hd);
        channel_parity[c][k]=(uint8_t)aptx_quantized_parity(ch);
        aptx_decode_channel(ch,s);
        int32_t *o=out+k*8u+c;o[0]=s[0];o[2]=s[1];o[4]=s[2];o[6]=s[3];
    }
}
#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static TaskHandle_t helper_task,helper_waiter;static volatile uint8_t helper_quit;
static struct { struct aptx_context *ctx;const uint8_t *in;size_t n;int32_t *out; } helper_job;
/* The helper never dies mid-job: stop asks it to exit at its next wake and waits for the
 * acknowledgement, so a job can never be left half-decoded with its waiter blocked. */
static void helper_main(void *arg) {
    (void)arg;
    for(;;) {
        ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
        if(helper_quit) { TaskHandle_t w=helper_waiter;helper_quit=0;xTaskNotifyGive(w);vTaskDelete(NULL); }
        decode_one_channel(helper_job.ctx,1u,helper_job.in,helper_job.n,helper_job.out);
        xTaskNotifyGive(helper_waiter);
    }
}
int wmp_aptx_parallel_start(unsigned core,unsigned priority) {
    if(helper_task)return 1;
    helper_quit=0;wmp_crumb(11,core);
    return xTaskCreatePinnedToCore(helper_main,"wmp_aptx",2048,0,priority,&helper_task,(BaseType_t)core)==pdPASS;
}
void wmp_aptx_parallel_stop(void) {
    if(!helper_task)return;
    (void)ulTaskNotifyTake(pdTRUE,0);
    helper_waiter=xTaskGetCurrentTaskHandle();helper_quit=1;xTaskNotifyGive(helper_task);
    (void)ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(200));
    helper_task=NULL;
}
#else
int wmp_aptx_parallel_start(unsigned core,unsigned priority) { (void)core;(void)priority;return 1; }
void wmp_aptx_parallel_stop(void) {}
#endif
size_t wmp_aptx_decode24_parallel(struct aptx_context *ctx,const uint8_t *in,size_t bytes,int32_t *out,size_t capacity_samples,size_t *samples) {
    const size_t q=ctx->hd?6u:4u;
    size_t n=bytes/q;if(n>capacity_samples/8u)n=capacity_samples/8u;if(n>WMP_APTX_MAX_CODEWORDS)n=WMP_APTX_MAX_CODEWORDS;
    *samples=0;if(!n)return 0;
#ifdef ESP_PLATFORM
    if(helper_task) {
        helper_job.ctx=ctx;helper_job.in=in;helper_job.n=n;helper_job.out=out;helper_waiter=xTaskGetCurrentTaskHandle();
        wmp_crumb(9,(uint32_t)n);
        xTaskNotifyGive(helper_task);
        decode_one_channel(ctx,0u,in,n,out);
        ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
        wmp_crumb(10,(uint32_t)n);
    } else
#endif
    { decode_one_channel(ctx,0u,in,n,out);decode_one_channel(ctx,1u,in,n,out); }
    size_t valid=n;
    for(size_t k=0;k<n;k++) {
        const int32_t eighth=ctx->sync_idx==7;ctx->sync_idx=(uint8_t)((ctx->sync_idx+1u)&7u);
        if((channel_parity[0][k]^channel_parity[1][k])^eighth) { valid=k;break; }
    }
    /* Leading decoder latency, as aptx_decode32: the first skip-1 codewords give nothing,
     * the skip-th gives its last 4-LATENCY_SAMPLES%4... samples from index LATENCY_SAMPLES%4. */
    size_t start=0;
    if(ctx->decode_skip_leading) {
        size_t skip=ctx->decode_skip_leading;
        if(valid<skip) { start=valid*4u;ctx->decode_skip_leading=(uint8_t)(skip-valid); }
        else { start=(skip-1u)*4u+LATENCY_SAMPLES%4;ctx->decode_skip_leading=0; }
    }
    size_t frames=valid*4u-start;
    if(start && frames)memmove(out,out+start*2u,frames*2u*sizeof(int32_t));
    *samples=frames*2u;return valid*q;
}

#ifdef ESP_PLATFORM
#include <stdlib.h>
#include "esp_cpu.h"
#include "freertos/FreeRTOS.h"
/* Diagnostic only (CODECBENCH): cycles for one 512-frame group decoded with interrupts off,
 * plus the QMF synthesis and the invert-quantise/prediction halves timed on their own. */
int wmp_aptx_bench(int hd,uint32_t result[4]) {
    const size_t q=hd?6u:4u,codewords=128u;
    struct aptx_context *enc=aptx_init(hd),*dec=aptx_init(hd);
    uint8_t *pcm=malloc(codewords*4u*6u),*coded=malloc(codewords*q+q);int32_t *out=malloc(codewords*4u*2u*4u);
    int ok=enc && dec && pcm && coded && out;
    if(ok) {
        for(size_t i=0;i<codewords*4u;i++) {             /* 1 kHz-ish tone, 24-bit, both channels */
            int32_t v=(int32_t)((i*37u%96u)*120000u)-5760000;
            for(int c=0;c<2;c++) { uint8_t *p=pcm+(i*2u+(unsigned)c)*3u;p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);p[2]=(uint8_t)(v>>16); }
        }
        size_t written=0;aptx_encode(enc,pcm,codewords*4u*6u,coded,codewords*q+q,&written);
        static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;size_t n=0;
        aptx_decode32(dec,coded,written,(unsigned char *)out,codewords*4u*2u*4u,&n);   /* warm caches and decoder */
        portENTER_CRITICAL(&mux);
        uint32_t c0=esp_cpu_get_cycle_count();
        aptx_decode32(dec,coded,written,(unsigned char *)out,codewords*4u*2u*4u,&n);
        uint32_t c1=esp_cpu_get_cycle_count();
        int32_t s[4];for(size_t i=0;i<codewords*2u;i++)aptx_decode_channel(&dec->channels[i&1u],s);
        uint32_t c2=esp_cpu_get_cycle_count();
        for(size_t i=0;i<codewords*2u;i++)aptx_invert_quantize_and_prediction(&dec->channels[i&1u],hd);
        uint32_t c3=esp_cpu_get_cycle_count();
        portEXIT_CRITICAL(&mux);
        result[0]=c1-c0;result[1]=c2-c1;result[2]=c3-c2;result[3]=(uint32_t)n;
    }
    if(enc)aptx_finish(enc);
    if(dec)aptx_finish(dec);
    free(pcm);free(coded);free(out);return ok;
}
#endif
