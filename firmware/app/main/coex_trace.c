#include "coex_trace.h"
#include "telemetry.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include <stdint.h>
#include <string.h>
/* One-shot merged radio timeline: coexistence scheduler callbacks, Wi-Fi
 * submit/completion with the coexistence phase index, phone A2DP packets and
 * BLE writes. Diagnostic only: allocated on demand, fixed records, no logging,
 * allocation or blocking inside callbacks. Recording stops when full. */
typedef struct { uint32_t at;uint32_t c;uint16_t b;uint8_t kind,a; } record;
_Static_assert(sizeof(record)==12u,"Compact trace record");
#define RECORDS 2048u
static record *volatile ring;
static volatile uint32_t used,armed,coex_calls,coex_args,kind_mask=0xffffffffu;
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;

/* Pinned v6.1 libcoexist exports (private header esp_coexist_internal.h).
 * Read-only queries from task context; never called inside callbacks. */
extern uint32_t coex_schm_interval_get(void);
extern uint8_t coex_schm_curr_period_get(void);
extern int coex_schm_curr_phase_idx_get(void);
extern int __real_coex_schm_register_callback(int type,void *callback);
typedef uint32_t (*schm_callback)(uint32_t,uint32_t,uint32_t,uint32_t);
static schm_callback bt_callback;

void IRAM_ATTR wmp_coex_trace_record(unsigned kind,unsigned a,unsigned b,uint32_t c) {
    if(!armed || !(kind_mask&(1u<<(kind&31u))))return;
    uint32_t at=(uint32_t)esp_timer_get_time();
    portENTER_CRITICAL_SAFE(&lock);
    record *r=ring;
    if(r && armed && used<RECORDS) { r[used]=(record){.at=at,.c=c,.b=(uint16_t)b,.kind=(uint8_t)kind,.a=(uint8_t)a};if(++used==RECORDS)armed=0; }
    portEXIT_CRITICAL_SAFE(&lock);
}
/* The BT controller's scheme callback signature is not public. Forward up to
 * four register arguments unchanged (Xtensa windowed ABI), recording the first
 * two. No coexistence API is called here: the scheme may hold its own lock. */
static uint32_t IRAM_ATTR bt_trampoline(uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    ++coex_calls;coex_args=a;
    wmp_coex_trace_record(WMP_RT_COEX_BT,a&255u,b&65535u,a);
    return bt_callback?bt_callback(a,b,c,d):0;
}
int __wrap_coex_schm_register_callback(int type,void *callback) {
    if(type==1 && callback) { bt_callback=(schm_callback)callback;return __real_coex_schm_register_callback(type,(void *)bt_trampoline); }
    return __real_coex_schm_register_callback(type,callback);
}
int wmp_coex_trace_active(void) { return armed!=0; }
int wmp_coex_phase(void) { return coex_schm_curr_phase_idx_get(); }
int wmp_coex_trace_arm(unsigned enabled) {
    /* 0 off, 1 all kinds, otherwise a bitmask of kinds (bit k = kind k). */
    kind_mask=enabled>1u?enabled:0xffffffffu;if(enabled>1u)enabled=1u;
    if(!enabled) {
        portENTER_CRITICAL(&lock);record *old=ring;ring=NULL;armed=0;used=0;portEXIT_CRITICAL(&lock);heap_caps_free(old);return 1;
    }
    record *created=ring?NULL:heap_caps_malloc(RECORDS*sizeof(record),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!ring && !created)return 0;
    portENTER_CRITICAL(&lock);if(!ring) { ring=created;created=NULL; }used=0;armed=1;portEXIT_CRITICAL(&lock);
    heap_caps_free(created);return 1;
}
void wmp_coex_trace_dump(unsigned long query) {
    uint32_t count;portENTER_CRITICAL(&lock);count=used;portEXIT_CRITICAL(&lock);
    wmp_telemetry_printf("WMP_RADIOTRACE_META query=%08lx records=%lu armed=%lu interval=%lu period=%u phase=%d bt_callbacks=%lu last_arg=%lu units=us32 kinds=1coex,2submit,3done,4rtp,5ble,6rx,7hci_in,8phase,9hci_out,10rx_bad\n",query,(unsigned long)count,(unsigned long)armed,(unsigned long)coex_schm_interval_get(),(unsigned)coex_schm_curr_period_get(),coex_schm_curr_phase_idx_get(),(unsigned long)coex_calls,(unsigned long)coex_args);
    for(uint32_t i=0;i<count;i++) {
        record r={0};portENTER_CRITICAL(&lock);if(ring)r=ring[i];portEXIT_CRITICAL(&lock);
        wmp_telemetry_printf("WMP_RT query=%08lx i=%lu at=%lu k=%u a=%u b=%u c=%lu\n",query,(unsigned long)i,(unsigned long)r.at,(unsigned)r.kind,(unsigned)r.a,(unsigned)r.b,(unsigned long)r.c);
    }
    wmp_telemetry_printf("WMP_RADIOTRACE_END query=%08lx records=%lu\n",query,(unsigned long)count);
}

