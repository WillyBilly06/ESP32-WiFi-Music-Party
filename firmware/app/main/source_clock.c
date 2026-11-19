#include "node.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>
/* The public TSF getter uses synchronous Wi-Fi ioctl outside its task. Only
 * this background sampler and radio-mode setup may call it. ESPtimer and the
 * free-running standalone AP TSF share the fixed oscillator (PM is disabled).
 * Keep measured sample span/deviation visible rather than claiming precision. */
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static uint64_t reference_tsf;
static int64_t reference_local;
static int64_t settled_since;
static uint32_t epoch,enabled,valid,samples,native_calls,rejected,span_us,max_deviation,stack_free;
static int32_t last_deviation;
static TaskHandle_t sampler_task;
static unsigned sampler_starting;
static uint32_t task_starts,task_stops,task_failures;
static int64_t retry_at;
void wmp_source_clock_service(void);

static void update(void) {
    uint32_t own_epoch,on;
    portENTER_CRITICAL(&lock);own_epoch=epoch;on=enabled;portEXIT_CRITICAL(&lock);
    if(!on)return;
    if(on==4u) {
        /* Pure broadcast (no SoftAP, no association): the source timeline is
         * its own esp_timer. Followers fit it from stamped ANNOUNCE frames. */
        int64_t t=esp_timer_get_time();
        portENTER_CRITICAL(&lock);
        if(enabled==4u && epoch==own_epoch) { if(!valid)settled_since=t;reference_tsf=(uint64_t)t;reference_local=t;span_us=0;valid=1;++samples; }
        portEXIT_CRITICAL(&lock);return;
    }
    uint64_t best=0;int64_t at=0;uint32_t span=UINT32_MAX;
    for(unsigned i=0;i<3;i++) {
        if(!wmp_radio_wifi_live())return;
        int64_t before=esp_timer_get_time();int64_t tsf=esp_wifi_get_tsf_time(on==2u?WIFI_IF_STA:WIFI_IF_AP);int64_t after=esp_timer_get_time();
        uint32_t width=(uint32_t)(after-before);
        if(tsf>0 && width<span) { best=(uint64_t)tsf;at=before+(after-before)/2;span=width; }
    }
    portENTER_CRITICAL(&lock);
    native_calls+=3;
    if(enabled && epoch==own_epoch) {
        if(!best || span>1000u)++rejected;
        else {
            int settled=valid && at>=reference_local && at-reference_local<=1000000;
            if(valid) {
                int64_t predicted=(int64_t)reference_tsf+at-reference_local;
                int64_t deviation=(int64_t)best-predicted;
                last_deviation=deviation>INT32_MAX?INT32_MAX:(deviation<INT32_MIN?INT32_MIN:(int32_t)deviation);
                uint64_t absolute=deviation<0?(uint64_t)(-(deviation+1))+1u:(uint64_t)deviation;
                uint32_t magnitude=absolute>UINT32_MAX?UINT32_MAX:(uint32_t)absolute;
                if(magnitude>max_deviation)max_deviation=magnitude;
                if(magnitude>2000u)settled=0;
            }
            if(!settled)settled_since=at;
            reference_tsf=best;reference_local=at;span_us=span;valid=1;++samples;
        }
    }
    portEXIT_CRITICAL(&lock);
}
uint64_t wmp_source_clock(void) {
    int64_t local;uint64_t tsf;uint32_t on,ready;
    portENTER_CRITICAL(&lock);local=reference_local;tsf=reference_tsf;on=enabled;ready=valid;portEXIT_CRITICAL(&lock);
    int64_t now=esp_timer_get_time();
    if(!on || !ready || now<local || now-local>1000000)return 0;
    return tsf+(uint64_t)(now-local);
}
int wmp_source_clock_shared_ready(uint32_t *out,uint64_t *ready_at) {
    if(!out || !ready_at)return 0;
    int64_t local,since;uint32_t on,ready,width,domain;
    portENTER_CRITICAL(&lock);local=reference_local;since=settled_since;on=enabled;ready=valid;width=span_us;domain=epoch;portEXIT_CRITICAL(&lock);
    int64_t now=esp_timer_get_time();
    if((on!=2u && on!=3u) || !ready || width>1000u || now<local || now-local>1000000)return 0;
    *out=domain;*ready_at=(uint64_t)since+2000000u;return 1;
}
int wmp_source_clock_shared_epoch(uint32_t *out) { uint64_t ready;return wmp_source_clock_shared_ready(out,&ready); }
void wmp_source_clock_invalidate(void) {
    /* Event-task context: invalidate immediately without a synchronous ioctl. */
    portENTER_CRITICAL(&lock);++epoch;valid=0;settled_since=0;portEXIT_CRITICAL(&lock);
}
void wmp_source_clock_enable(unsigned on) {
    portENTER_CRITICAL(&lock);++epoch;enabled=on;valid=0;settled_since=0;TaskHandle_t task=on?sampler_task:NULL;portEXIT_CRITICAL(&lock);
    /* Setup context only: never an HCI/media or radio callback. */
    if(on)update();
    if(task)xTaskNotifyGive(task);
    wmp_source_clock_service();
}
static void sampler(void *unused) {
    (void)unused;ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
    for(;;) {
        portENTER_CRITICAL(&lock);
        if(!enabled) {
            sampler_task=NULL;stack_free=0;++task_stops;portEXIT_CRITICAL(&lock);vTaskDelete(NULL);return;
        }
        portEXIT_CRITICAL(&lock);
        update();stack_free=(uint32_t)uxTaskGetStackHighWaterMark(0);ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(100));
    }
}
void wmp_source_clock_service(void) {
    int64_t now=esp_timer_get_time();
    portENTER_CRITICAL(&lock);
    if(!enabled || sampler_task || sampler_starting || now<retry_at) { portEXIT_CRITICAL(&lock);return; }
    sampler_starting=1;portEXIT_CRITICAL(&lock);
    TaskHandle_t created=NULL;BaseType_t result=xTaskCreatePinnedToCore(sampler,"wmp_clock",2048,0,2,&created,1);
    portENTER_CRITICAL(&lock);sampler_starting=0;
    if(result==pdPASS) { sampler_task=created;++task_starts; }
    else { ++task_failures;retry_at=now+1000000; }
    portEXIT_CRITICAL(&lock);
    if(result==pdPASS)xTaskNotifyGive(created);
}
void wmp_source_clock_init(void) { }
void wmp_source_clock_stats(unsigned long query) {
    uint32_t ready,count,calls,bad,span,worst,free,domain;int32_t delta;int64_t local,since;
    portENTER_CRITICAL(&lock);ready=valid&&enabled;count=samples;calls=native_calls;bad=rejected;span=span_us;worst=max_deviation;delta=last_deviation;local=reference_local;free=stack_free;domain=epoch;since=settled_since;portEXIT_CRITICAL(&lock);
    wmp_telemetry_printf("WMP_CLOCK_RESOURCES query=%08lx task_loaded=%u stack_bytes=%u starts=%lu stops=%lu failures=%lu\n",query,(unsigned)(sampler_task!=NULL),sampler_task?2048u:0u,(unsigned long)task_starts,(unsigned long)task_stops,(unsigned long)task_failures);
    wmp_telemetry_printf("WMP_SOURCE_CLOCK query=%08lx valid=%lu samples=%lu native_calls=%lu rejected=%lu span_us=%lu age_us=%lld delta_us=%ld max_delta_us=%lu stack_free=%lu interface_sta=%u domain_epoch=%lu settled_since_us=%lld\n",query,(unsigned long)ready,(unsigned long)count,(unsigned long)calls,(unsigned long)bad,(unsigned long)span,(long long)(esp_timer_get_time()-local),(long)delta,(unsigned long)worst,(unsigned long)free,(unsigned)(enabled==2u),(unsigned long)domain,(long long)since);
    wmp_telemetry_printf("WMP_CLOCK_DOMAIN query=%08lx shared=%u anchor_ap=%u\n",query,(unsigned)(enabled==2u || enabled==3u),(unsigned)(enabled==3u));
}