/* Coexistence table override (diagnostic and follower tuning).
 * Pinned v6.1 libcoexist keeps the active scheme in coex_schm_env: +0 points
 * at a const table {phase_count,1,{percent,type,0,flags}...}, +4 is the
 * current phase index, +6/+8/+10 are the Wi-Fi/BLE/BT status bitmaps, +12 is
 * the interval. The library re-selects a flash table only on status changes;
 * the main loop re-applies the override from task context after that. Phase
 * 0 is the Wi-Fi-side phase and phase 1 the BT phase in every 2-phase table
 * that this firmware selects, so the override keeps exactly two phases and
 * the current index always stays in range. esp_coex_preference_set() is a
 * stub (returns 0) in this library, so it cannot do this. */
extern uint8_t coex_schm_env[64];
extern int coex_schm_process_restart(void);
static uint8_t DRAM_ATTR override_table[12] __attribute__((aligned(4)));
static volatile uint32_t override_percent,override_applied;
static const uint8_t *volatile native_table;
static const uint8_t *const volatile *env_table(void) { return (const uint8_t *const volatile *)(void *)coex_schm_env; }
int wmp_coex_override(unsigned wifi_percent,unsigned wifi_type) {
    if(wifi_percent && (wifi_percent<20u || wifi_percent>95u || wifi_type>3u))return 0;
    if(!wifi_percent) {
        override_percent=0;
        const uint8_t *native=native_table;
        if(native && *env_table()==override_table) { *(const uint8_t *volatile *)(void *)coex_schm_env=native;coex_schm_process_restart(); }
        return 1;
    }
    uint8_t t[10]={2,1,(uint8_t)wifi_percent,(uint8_t)wifi_type,0,0,(uint8_t)(100u-wifi_percent),0,0,0};
    memcpy(override_table,t,sizeof(t));override_percent=wifi_percent;wmp_coex_override_poll();return 1;
}
void wmp_coex_override_poll(void) {
    if(!override_percent)return;
    const uint8_t *current=*env_table();
    if(current==override_table || !current)return;
    native_table=current;
    if(coex_schm_env[4]>1u)coex_schm_env[4]=0;
    *(const uint8_t *volatile *)(void *)coex_schm_env=override_table;
    ++override_applied;coex_schm_process_restart();
}
void wmp_coex_state(void) {
    const uint8_t *t=*env_table(),*n=native_table;
    uint16_t wifi,ble,bt;memcpy(&wifi,coex_schm_env+6,2);memcpy(&ble,coex_schm_env+8,2);memcpy(&bt,coex_schm_env+10,2);
    unsigned count=t?t[0]:0;
    wmp_telemetry_printf("WMP_COEXSTATE table=%08lx native=%08lx override_pct=%lu applied=%lu phase=%u interval=%lu wifi_st=%04x ble_st=%04x bt_st=%04x phases=",(unsigned long)(uintptr_t)t,(unsigned long)(uintptr_t)n,(unsigned long)override_percent,(unsigned long)override_applied,(unsigned)coex_schm_env[4],(unsigned long)coex_schm_interval_get(),(unsigned)wifi,(unsigned)ble,(unsigned)bt);
    for(unsigned i=0;i<count && i<4u;i++)wmp_telemetry_printf("%s%u/%u/%u",i?",":"",(unsigned)t[2+i*4],(unsigned)t[3+i*4],(unsigned)t[5+i*4]);
    wmp_telemetry_printf("\n");
}
/* Diagnostic: set/clear libcoexist status bits (type 0 Wi-Fi, 1 BLE, 2 BT).
 * The scheduler re-selects its table and arbitration inputs from these. */
extern void coex_schm_status_bit_set(uint32_t type,uint32_t status);
extern void coex_schm_status_bit_clear(uint32_t type,uint32_t status);
int wmp_coex_status_bit(unsigned type,unsigned bits,unsigned set) {
    if(type>2u || !bits || bits>0xffffu || set>1u)return 0;
    if(set)coex_schm_status_bit_set(type,bits);else coex_schm_status_bit_clear(type,bits);
    return 1;
}

/* Coexistence request observation (reverse engineering, pinned v6.1).
 * libcoexist coex_core_request(type,event,latency,duration) builds an
 * arbitration entry with priority coex_prio_tab[event] (42 bytes in DRAM)
 * and a [now+latency, now+latency+duration] window. The open adapters in
 * esp_wifi/esp32/esp_adapter.c and bt/controller/esp32/bt.c call these entry
 * points across objects, so --wrap observes every Wi-Fi and BT request. */
#define COEX_EVENTS 42u
extern uint8_t coex_prio_tab[COEX_EVENTS];
extern int __real_coex_wifi_request(uint32_t event,uint32_t latency,uint32_t duration);
extern int __real_coex_wifi_release(uint32_t event);
extern int __real_coex_bt_request(uint32_t event,uint32_t latency,uint32_t duration);
extern int __real_coex_bt_release(uint32_t event);
static volatile uint32_t wifi_requests[COEX_EVENTS+1],bt_requests[COEX_EVENTS+1],wifi_denied[COEX_EVENTS+1],bt_denied[COEX_EVENTS+1];
static volatile uint32_t wifi_duration_max[COEX_EVENTS+1],bt_duration_max[COEX_EVENTS+1];
static inline unsigned event_slot(uint32_t event) { return event<COEX_EVENTS?event:COEX_EVENTS; }
int IRAM_ATTR __wrap_coex_wifi_request(uint32_t event,uint32_t latency,uint32_t duration) {
    unsigned i=event_slot(event);++wifi_requests[i];if(duration>wifi_duration_max[i])wifi_duration_max[i]=duration;
    wmp_coex_trace_record(WMP_RT_COEX_WIFI_REQ,event&255u,duration>65535u?65535u:duration,latency);
    int r=__real_coex_wifi_request(event,latency,duration);if(r)++wifi_denied[i];return r;
}
int IRAM_ATTR __wrap_coex_wifi_release(uint32_t event) { wmp_coex_trace_record(WMP_RT_COEX_WIFI_REL,event&255u,0,0);return __real_coex_wifi_release(event); }
int IRAM_ATTR __wrap_coex_bt_request(uint32_t event,uint32_t latency,uint32_t duration) {
    unsigned i=event_slot(event);++bt_requests[i];if(duration>bt_duration_max[i])bt_duration_max[i]=duration;
    wmp_coex_trace_record(WMP_RT_COEX_BT_REQ,event&255u,duration>65535u?65535u:duration,latency);
    int r=__real_coex_bt_request(event,latency,duration);if(r)++bt_denied[i];return r;
}
int IRAM_ATTR __wrap_coex_bt_release(uint32_t event) { wmp_coex_trace_record(WMP_RT_COEX_BT_REL,event&255u,0,0);return __real_coex_bt_release(event); }
void wmp_coex_requests(unsigned long query) {
    for(unsigned i=0;i<=COEX_EVENTS;i++) {
        if(!wifi_requests[i] && !bt_requests[i] && i<COEX_EVENTS)continue;
        wmp_telemetry_printf("WMP_COEXREQ query=%08lx event=%u prio=%u wifi=%lu wifi_denied=%lu wifi_dur_max=%lu bt=%lu bt_denied=%lu bt_dur_max=%lu%c",query,i,i<COEX_EVENTS?(unsigned)coex_prio_tab[i]:0u,
            (unsigned long)wifi_requests[i],(unsigned long)wifi_denied[i],(unsigned long)wifi_duration_max[i],(unsigned long)bt_requests[i],(unsigned long)bt_denied[i],(unsigned long)bt_duration_max[i],10);
    }
    wmp_telemetry_printf("WMP_COEXREQ_END query=%08lx%c",query,10);
}
int wmp_coex_priority(unsigned event,unsigned priority) {
    if(event>=COEX_EVENTS || priority>15u)return 0;
    coex_prio_tab[event]=(uint8_t)priority;return 1;
}
