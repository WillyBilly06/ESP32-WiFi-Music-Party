#include "esp_mac.h"
#include "node.h"
#include "wmp_wire.h"
#include "wmp_time.h"
#include "wmp_copy_trace.h"
#include "wmp_playout.h"
#include "wmp_nack.h"
#include "wmp_peer_lease.h"
#include "coex_trace.h"
#include "esp_wifi.h"
#include "esp_private/wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_coexist.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <string.h>
#include <stddef.h>
_Static_assert(offsetof(wmp_copy_record,injected)==54u,"Copy trace CRC layout changed");
#include <stdio.h>
#define GROUP_ID UINT64_C(0x574d502d41554431)
#define SLOTS 20
#define TX_BUFFERS WMP_RADIO_TX_CAPACITY
#define TX_FRAME_BYTES (32u+WMP_HEADER_BYTES+WMP_LIVE_PAYLOAD)
uint8_t wmp_mac[6];
volatile wmp_radio_stats radio_stats;
typedef struct { wmp_header header;uint64_t ready_us;uint32_t copy_spacing;const uint8_t *payload;unsigned reference;uint8_t total_copies,repair_paced;uint32_t owner;uint8_t repair_to[6]; } message;
typedef struct { uint64_t generation,sequence;uint32_t rate,samples; } nack_request;
typedef struct { uint8_t mode,copies,keep_schedule;uint16_t payload,pps;uint32_t spacing; } command;
typedef struct { uint16_t bytes;uint32_t clock,callback_us,physical_tag;uint8_t sender[6],wire[WMP_HEADER_BYTES+WMP_LIVE_PAYLOAD]; } received;
typedef struct { message message;uint8_t active,next,waiting,paced,total;uint32_t spacing,dither;uint64_t due; } slot;
static QueueHandle_t incoming,source,commands,completed,nacks;
/* Solo / broadcast (user, 2026-11-18): Wi-Fi exists only while broadcast is on, so a solo
 * speaker keeps that heap (driver buffers and this 8-frame receive queue) for Bluetooth
 * audio. The receive task owns the queue's lifetime: wifi_down() asks it to park, then
 * deletes the queue; wifi_up() recreates it before promiscuous RX starts. */
static volatile uint8_t wifi_wanted,wifi_ready,incoming_retire,incoming_parked;
/* wifi_live: the driver is initialised (esp_wifi_init done, deinit not yet). Some SDK getters,
 * e.g. esp_wifi_sta_get_rssi, dereference driver state without an init check, so every API
 * call that can run outside wifi_up, the Wi-Fi callbacks and the parked radio tasks checks it. */
static volatile uint8_t wifi_live;
unsigned wmp_radio_wifi_live(void) { return wifi_live; }
static volatile int32_t wifi_status;static volatile uint32_t wifi_ups,wifi_downs;
static TaskHandle_t receiver_task,transmitter_task;
static volatile uint32_t feedback_rx_at;
static esp_timer_handle_t transmit_wakeup;
static portMUX_TYPE duration_lock=portMUX_INITIALIZER_UNLOCKED;
static uint64_t original_dwell_us,wake_late_us;
static uint32_t original_completions,original_dwell_max,original_over20ms,original_over100ms;
static uint32_t wake_calls,wake_late_max,wake_over1ms,wake_over5ms,credit_waits;
static portMUX_TYPE wake_window_lock=portMUX_INITIALIZER_UNLOCKED;
static uint64_t native_wake_local,native_wake_source;
static uint32_t native_wake_events,native_wake_gap_max,tx_wake_span,tx_wake_waits,tx_wake_missing;
typedef struct { uint64_t generation,sequence,presentation_us;uint32_t submitted,finished,tag,owner;uint8_t copy,type,flags,acked;volatile uint8_t state; } tx_buffer;
static tx_buffer tx_buffers[TX_BUFFERS];
/* Both pinned SDK paths copy input before returning. Completion metadata
 * stays owned; serialized submissions can reuse these bytes immediately. */
static uint8_t tx_scratch[TX_FRAME_BYTES];
static uint8_t native_completion_sample[80];
static unsigned native_completion_bytes;
/* Keep driver-owned copies bounded; excess native queue depth consumed heap
 * and delayed recovery while the application already has a64-slot pool. */
#define DEFAULT_TX_WINDOW 6u
#define SOURCE_QUEUE_DEPTH 16u
static volatile unsigned tx_window=DEFAULT_TX_WINDOW;
static volatile unsigned original_copy_override=4u;
static uint32_t next_native_tag,next_slot_owner;
static uint8_t tx_mac[6];
static wifi_interface_t tx_interface=WIFI_IF_STA;
static wifi_config_t infrastructure_config;
/* Setup-only configuration stays off the small transmitter task stack. */
static wifi_config_t anchor_config;
static unsigned infrastructure_pending,infrastructure_link_up,infrastructure_clock;
static volatile unsigned infrastructure_admitted,reconnect_pending;
static volatile uint32_t reconnect_due;
static uint32_t reconnect_delay=1000000u,reconnect_attempts,reconnect_successes;
static int reconnect_status;
static uint32_t keepalive_sequence,keepalive_sent,keepalive_done,keepalive_failed;
static volatile uint32_t keepalive_next;
#define KEEPALIVE_TYPE 0xfeu
static const uint8_t keepalive_magic[5]={'W','M','P','K','1'};
static volatile unsigned selected_rate=26; /* MCS3 HT20 long GI: user-selected default 2026-11-18 */
static unsigned wifi_started,rate_configured[2],rate_prestart_calls,rate_starts,rate_stops,rate_active_rejections,rate_precondition_rejections;
static int rate_config_status[2]={ESP_ERR_INVALID_STATE,ESP_ERR_INVALID_STATE};
static unsigned selected_channel=6;
static portMUX_TYPE publisher_lock=portMUX_INITIALIZER_UNLOCKED;
static uint64_t publisher_generation,source_ready_at;
static volatile uint32_t schedule_config=WMP_DEFAULT_COPY_SPACING_US,transmit_pace_us=0u;
static volatile uint32_t copy_dither_us=6000u;
int wmp_radio_copy_dither(unsigned us) {
    if(us>16000u || us>(schedule_config&UINT32_C(0x7fffffff))/4u || (us && (schedule_config>>31)))return 0;
    copy_dither_us=us;return 1;
}
void wmp_radio_dither_stats(unsigned long query) { wmp_telemetry_printf("WMP_TX_DITHER query=%08lx us=%lu first_copy_unchanged=1 absolute=1\n",query,(unsigned long)copy_dither_us); }
static volatile uint32_t audio_action_frames=1;
/* Coexistence-phase gating (0=off, the default). Bit i allows sending in scheme
 * phase i. Measured 2026-11-17 (evidence/txphase-ab-*): gating to phase 0 made
 * follower concealment ~30-200x worse, because held copies burst into the
 * native window together and lose their time diversity. Diagnostic only. */
static volatile uint32_t tx_phase_mask,tx_phase_urgent_us=80000u,tx_phase_waits,tx_phase_overrides;
int wmp_radio_tx_phase(unsigned mask,unsigned urgent_ms) {
    if(mask>0xffffu || urgent_ms<10u || urgent_ms>250u)return 0;
    tx_phase_urgent_us=urgent_ms*1000u;tx_phase_mask=mask;if(transmitter_task)xTaskNotifyGive(transmitter_task);return 1;
}
static volatile uint32_t receiver_beacon_tu=10000u;
static volatile unsigned replay_enabled=1;
static volatile uint32_t nack_delay_us=40000u;
/* Resend requests are single frames over the same lossy air as audio. Send
 * each as a short train; the source merges duplicates within 20 ms into one
 * repair. Only the last copy counts as the submitted attempt, so queued
 * copies are not retired as stale; a repair arriving mid-train cancels it. */
static volatile uint32_t nack_copies=2,nack_copy_spacing_us=6000;
int wmp_radio_nack_copies(unsigned copies,unsigned spacing_us) {
    if(copies<1u || copies>3u || spacing_us<1000u || spacing_us>20000u)return 0;
    nack_copies=copies;nack_copy_spacing_us=spacing_us;return 1;
}
int wmp_radio_nack_delay(unsigned us) { if(us<20000u || us>300000u)return 0;nack_delay_us=us;radio_stats.nack_delay_us=us;return 1; }
static volatile uint32_t nack_gate_us=0u,nack_gate_wait_max;
int wmp_radio_nack_gate(unsigned us) { if(us>1000000u)return 0;nack_gate_us=us;if(transmitter_task)xTaskNotifyGive(transmitter_task);return 1; }
static volatile unsigned native_roc_running;
static uint64_t native_roc_retry_at;
static volatile unsigned listen_running,listen_stopping;
static volatile uint32_t listen_next_at;
static uint8_t listen_bssid[6];
static wmp_nack *missing_audio;
/* Receive queue between the Wi-Fi callback and the receive task. Measured peak
 * depth in a 3-speaker party: 1 with effects off, 4 with every effect at max while
 * the effects ran above the Wi-Fi task (qfull=0 throughout). 6 keeps margin for a
 * settings save or flash stall without holding 8 x 1.5 KB. */
#define RX_QUEUE_DEPTH 6u
static volatile uint32_t rx_queue_peak;
static portMUX_TYPE nack_lock=portMUX_INITIALIZER_UNLOCKED;
static uint32_t nack_reservations,nack_submissions,nack_cancellations,nack_stale_requests,nack_extended_submissions,nack_peak_attempts;
static uint32_t nack_deadline_handoffs,nack_legacy_handoffs;
static int pending_nack(uint64_t generation,uint64_t sequence,uint64_t now) {
    portENTER_CRITICAL(&nack_lock);int pending=wmp_nack_pending(missing_audio,generation,sequence,now);portEXIT_CRITICAL(&nack_lock);return pending;
}
static void finish_nack(uint64_t generation,uint64_t sequence,uint64_t now,int submitted,uint32_t delay) {
    portENTER_CRITICAL(&nack_lock);
    if(submitted) { if(wmp_nack_submit(missing_audio,generation,sequence,now)) {
        ++nack_submissions;unsigned attempts=wmp_nack_attempts(missing_audio,generation,sequence);
        if(attempts>8u)++nack_extended_submissions;
        if(attempts>nack_peak_attempts)nack_peak_attempts=attempts;
    } }
    else if(wmp_nack_cancel(missing_audio,generation,sequence,now,delay))++nack_cancellations;
    portEXIT_CRITICAL(&nack_lock);
}
static message *replay_buffer;
/* Eight bounded records; never print, allocate or copy audio in callbacks. */
typedef struct { uint64_t generation,sequence,pts,source_clock;uint32_t at_us;uint8_t outcome,copies,attempted,accepted,completed; } repair_event;
static repair_event *repair_events;
static unsigned repair_next,repair_count;

/* One explicitly selected identity survives the rolling history window.
 * Disabled by default; no allocation, printing or payload copy in callbacks. */
typedef struct { uint32_t at,tag;int32_t status;uint8_t stage,copy,flags,pad; } delivery_event;
_Static_assert(offsetof(delivery_event,flags)==14u,"Delivery event CRC layout");
#define DELIVERY_EVENTS 128u
typedef struct { uint64_t generation,sequence;wmp_header header;delivery_event events[DELIVERY_EVENTS];uint32_t count,overflow,known; } delivery_capture;
static delivery_capture *volatile delivery_focus;
static void route_note(const wmp_header *header,unsigned bits);
static portMUX_TYPE delivery_lock=portMUX_INITIALIZER_UNLOCKED;
int wmp_radio_delivery_arm(uint64_t generation,uint64_t sequence) {
    if(!generation)return 0;
    portENTER_CRITICAL(&delivery_lock);delivery_capture *p=delivery_focus;
    int existing=p && p->generation==generation && p->sequence==sequence;
    portEXIT_CRITICAL(&delivery_lock);if(p)return existing;
    delivery_capture *created=heap_caps_calloc(1,sizeof(*created),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);if(!created)return 0;
    created->generation=generation;created->sequence=sequence;
    portENTER_CRITICAL(&delivery_lock);int ok=!delivery_focus;if(ok) { delivery_focus=created;created=NULL; }portEXIT_CRITICAL(&delivery_lock);
    heap_caps_free(created);return ok;
}
int wmp_radio_delivery_clear(void) {
    portENTER_CRITICAL(&delivery_lock);delivery_capture *p=delivery_focus;delivery_focus=NULL;portEXIT_CRITICAL(&delivery_lock);heap_caps_free(p);return 1;
}
static void delivery_note(uint64_t generation,uint64_t sequence,unsigned stage,unsigned copy,unsigned flags,uint32_t tag,uint32_t at,int status,const wmp_header *header) {
    if(!delivery_focus)return;
    portENTER_CRITICAL(&delivery_lock);delivery_capture *p=delivery_focus;
    if(p && p->generation==generation && p->sequence==sequence) {
        if(header && !p->known && (header->type==WMP_AUDIO || header->type==WMP_SILENCE)) { p->header=*header;p->known=1; }
        if(p->count<DELIVERY_EVENTS)p->events[p->count++]=(delivery_event){.at=at,.tag=tag,.status=status,.stage=(uint8_t)stage,.copy=(uint8_t)copy,.flags=(uint8_t)flags};else ++p->overflow;
    }
    portEXIT_CRITICAL(&delivery_lock);
}
void wmp_radio_delivery(const wmp_header *h,unsigned stage,int status) {
    if(h && stage==WMP_D_DECODE)route_note(h,status?WMP_ROUTE_DECODED:WMP_ROUTE_CODEC_BAD);
    else if(h && stage==WMP_D_LATE)route_note(h,WMP_ROUTE_LATE);
    else if(h && stage==WMP_D_DISCARD)route_note(h,WMP_ROUTE_PURGED);
    if(delivery_focus && h)delivery_note(h->generation,h->sequence,stage,h->copy_index,h->flags,0,(uint32_t)esp_timer_get_time(),status,h);
}
void wmp_radio_delivery_trace(unsigned long query) {
    delivery_capture *snapshot=heap_caps_malloc(sizeof(*snapshot),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!snapshot) { wmp_telemetry_printf("WMP_DELIVERY_END query=%08lx loaded=0 allocation_failed=1 count=0\n",query);return; }
    portENTER_CRITICAL(&delivery_lock);int loaded=delivery_focus!=NULL;if(loaded)*snapshot=*delivery_focus;portEXIT_CRITICAL(&delivery_lock);
    if(loaded) {
        wmp_telemetry_printf("WMP_DELIVERY_META query=%08lx generation=%llu seq=%llu known=%lu sample=%llu pts=%llu rate=%lu samples=%lu overflow=%lu bytes=%u\n",query,(unsigned long long)snapshot->generation,(unsigned long long)snapshot->sequence,(unsigned long)snapshot->known,(unsigned long long)snapshot->header.first_sample,(unsigned long long)snapshot->header.presentation_us,(unsigned long)snapshot->header.sample_rate,(unsigned long)snapshot->header.sample_count,(unsigned long)snapshot->overflow,(unsigned)sizeof(*snapshot));
        for(unsigned i=0;i<snapshot->count;i++) {
            const delivery_event *e=&snapshot->events[i];wmp_telemetry_printf("WMP_DELIVERY query=%08lx index=%u stage=%u copy=%u flags=%u tag=%lu at_us32=%lu status=%ld crc=%lu\n",query,i,e->stage,e->copy,e->flags,(unsigned long)e->tag,(unsigned long)e->at,(long)e->status,(unsigned long)wmp_crc32((const uint8_t *)e,15u));
        }
    }
    wmp_telemetry_printf("WMP_DELIVERY_END query=%08lx loaded=%u count=%lu allocation_failed=0\n",query,(unsigned)loaded,loaded?(unsigned long)snapshot->count:0ul);heap_caps_free(snapshot);
}
typedef struct { uint64_t generation,sequence,until; } suppression;
static suppression heard_nacks[8];
static portMUX_TYPE suppression_lock=portMUX_INITIALIZER_UNLOCKED;
int wmp_radio_source_ready(void) {
    uint64_t now=(uint64_t)esp_timer_get_time();
    /* Readiness and its published flag belong to the same role transition.
     * A host preemption between a source-mode read and the flag store must
     * not restore preparing=1 after the transmitter has become a follower. */
    portENTER_CRITICAL(&publisher_lock);
    uint64_t ready=source_ready_at;
    int source=radio_stats.mode==2u;
    int ok=source && !radio_stats.transition_pending && ready && now>=ready;
    radio_stats.preparing=source && !ok;
    portEXIT_CRITICAL(&publisher_lock);return ok;
}
int wmp_radio_replay(unsigned enabled) { if(enabled>1u)return 0;replay_enabled=enabled;radio_stats.replay_enabled=enabled;return 1; }
int wmp_radio_repair_unicast(unsigned enabled) { if(enabled>1u)return 0;radio_stats.repair_unicast=enabled;return 1; }
/* Sniffer frame classes: 1 management (beacons, WMP action frames), 2 data
 * (needed only by the data-frame carrier and Ethernet diagnostics). A
 * passive listener on a busy channel otherwise receives every neighbour
 * network's data frames through the driver's shared RX buffers. */
static volatile uint32_t rx_filter_classes=3,rx_fcs_any,rx_fcs_leader;
/* Class 4 also delivers FCS-failed frames, counted (never decoded) to tell
 * damaged-on-air copies (collision/interference) from never-heard copies. */
int wmp_radio_rx_filter(unsigned classes) {
    if(!(classes&3u) || classes>7u || !wifi_live)return 0;
    wifi_promiscuous_filter_t filter={.filter_mask=((classes&1u)?WIFI_PROMIS_FILTER_MASK_MGMT:0u)|((classes&2u)?WIFI_PROMIS_FILTER_MASK_DATA:0u)|((classes&4u)?WIFI_PROMIS_FILTER_MASK_FCSFAIL:0u)};
    int status=esp_wifi_set_promiscuous_filter(&filter);if(!status)rx_filter_classes=classes;return status==ESP_OK;
}
unsigned wmp_radio_rx_filter_classes(void) { return rx_filter_classes; }
/* A zero-join listener is a disconnected station: with
 * CONFIG_ESP_WIFI_STA_DISCONNECTED_PM_ENABLE the driver may close RF between
 * connectionless wake windows. Hold an explicit wake reference so the
 * receiver stays open. Reference-counted in the driver; we hold at most one. */
static volatile uint32_t rf_awake_held;static volatile int32_t rf_awake_status;
int wmp_radio_rf_awake(unsigned hold) {
    if(hold>1u || !wifi_live)return 0;
    if(hold==rf_awake_held)return 1;
    esp_err_t status=hold?esp_wifi_force_wakeup_acquire():esp_wifi_force_wakeup_release();
    rf_awake_status=status;if(status==ESP_OK)rf_awake_held=hold;
    return status==ESP_OK;
}
unsigned wmp_radio_rf_awake_held(void) { return rf_awake_held; }

void wmp_radio_rx_fcs(unsigned long *any,unsigned long *leader) { *any=rx_fcs_any;*leader=rx_fcs_leader; }
int wmp_radio_native_rx(unsigned enabled) { if(enabled>1u || (enabled && (radio_stats.mode==2u || radio_stats.direct_rx_enabled)))return 0;radio_stats.native_rx_requested=enabled;if(transmitter_task)xTaskNotifyGive(transmitter_task);return 1; }
static volatile uint32_t requested_latency_us=300000; /* phone input can run ~180 ms behind */
static uint32_t latency_version,applied_latency_version,pending_late_frames;
static wmp_playout playout;
static portMUX_TYPE feedback_lock=portMUX_INITIALIZER_UNLOCKED;
static uint64_t feedback_generation;static uint32_t feedback_frames,feedback_rate,feedback_samples;
static uint64_t leader_generation;static uint32_t leader_rate,leader_samples;
/* Party size for the stereo pair (JBL-style: Left/Right only while exactly two speakers play).
 * Followers in a party send a one-copy presence frame (SYNC_REPLY, payload[0]=3) about once a
 * second. The master keeps three slots of followers heard within PRESENCE_FRESH_US (it only
 * needs to tell 1, 2 and 3+ apart) and writes the size into every copy's clock trailer. */
#define PRESENCE_PERIOD_US 1000000u
#define PRESENCE_FRESH_US 6000000u /* six periods: a busy master hears ~40% of single frames */
static struct { uint8_t mac[6];uint64_t at; } presence_seen[3];
static portMUX_TYPE presence_lock=portMUX_INITIALIZER_UNLOCKED;
static uint64_t presence_next_us;
static volatile uint32_t presence_sent,presence_heard;
static void presence_note(const uint8_t mac[6],uint64_t now) {
    portENTER_CRITICAL(&presence_lock);
    int slot=-1;
    for(int i=0;i<3;i++)if(presence_seen[i].at && !memcmp(presence_seen[i].mac,mac,6)) { slot=i;break; }
    if(slot<0)for(int i=0;i<3;i++)if(!presence_seen[i].at || now-presence_seen[i].at>PRESENCE_FRESH_US) { slot=i;break; }
    if(slot<0) { slot=0;for(int i=1;i<3;i++)if(presence_seen[i].at<presence_seen[slot].at)slot=i; }
    memcpy(presence_seen[slot].mac,mac,6);presence_seen[slot].at=now;++presence_heard;
    portEXIT_CRITICAL(&presence_lock);
}
static unsigned master_party_size(uint64_t now) {
    unsigned followers=0;
    portENTER_CRITICAL(&presence_lock);
    for(int i=0;i<3;i++)if(presence_seen[i].at && now-presence_seen[i].at<=PRESENCE_FRESH_US)++followers;
    portEXIT_CRITICAL(&presence_lock);
    return followers>=2u?3u:followers+1u;
}
static uint8_t leader_mac[6],leader_valid;
/* Leadership start on the shared clock: the first broadcaster keeps the party.
 * Own value is set when this board becomes source; peer value from ANNOUNCE. */
static uint64_t own_leader_start,peer_leader_start;static uint8_t own_leader_shared,peer_leader_shared;
#define ANNOUNCE_BYTES 44u /* 32 + version byte + the karaoke effect settings (WMP_FX_BYTES) */
#define ANNOUNCE_CLOCK_BYTES 32u
/* payload[24..31] (pure broadcast source): its coexistence Wi-Fi window.
 * u32 age of the latest Wi-Fi phase start relative to the stamp (us),
 * u16 period and u16 Wi-Fi length in 10 us units; zero when unknown.
 * Measured: a source receiving phone A2DP hears 83% of frames that arrive in
 * its Wi-Fi phase but only 32% in its Bluetooth phase (broadcast frames get
 * no MAC ACK/retry), so followers time their requests into that window. */
/* ANNOUNCE flags (payload[7]): bit0 start is on a shared network clock,
 * bit1 the sender's timeline is its own local clock (pure broadcast).
 * payload[16..23] carries the sender's source clock stamped immediately
 * before encode/TX: followers fit their clock to it (no SoftAP beacons). */
#define ANNOUNCE_FLAG_SHARED 1u
#define ANNOUNCE_FLAG_LOCAL 2u
/* Stamped while no other frame was in flight: stamp-to-air delay is constant, so only these feed clock fits. */
#define ANNOUNCE_FLAG_QUIET 4u
/* Pure broadcast: no board ever runs a SoftAP or associates. Every board
 * stays an unconnected station on the fixed channel and exchanges only raw
 * broadcast action frames (ESP-NOW style, without ESP-NOW). */
static volatile uint32_t pure_broadcast=1;
static volatile uint32_t late_growth=0,late_reports_ignored;
int wmp_radio_late_growth(unsigned enabled) { if(enabled>1u)return 0;late_growth=enabled;return 1; }
static volatile uint32_t own_clock_local;
static volatile uint32_t announce_stamps,announce_clock_fed,announce_quiet_sent,announce_busy_sent,announce_busy_skipped;
/* Per-copy clock trailer: 0 off, 1 send only, 2 send and use received audio stamps for the clock fit. */
static volatile uint32_t clock_copy_mode=2,tx_serial_counter,copy_stamps_sent,copy_stamps_quiet,audio_clock_seen,audio_clock_pushed,announce_superseded;
static volatile uint32_t last_audio_clock_us;
/* Auto channel (pure broadcast): idle boards cycle 1/6/11, measuring other Wi-Fi airtime and
 * listening for a broadcasting party; receivers lock while they hear one; a board that becomes
 * the source moves to the least-used channel from a fresh survey. */
#define SCAN_DWELL_US 120000u /* a source sends ~90 frames/s: 120 ms per channel still hears it many times */
#define SCAN_LOST_US 2500000u /* stay locked through short gaps; rescan only if the master is really gone */
#define SURVEY_FRESH_US 30000000u
static const uint8_t scan_channels[3]={1,6,11};
static volatile uint32_t auto_channel=1,dwell_airtime_us,dwell_frames,last_party_rx_us;
static volatile uint32_t survey_airtime_us[3],survey_window_us[3],survey_frames[3],survey_at_us[3];
static volatile uint32_t scan_state,scan_hops,scan_locks,scan_lock_ms_last,scan_lock_ms_max,scan_started_us,source_picks,source_pick_channel;
static uint32_t dwell_started_us,scan_hops_at_start;static unsigned dwell_index;
int wmp_radio_clock_copy(unsigned mode) { if(mode>2u)return 0;clock_copy_mode=mode;return 1; }
static volatile uint64_t window_start;static volatile uint32_t window_period,window_len;static int window_phase=-1;
static volatile uint64_t peer_window_start,peer_window_at;static volatile uint32_t peer_window_period,peer_window_len,window_deferrals,window_deferred_us_max;
static void track_window(uint64_t t) {
    int ph=wmp_coex_phase();if(ph==window_phase)return;
    if(ph==0 && window_phase==1) {
        if(window_start) { uint64_t per=t-window_start;if(per>50000u && per<200000u)window_period=window_period?(uint32_t)((window_period*7u+per)/8u):(uint32_t)per; }
        window_start=t;
    } else if(ph==1 && window_phase==0 && window_start) {
        uint64_t len=t-window_start;if(len>5000u && len<200000u)window_len=window_len?(uint32_t)((window_len*7u+len)/8u):(uint32_t)len;
    }
    window_phase=ph;
}
/* Follower coexistence alignment. Source and follower run the same 100 ms
 * scheme period with unrelated phase. A source transmits only inside its
 * Wi-Fi window; a follower is deaf in its own Bluetooth phase. Restart the
 * follower's scheme so its Bluetooth phase sits in the middle of the
 * source's Bluetooth phase, where the source cannot transmit anyway. */
extern int coex_schm_process_restart(void);
extern int coex_schm_interval_set(uint32_t interval);
extern uint32_t coex_schm_interval_get(void);
static volatile uint32_t align_enabled=1,align_restarts,align_error_us,align_interval_sets;static volatile int32_t align_last_error_us;static uint64_t align_last_at;
int wmp_radio_coex_align(unsigned enabled) { if(enabled>1u)return 0;align_enabled=enabled;return 1; }
static int align_tick(uint64_t now) {
    uint32_t period=peer_window_period,len=peer_window_len;uint64_t pstart=peer_window_start,at=peer_window_at,ostart=window_start;uint32_t olen=window_len;
    if(!align_enabled || !pure_broadcast || !period || !len || !pstart || !ostart || !olen || olen>=period || now-at>2000000u || now<pstart)return 0;
    uint32_t fb=period-olen;if(fb+len>=period)return 0;
    uint32_t target=(len+(period-len-fb)/2u+fb)%period;   /* source position where our phase 0 should start */
    int64_t diff=(int64_t)ostart-(int64_t)pstart;int64_t m=((diff%(int64_t)period)+(int64_t)period)%(int64_t)period;
    int32_t err=(int32_t)(m-(int64_t)target);if(err>(int32_t)period/2)err-=(int32_t)period;if(err<-(int32_t)period/2)err+=(int32_t)period;
    align_last_error_us=err;align_error_us=(uint32_t)(err<0?-err:err);
    /* Match the source's measured period first (interval unit 0.1 ms):
     * equal periods turn a one-off restart into a lasting alignment. */
    static uint64_t period_avg;period_avg=period_avg?(period_avg*31u+period)/32u:period;
    uint32_t want=(uint32_t)((period_avg+50u)/100u),current=coex_schm_interval_get();
    if(want>=800u && want<=1200u && (want>current+1u || current>want+1u) && (!align_last_at || now-align_last_at>1000000u)) { coex_schm_interval_set(want);++align_interval_sets;align_last_at=now;return 1; }
    if(align_error_us<=3000u || (align_last_at && now-align_last_at<1000000u))return 1;
    uint32_t pos=(uint32_t)((now-pstart)%period);
    if(pos>=target && pos<target+1500u) { coex_schm_process_restart();align_last_at=now;++align_restarts; }
    return 2;   /* waiting for the restart point: poll at 1 ms */
}
/* Follower: delay (us) until the source's next usable Wi-Fi window, 0 when
 * inside it or when the schedule is unknown/stale. */
static uint32_t window_delay(uint64_t now) {
    uint32_t period=peer_window_period,len=peer_window_len;uint64_t start=peer_window_start,at=peer_window_at;
    if(!pure_broadcast || !period || !len || !start || now<start || now-at>2000000u)return 0;
    const uint32_t head=2000u,tail=4000u;if(len<=head+tail+2000u)return 0;
    uint32_t pos=(uint32_t)((now-start)%period),usable=len-tail;
    if(pos>=head && pos<usable)return 0;
    return pos<head?head-pos:period-pos+head;
}
int wmp_radio_peer_precedes(void) {
    uint64_t own=((uint64_t)wmp_base_mac[0]<<40)|((uint64_t)wmp_base_mac[1]<<32)|((uint64_t)wmp_base_mac[2]<<24)|((uint64_t)wmp_base_mac[3]<<16)|((uint64_t)wmp_base_mac[4]<<8)|wmp_base_mac[5];
    uint64_t peer=((uint64_t)radio_stats.peer_id_hi<<16)|radio_stats.peer_id_lo,own_start,peer_start;int comparable;
    portENTER_CRITICAL(&feedback_lock);own_start=own_leader_start;peer_start=peer_leader_start;comparable=own_leader_shared && peer_leader_shared;portEXIT_CRITICAL(&feedback_lock);
    return wmp_leader_precedes(peer,peer_start,own,own_start,comparable);
}
static uint8_t associated_bssid[6];
/* Public vendor action: category4,action9,our own local OUI. Final3 carrier
 * bytes remain the physical TX tag; logical WMP/SBC framing is unchanged. */
static const uint8_t control_prefix[8]={4,9,2,0x57,0x4d,0,0,0};
static wmp_copy_trace *copy_history;
#define copy_trace (*copy_history)
static portMUX_TYPE copy_lock=portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t gap_reset_serial;
int wmp_radio_trace_resources(unsigned enabled) {
    if(enabled>1u)return 0;
    if(enabled) {
        if(copy_history)return 1;
        wmp_copy_trace *created=heap_caps_calloc(1,sizeof(*created),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
        repair_event *events=heap_caps_calloc(8,sizeof(*events),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
        if(!created || !events) { heap_caps_free(created);heap_caps_free(events);return 0; }
        portENTER_CRITICAL(&copy_lock);
        if(!copy_history) { copy_history=created;repair_events=events;created=NULL;events=NULL;repair_next=repair_count=0;radio_stats.trace_resource_bytes=sizeof(*copy_history)+8u*sizeof(*repair_events); }
        portEXIT_CRITICAL(&copy_lock);heap_caps_free(created);heap_caps_free(events);return 1;
    }
    portENTER_CRITICAL(&copy_lock);wmp_copy_trace *retired=copy_history;repair_event *old_events=repair_events;copy_history=NULL;repair_events=NULL;repair_next=repair_count=0;radio_stats.trace_resource_bytes=0;portEXIT_CRITICAL(&copy_lock);heap_caps_free(retired);heap_caps_free(old_events);return 1;
}

void wmp_radio_gap_reset(void) {
    portENTER_CRITICAL(&copy_lock);if(copy_history)wmp_copy_trace_reset_gaps(&copy_trace);++gap_reset_serial;portEXIT_CRITICAL(&copy_lock);
}
void wmp_radio_gap_stats(unsigned long query) {
    uint32_t maximum[4]={0},first[4]={0},last[4]={0},events[4]={0};unsigned kind=0;
    portENTER_CRITICAL(&copy_lock);if(copy_history) { memcpy(maximum,copy_trace.gap_max,sizeof(maximum));memcpy(first,copy_trace.gap_first,sizeof(first));memcpy(last,copy_trace.gap_last,sizeof(last));memcpy(events,copy_trace.gap_events,sizeof(events));kind=copy_trace.kind; }portEXIT_CRITICAL(&copy_lock);
    uint32_t now=(uint32_t)esp_timer_get_time();
    wmp_telemetry_printf("WMP_GAPS query=%08lx kind=%u submit_max_us=%lu accepted_max_us=%lu completion_max_us=%lu firstcopy_max_us=%lu submit_events=%lu completion_events=%lu span_us=%lu open_submit_us=%lu useful_rx_max_us=%lu\n",query,kind,(unsigned long)maximum[0],(unsigned long)maximum[1],(unsigned long)maximum[2],(unsigned long)maximum[3],(unsigned long)events[0],(unsigned long)events[2],(unsigned long)(events[0]?last[0]-first[0]:0),(unsigned long)(kind==1u && events[0] && bt_stats.streaming?now-last[0]:0),(unsigned long)radio_stats.rx_max_gap_us);
}
void wmp_radio_presence_stats(unsigned long query) {
    unsigned party=radio_stats.mode==2u?master_party_size((uint64_t)esp_timer_get_time()):0u;
    wmp_telemetry_printf("WMP_PRESENCE query=%08lx sent=%lu heard=%lu master_party=%u\n",query,(unsigned long)presence_sent,(unsigned long)presence_heard,party);
}
void wmp_radio_beacon_stats(unsigned long query) {
    uint8_t leader[6],valid;portENTER_CRITICAL(&feedback_lock);memcpy(leader,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
    uint32_t hi=((uint32_t)leader[0]<<24)|((uint32_t)leader[1]<<16)|((uint32_t)leader[2]<<8)|leader[3],lo=((uint32_t)leader[4]<<8)|leader[5];
    uint32_t now=(uint32_t)esp_timer_get_time();
    wmp_telemetry_printf("WMP_BEACONS query=%08lx seen=%lu bad=%lu matched=%lu matched_bad=%lu queued=%lu last_rate=%lu matched_rate=%lu leader_valid=%u leader_hi=%lu leader_lo=%lu last_hi=%lu last_lo=%lu any_age_us=%lu match_age_us=%lu\n",query,
        (unsigned long)radio_stats.beacon_seen,(unsigned long)radio_stats.beacon_bad,(unsigned long)radio_stats.beacon_matched,(unsigned long)radio_stats.beacon_matched_bad,(unsigned long)radio_stats.beacon_queued,(unsigned long)radio_stats.beacon_rate,(unsigned long)radio_stats.beacon_match_rate,(unsigned)valid,
        (unsigned long)hi,(unsigned long)lo,(unsigned long)radio_stats.beacon_last_hi,(unsigned long)radio_stats.beacon_last_lo,
        (unsigned long)(radio_stats.beacon_last_at?now-radio_stats.beacon_last_at:UINT32_MAX),(unsigned long)(radio_stats.beacon_match_at?now-radio_stats.beacon_match_at:UINT32_MAX));
    wmp_telemetry_printf("WMP_PURE_BROADCAST query=%08lx enabled=%lu own_clock_local=%lu wifi_ap=%lu announce_stamps=%lu clock_fed=%lu quiet_sent=%lu busy_sent=%lu busy_skipped=%lu clock_copy=%lu copy_stamps=%lu copy_quiet=%lu audio_clock_seen=%lu audio_clock_pushed=%lu announce_superseded=%lu\n",query,(unsigned long)pure_broadcast,(unsigned long)own_clock_local,(unsigned long)radio_stats.wifi_ap,(unsigned long)announce_stamps,(unsigned long)announce_clock_fed,(unsigned long)announce_quiet_sent,(unsigned long)announce_busy_sent,(unsigned long)announce_busy_skipped,(unsigned long)clock_copy_mode,(unsigned long)copy_stamps_sent,(unsigned long)copy_stamps_quiet,(unsigned long)audio_clock_seen,(unsigned long)audio_clock_pushed,(unsigned long)announce_superseded);
    { uint32_t now=(uint32_t)esp_timer_get_time();unsigned pm[3];for(unsigned k=0;k<3u;k++)pm[k]=survey_window_us[k]?(unsigned)((uint64_t)survey_airtime_us[k]*1000u/survey_window_us[k]):0u;
      wmp_telemetry_printf("WMP_AUTOCHANNEL query=%08lx enabled=%lu state=%lu channel=%u hops=%lu locks=%lu lock_ms=%lu lock_max_ms=%lu picks=%lu pick_channel=%lu ch1_permille=%u ch6_permille=%u ch11_permille=%u ch1_frames=%lu ch6_frames=%lu ch11_frames=%lu survey_age_ms=%lu party_age_ms=%lu%c",
        query,(unsigned long)auto_channel,(unsigned long)scan_state,(unsigned)selected_channel,(unsigned long)scan_hops,(unsigned long)scan_locks,(unsigned long)scan_lock_ms_last,(unsigned long)scan_lock_ms_max,
        (unsigned long)source_picks,(unsigned long)source_pick_channel,pm[0],pm[1],pm[2],(unsigned long)survey_frames[0],(unsigned long)survey_frames[1],(unsigned long)survey_frames[2],
        (unsigned long)(survey_at_us[0]?(now-survey_at_us[0])/1000u:0u),(unsigned long)(last_party_rx_us?(now-last_party_rx_us)/1000u:0u),10); }
    wmp_telemetry_printf("WMP_LATE_POLICY query=%08lx growth=%lu ignored_reports=%lu%c",query,(unsigned long)late_growth,(unsigned long)late_reports_ignored,10);
    wmp_telemetry_printf("WMP_COEX_WINDOW query=%08lx own_period_us=%lu own_wifi_us=%lu peer_period_us=%lu peer_wifi_us=%lu peer_age_ms=%lu deferrals=%lu deferred_max_us=%lu align_enabled=%lu align_restarts=%lu align_error_us=%ld interval_sets=%lu interval=%lu%c",query,(unsigned long)window_period,(unsigned long)window_len,(unsigned long)peer_window_period,(unsigned long)peer_window_len,(unsigned long)(peer_window_at?((uint64_t)esp_timer_get_time()-peer_window_at)/1000u:0u),(unsigned long)window_deferrals,(unsigned long)window_deferred_us_max,(unsigned long)align_enabled,(unsigned long)align_restarts,(long)align_last_error_us,(unsigned long)align_interval_sets,(unsigned long)coex_schm_interval_get(),10);
}
static uint64_t drop_generation,drop_sequence,drop_end;static unsigned drop_active,drop_remaining;
typedef struct { uint64_t sequence,sample,pts;uint8_t mask; } drop_record;
static drop_record *drop_records;
typedef struct { uint32_t gap,guard,budget,spacing,paced; } repair_policy;
static repair_policy repair_settings={8000u,80000u,40000u,40000u,0u};
static volatile unsigned repair_dedup=1;
static uint32_t repair_pending_suppressed;
static uint32_t repair_admission_deferred;
int wmp_radio_repair_dedup(unsigned enabled) { if(enabled>1u)return 0;repair_dedup=enabled;return 1; }
void wmp_radio_repair_gate_stats(unsigned long query) { wmp_telemetry_printf("WMP_REPAIR_GATE query=%08lx enabled=%u pending_suppressed=%lu limit=%u admission_deferred=%lu\n",query,repair_dedup,(unsigned long)repair_pending_suppressed,wmp_audio_repair_limit_value(),(unsigned long)repair_admission_deferred); }
int wmp_radio_repair_pace(unsigned us) { if(us<1000u || us>80000u)return 0;portENTER_CRITICAL(&copy_lock);repair_settings.gap=us;portEXIT_CRITICAL(&copy_lock);return 1; }
int wmp_radio_repair_config(unsigned gap,unsigned guard,unsigned budget,unsigned spacing,unsigned paced) {
    if(gap<1000u || gap>80000u || guard<2000u || guard>160000u || budget<1000u || budget>200000u || spacing<1000u || spacing>160000u || paced>1u)return 0;
    portENTER_CRITICAL(&copy_lock);repair_settings=(repair_policy){gap,guard,budget,spacing,paced};portEXIT_CRITICAL(&copy_lock);return 1;
}
void wmp_radio_session_stats(unsigned long query,unsigned source_role) {
    uint64_t gen;
    if(source_role) { portENTER_CRITICAL(&publisher_lock);gen=publisher_generation;portEXIT_CRITICAL(&publisher_lock); }
    else { portENTER_CRITICAL(&feedback_lock);gen=leader_generation;portEXIT_CRITICAL(&feedback_lock); }
    uint8_t wire[9];for(unsigned i=0;i<8;i++)wire[i]=(uint8_t)(gen>>(8u*i));wire[8]=(uint8_t)source_role;
    wmp_telemetry_printf("WMP_SESSION query=%08lx generation=%llu source=%u crc=%lu\n",query,(unsigned long long)gen,source_role,(unsigned long)wmp_crc32(wire,sizeof(wire)));
}
int wmp_radio_drop_test(unsigned ahead) { return wmp_radio_drop_range(ahead,1u); }
int wmp_radio_drop_range(unsigned ahead,unsigned groups) {
    if(ahead<5u || ahead>100u || groups<1u || groups>16u || radio_stats.mode!=2u)return 0;
    if(!drop_records) { drop_records=heap_caps_calloc(16,sizeof(*drop_records),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);if(!drop_records)return 0; }
    int ok=0;portENTER_CRITICAL(&copy_lock);
    if(copy_history && copy_trace.valid && copy_trace.kind==1u && !drop_active) { memset(drop_records,0,16*sizeof(*drop_records));drop_generation=copy_trace.generation;drop_sequence=copy_trace.highest+ahead;drop_end=drop_sequence+groups;drop_remaining=groups*radio_stats.copies;drop_active=1;ok=1; }
    uint64_t gen=drop_generation,first=drop_sequence,end=drop_end;unsigned remaining=drop_remaining;
    portEXIT_CRITICAL(&copy_lock);
    if(ok)wmp_telemetry_printf("WMP_DROP_TARGET generation=%llu first=%llu end=%llu copies=%u diagnostic=1\n",(unsigned long long)gen,(unsigned long long)first,(unsigned long long)end,remaining);
    return ok;
}
int wmp_radio_drop_clear(void) {
    portENTER_CRITICAL(&copy_lock);drop_active=drop_remaining=0;
    drop_record *old=drop_records;drop_records=NULL;portEXIT_CRITICAL(&copy_lock);heap_caps_free(old);return 1;
}
void wmp_radio_drop_trace(unsigned long query) {
    for(unsigned i=0;i<16u;i++) {
        drop_record r={0};portENTER_CRITICAL(&copy_lock);if(drop_records)r=drop_records[i];portEXIT_CRITICAL(&copy_lock);
        if(r.mask)wmp_telemetry_printf("WMP_DROP_RECORD query=%08lx seq=%llu sample=%llu pts=%llu mask=%u\n",query,(unsigned long long)r.sequence,(unsigned long long)r.sample,(unsigned long long)r.pts,r.mask);
    }
    portENTER_CRITICAL(&copy_lock);unsigned remaining=drop_remaining,active=drop_active;uint64_t gen=drop_generation;portEXIT_CRITICAL(&copy_lock);
    portENTER_CRITICAL(&copy_lock);repair_policy p=repair_settings;portEXIT_CRITICAL(&copy_lock);
    wmp_telemetry_printf("WMP_DROP_END query=%08lx generation=%llu remaining=%u active=%u pace_us=%lu guard_us=%lu budget_us=%lu spacing_us=%lu paced=%lu\n",query,(unsigned long long)gen,remaining,active,(unsigned long)p.gap,(unsigned long)p.guard,(unsigned long)p.budget,(unsigned long)p.spacing,(unsigned long)p.paced);
}
static uint64_t wire64(const uint8_t *p) { uint64_t v=0;for(unsigned j=0;j<8;j++)v|=(uint64_t)p[j]<<(8u*j);return v; }
static void copy_event(uint64_t gen,uint64_t seq,uint64_t pts,unsigned stage,unsigned copy,uint32_t a,uint32_t b) {
    portENTER_CRITICAL(&copy_lock);if(copy_history)wmp_copy_trace_event(&copy_trace,gen,seq,pts,stage,copy,a,b);portEXIT_CRITICAL(&copy_lock);
}
static void route_note(const wmp_header *h,unsigned bits) {
    if(!h || !copy_history || radio_stats.mode==2u)return;
    portENTER_CRITICAL(&copy_lock);if(copy_history)wmp_copy_trace_route(&copy_trace,h->generation,h->sequence,bits);portEXIT_CRITICAL(&copy_lock);
}
int wmp_radio_schedule(unsigned spacing,unsigned paced) {
    if(spacing<1000u || spacing>160000u || paced>1u)return 0;
    if(copy_dither_us>spacing/4u || (copy_dither_us && paced))return 0;
    schedule_config=spacing|(paced<<31);return 1;
}
int wmp_radio_pace(unsigned microseconds) {
    if(microseconds && (microseconds<500u || microseconds>6000u))return 0;
    transmit_pace_us=microseconds;radio_stats.tx_pace_us=microseconds;return 1;
}
int wmp_radio_path(unsigned action_frames) {
    if(action_frames>1u)return 0;
    audio_action_frames=action_frames;radio_stats.tx_action_frames=action_frames;return 1;
}
int wmp_radio_receiver_beacon(unsigned tu) {
    if(tu<100u || tu>60000u || tu%100u)return 0;
    receiver_beacon_tu=tu;return 1;
}
int wmp_radio_channel(unsigned channel) {
    if(channel<1u || channel>11u)return ESP_ERR_INVALID_ARG;
    if(!wifi_live)return ESP_ERR_WIFI_NOT_INIT;
    if(channel==selected_channel) { radio_stats.channel=channel;return ESP_OK; }
    /* A live retune under BTDM caused a real supervision timeout and invalid
     * deadline fits. Frequency selection is an idle-only diagnostic. */
    if(bt_stats.working || radio_stats.mode==2u || radio_stats.rx_active)return ESP_ERR_INVALID_STATE;
    esp_err_t result=esp_wifi_set_channel((uint8_t)channel,WIFI_SECOND_CHAN_NONE);
    if(result==ESP_OK) { selected_channel=channel;radio_stats.channel=channel; }
    return result;
}
/* Diagnostic live retune (channel-switch feasibility): bypasses the idle-only
 * guard above and reports how long the driver call blocked. Not used by any
 * automatic path. */
static volatile uint32_t live_retunes,live_retune_last_us,live_retune_max_us;static volatile int32_t live_retune_status;
int wmp_radio_auto_channel(unsigned enabled) { if(enabled>1u)return 0;auto_channel=enabled;if(!enabled)scan_state=0;return 1; }
static uint32_t frame_airtime_us(const wifi_pkt_rx_ctrl_t *c,unsigned bytes) {
    /* Approximate airtime: preamble plus payload at the received rate (tenths of Mbps). */
    static const uint16_t legacy[16]={10,20,55,110,10,20,55,110,480,240,120,60,540,360,180,90};
    static const uint16_t ht[8]={65,130,195,260,390,520,585,650};
    unsigned rate=c->sig_mode?ht[c->mcs&7u]*(c->cwb?2u:1u):legacy[c->rate&15u];
    unsigned preamble=c->sig_mode?36u:(c->rate<8u?192u:20u);
    return preamble+(uint32_t)((uint64_t)bytes*80u/(rate?rate:60u));
}
int wmp_radio_channel_live(unsigned channel,unsigned *elapsed_us) {
    if(channel<1u || channel>13u)return ESP_ERR_INVALID_ARG;
    if(!wifi_live)return ESP_ERR_WIFI_NOT_INIT;
    int64_t before=esp_timer_get_time();
    esp_err_t result=esp_wifi_set_channel((uint8_t)channel,WIFI_SECOND_CHAN_NONE);
    uint32_t elapsed=(uint32_t)(esp_timer_get_time()-before);
    live_retune_status=result;live_retune_last_us=elapsed;if(elapsed>live_retune_max_us)live_retune_max_us=elapsed;++live_retunes;
    if(result==ESP_OK) { selected_channel=channel;radio_stats.channel=channel; }
    if(elapsed_us)*elapsed_us=elapsed;
    return result;
}
/* The source's channel is chosen BEFORE its first frame: picking after the role change made it
 * transmit a few frames on whatever channel it was scanning, followers locked there, then lost it
 * when it moved and waited SCAN_LOST_US (2.5 s) before rescanning (measured 2.2 s silence per
 * party start). The pick is held while the role change completes. */
static volatile uint32_t pick_hold_until;
static void source_pick(uint32_t now) {
    /* Least-used of 1/6/11 from a fresh survey; otherwise stay put (followers are here). */
    int best=-1;uint64_t best_ratio=0;
    for(unsigned i=0;i<3u;i++) {
        if(!survey_at_us[i] || now-survey_at_us[i]>SURVEY_FRESH_US || !survey_window_us[i])continue;
        uint64_t ratio=(uint64_t)survey_airtime_us[i]*1000000u/survey_window_us[i];
        if(best<0 || ratio<best_ratio) { best=(int)i;best_ratio=ratio; }
    }
    /* A speaker that was just in a party here keeps this channel (its followers are here). */
    int recent_party=last_party_rx_us && now-last_party_rx_us<30000000u;
    if(!recent_party && best>=0 && scan_channels[best]!=selected_channel)wmp_radio_channel_live(scan_channels[best],NULL);
    source_pick_channel=selected_channel;++source_picks;scan_state=3u;
}
void wmp_radio_source_channel(void) {
    if(!auto_channel || !pure_broadcast || radio_stats.wifi_ap || radio_stats.infrastructure_requested || radio_stats.ethernet_enabled || !wifi_started)return;
    uint32_t now=(uint32_t)esp_timer_get_time();
    if(radio_stats.mode!=2u && scan_state!=3u)source_pick(now);
    pick_hold_until=now+3000000u;
}
void wmp_radio_channel_service(void) {
    if(!auto_channel || !pure_broadcast || radio_stats.wifi_ap || radio_stats.infrastructure_requested || radio_stats.ethernet_enabled || !wifi_started)return;
    uint32_t now=(uint32_t)esp_timer_get_time();
    if(radio_stats.mode==2u) {
        if(scan_state!=3u)source_pick(now);
        return;
    }
    if(scan_state==3u && (int32_t)(pick_hold_until-now)>0)return; /* becoming the source: stay on the pick */
    if(last_party_rx_us && now-last_party_rx_us<SCAN_LOST_US) {
        if(scan_state==1u && scan_hops!=scan_hops_at_start) { uint32_t ms=(now-scan_started_us)/1000u;scan_lock_ms_last=ms;if(ms>scan_lock_ms_max)scan_lock_ms_max=ms;++scan_locks; }
        scan_state=2u;return;
    }
    if(scan_state!=1u) {
        scan_state=1u;scan_started_us=now;scan_hops_at_start=scan_hops;dwell_index=0;
        for(unsigned i=0;i<3u;i++)if(scan_channels[i]==selected_channel)dwell_index=i;
        dwell_started_us=now;dwell_airtime_us=dwell_frames=0;return;
    }
    if(now-dwell_started_us<SCAN_DWELL_US)return;
    unsigned i=dwell_index;survey_airtime_us[i]=dwell_airtime_us;survey_frames[i]=dwell_frames;survey_window_us[i]=now-dwell_started_us;survey_at_us[i]=now;
    dwell_index=(dwell_index+1u)%3u;wmp_radio_channel_live(scan_channels[dwell_index],NULL);++scan_hops;
    dwell_started_us=(uint32_t)esp_timer_get_time();dwell_airtime_us=dwell_frames=0;
}
int wmp_radio_receive_pause(unsigned paused) {
    if(paused>1u || radio_stats.mode==2u)return 0;
    command c={.mode=(uint8_t)(paused?5u:6u),.pps=90,.copies=4,.spacing=WMP_DEFAULT_COPY_SPACING_US,.payload=476};return xQueueSend(commands,&c,0)==pdTRUE;
}
int wmp_radio_receiver_mode(unsigned ap) {
    if(ap>1u || radio_stats.mode==2u || radio_stats.infrastructure_requested)return 0;
    /* Selecting the passive interface also establishes its fixed raw TX
     * rate before start. This is cold setup, never a live retune. */
    if(!ap && (bt_stats.requested || bt_stats.working || bt_stats.phone || bt_stats.ble || audio_stats.running))return 0;
    command c={.mode=(uint8_t)(ap?3u:4u),.pps=90,.copies=4,.spacing=WMP_DEFAULT_COPY_SPACING_US,.payload=476};
    return xQueueSend(commands,&c,0)==pdTRUE;
}
int wmp_radio_associate(unsigned enabled) {
    if(enabled>1u || radio_stats.mode || radio_stats.wifi_ap || radio_stats.native_rx_requested || radio_stats.infrastructure_requested)return 0;
    command c={.mode=(uint8_t)(enabled?7u:8u)};
    return xQueueSend(commands,&c,0)==pdTRUE;
}
int wmp_radio_network(const char *ssid,const char *password) {
    if(radio_stats.mode || radio_stats.transition_pending || infrastructure_pending || bt_stats.phone || bt_stats.ble)return 0;
    unsigned enabled=ssid && password;
    if(enabled && (!ssid[0] || strlen(ssid)>32u || strlen(password)<8u || strlen(password)>63u))return 0;
    memset(&infrastructure_config,0,sizeof(infrastructure_config));
    if(enabled) {
        memcpy(infrastructure_config.sta.ssid,ssid,strlen(ssid));memcpy(infrastructure_config.sta.password,password,strlen(password));
        infrastructure_config.sta.scan_method=WIFI_FAST_SCAN;infrastructure_config.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK;
        infrastructure_config.sta.listen_interval=1;
    }
    command c={.mode=(uint8_t)(enabled?11u:12u)};infrastructure_pending=1;
    int queued=xQueueSend(commands,&c,0)==pdTRUE;if(!queued)infrastructure_pending=0;return queued;
}
int wmp_radio_anchor(const char *ssid,const char *password) {
    if(!ssid || !password || !ssid[0] || strlen(ssid)>32u || strlen(password)<8u || strlen(password)>63u ||
       radio_stats.mode || radio_stats.transition_pending || infrastructure_pending || bt_stats.phone || bt_stats.ble)return 0;
    memset(&anchor_config,0,sizeof(anchor_config));
    memcpy(anchor_config.ap.ssid,ssid,strlen(ssid));anchor_config.ap.ssid_len=(uint8_t)strlen(ssid);
    memcpy(anchor_config.ap.password,password,strlen(password));anchor_config.ap.authmode=WIFI_AUTH_WPA2_PSK;
    anchor_config.ap.channel=(uint8_t)selected_channel;anchor_config.ap.max_connection=8;anchor_config.ap.beacon_interval=100;
    command c={.mode=13u};infrastructure_pending=1;
    int queued=xQueueSend(commands,&c,0)==pdTRUE;if(!queued)infrastructure_pending=0;return queued;
}
int wmp_radio_clock_ready(void) {
    return radio_stats.network_anchor?radio_stats.anchor_active:radio_stats.association_connected;
}
int wmp_radio_ethernet(unsigned enabled) {
    if(enabled>2u || radio_stats.native_rx_requested || radio_stats.direct_rx_enabled)return 0;
    if(enabled && !radio_stats.wifi_ap && !radio_stats.association_connected)return 0;
    command c={.mode=(uint8_t)(enabled?9u:10u),.copies=(uint8_t)enabled};
    return xQueueSend(commands,&c,0)==pdTRUE;
}
int wmp_radio_power_save(unsigned mode) { if(!wifi_live)return ESP_ERR_WIFI_NOT_INIT;return mode<=2u?esp_wifi_set_ps((wifi_ps_type_t)mode):ESP_ERR_INVALID_ARG; }
int wmp_radio_tx_window(unsigned buffers) { if(!buffers || buffers>TX_BUFFERS)return 0;tx_window=buffers;radio_stats.tx_window=buffers;if(transmitter_task)xTaskNotifyGive(transmitter_task);return 1; }
int wmp_radio_copies(unsigned copies) { if(copies!=0u && copies!=3u && copies!=4u)return 0;original_copy_override=copies;if(transmitter_task)xTaskNotifyGive(transmitter_task);return 1; }
int wmp_radio_scan_listen(unsigned enabled) {
    if(enabled>1u || (enabled && (radio_stats.mode || radio_stats.wifi_ap || radio_stats.native_rx_requested || radio_stats.ethernet_enabled)))return 0;
    radio_stats.listen_requested=enabled;if(transmitter_task)xTaskNotifyGive(transmitter_task);return 1;
}
int wmp_radio_latency(unsigned milliseconds) {
    if(milliseconds*1000u<WMP_PLAYOUT_MIN_US || milliseconds*1000u>WMP_PLAYOUT_MAX_US || milliseconds>WMP_PLAYOUT_MAX_US/1000u)return 0;
    portENTER_CRITICAL(&publisher_lock);requested_latency_us=milliseconds*1000u;++latency_version;portEXIT_CRITICAL(&publisher_lock);return 1;
}
void wmp_radio_late_report(uint64_t generation,unsigned frames,unsigned rate,unsigned samples) {
    if(radio_stats.mode==2u || !generation || !frames || !samples)return;
    if(frames>32u)frames=32u;
    portENTER_CRITICAL(&feedback_lock);
    if(feedback_generation!=generation)feedback_frames=0;
    feedback_generation=generation;feedback_rate=rate;feedback_samples=samples;
    if(frames>feedback_frames)feedback_frames=frames;
    portEXIT_CRITICAL(&feedback_lock);
}
int wmp_radio_test_report(unsigned frames) {
    if(!frames || frames>32u || radio_stats.mode==2u)return 0;
    uint64_t gen;uint32_t rate,samples;
    portENTER_CRITICAL(&feedback_lock);gen=leader_generation;rate=leader_rate;samples=leader_samples;portEXIT_CRITICAL(&feedback_lock);
    if(!gen || !rate || !samples)return 0;
    wmp_radio_late_report(gen,frames,rate,samples);return 1;
}
int wmp_radio_rx_priority(unsigned priority) {
    if(priority<3u || priority>24u || !receiver_task)return 0;
    vTaskPrioritySet(receiver_task,priority);radio_stats.rx_priority=priority;return 1;
}
int wmp_radio_preference(unsigned prefer) {
    if(prefer>2u)return ESP_ERR_INVALID_ARG;
    const esp_coex_prefer_t options[3]={ESP_COEX_PREFER_BALANCE,ESP_COEX_PREFER_WIFI,ESP_COEX_PREFER_BT};
    int result=esp_coex_preference_set(options[prefer]);if(!result)radio_stats.coex_preference=prefer;return result;
}
void wmp_radio_copy_summary(unsigned long query) {
    uint32_t counts[3][4]={{0}},retired[16]={0},repeated=0;uint64_t gen=0,highest=0;unsigned kind=0;
    portENTER_CRITICAL(&copy_lock);if(copy_history) { memcpy(counts,copy_trace.counts,sizeof(counts));memcpy(retired,copy_trace.retired,sizeof(retired));repeated=copy_trace.repeated;gen=copy_trace.generation;highest=copy_trace.highest;kind=copy_trace.kind; }portEXIT_CRITICAL(&copy_lock);
    wmp_telemetry_printf("WMP_COPY query=%08lx kind=%u generation=%llu highest=%llu raw=%lu,%lu,%lu,%lu accepted=%lu,%lu,%lu,%lu done=%lu,%lu,%lu,%lu repeated=%lu paced=%lu spacing=%lu fec=0 retired_masks=",query,kind,(unsigned long long)gen,(unsigned long long)highest,(unsigned long)counts[0][0],(unsigned long)counts[0][1],(unsigned long)counts[0][2],(unsigned long)counts[0][3],(unsigned long)counts[1][0],(unsigned long)counts[1][1],(unsigned long)counts[1][2],(unsigned long)counts[1][3],(unsigned long)counts[2][0],(unsigned long)counts[2][1],(unsigned long)counts[2][2],(unsigned long)counts[2][3],(unsigned long)repeated,(unsigned long)(schedule_config>>31),(unsigned long)(schedule_config&UINT32_C(0x7fffffff)));
    for(unsigned i=0;i<16;i++) { wmp_telemetry_printf("%s%lu",i?",":"",(unsigned long)retired[i]); }
    wmp_telemetry_printf(" coex_hint=%lu\n",(unsigned long)radio_stats.coex_preference);
}
void wmp_radio_copy_trace(unsigned long query) {
    if(!copy_history) { wmp_telemetry_printf("WMP_TRACE_END query=%08lx rows=0 unloaded=1\n",query);return; }
    wmp_copy_record *records=heap_caps_malloc(64u*sizeof(*records),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!records) { wmp_telemetry_printf("WMP_TRACE_END query=%08lx rows=0 allocation_failed=1\n",query);return; }
    uint64_t gen,highest,first;unsigned kind,valid;
    portENTER_CRITICAL(&copy_lock);if(copy_history) { gen=copy_trace.generation;highest=copy_trace.highest;first=copy_trace.first;kind=copy_trace.kind;valid=copy_trace.valid; }else { gen=highest=first=0;kind=valid=0; }portEXIT_CRITICAL(&copy_lock);
    /* One tiny lock per record; never hold a cross-core lock while printing. */
    for(unsigned i=0;i<64;i++) { portENTER_CRITICAL(&copy_lock);if(copy_history)records[i]=copy_trace.records[i];else memset(&records[i],0,sizeof(records[i]));portEXIT_CRITICAL(&copy_lock); }
    wmp_telemetry_printf("WMP_TRACE_META query=%08lx kind=%u generation=%llu highest=%llu a=%s b=%s units=us32 copy_bits=0123 row_crc=1 route_bits=1\n",query,kind,(unsigned long long)gen,(unsigned long long)highest,kind==1u?"submit_esp":"mac_rx",kind==1u?"tx_done_callback_esp":"rx_callback_esp");
    unsigned rows=0;uint64_t begin=highest>63u?highest-63u:0;if(begin<first)begin=first;
    if(valid && highest>=4u)for(uint64_t seq=begin;seq<=highest-4u;seq++) {
        wmp_copy_record *r=&records[seq&63u];if(!r->valid || r->sequence!=seq)continue;
        wmp_telemetry_printf("WMP_TRACE query=%08lx seq=%llu pts=%llu raw=%u accepted=%u done=%u failed=%u repaired=%u injected=%u route=%u crc=%lu a=%lu,%lu,%lu,%lu b=%lu,%lu,%lu,%lu\n",query,(unsigned long long)seq,(unsigned long long)r->presentation_us,r->seen,r->accepted,r->completed,r->failed,r->repaired,r->injected,r->route,(unsigned long)wmp_crc32((const uint8_t*)r,offsetof(wmp_copy_record,route)+1u),(unsigned long)r->a[0],(unsigned long)r->a[1],(unsigned long)r->a[2],(unsigned long)r->a[3],(unsigned long)r->b[0],(unsigned long)r->b[1],(unsigned long)r->b[2],(unsigned long)r->b[3]);++rows;
    }
    wmp_telemetry_printf("WMP_TRACE_END query=%08lx rows=%u\n",query,rows);
    heap_caps_free(records);
}
static int handoff_audio(wmp_dedup *delivered,const wmp_header *h,const uint8_t *payload,uint32_t crc,uint32_t physical_tag) {
    if(radio_stats.mode==2u)return 1;
    int decision=wmp_dedup_query(delivered,h->group,h->generation,h->sequence,crc);
    delivery_note(h->generation,h->sequence,WMP_D_DELIVERY_DEDUP,h->copy_index,h->flags,physical_tag,(uint32_t)esp_timer_get_time(),decision,h);
    if(decision==0)route_note(h,WMP_ROUTE_DUPLICATE);
    if(decision!=1)return decision==0;
    int queued=wmp_audio_offer(h,payload,0);delivery_note(h->generation,h->sequence,WMP_D_HANDOFF,h->copy_index,h->flags,physical_tag,(uint32_t)esp_timer_get_time(),queued,h);
    if(!queued) { route_note(h,WMP_ROUTE_QUEUE_FAILED);return 0; }
    if(queued==2)route_note(h,WMP_ROUTE_DEFERRED);
    int accepted=wmp_dedup_commit_delivery(delivered,h->group,h->generation,h->sequence,crc,queued)==1;
    if(accepted)route_note(h,WMP_ROUTE_QUEUED);
    if(accepted) {
        uint64_t now=(uint64_t)esp_timer_get_time();uint32_t backoff=esp_random()%20000u;
        /* Earlier missing groups cannot be needed after this packet's own
         * presentation. Use that source-owned ceiling minus20ms rather than
         * expiring every request400ms after discovery. The source still checks
         * each missing group's exact cached presentation time before replay. */
        uint64_t clock=wmp_source_clock(),until=now;
        if(clock && h->presentation_us>clock && h->presentation_us-clock>20000u) {
            uint64_t span=h->presentation_us-clock-20000u;until=now>UINT64_MAX-span?UINT64_MAX:now+span;
        }
        /* Pure broadcast follower: no source clock, but the fitted ANNOUNCE
         * clock maps this packet's presentation onto our esp_timer. */
        int64_t local_deadline=own_clock_local && !clock?wmp_audio_local_deadline(h->presentation_us):0;
        if(local_deadline && (uint64_t)local_deadline>now+20000u)until=(uint64_t)local_deadline-20000u;
        int shared=((radio_stats.ethernet_enabled || radio_stats.direct_rx_enabled) && wmp_radio_clock_ready()) || local_deadline>0;
        portENTER_CRITICAL(&nack_lock);
        if(shared) { wmp_nack_observe_until(missing_audio,h->generation,h->sequence,now,backoff,nack_delay_us,until);++nack_deadline_handoffs; }
        else { wmp_nack_observe_after(missing_audio,h->generation,h->sequence,now,backoff,nack_delay_us);++nack_legacy_handoffs; }
        portEXIT_CRITICAL(&nack_lock);
        if(h->flags&WMP_FLAG_REPLAY)++radio_stats.replay_delivered;
    }
    return accepted;
}
static uint32_t repair_miss_hist[6],repair_miss_untracked,repair_miss_first_age_us,repair_miss_last_age_us;
static repair_event repair_miss_last;static int64_t repair_miss_ahead_us,repair_miss_newest_delta;
static void record_repair(const wmp_header *request,unsigned outcome,unsigned copies,uint64_t clock,uint64_t pts) {
    wmp_radio_delivery(request,WMP_D_REQUEST,(int)outcome);
    /* The unserved-repair record below must not depend on the optional
     * COPYLOG ring (it stayed empty while that was off). */
    if(!repair_events && outcome!=1u && outcome!=2u)return;
    repair_event event={.generation=request->generation,.sequence=request->sequence,.pts=pts,.source_clock=clock,
        .at_us=(uint32_t)esp_timer_get_time(),.outcome=(uint8_t)outcome,.copies=(uint8_t)copies};
    portENTER_CRITICAL(&copy_lock);
    const wmp_copy_record *r=copy_history?&copy_trace.records[request->sequence&63u]:NULL;
    if(r && copy_trace.valid && copy_trace.generation==request->generation && r->valid && r->sequence==request->sequence) {
        if(!event.pts)event.pts=r->presentation_us;
        event.attempted=r->seen;event.accepted=r->accepted;event.completed=r->completed;
    }
    if(repair_events) { repair_events[repair_next]=event;repair_next=(repair_next+1u)%8u;if(repair_count<8u)++repair_count; }
    if(outcome==1u || outcome==2u) {
        /* Unserved repair (cache miss or too late): where the group stood against the shared clock. */
        int tracked=r && copy_trace.valid && copy_trace.generation==request->generation && r->valid && r->sequence==request->sequence;
        unsigned bin=5u;int64_t ahead=0;
        if(event.pts && clock) { ahead=(int64_t)(event.pts-clock);bin=ahead<=0?0u:ahead<20000?1u:ahead<100000?2u:ahead<300000?3u:4u; }
        ++repair_miss_hist[bin];if(!tracked)++repair_miss_untracked;
        repair_miss_last=event;repair_miss_ahead_us=ahead;
        repair_miss_newest_delta=copy_trace.valid && copy_trace.generation==request->generation?(int64_t)(copy_trace.highest-request->sequence):INT64_MIN;
        repair_miss_first_age_us=tracked && (r->completed&1u)?event.at_us-r->b[0]:0;
        repair_miss_last_age_us=0;
        if(tracked)for(unsigned i=0;i<4u;i++)if(r->completed&(1u<<i)) { uint32_t age=event.at_us-r->b[i];if(!repair_miss_last_age_us || age<repair_miss_last_age_us)repair_miss_last_age_us=age; }
    }
    portEXIT_CRITICAL(&copy_lock);
}
static void print_repair(unsigned long query,const char *kind,const repair_event *e) {
    wmp_telemetry_printf("%s query=%08lx generation=%llu seq=%llu pts=%llu source_clock=%llu at_us32=%lu outcome=%u copies=%u attempted=%u accepted=%u completed=%u\n",
        kind,query,(unsigned long long)e->generation,(unsigned long long)e->sequence,(unsigned long long)e->pts,
        (unsigned long long)e->source_clock,(unsigned long)e->at_us,(unsigned)e->outcome,(unsigned)e->copies,
        (unsigned)e->attempted,(unsigned)e->accepted,(unsigned)e->completed);
}
void wmp_radio_repair_last(unsigned long query) {
    repair_event e={0};
    portENTER_CRITICAL(&copy_lock);if(repair_events && repair_count)e=repair_events[(repair_next+7u)%8u];portEXIT_CRITICAL(&copy_lock);
    print_repair(query,"WMP_REPAIR_LAST",&e);
    uint32_t h[6],untracked,first_age,last_age;repair_event m;int64_t ahead,newest;
    portENTER_CRITICAL(&copy_lock);memcpy(h,repair_miss_hist,sizeof(h));untracked=repair_miss_untracked;m=repair_miss_last;ahead=repair_miss_ahead_us;
    newest=repair_miss_newest_delta;first_age=repair_miss_first_age_us;last_age=repair_miss_last_age_us;portEXIT_CRITICAL(&copy_lock);
    wmp_telemetry_printf("WMP_REPAIR_MISS query=%08lx past=%lu ahead_lt20ms=%lu ahead_lt100ms=%lu ahead_lt300ms=%lu ahead_ge300ms=%lu no_pts=%lu untracked=%lu last_outcome=%u last_seq=%llu last_ahead_us=%lld last_behind_newest=%lld last_first_copy_age_us=%lu last_latest_copy_age_us=%lu last_completed=%u\n",
        query,(unsigned long)h[0],(unsigned long)h[1],(unsigned long)h[2],(unsigned long)h[3],(unsigned long)h[4],(unsigned long)h[5],(unsigned long)untracked,
        (unsigned)m.outcome,(unsigned long long)m.sequence,(long long)ahead,(long long)(newest==INT64_MIN?-1:newest),(unsigned long)first_age,(unsigned long)last_age,(unsigned)m.completed);
}
void wmp_radio_repair_trace(unsigned long query) {
    repair_event records[8];unsigned count;
    portENTER_CRITICAL(&copy_lock);count=repair_count;
    for(unsigned i=0;i<count;i++)records[i]=repair_events[(repair_next+8u-count+i)%8u];
    portEXIT_CRITICAL(&copy_lock);
    for(unsigned i=0;i<count;i++)print_repair(query,"WMP_REPAIR_TRACE",&records[i]);
    wmp_telemetry_printf("WMP_REPAIR_END query=%08lx count=%u\n",query,count);
}
static void replay(const wmp_header *request,const uint8_t requester[6]) {
    if(radio_stats.transition_pending) { ++radio_stats.replay_deferred;record_repair(request,9,0,0,0);return; }
    message *m=replay_buffer;if(!m) { ++radio_stats.replay_deferred;record_repair(request,10,0,0,0);return; }
    static uint64_t next_repair;
    portENTER_CRITICAL(&copy_lock);repair_policy policy=repair_settings;portEXIT_CRITICAL(&copy_lock);
    static uint64_t generation,last_sequence[32];static uint32_t last_time[32];
    uint32_t now=(uint32_t)esp_timer_get_time();unsigned slot=(unsigned)(request->sequence&31u);uint64_t clock=wmp_source_clock();
    if(generation!=request->generation) { generation=request->generation;next_repair=0;memset(last_time,0,sizeof(last_time)); }
    /* Receiver retries are40ms apart. A40ms sender suppression measured at
     * arrival can swallow the final retry when radio/queue delays differ. */
    if(last_time[slot] && last_sequence[slot]==request->sequence && now-last_time[slot]<20000u) { ++radio_stats.nack_suppressed;record_repair(request,5,0,clock,0);return; }
    memset(m,0,sizeof(*m));
    int borrowed;
    if(repair_dedup)borrowed=wmp_audio_borrow_repair(request->generation,request->sequence,&m->header,&m->payload,&m->reference);
    else borrowed=(m->reference=wmp_audio_borrow(request->generation,request->sequence,&m->header,&m->payload))?1:0;
    if(borrowed==-2) { ++repair_admission_deferred;++radio_stats.replay_deferred;record_repair(request,7,0,clock,0);return; }
    if(borrowed<0) { ++repair_pending_suppressed;++radio_stats.nack_suppressed;record_repair(request,6,0,clock,0);return; }
    if(!borrowed) { ++radio_stats.replay_cache_miss;record_repair(request,1,0,clock,0);return; }
    clock=wmp_source_clock();
    uint64_t local_now=(uint64_t)esp_timer_get_time(),remaining=clock && m->header.presentation_us>clock?m->header.presentation_us-clock:0;
    uint64_t ready=wmp_replay_start(local_now,next_repair,remaining,20000u),delay=ready-local_now;
    uint32_t minimum_spacing=policy.spacing>policy.budget?policy.spacing:policy.budget,repair_spacing=0;
    unsigned copies=remaining>delay?wmp_replay_plan(remaining-delay,minimum_spacing,policy.guard,20000u,&repair_spacing):0;
    if(!copies) { wmp_audio_unshare(m->reference);m->reference=0;++radio_stats.replay_too_late;record_repair(request,2,0,clock,m->header.presentation_us);return; }
    m->header.flags|=WMP_FLAG_REPLAY;m->ready_us=ready;m->total_copies=(uint8_t)copies;m->copy_spacing=repair_spacing;m->repair_paced=(uint8_t)policy.paced;memcpy(m->repair_to,requester,6);
    portENTER_CRITICAL(&publisher_lock);
    int allowed=!radio_stats.transition_pending && radio_stats.mode==2u && m->header.generation==publisher_generation;
    portEXIT_CRITICAL(&publisher_lock);
    /* A stream of new repair requests must not overtake originals already
     * waiting for a staging slot. Dispatch separately orders ready slots by
     * their actual presentation deadlines. Queue APIs never run inside a
     * spinlock; the transmitter discards any message whose generation it no
     * longer owns, so a transition racing this send is harmless. */
    int queued=allowed && xQueueSend(source,m,0)==pdTRUE;
    if(!queued) { wmp_audio_unshare(m->reference);m->reference=0;++radio_stats.replay_deferred;record_repair(request,4,copies,clock,m->header.presentation_us);return; }
    uint64_t next=ready>UINT64_MAX-policy.gap?UINT64_MAX:ready+policy.gap;if(next>next_repair)next_repair=next;
    last_sequence[slot]=request->sequence;last_time[slot]=now;++radio_stats.replay_queued;record_repair(request,delay?11u:3u,copies,clock,m->header.presentation_us);
    if(transmitter_task)xTaskNotifyGive(transmitter_task);
}
static volatile uint32_t raw_tx_54m,raw_tx_mcs7,raw_tx_other;
/* Congestion shedding (TXSHED, default on): retire original copies that can no longer play and send
 * first copies before repeats. A 40 s busy-air episode at the master overflowed the send queue with
 * repeats of groups already past their play time, and the backlog then dropped new groups at the
 * source for every speaker (party soak 2026-11-20, 14:45). */
static volatile uint32_t tx_shed=1,original_expired_trains,original_expired_copies,original_expired_unsent;
int wmp_radio_tx_shed(unsigned on) { if(on>1u)return 0;tx_shed=on;return 1; }
static volatile uint32_t original_late_submit,repair_late_submit,repair_expired_trains,repair_expired_copies,repair_clock_waits,original_late_done,repair_late_done;
static volatile uint32_t air_nonht,air_ht20_mcs7,air_ht40_mcs7,air_other;
static volatile int32_t air_rssi_min,air_rssi_max;
static int rate_value(unsigned mbps,wifi_phy_rate_t *rate) {
    switch(mbps) {
    case 6:*rate=WIFI_PHY_RATE_6M;break;case 9:*rate=WIFI_PHY_RATE_9M;break;
    case 12:*rate=WIFI_PHY_RATE_12M;break;case 18:*rate=WIFI_PHY_RATE_18M;break;
    case 24:*rate=WIFI_PHY_RATE_24M;break;case 36:*rate=WIFI_PHY_RATE_36M;break;
    case 48:*rate=WIFI_PHY_RATE_48M;break;case 54:*rate=WIFI_PHY_RATE_54M;break;
    case 26:*rate=WIFI_PHY_RATE_MCS3_LGI;break; /* HT20,800ns GI; default broadcast profile */
    case 65:*rate=WIFI_PHY_RATE_MCS7_LGI;break; /* HT20,800ns GI; former profile, diagnostics only */
    default:return ESP_ERR_INVALID_ARG;
    }return ESP_OK;
}
static int configure_rate_before_start(wifi_interface_t iface,unsigned mbps) {
    wifi_phy_rate_t rate;int status=rate_value(mbps,&rate);if(status)return status;
    if(wifi_started) { ++rate_precondition_rejections;return ESP_ERR_INVALID_STATE; }
    /* NVS is deliberately preserved; never inherit an old HT40 selection.
     * MCS3_LGI (26) and MCS7_LGI (65) are these rates only at HT20. Configure each active interface
     * explicitly before start, together with its raw transmission rate. */
    status=esp_wifi_set_bandwidth(iface,WIFI_BW20);
    if(status!=ESP_OK) { rate_config_status[iface]=status;return status; }
    ++rate_prestart_calls;status=esp_wifi_config_80211_tx_rate(iface,rate);rate_config_status[iface]=status;
    if(status==ESP_OK)rate_configured[iface]=mbps;
    return status;
}
static int start_wifi(void) {
    int status=esp_wifi_start();if(status==ESP_OK) { wifi_started=1;++rate_starts; }return status;
}
static int stop_wifi(void) {
    int status=esp_wifi_stop();if(status==ESP_OK) { wifi_started=0;++rate_stops; }return status;
}
void wmp_radio_rate_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_RATE_SETUP query=%08lx selected_mbps=%u interface=%u started=%u configured_mbps=%u sta_mbps=%u ap_mbps=%u sta_status=%d ap_status=%d prestart_calls=%u starts=%u stops=%u precondition_rejections=%u active_rejections=%u actual_rf_known=0\n",query,(unsigned)selected_rate,(unsigned)tx_interface,wifi_started,rate_configured[tx_interface],rate_configured[WIFI_IF_STA],rate_configured[WIFI_IF_AP],rate_config_status[WIFI_IF_STA],rate_config_status[WIFI_IF_AP],rate_prestart_calls,rate_starts,rate_stops,rate_precondition_rejections,rate_active_rejections);
    wmp_telemetry_printf("WMP_RATE_OBSERVED query=%08lx raw_54m=%lu raw_mcs7=%lu raw_other=%lu driver_completion_only=1 independent_rf_capture=0\n",query,(unsigned long)raw_tx_54m,(unsigned long)raw_tx_mcs7,(unsigned long)raw_tx_other);
    wmp_telemetry_printf("WMP_TX_LIFETIME query=%08lx original_late_submit=%lu repair_late_submit=%lu expired_repair_trains=%lu expired_repair_copies=%lu repair_clock_waits=%lu original_late_done=%lu repair_late_done=%lu guard_us=20000 shed=%lu original_expired_trains=%lu original_expired_copies=%lu original_expired_unsent=%lu\n",query,(unsigned long)original_late_submit,(unsigned long)repair_late_submit,(unsigned long)repair_expired_trains,(unsigned long)repair_expired_copies,(unsigned long)repair_clock_waits,(unsigned long)original_late_done,(unsigned long)repair_late_done,(unsigned long)tx_shed,(unsigned long)original_expired_trains,(unsigned long)original_expired_copies,(unsigned long)original_expired_unsent);
    wmp_telemetry_printf("WMP_RX_RADIO_OBS query=%08lx frames=%lu nonht=%lu ht20_mcs7_lgi=%lu ht40_mcs7_lgi=%lu other=%lu rssi_min=%ld rssi_max=%ld surviving_frames_only=1\n",query,(unsigned long)radio_stats.direct_air_frames,(unsigned long)air_nonht,(unsigned long)air_ht20_mcs7,(unsigned long)air_ht40_mcs7,(unsigned long)air_other,(long)air_rssi_min,(long)air_rssi_max);
    wifi_bandwidth_t bandwidth=0;int8_t power=0;int bs=ESP_ERR_WIFI_NOT_INIT,ps=ESP_ERR_WIFI_NOT_INIT;
    if(wifi_live) { bs=esp_wifi_get_bandwidth(tx_interface,&bandwidth);ps=esp_wifi_get_max_tx_power(&power); }
    wmp_telemetry_printf("WMP_PHY_CONFIG query=%08lx bandwidth_status=%d bandwidth_code=%u power_status=%d max_power_qdbm=%d\n",query,bs,bs?0u:(unsigned)bandwidth,ps,ps?0:(int)power);
}
int wmp_radio_rate(unsigned mbps) {
    wifi_phy_rate_t value;int status=rate_value(mbps,&value);if(status)return status;
    if(!wifi_live) { if(mbps==selected_rate)return ESP_OK;return ESP_ERR_WIFI_NOT_INIT; }
    /* An unchanged, already configured rate is an observation, not a live
     * SDK reconfiguration. Different rates are cold AP diagnostics only. */
    if(mbps==selected_rate && rate_configured[tx_interface]==mbps)return ESP_OK;
    if(bt_stats.requested || bt_stats.working || bt_stats.phone || bt_stats.ble || audio_stats.running ||
       radio_stats.mode || radio_stats.transition_pending || radio_stats.rx_active || radio_stats.rx_claim ||
       radio_stats.infrastructure_requested || radio_stats.association_connected || radio_stats.direct_rx_enabled ||
       radio_stats.native_rx_requested || radio_stats.ethernet_enabled || !radio_stats.wifi_ap || uxQueueMessagesWaiting(commands)) {
        ++rate_active_rejections;return ESP_ERR_INVALID_STATE;
    }
    bool promiscuous=false;status=esp_wifi_get_promiscuous(&promiscuous);if(status)return status;
    status=stop_wifi();if(status)return status;
    status=configure_rate_before_start(tx_interface,mbps);
    if(status!=ESP_OK) {
        /* Restore the previous idle configuration; never restart an active
         * media epoch or conceal a failed requested rate as success. */
        ESP_ERROR_CHECK(configure_rate_before_start(tx_interface,selected_rate));
        ESP_ERROR_CHECK(start_wifi());ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
        ESP_ERROR_CHECK(esp_wifi_set_promiscuous(promiscuous));return status;
    }
    status=start_wifi();if(status)return status;
    status=esp_wifi_set_ps(WIFI_PS_NONE);if(status)return status;
    status=esp_wifi_set_promiscuous(promiscuous);if(status)return status;
    selected_rate=mbps;radio_stats.phy_mbps=mbps;return ESP_OK;
}
/* Diagnostic: change the raw broadcast PHY rate while running (wifi_phy_rate_t code, e.g. 0x10+n = MCSn LGI).
 * The applied rate is verified from TX completions (WMP_PHY tx_rate), not from this return code. */
int wmp_radio_tx_rate_live(unsigned code,int *status_ht) {
    if(code>0x1fu)return ESP_ERR_INVALID_ARG;
    if(!wifi_live)return ESP_ERR_WIFI_NOT_INIT;
    int status=esp_wifi_config_80211_tx_rate(tx_interface,(wifi_phy_rate_t)code);
    int ht=ESP_OK;
    if(status!=ESP_OK) { wifi_tx_rate_config_t c={.phymode=code>=0x10u?WIFI_PHY_MODE_HT20:WIFI_PHY_MODE_11G,.rate=(wifi_phy_rate_t)code}; ht=esp_wifi_config_80211_tx(tx_interface,&c); }
    if(status_ht)*status_ht=ht;
    return status;
}
static const uint8_t network[6]={0x02,0x57,0x4d,0x50,0x00,0x01};
static void configure_ap(unsigned tu) {
    wifi_config_t ap={0};snprintf((char *)ap.ap.ssid,sizeof(ap.ap.ssid),"WMP-SYNC-%02X%02X",wmp_base_mac[4],wmp_base_mac[5]);
    ap.ap.channel=(uint8_t)selected_channel;ap.ap.max_connection=1;ap.ap.authmode=WIFI_AUTH_OPEN;ap.ap.beacon_interval=(uint16_t)tu;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP,&ap));radio_stats.beacon_tu=tu;
}
static void wake_transmitter(void *unused) {
    (void)unused;if(transmitter_task)xTaskNotifyGive(transmitter_task);
}
static void wait_transmitter(uint64_t deadline) {
    int64_t delay=(int64_t)deadline-esp_timer_get_time();if(delay<=0)return;
    esp_timer_stop(transmit_wakeup);
    ESP_ERROR_CHECK(esp_timer_start_once(transmit_wakeup,(uint64_t)delay));
    ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(20));
    esp_timer_stop(transmit_wakeup);
    int64_t late=esp_timer_get_time()-(int64_t)deadline;
    portENTER_CRITICAL(&duration_lock);++wake_calls;
    if(late>0) { wake_late_us+=(uint64_t)late;if(late>wake_late_max)wake_late_max=(uint32_t)late;if(late>1000)++wake_over1ms;if(late>5000)++wake_over5ms; }
    portEXIT_CRITICAL(&duration_lock);
}
static void finish_tx_buffer(unsigned index,int success,int unicast) {
    tx_buffer *b=&tx_buffers[index];
    if(b->state!=1u && b->state!=3u) { ++radio_stats.tx_unmatched;return; }
    b->finished=(uint32_t)esp_timer_get_time();b->acked=(uint8_t)((b->flags&WMP_FLAG_REPLAY) && unicast && success);
    if((b->type==WMP_AUDIO || b->type==WMP_SILENCE) && b->presentation_us) {
        uint64_t clock=wmp_source_clock();
        if(clock && clock>=b->presentation_us) { if(b->flags&WMP_FLAG_REPLAY)++repair_late_done;else ++original_late_done; }
    }
    delivery_note(b->generation,b->sequence,WMP_D_DONE,b->copy,b->flags,b->tag,b->finished,success?0:1,NULL);
    if(b->type==WMP_AUDIO && !(b->flags&WMP_FLAG_REPLAY)) {
        uint32_t dwell=b->finished-b->submitted;
        portENTER_CRITICAL(&duration_lock);++original_completions;original_dwell_us+=dwell;if(dwell>original_dwell_max)original_dwell_max=dwell;if(dwell>20000u)++original_over20ms;if(dwell>100000u)++original_over100ms;portEXIT_CRITICAL(&duration_lock);
    }
    if((b->type==WMP_AUDIO || b->type==WMP_SILENCE) && !(b->flags&WMP_FLAG_REPLAY))copy_event(b->generation,b->sequence,b->presentation_us,success?WMP_COPY_COMPLETE:WMP_COPY_TX_FAIL,b->copy,0,b->finished);
    b->state=2;++radio_stats.tx_done;if(!success)++radio_stats.tx_fail;
    wmp_coex_trace_record(WMP_RT_TX_DONE,b->copy|((b->flags&WMP_FLAG_REPLAY)?0x80u:0u)|(success?0u:0x40u),b->type,(uint32_t)b->sequence);
    if(b->flags&WMP_FLAG_REPLAY) { ++radio_stats.replay_completed;if(!success)++radio_stats.replay_failed; }
    if((b->flags&WMP_FLAG_REPLAY) && unicast) { ++radio_stats.repair_unicast_done;if(!success)++radio_stats.repair_unicast_failed; }
    if(xQueueSend(completed,&index,0)!=pdTRUE)++radio_stats.tx_callback_queue_full;
    if(transmitter_task)xTaskNotifyGive(transmitter_task);
}
static void finish_tx(const uint8_t *wire,uint32_t tag,int success,int unicast) {
    uint64_t generation=wire64(wire+16),sequence=wire64(wire+24);
    for(unsigned i=0;i<TX_BUFFERS;i++) {
        tx_buffer *b=&tx_buffers[i];
        if((b->state==1u || b->state==3u) && b->tag==tag && b->generation==generation && b->sequence==sequence && b->copy==wire[64] && b->type==wire[5] && b->flags==wire[65]) {
            finish_tx_buffer(i,success,unicast);return;
        }
    }
    ++radio_stats.tx_unmatched;
}
static void tx_callback(const esp_80211_tx_info_t *info) {
    if(!info || !info->data || radio_stats.ethernet_enabled==1u)return;
    radio_stats.tx_phy_rate=info->rate;const uint8_t *p=info->data;
    if(!((p[0]==8u && (p[1]&0xc7u)==2u)||(p[0]==0xd0u && (p[1]&0xc7u)==0u)) || memcmp(p+32,"WMP1",4)) { ++radio_stats.tx_unmatched;return; }
    if(info->rate==WIFI_PHY_RATE_54M)++raw_tx_54m;
    else if(info->rate==WIFI_PHY_RATE_MCS7_LGI)++raw_tx_mcs7;
    else ++raw_tx_other;
    uint32_t tag=p[0]==0xd0u?((uint32_t)p[29]|((uint32_t)p[30]<<8)|((uint32_t)p[31]<<16)):0u;
    finish_tx(p+32,tag,info->tx_status==WIFI_SEND_SUCCESS,!(p[4]&1u));
}
static void ethernet_tx_done(uint8_t ifidx,uint8_t *data,uint16_t *len,bool success) {
    if(!data || !len)return;
    /* A small normal WPA2 data frame keeps the admitted STA active at its
     * AP. It shares the existing transmit credits and completion ownership;
     * audio remains our four-copy raw broadcast, with no per-sink ACK. */
    unsigned maintenance=0;
    if(ifidx==WIFI_IF_STA && *len>=23u && data[12]==0x88u && data[13]==0xb6u)maintenance=14u;
    else if(ifidx==WIFI_IF_STA && *len>=32u && (data[0]==0x08u || data[0]==0x88u)) {
        unsigned h=24u+((data[1]&3u)==3u?6u:0u)+(data[0]==0x88u?2u:0u);
        if(data[0]==0x88u && (data[1]&0x80u))h+=4u;
        if(data[1]&0x40u)h+=8u;
        static const uint8_t llc[8]={0xaa,0xaa,3,0,0,0,0x88,0xb6};
        if(*len>=h+17u && !memcmp(data+h,llc,8))maintenance=h+8u;
    }
    if(maintenance && *len>=maintenance+9u && !memcmp(data+maintenance,keepalive_magic,5)) {
        uint32_t sequence=(uint32_t)data[maintenance+5u]|((uint32_t)data[maintenance+6u]<<8)|((uint32_t)data[maintenance+7u]<<16)|((uint32_t)data[maintenance+8u]<<24);
        for(unsigned i=0;i<TX_BUFFERS;i++)if(tx_buffers[i].state && tx_buffers[i].type==KEEPALIVE_TYPE && tx_buffers[i].sequence==sequence) {
            if(success)++keepalive_done;else ++keepalive_failed;
            finish_tx_buffer(i,success,1);return;
        }
        return;
    }
    if(radio_stats.ethernet_enabled!=1u)return;
    ++radio_stats.ethernet_tx_done;
    if(*len>=4u)radio_stats.ethernet_tx_prefix=(uint32_t)data[0]|((uint32_t)data[1]<<8)|((uint32_t)data[2]<<16)|((uint32_t)data[3]<<24);
    unsigned carrier=0;int unicast=0;
    if(*len>=26u && data[12]==0x88u && data[13]==0xb5u) { carrier=14u;unicast=!(data[0]&1u); }
    else if(*len>=44u && (data[0]==0x08u || data[0]==0x88u)) {
        /* ESP32 v6.1 returns the actual 802.11 data frame here, unlike the
         * Ethernet buffer supplied to internal_tx. Account for its LLC/QoS. */
        unsigned header=24u+((data[1]&3u)==3u?6u:0u)+(data[0]==0x88u?2u:0u);
        /* WPA2 STA data includes the8-byte CCMP header before LLC. The
         * controller encrypts on air; its completion buffer retains this
         * framing. Bounds below still use the reported available length. */
        if(data[0]==0x88u && (data[1]&0x80u))header+=4u;
        if(data[1]&0x40u)header+=8u;
        static const uint8_t llc[8]={0xaa,0xaa,3,0,0,0,0x88,0xb5};
        if(*len>=header+20u && !memcmp(data+header,llc,8)) {
            carrier=header+8u;
            unicast=!(data[((data[1]&3u)==1u)?16u:4u]&1u);
        }
    }
    if(!carrier || memcmp(data+carrier,control_prefix,5) || memcmp(data+carrier+8,"WMP1",4)) {
        ++radio_stats.ethernet_tx_unknown;
        portENTER_CRITICAL(&copy_lock);
        if(!native_completion_bytes) { native_completion_bytes=*len<sizeof(native_completion_sample)?*len:sizeof(native_completion_sample);memcpy(native_completion_sample,data,native_completion_bytes); }
        portEXIT_CRITICAL(&copy_lock);return;
    }
    uint32_t tag=(uint32_t)data[carrier+5]|((uint32_t)data[carrier+6]<<8)|((uint32_t)data[carrier+7]<<16);
    /* Native submission copied the bytes before returning. Only the unique
     * physical tag and immutable metadata must survive until completion. */
    for(unsigned i=0;i<TX_BUFFERS;i++)if(tx_buffers[i].state && tx_buffers[i].tag==tag) {
        finish_tx_buffer(i,success,unicast);return;
    }
    ++radio_stats.tx_unmatched;
}
void wmp_radio_completion_trace(unsigned long query) {
    uint8_t sample[80];unsigned bytes;
    portENTER_CRITICAL(&copy_lock);bytes=native_completion_bytes;memcpy(sample,native_completion_sample,sizeof(sample));portEXIT_CRITICAL(&copy_lock);
    wmp_telemetry_printf("WMP_COMPLETION query=%08lx bytes=%u prefix=",query,bytes);
    for(unsigned i=0;i<bytes;i++) { wmp_telemetry_printf("%02x",sample[i]); }
    wmp_telemetry_printf("\n");
}
static esp_err_t ethernet_rx(void *buffer,uint16_t len,void *eb) {
    const uint8_t *p=buffer;++radio_stats.ethernet_rx;
    unsigned offset=wmp_ethernet_payload_offset(p,len);
    if(offset && radio_stats.ethernet_enabled && wire64(p+offset+8)==GROUP_ID && memcmp(p+6,tx_mac,6)) {
        const uint8_t *wire=p+offset;unsigned bytes=len-offset;
        /* Raw FromDS uses our bridge SA; the associated driver already checks
         * the transmitting BSSID. Preserve that radio identity for leadership. */
        int direct=offset==14u;
        if(bytes<=WMP_HEADER_BYTES+WMP_LIVE_PAYLOAD && (!direct || (radio_stats.association_connected && !memcmp(p+6,network,6)))) {
            received item;item.bytes=(uint16_t)bytes;item.clock=esp_wifi_internal_get_mac_clock_time();item.callback_us=(uint32_t)esp_timer_get_time();item.physical_tag=direct?0u:((uint32_t)wire[-3]|((uint32_t)wire[-2]<<8)|((uint32_t)wire[-1]<<16));memcpy(item.sender,direct?associated_bssid:p+6,6);memcpy(item.wire,wire,bytes);++radio_stats.rx_callback;
            if((wire[5]==WMP_AUDIO || wire[5]==WMP_SILENCE) && !(wire[65]&WMP_FLAG_REPLAY) && wire[64]<4u)copy_event(wire64(wire+16),wire64(wire+24),wire64(wire+40),WMP_COPY_RX,wire[64],item.clock,item.callback_us);
            if(incoming && xQueueSend(incoming,&item,0)==pdTRUE)++radio_stats.ethernet_queued;else ++radio_stats.rx_queue_full;
        }
    }
    if(eb)esp_wifi_internal_free_rx_buffer(eb);
    return ESP_OK;
}
static int ethernet_mode(unsigned enabled) {
    wifi_phy_rate_t rate;int mapped=rate_value(selected_rate,&rate);if(mapped!=ESP_OK)return mapped;
    int status=ESP_OK;
    if(!WIFI_AMPDU_TX_ENABLED && !radio_stats.infrastructure_requested)status=esp_wifi_internal_set_fix_rate(tx_interface,enabled!=0u,rate);
    radio_stats.ethernet_rate_status=status;if(status!=ESP_OK)return status;
    status=esp_wifi_internal_reg_rxcb(tx_interface,enabled?ethernet_rx:NULL);
    if(status!=ESP_OK)return status;
    status=esp_wifi_set_promiscuous(!enabled);if(status!=ESP_OK)return status;
    radio_stats.ethernet_enabled=enabled;tx_window=DEFAULT_TX_WINDOW;radio_stats.tx_window=tx_window;
    if(!radio_stats.wifi_ap) {
        /* Both paths share the associated AP clock; data routing must not
         * stop the hardware TSF sampler in the infrastructure profile. */
        wmp_source_clock_enable((enabled || radio_stats.infrastructure_requested)?2u:0u);wmp_audio_prepare_peer();
    }
    return ESP_OK;
}
static int native_action_rx(uint8_t *hdr,uint8_t *payload,size_t len,uint8_t channel) {
    ++radio_stats.native_rx_callbacks;
    if(!hdr || !payload)return ESP_OK;
    if(len>=4u)radio_stats.native_rx_prefix=(uint32_t)payload[0]|((uint32_t)payload[1]<<8)|((uint32_t)payload[2]<<16)|((uint32_t)payload[3]<<24);
    if(len<8u+WMP_HEADER_BYTES || channel!=selected_channel || !radio_stats.native_rx_active || radio_stats.mode==2u ||
       memcmp(payload,control_prefix,5) || memcmp(payload+8,"WMP1",4) || wire64(payload+16)!=GROUP_ID || !memcmp(hdr+10,tx_mac,6))return ESP_OK;
    const uint8_t *wire=payload+8;unsigned bytes=WMP_HEADER_BYTES+wire[56]+((unsigned)wire[57]<<8)+((wire[65]&WMP_FLAG_CLOCK)?WMP_CLOCK_TRAILER:0u);
    if(bytes>WMP_HEADER_BYTES+WMP_LIVE_PAYLOAD || (len-8u!=bytes && len-8u!=bytes+4u))return ESP_OK;
    received item;item.bytes=(uint16_t)bytes;item.clock=esp_wifi_internal_get_mac_clock_time();item.callback_us=(uint32_t)esp_timer_get_time();item.physical_tag=(uint32_t)payload[5]|((uint32_t)payload[6]<<8)|((uint32_t)payload[7]<<16);memcpy(item.sender,hdr+10,6);memcpy(item.wire,wire,bytes);
    ++radio_stats.rx_callback;
    if((wire[5]==WMP_AUDIO || wire[5]==WMP_SILENCE) && !(wire[65]&WMP_FLAG_REPLAY) && wire[64]<4u)
        copy_event(wire64(wire+16),wire64(wire+24),wire64(wire+40),WMP_COPY_RX,wire[64],item.clock,item.callback_us);
    if(incoming && xQueueSend(incoming,&item,0)==pdTRUE)++radio_stats.native_rx_queued;else ++radio_stats.rx_queue_full;
    return ESP_OK;
}
/* Pinned v6.1 libnet80211 Action table ABI, audited in research. This is a
 * callback registration, with no SDK patch and no ESP-NOW initialization. */
typedef int (*wmp_action_handler)(void *,const uint8_t *,const uint8_t *,const uint8_t *);
extern int ieee80211_recv_action_register(unsigned category,unsigned action,wmp_action_handler handler);
extern void ieee80211_recv_action_unregister(unsigned category,unsigned action);
/* The generic PM registry retains the caller's uint16_t pointer. Keep ours
 * in static storage; this is independent of any transport implementation. */
extern esp_err_t esp_wifi_internal_configure_wake_window(bool enabled,uint16_t *window);
static uint16_t receive_wake_window=UINT16_MAX;
static unsigned direct_window_owned;
int wmp_radio_receive_interval(unsigned milliseconds) {
    if(milliseconds && (milliseconds<10u || milliseconds>1000u))return ESP_ERR_INVALID_ARG;
    if(!wifi_live)return ESP_ERR_WIFI_NOT_INIT;
    portENTER_CRITICAL(&wake_window_lock);unsigned gated=tx_wake_span;portEXIT_CRITICAL(&wake_window_lock);
    if(gated && milliseconds!=radio_stats.receive_interval_ms)return ESP_ERR_INVALID_STATE;
    int status=esp_wifi_connectionless_module_set_wake_interval((uint16_t)milliseconds);
    radio_stats.receive_interval_status=status;if(status==ESP_OK) {
        radio_stats.receive_interval_ms=milliseconds;
        portENTER_CRITICAL(&wake_window_lock);native_wake_local=native_wake_source=0;portEXIT_CRITICAL(&wake_window_lock);
    }
    return status;
}
int wmp_radio_receive_window(unsigned enabled) {
    if(enabled>1u || !wifi_live)return 0;
    portENTER_CRITICAL(&wake_window_lock);unsigned gated=tx_wake_span;portEXIT_CRITICAL(&wake_window_lock);
    if(!enabled && gated)return 0;
    if(enabled==radio_stats.receive_window_enabled)return 1;
    int status=esp_wifi_internal_configure_wake_window(enabled!=0u,&receive_wake_window);
    radio_stats.receive_window_status=status;
    if(status==ESP_OK)radio_stats.receive_window_enabled=enabled;
    return status==ESP_OK;
}
unsigned wmp_radio_receive_window_value(void) { return receive_wake_window; }
int wmp_radio_receive_window_size(unsigned milliseconds) {
    if(!milliseconds || milliseconds>UINT16_MAX)return 0;
    portENTER_CRITICAL(&wake_window_lock);unsigned gated=tx_wake_span;portEXIT_CRITICAL(&wake_window_lock);
    if(gated && gated>=milliseconds*1000u)return 0;
    uint16_t previous=receive_wake_window;unsigned active=radio_stats.receive_window_enabled;
    if(previous==milliseconds)return 1;
    if(active && !wmp_radio_receive_window(0u))return 0;
    receive_wake_window=(uint16_t)milliseconds;
    if(active && !wmp_radio_receive_window(1u)) {
        receive_wake_window=previous;wmp_radio_receive_window(1u);return 0;
    }
    return 1;
}
int wmp_radio_tx_wake(unsigned microseconds) {
    if(microseconds>10000u)return 0;
    uint64_t now=(uint64_t)esp_timer_get_time(),due=0;
    portENTER_CRITICAL(&wake_window_lock);
    int valid=!microseconds || (radio_stats.receive_window_enabled && microseconds<(unsigned)receive_wake_window*1000u &&
        wmp_wake_due(now,native_wake_local,radio_stats.receive_interval_ms*1000u,microseconds,&due));
    if(valid)tx_wake_span=microseconds;
    portEXIT_CRITICAL(&wake_window_lock);
    if(valid && transmitter_task)xTaskNotifyGive(transmitter_task);
    return valid;
}
static void connectionless_wake(void *context,esp_event_base_t base,int32_t id,void *data) {
    (void)context;(void)base;(void)id;(void)data;
    uint64_t local=(uint64_t)esp_timer_get_time(),source=wmp_source_clock();
    portENTER_CRITICAL(&wake_window_lock);
    if(native_wake_local && local>=native_wake_local) {
        uint64_t gap=local-native_wake_local;if(gap>native_wake_gap_max)native_wake_gap_max=gap>UINT32_MAX?UINT32_MAX:(uint32_t)gap;
    }
    native_wake_local=local;native_wake_source=source;++native_wake_events;portEXIT_CRITICAL(&wake_window_lock);
    if(transmitter_task)xTaskNotifyGive(transmitter_task);
}
void wmp_radio_wake_stats(unsigned long query) {
    uint64_t local,source;uint32_t events,gap,span,waits,missing;
    portENTER_CRITICAL(&wake_window_lock);local=native_wake_local;source=native_wake_source;events=native_wake_events;gap=native_wake_gap_max;span=tx_wake_span;waits=tx_wake_waits;missing=tx_wake_missing;portEXIT_CRITICAL(&wake_window_lock);
    wmp_telemetry_printf("WMP_WAKE query=%08lx events=%lu local_us=%llu source_tsf=%llu age_us=%llu gap_max_us=%lu tx_span_us=%lu waits=%lu missing=%lu interval_ms=%lu window_ms=%u\n",query,(unsigned long)events,(unsigned long long)local,(unsigned long long)source,(unsigned long long)(local?(uint64_t)esp_timer_get_time()-local:UINT64_MAX),(unsigned long)gap,(unsigned long)span,(unsigned long)waits,(unsigned long)missing,(unsigned long)radio_stats.receive_interval_ms,(unsigned)receive_wake_window);
}
static int direct_action_rx(void *node,const uint8_t *hdr,const uint8_t *body,const uint8_t *end) {
    (void)node;++radio_stats.direct_rx_callbacks;
    uintptr_t first=(uintptr_t)body,last=(uintptr_t)end;
    if(!radio_stats.direct_rx_enabled || !hdr || !body || last<first || last-first<8u+WMP_HEADER_BYTES || last-first>8u+WMP_HEADER_BYTES+WMP_LIVE_PAYLOAD+4u)return 0;
    if(hdr[0]!=0xd0u || (hdr[1]&0xc7u) || !memcmp(hdr+10,tx_mac,6) || memcmp(body,control_prefix,5) || memcmp(body+8,"WMP1",4) || wire64(body+16)!=GROUP_ID)return 0;
    const uint8_t *wire=body+8;unsigned bytes=WMP_HEADER_BYTES+wire[56]+((unsigned)wire[57]<<8)+((wire[65]&WMP_FLAG_CLOCK)?WMP_CLOCK_TRAILER:0u);
    if(bytes>WMP_HEADER_BYTES+WMP_LIVE_PAYLOAD || (last-first-8u!=bytes && last-first-8u!=bytes+4u))return 0;
    if(!radio_stats.mode && (wire[5]==WMP_AUDIO || wire[5]==WMP_SILENCE)) {
        feedback_rx_at=(uint32_t)esp_timer_get_time();if(transmitter_task)xTaskNotifyGive(transmitter_task);
    }
    received item;item.bytes=(uint16_t)bytes;item.clock=esp_wifi_internal_get_mac_clock_time();item.callback_us=(uint32_t)esp_timer_get_time();item.physical_tag=(uint32_t)body[5]|((uint32_t)body[6]<<8)|((uint32_t)body[7]<<16);memcpy(item.sender,hdr+10,6);memcpy(item.wire,wire,bytes);++radio_stats.rx_callback;
    if(wire[5]==WMP_AUDIO || wire[5]==WMP_SILENCE)wmp_coex_trace_record(WMP_RT_RX,(wire[64]&15u)|((wire[65]&WMP_FLAG_REPLAY)?0x80u:0u),(unsigned)(bytes&0xffffu),(uint32_t)wire64(wire+24));
    if(radio_stats.mode!=2u && (wire[5]==WMP_AUDIO || wire[5]==WMP_SILENCE) && !(wire[65]&WMP_FLAG_REPLAY) && wire[64]<4u)copy_event(wire64(wire+16),wire64(wire+24),wire64(wire+40),WMP_COPY_RX,wire[64],item.clock,item.callback_us);
    delivery_note(wire64(wire+16),wire64(wire+24),WMP_D_RX,wire[64],wire[65],item.physical_tag,item.callback_us,0,NULL);
    int queued=incoming && xQueueSend(incoming,&item,0)==pdTRUE;if(queued)++radio_stats.direct_rx_queued;else ++radio_stats.rx_queue_full;
    delivery_note(wire64(wire+16),wire64(wire+24),WMP_D_RX_QUEUE,wire[64],wire[65],item.physical_tag,(uint32_t)esp_timer_get_time(),queued?0:1,NULL);
    return 0;
}
int wmp_radio_direct_rx(unsigned enabled) {
    if(enabled>1u || (enabled && ((radio_stats.mode && !radio_stats.infrastructure_requested) || (radio_stats.wifi_ap && !radio_stats.network_anchor) || radio_stats.native_rx_requested || radio_stats.ethernet_enabled==2u || radio_stats.listen_requested)))return 0;
    if(enabled==radio_stats.direct_rx_enabled)return 1;
    /* This connectionless protocol must participate in the PM/coexistence
     * window registry. WIFI_PS_NONE alone does not register its receive need.
     * Register from this normal control task before accepting action frames. */
    unsigned added_window=enabled && !radio_stats.receive_window_enabled;
    if(added_window && !wmp_radio_receive_window(1u)) { radio_stats.direct_rx_status=radio_stats.receive_window_status;return 0; }
    int status=0;
    if(enabled)status=ieee80211_recv_action_register(4u,9u,direct_action_rx);
    else ieee80211_recv_action_unregister(4u,9u);
    if(status && added_window)wmp_radio_receive_window(0u);
    radio_stats.direct_rx_status=status;
    if(!status) {
        if(added_window)direct_window_owned=1u;
        if(!enabled && direct_window_owned) {
            if(!wmp_radio_receive_window(0u)) { radio_stats.direct_rx_status=radio_stats.receive_window_status;radio_stats.direct_rx_enabled=0;return 0; }
            direct_window_owned=0;
        }
        radio_stats.direct_rx_enabled=enabled;
        if(!radio_stats.infrastructure_requested)wmp_source_clock_enable((enabled || radio_stats.ethernet_enabled) && radio_stats.association_connected?2u:0u);
        if(radio_stats.mode!=2u)wmp_audio_prepare_peer();
    }
    return status==0;
}
static void native_roc_done(uint32_t context,uint8_t op_id,wifi_roc_done_status_t status) {
    (void)context;(void)op_id;(void)status;native_roc_running=0;++radio_stats.native_roc_done;
    if(transmitter_task)xTaskNotifyGive(transmitter_task);
}
static void native_receive_tick(unsigned mode,uint64_t now) {
    int wanted=!mode && radio_stats.native_rx_requested;
    if(!wanted) {
        if(native_roc_running) {
            wifi_roc_req_t cancel={.ifx=tx_interface,.type=WIFI_ROC_CANCEL};
            radio_stats.native_roc_status=esp_wifi_remain_on_channel(&cancel);native_roc_running=0;
        }
        radio_stats.native_rx_active=0;return;
    }
    if(native_roc_running || now<native_roc_retry_at)return;
    wifi_roc_req_t req={.ifx=tx_interface,.type=WIFI_ROC_REQ,.channel=(uint8_t)selected_channel,.sec_channel=WIFI_SECOND_CHAN_NONE,.wait_time_ms=1000,.rx_cb=native_action_rx,.done_cb=native_roc_done,.allow_broadcast=true};
    ++radio_stats.native_roc_calls;native_roc_running=1;
    int result=esp_wifi_remain_on_channel(&req);radio_stats.native_roc_status=result;
    if(result==ESP_OK)radio_stats.native_rx_active=1;
    else { native_roc_running=0;radio_stats.native_rx_active=0;++radio_stats.native_roc_failed;native_roc_retry_at=now+1000000u; }
}
static void association_tick(uint64_t now) {
    static uint64_t next;
    if(now<next)return;
    next=now+1000000u;
    if(radio_stats.network_anchor) {
        /* AP ownership supplies the clock; it is not a station association.
         * Audio leadership can change without changing this Wi-Fi role. */
        radio_stats.association_connected=0;
        if(radio_stats.anchor_active && !infrastructure_clock) { wmp_source_clock_enable(3u);infrastructure_clock=1; }
        else if(!radio_stats.anchor_active && infrastructure_clock) { wmp_source_clock_enable(0);infrastructure_clock=0; }
        return;
    }
    if(!radio_stats.association_requested || radio_stats.wifi_ap) { radio_stats.association_connected=0;return; }
    wifi_ap_record_t ap;
    int info=esp_wifi_sta_get_ap_info(&ap);
    radio_stats.association_connected=infrastructure_link_up && info==ESP_OK;
    if(radio_stats.infrastructure_requested) {
        if(radio_stats.association_connected) {
            selected_channel=ap.primary;radio_stats.channel=selected_channel;
            if(!infrastructure_clock) { wmp_source_clock_enable(2u);infrastructure_clock=1; }
        } else if(infrastructure_clock) { wmp_source_clock_enable(0);infrastructure_clock=0; }
        if(!radio_stats.association_connected && infrastructure_admitted && !infrastructure_pending && !reconnect_pending && (int32_t)((uint32_t)now-reconnect_due)>=0) {
            /* Only a previously admitted, terminally disconnected AP is
             * retried. Its remembered BSSID/channel avoid broad scans. One
             * asynchronous attempt stays pending until a Wi-Fi event. */
            reconnect_pending=1;++reconnect_attempts;
            reconnect_status=esp_wifi_set_config(WIFI_IF_STA,&infrastructure_config);
            if(reconnect_status==ESP_OK)reconnect_status=esp_wifi_connect();
            if(reconnect_status!=ESP_OK) { reconnect_due=(uint32_t)now+reconnect_delay;reconnect_pending=0; }
            if(reconnect_delay<30000000u) { reconnect_delay*=2u;if(reconnect_delay>30000000u)reconnect_delay=30000000u; }
        }
    }
}
static void station_keepalive(uint64_t now) {
    if(radio_stats.network_anchor || !radio_stats.infrastructure_requested || !radio_stats.association_connected || (keepalive_next && (int32_t)((uint32_t)now-keepalive_next)<0) || tx_window<2u)return;
    for(unsigned i=0;i<TX_BUFFERS;i++)if(tx_buffers[i].state && tx_buffers[i].type==KEEPALIVE_TYPE)return;
    unsigned index=TX_BUFFERS;
    for(unsigned i=0;i<tx_window-1u;i++)if(!tx_buffers[i].state) { index=i;break; }
    if(index==TX_BUFFERS)return;
    uint8_t *frame=tx_scratch;memset(frame,0,60u);memcpy(frame,associated_bssid,6);memcpy(frame+6,tx_mac,6);
    frame[12]=0x88;frame[13]=0xb6;memcpy(frame+14,keepalive_magic,5);
    uint32_t sequence=++keepalive_sequence;for(unsigned i=0;i<4;i++)frame[19u+i]=(uint8_t)(sequence>>(8u*i));
    tx_buffer *owned=&tx_buffers[index];memset(owned,0,sizeof(*owned));owned->type=KEEPALIVE_TYPE;owned->sequence=sequence;owned->submitted=(uint32_t)now;
    __asm__ __volatile__("memw":::"memory");owned->state=1;
    ++radio_stats.tx_attempt;int result=esp_wifi_internal_tx(WIFI_IF_STA,frame,60u);
    if(result==ESP_OK) { ++keepalive_sent;++radio_stats.tx_accepted; }
    else { owned->state=0;++keepalive_failed;++radio_stats.tx_api_error; }
    keepalive_next=(uint32_t)now+10000000u;
}
void wmp_radio_network_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_ANCHOR_STAT query=%08lx requested=%lu active=%lu peers=%lu clock_ready=%u\n",query,(unsigned long)radio_stats.network_anchor,(unsigned long)radio_stats.anchor_active,(unsigned long)radio_stats.anchor_peers,(unsigned)wmp_radio_clock_ready());
    wmp_telemetry_printf("WMP_NETWORK_LIVENESS query=%08lx admitted=%u reconnect_pending=%u reconnect_attempts=%lu reconnect_successes=%lu reconnect_status=%d keepalive_sent=%lu keepalive_done=%lu keepalive_failed=%lu keepalive_interval_ms=10000\n",query,infrastructure_admitted,reconnect_pending,(unsigned long)reconnect_attempts,(unsigned long)reconnect_successes,reconnect_status,(unsigned long)keepalive_sent,(unsigned long)keepalive_done,(unsigned long)keepalive_failed);
}
int wmp_radio_network_drop(void) {
    if(!wifi_live || !radio_stats.infrastructure_requested || !radio_stats.association_connected || !infrastructure_admitted || reconnect_pending)return ESP_ERR_INVALID_STATE;
    return esp_wifi_disconnect();
}
static void association_event(void *context,esp_event_base_t base,int32_t id,void *data) {
    (void)context;(void)base;
    if(id==WIFI_EVENT_AP_START) { if(radio_stats.network_anchor)radio_stats.anchor_active=1;return; }
    if(id==WIFI_EVENT_AP_STOP) { radio_stats.anchor_active=0;radio_stats.anchor_peers=0;if(radio_stats.network_anchor)wmp_source_clock_invalidate();return; }
    if(id==WIFI_EVENT_AP_STACONNECTED) { if(radio_stats.network_anchor)++radio_stats.anchor_peers;return; }
    if(id==WIFI_EVENT_AP_STADISCONNECTED) { if(radio_stats.anchor_peers)--radio_stats.anchor_peers;return; }
    if(id==WIFI_EVENT_STA_CONNECTED) {
        wmp_source_clock_invalidate();wmp_audio_prepare_peer();
        const wifi_event_sta_connected_t *event=data;
        if(event) {
            memcpy(associated_bssid,event->bssid,6);
            if(radio_stats.infrastructure_requested) { memcpy(infrastructure_config.sta.bssid,event->bssid,6);infrastructure_config.sta.bssid_set=true;infrastructure_config.sta.channel=event->channel; }
        }
        if(reconnect_pending)++reconnect_successes;
        infrastructure_admitted=radio_stats.infrastructure_requested;reconnect_pending=0;reconnect_due=0;reconnect_delay=1000000u;keepalive_next=0;
        infrastructure_link_up=1;
        ++radio_stats.association_connect_events;radio_stats.association_connected=1;
    } else if(id==WIFI_EVENT_STA_DISCONNECTED) {
        wmp_source_clock_invalidate();wmp_audio_prepare_peer();
        const wifi_event_sta_disconnected_t *event=data;
        infrastructure_link_up=0;++radio_stats.association_disconnect_events;radio_stats.association_connected=0;
        radio_stats.association_disconnect_reason=event?event->reason:0;
        reconnect_due=(uint32_t)esp_timer_get_time()+reconnect_delay;reconnect_pending=0;
    }
}
static void listen_scan_done(void *context,esp_event_base_t base,int32_t id,void *data) {
    (void)context;(void)base;(void)id;
    if(!listen_running && !listen_stopping)return;
    wifi_event_sta_scan_done_t *event=data;
    ++radio_stats.listen_done;if(event && event->status)++radio_stats.listen_errors;
    esp_wifi_clear_ap_list();listen_next_at=(uint32_t)esp_timer_get_time()+20000u;
    listen_running=listen_stopping=radio_stats.listen_active=0;
    if(transmitter_task)xTaskNotifyGive(transmitter_task);
}
static void listen_tick(unsigned mode,uint64_t now) {
    int wanted=!mode && !radio_stats.wifi_ap && radio_stats.listen_requested && !radio_stats.native_rx_requested && !radio_stats.ethernet_enabled;
    if(!wanted) {
        if(listen_running && !listen_stopping) {
            int result=esp_wifi_scan_stop();radio_stats.listen_status=result;
            if(result==ESP_OK)listen_stopping=1;else listen_running=radio_stats.listen_active=0;
        }
        return;
    }
    if(listen_running || listen_stopping || (listen_next_at && (int32_t)((uint32_t)now-listen_next_at)<0))return;
    uint8_t valid;portENTER_CRITICAL(&feedback_lock);valid=leader_valid;memcpy(listen_bssid,leader_mac,6);portEXIT_CRITICAL(&feedback_lock);
    if(!valid)return;
    wifi_scan_config_t cfg={.bssid=listen_bssid,.channel=(uint8_t)selected_channel,.show_hidden=false,.scan_type=WIFI_SCAN_TYPE_PASSIVE};cfg.scan_time.passive=80;
    listen_running=radio_stats.listen_active=1;++radio_stats.listen_starts;
    int result=esp_wifi_scan_start(&cfg,false);radio_stats.listen_status=result;
    if(result!=ESP_OK) { ++radio_stats.listen_errors;listen_running=radio_stats.listen_active=0;listen_next_at=(uint32_t)now+1000000u; }
}
static int associate_leader(unsigned enabled) {
    /* Bounded two-board diagnostic only. No IP/netif allocation, scanning
     * arbitrary networks, automatic reconnection, or 100-node claim. */
    if(radio_stats.wifi_ap || radio_stats.mode || radio_stats.native_rx_requested)return ESP_ERR_INVALID_STATE;
    wmp_audio_prepare_peer();radio_stats.association_requested=enabled;radio_stats.association_connected=0;infrastructure_link_up=0;
    if(!enabled)return esp_wifi_disconnect();
    uint8_t mac[6],valid;
    portENTER_CRITICAL(&feedback_lock);memcpy(mac,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
    if(!valid) { radio_stats.association_requested=0;return ESP_ERR_INVALID_STATE; }
    wifi_config_t cfg={0};memcpy(cfg.sta.bssid,mac,6);cfg.sta.bssid_set=true;cfg.sta.channel=(uint8_t)selected_channel;
    for(int i=5;i>=0;i--)if(mac[i]--!=0u)break;
    snprintf((char *)cfg.sta.ssid,sizeof(cfg.sta.ssid),"WMP-SYNC-%02X%02X",mac[4],mac[5]);
    cfg.sta.threshold.authmode=WIFI_AUTH_OPEN;cfg.sta.scan_method=WIFI_FAST_SCAN;cfg.sta.listen_interval=1;
    ++radio_stats.association_attempts;
    int status=esp_wifi_set_config(WIFI_IF_STA,&cfg);
    return status==ESP_OK?esp_wifi_connect():status;
}
static int apply_network(unsigned enabled) {
    if(radio_stats.mode)return ESP_ERR_INVALID_STATE;
    if(radio_stats.ethernet_enabled)ethernet_mode(0);
    if(radio_stats.direct_rx_enabled)wmp_radio_direct_rx(0);
    infrastructure_admitted=reconnect_pending=0;reconnect_due=0;keepalive_next=0;
    wmp_audio_prepare_peer();wmp_source_clock_enable(0);infrastructure_clock=infrastructure_link_up=0;
    radio_stats.network_anchor=enabled==2u;radio_stats.anchor_active=radio_stats.anchor_peers=0;
    radio_stats.infrastructure_requested=enabled!=0u;radio_stats.association_requested=enabled==1u;radio_stats.association_connected=0;
    esp_wifi_disconnect();
    int status=esp_wifi_set_promiscuous(false);if(status!=ESP_OK)return status;
    status=stop_wifi();if(status!=ESP_OK)return status;
    status=esp_wifi_set_mode(enabled==1u?WIFI_MODE_STA:WIFI_MODE_AP);if(status!=ESP_OK)return status;
    if(enabled==2u) {
        tx_interface=WIFI_IF_AP;radio_stats.wifi_ap=1;radio_stats.beacon_tu=100;
        status=esp_wifi_get_mac(WIFI_IF_AP,tx_mac);if(status!=ESP_OK)return status;
        status=esp_wifi_set_config(WIFI_IF_AP,&anchor_config);if(status!=ESP_OK)return status;
        status=configure_rate_before_start(WIFI_IF_AP,selected_rate);if(status!=ESP_OK)return status;
        status=start_wifi();if(status!=ESP_OK)return status;
        status=esp_wifi_set_ps(WIFI_PS_NONE);if(status!=ESP_OK)return status;
        return ESP_OK;
    }
    if(enabled==1u) {
        tx_interface=WIFI_IF_STA;radio_stats.wifi_ap=0;radio_stats.beacon_tu=0;
        status=esp_wifi_get_mac(WIFI_IF_STA,tx_mac);if(status!=ESP_OK)return status;
        status=esp_wifi_set_config(WIFI_IF_STA,&infrastructure_config);if(status!=ESP_OK)return status;
        status=configure_rate_before_start(WIFI_IF_STA,selected_rate);if(status!=ESP_OK)return status;
        status=start_wifi();if(status!=ESP_OK)return status;
        status=esp_wifi_set_ps(WIFI_PS_NONE);if(status!=ESP_OK)return status;
        ++radio_stats.association_attempts;return esp_wifi_connect();
    }
    tx_interface=WIFI_IF_AP;radio_stats.wifi_ap=1;configure_ap(receiver_beacon_tu);
    memset(&infrastructure_config,0,sizeof(infrastructure_config));
    status=esp_wifi_get_mac(WIFI_IF_AP,tx_mac);if(status!=ESP_OK)return status;
    status=configure_rate_before_start(WIFI_IF_AP,selected_rate);if(status!=ESP_OK)return status;
    status=start_wifi();if(status!=ESP_OK)return status;
    status=esp_wifi_set_ps(WIFI_PS_NONE);if(status!=ESP_OK)return status;
    return esp_wifi_set_promiscuous(true);
}
static void rx_callback(void *buffer,wifi_promiscuous_pkt_type_t type) {
    if(!buffer)return;
    wifi_promiscuous_pkt_t *packet=buffer;const uint8_t *p=packet->payload;
    unsigned length=packet->rx_ctrl.sig_len;
    if(length>=48u && !memcmp(p+32,"WMP1",4)) {
        if(!packet->rx_ctrl.rx_state && memcmp(p+10,tx_mac,6) && wire64(p+40)==GROUP_ID && (p[37]==WMP_AUDIO || p[37]==WMP_SILENCE || p[37]==WMP_ANNOUNCE))
            last_party_rx_us=(uint32_t)esp_timer_get_time();
    } else { dwell_airtime_us+=frame_airtime_us(&packet->rx_ctrl,length);++dwell_frames; }
    if(radio_stats.ethernet_enabled && type==WIFI_PKT_DATA && length>=24u) {
        uint8_t master[6],valid;portENTER_CRITICAL(&feedback_lock);memcpy(master,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
        if(valid && !packet->rx_ctrl.rx_state && (!memcmp(p+10,master,6) ||
           (radio_stats.infrastructure_requested && !memcmp(p+10,associated_bssid,6) && !memcmp(p+16,master,6)))) {
            ++radio_stats.ethernet_sniff_frames;radio_stats.ethernet_sniff_rate=packet->rx_ctrl.rate;radio_stats.ethernet_sniff_rssi=packet->rx_ctrl.rssi;
        }
        return; /* measurement only; normal Ethernet callback owns handoff */
    }
    if(length>=40u && p[0]==0x80u) {
        ++radio_stats.beacon_seen;radio_stats.beacon_rate=packet->rx_ctrl.rate;radio_stats.beacon_last_at=(uint32_t)esp_timer_get_time();
        radio_stats.beacon_last_hi=((uint32_t)p[10]<<24)|((uint32_t)p[11]<<16)|((uint32_t)p[12]<<8)|p[13];radio_stats.beacon_last_lo=((uint32_t)p[14]<<8)|p[15];
        uint8_t master[6],valid;portENTER_CRITICAL(&feedback_lock);memcpy(master,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
        int matches=valid && radio_stats.mode!=2u && !memcmp(p+10,master,6);
        if(packet->rx_ctrl.rx_state) { ++radio_stats.beacon_bad;if(matches)++radio_stats.beacon_matched_bad;return; }
        if(matches) {
            ++radio_stats.beacon_matched;radio_stats.beacon_match_rate=packet->rx_ctrl.rate;radio_stats.beacon_match_at=(uint32_t)esp_timer_get_time();
            if(wmp_audio_beacon(wire64(p+24),packet->rx_ctrl.timestamp))++radio_stats.beacon_queued;
        }
        return;
    }
    if(radio_stats.direct_rx_enabled && type==WIFI_PKT_MGMT && length>=32u+WMP_HEADER_BYTES+4u && p[0]==0xd0u &&
       !memcmp(p+24,control_prefix,5) && !memcmp(p+32,"WMP1",4) && wire64(p+40)==GROUP_ID && memcmp(p+10,tx_mac,6)) {
        if(packet->rx_ctrl.rx_state)++radio_stats.direct_air_bad;
        else {
            int32_t rssi=packet->rx_ctrl.rssi;
            if(!radio_stats.direct_air_frames)air_rssi_min=air_rssi_max=rssi;
            else { if(rssi<air_rssi_min)air_rssi_min=rssi;if(rssi>air_rssi_max)air_rssi_max=rssi; }
            if(!packet->rx_ctrl.sig_mode)++air_nonht;
            else if(packet->rx_ctrl.sig_mode==1u && packet->rx_ctrl.mcs==7u && !packet->rx_ctrl.sgi) { if(packet->rx_ctrl.cwb)++air_ht40_mcs7;else ++air_ht20_mcs7; }
            else ++air_other;
            unsigned sequence=((unsigned)p[22]|((unsigned)p[23]<<8))>>4;
            if(radio_stats.direct_air_frames && radio_stats.direct_air_last_sequence==sequence)++radio_stats.direct_air_same_sequence;
            radio_stats.direct_air_last_sequence=sequence;++radio_stats.direct_air_frames;
        }
    }
    if(packet->rx_ctrl.rx_state && length>=24u) {
        ++rx_fcs_any;uint8_t master[6],valid;portENTER_CRITICAL(&feedback_lock);memcpy(master,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
        if(valid && radio_stats.mode!=2u && !memcmp(p+10,master,6)) { ++rx_fcs_leader;wmp_coex_trace_record(WMP_RT_RX_BAD,packet->rx_ctrl.rx_state&255u,(unsigned)(length&0xffffu),packet->rx_ctrl.timestamp); }
        return;
    }
    if(length<24u+8u+WMP_HEADER_BYTES+4u || length>24u+8u+WMP_HEADER_BYTES+WMP_LIVE_PAYLOAD+4u ||
       !memcmp(p+10,tx_mac,6))return;
    static const uint8_t llc[8]={0xaa,0xaa,3,0,0,0,0x88,0xb5};
    /* Retry, Power Management and More Data are legal MAC flags, not an
     * application packet format change. Protected/fragmented/ordered frames
     * remain excluded. Count flags so a filtering hypothesis is measurable. */
    int data=type==WIFI_PKT_DATA && p[0]==8u && (p[1]&0xc7u)==2u && !memcmp(p+16,network,6) && !memcmp(p+24,llc,8);
    /* Last3 carrier bytes identify the physical TX transaction. They do not
     * change the logical audio identity or its CRC-protected SBC payload. */
    int control=type==WIFI_PKT_MGMT && p[0]==0xd0u && (p[1]&0xc7u)==0u && !memcmp(p+24,control_prefix,5);
    if((!data && !control) || memcmp(p+32,"WMP1",4) || wire64(p+40)!=GROUP_ID)return;
    if((radio_stats.native_rx_active || radio_stats.direct_rx_enabled) && radio_stats.mode!=2u)return; /* native beacons above retain hardware clock capture */
    ++radio_stats.rx_callback;
    if(p[1]&0x38u)++radio_stats.rx_native_flags;
    radio_stats.rx_phy_rate=packet->rx_ctrl.rate;radio_stats.rx_rssi=packet->rx_ctrl.rssi;
    radio_stats.noise_floor=packet->rx_ctrl.noise_floor;
    if(packet->rx_ctrl.rx_state) { ++radio_stats.rx_rejected;return; }
    if(p[37]==WMP_SYNC_REPLY)wmp_coex_trace_record(WMP_RT_RX,0x20u|(p[96]&15u),((unsigned)packet->rx_ctrl.rssi&255u)|(((unsigned)p[32u+WMP_HEADER_BYTES]&255u)<<8),(uint32_t)wire64(p+56));
    if(p[37]==WMP_AUDIO || p[37]==WMP_SILENCE)wmp_coex_trace_record(WMP_RT_RX,(p[96]&15u)|((p[97]&WMP_FLAG_REPLAY)?0x80u:0u)|0x40u,((unsigned)packet->rx_ctrl.rssi&255u)|(((unsigned)packet->rx_ctrl.noise_floor&255u)<<8),(uint32_t)wire64(p+56));
    if(radio_stats.mode!=2u && (p[37]==WMP_AUDIO || p[37]==WMP_SILENCE) && !(p[97]&WMP_FLAG_REPLAY) && !memcmp(p+32,"WMP1",4) && p[96]<4u)
        copy_event(wire64(p+48),wire64(p+56),wire64(p+72),WMP_COPY_RX,p[96],packet->rx_ctrl.timestamp,(uint32_t)esp_timer_get_time());
    received item;item.bytes=(uint16_t)(length-36u);item.clock=packet->rx_ctrl.timestamp;item.callback_us=(uint32_t)esp_timer_get_time();item.physical_tag=p[0]==0xd0u?((uint32_t)p[29]|((uint32_t)p[30]<<8)|((uint32_t)p[31]<<16)):0u;memcpy(item.sender,p+10,6);
    uint32_t now=esp_wifi_internal_get_mac_clock_time(),delay=now-item.clock;
    radio_stats.clock_sample=item.clock;radio_stats.clock_callback=now;
    if(delay<0x80000000u && delay>radio_stats.rx_callback_delay_max_us)radio_stats.rx_callback_delay_max_us=delay;
    memcpy(item.wire,p+32,item.bytes);
    if(!incoming || xQueueSend(incoming,&item,0)!=pdTRUE)++radio_stats.rx_queue_full;
    else { UBaseType_t depth=uxQueueMessagesWaiting(incoming);if(depth>rx_queue_peak)rx_queue_peak=(uint32_t)depth; }
}
/* Heap held by the party transport, for RAM work (STACKS console command). */
void wmp_radio_ram(void) {
    wmp_telemetry_printf(" rx_queue=%u(peak %lu of %u) source_queue=%u slots=%u nack=%u replay=%u tx_frames=%u",
        (unsigned)(incoming?RX_QUEUE_DEPTH*sizeof(received):0u),(unsigned long)rx_queue_peak,(unsigned)RX_QUEUE_DEPTH,(unsigned)(source?SOURCE_QUEUE_DEPTH*sizeof(message):0u),
        (unsigned)radio_stats.slot_resource_bytes,(unsigned)(missing_audio?sizeof(*missing_audio):0u),(unsigned)(replay_buffer?sizeof(*replay_buffer):0u),(unsigned)sizeof(tx_buffers));
}
static void receiver(void *unused) {
    (void)unused;received item;wmp_dedup dedup={0},delivered={0};uint64_t generation=0;uint32_t last_clock=0;int64_t last_audio=0;wmp_peer_lease peer_lease={0};
    uint64_t owner_start=0;uint8_t owner_shared=0;
    uint32_t gap_serial=0;
    for(;;) {
        if(!incoming || incoming_retire) { incoming_parked=1;radio_stats.rx_active=0;radio_stats.rx_claim=0;vTaskDelay(pdMS_TO_TICKS(20));continue; }
        incoming_parked=0;
        if(gap_serial!=gap_reset_serial) { gap_serial=gap_reset_serial;last_clock=0;radio_stats.rx_max_gap_us=0; }
        int64_t now=esp_timer_get_time();
        uint32_t beacon_at=radio_stats.beacon_match_at,beacon_age=(uint32_t)now-beacon_at;
        if(beacon_at && beacon_age<1500000u)wmp_peer_beacon(&peer_lease,(uint64_t)now-beacon_age);
        radio_stats.rx_active=last_audio && now-last_audio<500000;radio_stats.rx_claim=wmp_peer_claimed(&peer_lease,(uint64_t)now);
        if(replay_enabled && radio_stats.mode!=2u && audio_stats.clock_valid && audio_stats.running) {
            uint64_t sequence,epoch=0;int reserved=0;
            portENTER_CRITICAL(&nack_lock);
            if(wmp_nack_plan(missing_audio,(uint64_t)now,&sequence)) {
                epoch=missing_audio->generation;reserved=wmp_nack_reserve(missing_audio,epoch,sequence,(uint64_t)now);if(reserved)++nack_reservations;
            }
            portEXIT_CRITICAL(&nack_lock);
            if(reserved) {
                nack_request r={.generation=epoch,.sequence=sequence,.rate=leader_rate,.samples=leader_samples};
                if(r.rate && r.samples && xQueueSend(nacks,&r,0)==pdTRUE) {
                    ++radio_stats.nack_queued;
                    if(transmitter_task)xTaskNotifyGive(transmitter_task);
                } else finish_nack(epoch,sequence,(uint64_t)now,0,0u);
            }
        }
        if(xQueueReceive(incoming,&item,pdMS_TO_TICKS(20))!=pdTRUE)continue;
        now=esp_timer_get_time();
        uint32_t dispatch=(uint32_t)esp_timer_get_time()-item.callback_us;
        if(dispatch>radio_stats.rx_dispatch_max_us)radio_stats.rx_dispatch_max_us=dispatch;
        if(dispatch>1000u)++radio_stats.rx_dispatch_over1;
        if(dispatch>5000u)++radio_stats.rx_dispatch_over5;
        if(dispatch>10000u)++radio_stats.rx_dispatch_over10;
        if(dispatch>20000u)++radio_stats.rx_dispatch_over20;
        wmp_header h;const uint8_t *payload;
        if(!wmp_decode(item.wire,item.bytes,&h,&payload) || h.group!=GROUP_ID || h.fragment_count!=1u) { delivery_note(wire64(item.wire+16),wire64(item.wire+24),WMP_D_VALID,item.wire[64],item.wire[65],item.physical_tag,(uint32_t)esp_timer_get_time(),1,NULL);++radio_stats.rx_bad;continue; }
        delivery_note(h.generation,h.sequence,WMP_D_VALID,h.copy_index,h.flags,item.physical_tag,(uint32_t)esp_timer_get_time(),0,&h);
        if(delivery_focus) {
            /* Capture control classification and the exact admission state.
             * Only control-message signature bytes are retained, never SBC. */
            uint32_t state=(h.type&15u)|((radio_stats.mode&15u)<<4)|((replay_enabled!=0u)<<8)|((radio_stats.transition_pending!=0u)<<9)|((h.payload_bytes<15u?h.payload_bytes:15u)<<26);
            if(h.type==WMP_SYNC_REPLY && h.payload_bytes>=2u)state|=((uint32_t)payload[0]<<10)|((uint32_t)payload[1]<<18);
            wmp_radio_delivery(&h,WMP_D_CONTROL,(int32_t)state);
        }
        if(h.type==WMP_SYNC_REPLY && h.payload_bytes==8u && payload[0]==2u && payload[1]==1u) {
            ++radio_stats.nack_heard;
            if(replay_enabled && radio_stats.mode==2u) {
                uint64_t own;portENTER_CRITICAL(&publisher_lock);own=publisher_generation;portEXIT_CRITICAL(&publisher_lock);
                if(wmp_repair_request(&h,payload,GROUP_ID,own))replay(&h,item.sender);
                else wmp_radio_delivery(&h,WMP_D_REQUEST,8);
            } else if(h.generation==generation) {
                portENTER_CRITICAL(&nack_lock);wmp_nack_heard(missing_audio,h.generation,h.sequence,(uint64_t)now);portEXIT_CRITICAL(&nack_lock);
                suppression seen={.generation=h.generation,.sequence=h.sequence,.until=(uint64_t)now+40000u};
                portENTER_CRITICAL(&suppression_lock);heard_nacks[h.sequence&7u]=seen;portEXIT_CRITICAL(&suppression_lock);
            }
            continue;
        }
        if(h.type==WMP_SYNC_REPLY && h.payload_bytes==8u && payload[0]==3u) {
            if(radio_stats.mode==2u) {
                uint64_t own;portENTER_CRITICAL(&publisher_lock);own=publisher_generation;portEXIT_CRITICAL(&publisher_lock);
                if(h.generation==own)presence_note(item.sender,(uint64_t)now);
            }
            continue;
        }
        if(h.type==WMP_SYNC_REPLY && radio_stats.mode==2u) {
            uint64_t own;portENTER_CRITICAL(&publisher_lock);own=publisher_generation;portEXIT_CRITICAL(&publisher_lock);
            if(h.generation==own && h.payload_bytes==8u && payload[0]==1u && h.sample_count && h.sample_count<=WMP_MAX_GROUP_SAMPLES && h.sample_rate==bt_stats.rate) {
                unsigned late=payload[2]|((unsigned)payload[3]<<8);if(late>32u)late=32u;
                portENTER_CRITICAL(&publisher_lock);if(late>pending_late_frames)pending_late_frames=late;portEXIT_CRITICAL(&publisher_lock);
                ++radio_stats.qos_received;
            }
            continue;
        }
        if(h.type==WMP_ANNOUNCE) {
            if(h.payload_bytes!=7u && h.payload_bytes!=16u && h.payload_bytes!=24u && h.payload_bytes!=ANNOUNCE_CLOCK_BYTES && h.payload_bytes!=ANNOUNCE_BYTES)continue;
            uint64_t peer=0;for(unsigned j=0;j<6u;j++)peer=(peer<<8)|payload[1u+j];
            /* 16-byte form adds flags (bit0: start is on a shared clock) and
             * the broadcaster's leadership start; 7-byte peers compare by MAC. */
            uint64_t start=0;int shared=0;
            uint64_t stamp=0;
            if(h.payload_bytes>=16u) { shared=payload[7]&ANNOUNCE_FLAG_SHARED;for(unsigned j=0;j<8u;j++)start|=(uint64_t)payload[8u+j]<<(8u*j); }
            uint32_t peer_age=0,peer_period=0,peer_len=0;
            if(h.payload_bytes>=ANNOUNCE_CLOCK_BYTES) {
                for(unsigned j=0;j<4u;j++)peer_age|=(uint32_t)payload[24u+j]<<(8u*j);
                peer_period=((uint32_t)payload[28]|((uint32_t)payload[29]<<8))*10u;peer_len=((uint32_t)payload[30]|((uint32_t)payload[31]<<8))*10u;
            }
            if(h.payload_bytes>=24u) {
                for(unsigned j=0;j<8u;j++)stamp|=(uint64_t)payload[16u+j]<<(8u*j);
                /* Independent clocks are compared by broadcast age: the
                 * sender's start becomes a start on our own esp_timer. The
                 * one-way delivery delay (well under the 2ms tie) is ignored. */
                if((payload[7]&ANNOUNCE_FLAG_LOCAL) && own_clock_local && start && stamp>=start) {
                    uint64_t age=stamp-start,local=(uint64_t)esp_timer_get_time();start=local>age?local-age:1u;shared=1;
                }
            }
            if(!wmp_peer_announce_at(&peer_lease,peer,payload[0]==1u,(uint64_t)esp_timer_get_time(),start,owner_start,shared && owner_shared))continue;
            if(peer==peer_lease.peer) { owner_start=start;owner_shared=(uint8_t)shared; }
            portENTER_CRITICAL(&feedback_lock);peer_leader_start=start;peer_leader_shared=(uint8_t)shared;portEXIT_CRITICAL(&feedback_lock);
            if(generation!=h.generation) {
                portENTER_CRITICAL(&nack_lock);wmp_nack_reset(missing_audio);portEXIT_CRITICAL(&nack_lock);
                generation=h.generation;wmp_dedup_bind(&dedup,h.group,h.generation);wmp_dedup_bind(&delivered,h.group,h.generation);last_clock=0;
                radio_stats.rx_unique=radio_stats.rx_duplicate=radio_stats.rx_conflict=radio_stats.rx_stale=radio_stats.rx_bytes=radio_stats.rx_sbc_frames=0;
                radio_stats.rx_first=radio_stats.rx_highest=radio_stats.rx_max_gap_us=0;++radio_stats.rx_sessions;
                portENTER_CRITICAL(&feedback_lock);memcpy(leader_mac,item.sender,6);leader_valid=1;leader_generation=h.generation;portEXIT_CRITICAL(&feedback_lock);radio_stats.beacon_match_at=0;
                if(radio_stats.mode!=2u) { if(payload[0]==1u)wmp_audio_prepare_stream();else wmp_audio_prepare_peer(); }
            }
            if(stamp && radio_stats.mode!=2u) {
                uint8_t master[6],valid;portENTER_CRITICAL(&feedback_lock);memcpy(master,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
                int clock_ok=h.payload_bytes<ANNOUNCE_CLOCK_BYTES || !(payload[7]&ANNOUNCE_FLAG_LOCAL) || (payload[7]&ANNOUNCE_FLAG_QUIET);
                if(valid && !memcmp(item.sender,master,6) && h.generation==generation && !clock_ok)++announce_busy_skipped;
                int superseded=clock_copy_mode>=2u && last_audio_clock_us && (uint32_t)esp_timer_get_time()-last_audio_clock_us<500000u;
                if(valid && !memcmp(item.sender,master,6) && h.generation==generation && clock_ok && superseded) { ++announce_superseded;radio_stats.beacon_match_at=(uint32_t)esp_timer_get_time(); }
                if(valid && !memcmp(item.sender,master,6) && h.generation==generation && clock_ok && !superseded) {
                    ++radio_stats.beacon_matched;radio_stats.beacon_match_at=(uint32_t)esp_timer_get_time();++announce_clock_fed;
                    if(peer_period && peer_len) { uint64_t local=(uint64_t)esp_timer_get_time();peer_window_start=local>peer_age?local-peer_age:0;peer_window_period=peer_period;peer_window_len=peer_len;peer_window_at=local; }
                    if(wmp_audio_beacon(stamp,item.clock))++radio_stats.beacon_queued;
                }
            }
            if(radio_stats.mode!=2u && h.payload_bytes==ANNOUNCE_BYTES && payload[32]==1u) {
                uint8_t master[6],valid;portENTER_CRITICAL(&feedback_lock);memcpy(master,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
                if(valid && !memcmp(item.sender,master,6) && h.generation==generation)wmp_fx_party(payload+33);
            }
            radio_stats.rx_claim=wmp_peer_claimed(&peer_lease,(uint64_t)esp_timer_get_time());
            radio_stats.peer_id_hi=(uint32_t)(peer>>16);radio_stats.peer_id_lo=(uint32_t)(peer&65535u);
            if(payload[0]!=1u) { last_audio=0;radio_stats.rx_active=0; }
            continue;
        }
        if(h.generation!=generation || (h.type!=WMP_AUDIO && h.type!=WMP_SILENCE && h.type!=WMP_PROBE)) { ++radio_stats.rx_stale;continue; }
        if(h.type==WMP_AUDIO || h.type==WMP_SILENCE) {
            const uint8_t *frame;uint16_t bytes;
            if(h.type==WMP_AUDIO && (!h.frame_count || h.frame_count>WMP_MAX_GROUP_FRAMES || !wmp_group_frame_codec(h.codec,payload,h.payload_bytes,h.frame_count,0,&frame,&bytes))) { ++radio_stats.rx_bad;continue; }
            portENTER_CRITICAL(&feedback_lock);leader_rate=h.sample_rate;leader_samples=h.sample_count;portEXIT_CRITICAL(&feedback_lock);
            if((h.flags&WMP_FLAG_CLOCK) && radio_stats.mode!=2u) { wmp_audio_party_size(h.party);wmp_audio_party_volume(wmp_sync_volume_decode(h.sync_flags)); }
            if(h.type==WMP_AUDIO && (h.flags&WMP_FLAG_CLOCK) && h.send_stamp && radio_stats.mode!=2u && clock_copy_mode>=2u) {
                /* Least-delayed copy in each 100 ms window feeds the clock fit (about 10 samples/s). */
                static uint32_t window_start,best_clock;static uint64_t best_stamp;static int64_t best_delta;static int have_best;
                uint8_t master[6],valid;portENTER_CRITICAL(&feedback_lock);memcpy(master,leader_mac,6);valid=leader_valid;portEXIT_CRITICAL(&feedback_lock);
                if(valid && !memcmp(item.sender,master,6)) {
                    ++audio_clock_seen;int64_t delta=(int64_t)h.send_stamp-(int64_t)item.clock;
                    if(have_best && (item.clock<window_start || item.clock-window_start>=100000u)) {
                        if(item.clock>=window_start && wmp_audio_beacon(best_stamp,best_clock)) { ++audio_clock_pushed;++radio_stats.beacon_queued;last_audio_clock_us=(uint32_t)esp_timer_get_time();radio_stats.beacon_match_at=last_audio_clock_us; }
                        have_best=0;
                    }
                    if(!have_best) { window_start=item.clock;best_delta=delta;best_stamp=h.send_stamp;best_clock=item.clock;have_best=1; }
                    else if(delta>best_delta) { best_delta=delta;best_stamp=h.send_stamp;best_clock=item.clock; }
                }
            }
            if(h.flags&WMP_FLAG_REPLAY) {
                ++radio_stats.replay_received;
                if(radio_stats.mode!=2u)copy_event(h.generation,h.sequence,h.presentation_us,WMP_COPY_REPAIR,0,0,0);
            } else if(radio_stats.mode!=2u && h.copy_index<4u)copy_event(h.generation,h.sequence,h.presentation_us,WMP_COPY_VALID,h.copy_index,0,0);
        } else {
            int intact=1;for(unsigned i=0;i<h.payload_bytes;i++)if(payload[i]!=(uint8_t)(i*29u+7u)) { intact=0;break; }
            if(!intact) { ++radio_stats.rx_bad;continue; }
        }
        uint32_t logical_crc=wmp_logical_crc(item.wire,item.bytes);
        int result=wmp_dedup_accept(&dedup,h.group,h.generation,h.sequence,logical_crc);
        delivery_note(h.generation,h.sequence,WMP_D_RAW_DEDUP,h.copy_index,h.flags,item.physical_tag,(uint32_t)esp_timer_get_time(),result,&h);
        if(!result) { ++radio_stats.rx_duplicate;if(h.type==WMP_AUDIO || h.type==WMP_SILENCE)handoff_audio(&delivered,&h,payload,logical_crc,item.physical_tag);continue; }
        if(result<0) { if(result==-3)++radio_stats.rx_conflict;else ++radio_stats.rx_stale;continue; }
        if(!radio_stats.rx_unique)radio_stats.rx_first=(uint32_t)h.sequence;
        if(h.sequence>radio_stats.rx_highest)radio_stats.rx_highest=(uint32_t)h.sequence;
        ++radio_stats.rx_unique;radio_stats.rx_bytes+=h.payload_bytes;
        if(h.type==WMP_SILENCE) {
            ++radio_stats.silence_received;last_audio=0;radio_stats.rx_active=0;
            /* Silence can be a delayed duplicate from before resume, or a
             * fill during a temporary RTP stall. Current ANNOUNCE transport
             * state owns the lease; an old audio packet cannot revoke it. */
            radio_stats.rx_claim=wmp_peer_claimed(&peer_lease,(uint64_t)now);
            handoff_audio(&delivered,&h,payload,logical_crc,item.physical_tag);
        }
        if(h.type==WMP_AUDIO) { last_audio=esp_timer_get_time();wmp_peer_audio(&peer_lease,(uint64_t)last_audio);radio_stats.rx_active=1;radio_stats.rx_sbc_frames+=h.frame_count;handoff_audio(&delivered,&h,payload,logical_crc,item.physical_tag); }
        if(last_clock) { uint32_t gap=item.clock-last_clock;if(gap<0x80000000u && gap>radio_stats.rx_max_gap_us)radio_stats.rx_max_gap_us=gap; }
        last_clock=item.clock;
    }
}
static int send_message(message *m,unsigned copy) {
    if(m->header.type==WMP_AUDIO || m->header.type==WMP_SILENCE || m->header.type==WMP_PROBE) {
        uint64_t due=0,now=(uint64_t)esp_timer_get_time();
        portENTER_CRITICAL(&wake_window_lock);unsigned span=tx_wake_span;
        int valid=!span || wmp_wake_due(now,native_wake_local,radio_stats.receive_interval_ms*1000u,span,&due);
        if(span && !valid)++tx_wake_missing;else if(span && due>now)++tx_wake_waits;
        portEXIT_CRITICAL(&wake_window_lock);
        if(span && (!valid || due>now))return 0;
    }
    int suppress=0;
    if((m->header.type==WMP_AUDIO || m->header.type==WMP_SILENCE) && !(m->header.flags&WMP_FLAG_REPLAY)) {
        portENTER_CRITICAL(&copy_lock);
        if(drop_active && m->header.generation==drop_generation && m->header.sequence>=drop_sequence && m->header.sequence<drop_end) {
            if(drop_records) { drop_record *r=&drop_records[m->header.sequence-drop_sequence];r->sequence=m->header.sequence;r->sample=m->header.first_sample;r->pts=m->header.presentation_us;r->mask|=(uint8_t)(1u<<copy); }
            suppress=1;++radio_stats.diagnostic_dropped_copies;if(drop_remaining && !--drop_remaining)drop_active=0;
        }
        portEXIT_CRITICAL(&copy_lock);
    }
    if(suppress) { delivery_note(m->header.generation,m->header.sequence,WMP_D_INJECT,copy,m->header.flags,0,(uint32_t)esp_timer_get_time(),0,&m->header);copy_event(m->header.generation,m->header.sequence,m->header.presentation_us,WMP_COPY_INJECT,copy,0,0);return 2; } // never a radio submission
    int action=radio_stats.ethernet_enabled==1u || audio_action_frames || m->header.type==WMP_SYNC_REPLY;
    if(!action)for(unsigned i=0;i<TX_BUFFERS;i++) {
        tx_buffer *b=&tx_buffers[i];
        if(b->state && !b->tag && b->generation==m->header.generation && b->sequence==m->header.sequence && b->copy==copy && b->type==m->header.type && b->flags==m->header.flags)return 0;
    }
    /* All ready traffic competes by presentation deadline. Permanently
     * excluding originals from one credit wastes capacity when no repair is
     * ready and can leave an earliest-deadline original unable to progress. */
    unsigned limit=tx_window;
    tx_buffer *owned=0;for(unsigned i=0;i<limit;i++)if(!tx_buffers[i].state) { owned=&tx_buffers[i];break; }
    if(!owned) { portENTER_CRITICAL(&duration_lock);++credit_waits;portEXIT_CRITICAL(&duration_lock);return 0; }
    uint8_t *frame=tx_scratch;memset(frame,0,32u);frame[0]=action?0xd0u:0x08u;frame[1]=action?0u:0x02u;memset(frame+4,255,6);memcpy(frame+10,tx_mac,6);
    if(radio_stats.repair_unicast && ((m->header.flags&WMP_FLAG_REPLAY) || (m->header.type==WMP_SYNC_REPLY && m->payload && m->payload[0]==2u)))memcpy(frame+4,m->repair_to,6);
    if(action)memset(frame+16,255,6);else memcpy(frame+16,network,6);
    static const uint8_t llc[8]={0xaa,0xaa,3,0,0,0,0x88,0xb5};memcpy(frame+24,action?control_prefix:llc,8);
    owned->tag=0;owned->owner=m->owner;owned->acked=0;
    if(action) {
        int collision;
        do { next_native_tag=(next_native_tag+1u)&0xffffffu;collision=!next_native_tag;
            for(unsigned i=0;i<TX_BUFFERS;i++)if(tx_buffers[i].state && tx_buffers[i].tag==next_native_tag)collision=1;
        }while(collision);
        owned->tag=next_native_tag;frame[29]=(uint8_t)owned->tag;frame[30]=(uint8_t)(owned->tag>>8);frame[31]=(uint8_t)(owned->tag>>16);
    }
    const uint8_t *payload=m->payload;
    if(m->header.type==WMP_PROBE) {
        uint8_t *pattern=frame+32u+WMP_HEADER_BYTES;
        for(unsigned i=0;i<m->header.payload_bytes;i++)pattern[i]=(uint8_t)(i*29u+7u);
        payload=pattern;
    }
    uint8_t stamped[ANNOUNCE_BYTES];
    if(m->header.type==WMP_ANNOUNCE && m->header.payload_bytes==ANNOUNCE_BYTES && payload) {
        uint64_t clock=wmp_source_clock();memcpy(stamped,payload,ANNOUNCE_BYTES);
        for(unsigned j=0;j<8u;j++)stamped[16u+j]=(uint8_t)(clock>>(8u*j));
        memset(stamped+24,0,8);
        uint64_t start=window_start;uint32_t period=window_period,len=window_len;
        if(own_clock_local && start && period && len && clock>=start && clock-start<1000000u) {
            uint32_t age=(uint32_t)(clock-start);uint16_t p10=(uint16_t)(period/10u),l10=(uint16_t)(len/10u);
            for(unsigned j=0;j<4u;j++)stamped[24u+j]=(uint8_t)(age>>(8u*j));
            stamped[28]=(uint8_t)p10;stamped[29]=(uint8_t)(p10>>8);stamped[30]=(uint8_t)l10;stamped[31]=(uint8_t)(l10>>8);
        }
        payload=stamped;++announce_stamps;
    }
    if((m->header.type==WMP_AUDIO || m->header.type==WMP_SILENCE) && clock_copy_mode && m->header.payload_bytes+WMP_CLOCK_TRAILER<=WMP_LIVE_PAYLOAD) {
        int quiet=1;for(unsigned i=0;i<TX_BUFFERS;i++)if(tx_buffers[i].state)quiet=0;
        m->header.flags|=WMP_FLAG_CLOCK;m->header.tx_serial=(uint16_t)++tx_serial_counter;m->header.sync_flags=(uint8_t)((quiet?WMP_SYNC_QUIET:0u)|(radio_stats.mode==2u?wmp_sync_volume_encode(wmp_audio_volume()):0u));
        m->header.send_stamp=wmp_source_clock();++copy_stamps_sent;if(quiet)++copy_stamps_quiet;
        if(radio_stats.mode==2u) { unsigned party=master_party_size((uint64_t)esp_timer_get_time());m->header.party=(uint8_t)party;wmp_audio_party_size(party); }
        else m->header.party=0;
    } else m->header.flags&=(uint8_t)~WMP_FLAG_CLOCK;
    m->header.copy_index=(uint8_t)copy;int bytes=wmp_encode(frame+32,TX_FRAME_BYTES-32u,&m->header,payload);
    if(!bytes) { if(m->header.flags&WMP_FLAG_REPLAY)++radio_stats.replay_invalid;return 0; }
    owned->generation=m->header.generation;owned->sequence=m->header.sequence;owned->presentation_us=m->header.presentation_us;owned->copy=(uint8_t)copy;owned->type=m->header.type;owned->flags=m->header.flags;owned->submitted=(uint32_t)esp_timer_get_time();
    __asm__ __volatile__("memw":::"memory");owned->state=1;
    ++radio_stats.tx_attempt;if(m->header.flags&WMP_FLAG_REPLAY)++radio_stats.replay_attempted;int64_t before=esp_timer_get_time();
    if(wmp_coex_trace_active())wmp_coex_trace_record(WMP_RT_TX_SUBMIT,copy|((m->header.flags&WMP_FLAG_REPLAY)?0x80u:0u),(unsigned)wmp_coex_phase()&0xffffu,(uint32_t)m->header.sequence);
    int control_signature=m->header.type==WMP_SYNC_REPLY && m->header.payload_bytes==8u?((unsigned)frame[32u+WMP_HEADER_BYTES]|((unsigned)frame[33u+WMP_HEADER_BYTES]<<8)):0;
    delivery_note(m->header.generation,m->header.sequence,WMP_D_SUBMIT,copy,m->header.flags,owned->tag,(uint32_t)before,control_signature,&m->header);
    if((m->header.type==WMP_AUDIO || m->header.type==WMP_SILENCE) && !(m->header.flags&WMP_FLAG_REPLAY))copy_event(m->header.generation,m->header.sequence,m->header.presentation_us,WMP_COPY_SUBMIT,copy,(uint32_t)before,0);
    esp_err_t result;
    if(radio_stats.ethernet_enabled==1u) {
        uint8_t destination[6];memcpy(destination,frame+4,6);
        /* Reuse the buffer: Ethernet14+carrier8 puts WMP at the same offset32. */
        memcpy(frame+10,destination,6);memcpy(frame+16,tx_mac,6);frame[22]=0x88;frame[23]=0xb5;
        result=esp_wifi_internal_tx(tx_interface,frame+10,(uint16_t)(22+bytes));
    } else result=esp_wifi_80211_tx(tx_interface,frame,32+bytes,true);
    uint32_t elapsed=(uint32_t)(esp_timer_get_time()-before);if(elapsed>radio_stats.tx_api_max_us)radio_stats.tx_api_max_us=elapsed;
    ++radio_stats.tx_api_calls;radio_stats.tx_api_total_us+=elapsed;if(elapsed>1000u)++radio_stats.tx_api_over1ms;if(elapsed>5000u)++radio_stats.tx_api_over5ms;
    radio_stats.tx_last_api_status=result;
    delivery_note(m->header.generation,m->header.sequence,WMP_D_API,copy,m->header.flags,owned->tag,(uint32_t)esp_timer_get_time(),result,NULL);
    if(result!=ESP_OK) { owned->state=0;++radio_stats.tx_api_error;if((m->header.type==WMP_AUDIO || m->header.type==WMP_SILENCE) && !(m->header.flags&WMP_FLAG_REPLAY))copy_event(m->header.generation,m->header.sequence,m->header.presentation_us,WMP_COPY_TX_FAIL,copy,0,0);return -1; }
    if((m->header.type==WMP_AUDIO || m->header.type==WMP_SILENCE) && !(m->header.flags&WMP_FLAG_REPLAY))copy_event(m->header.generation,m->header.sequence,m->header.presentation_us,WMP_COPY_ACCEPT,copy,(uint32_t)esp_timer_get_time(),0);
    ++radio_stats.tx_accepted;if(m->header.flags&WMP_FLAG_REPLAY)++radio_stats.replay_accepted;
    if((m->header.flags&WMP_FLAG_REPLAY) && !(frame[4]&1u))++radio_stats.repair_unicast_sent;
    return 1;
}
static void transmitter(void *unused) {
    (void)unused;radio_stats.tx_task_core=(uint32_t)xPortGetCoreID();slot *slots=NULL;unsigned slot_count=0;uint64_t sequence=0,announcement_sequence=0,generation=1,next=0,announce_at=0;message queued;
    uint64_t copy_due=0,last_loop=0;
    static message feedback_message;static uint8_t feedback_payload[8];unsigned feedback_copy=3,feedback_total=3,feedback_nack=0;uint64_t feedback_due=0,last_feedback=0,feedback_sequence=0,feedback_wait_since=0;
    unsigned mode=0,pps=90,copies=4,spacing=WMP_DEFAULT_COPY_SPACING_US,payload=476;
    command pending_command={0};int command_pending=0;uint64_t transition_started=0;int traced_phase=-1;
    for(;;) {
        if(!wifi_ready) {
            /* Solo: no Wi-Fi. Nothing is sent; a leadership request is refused, stop is a no-op. */
            command c;
            while(xQueueReceive(commands,&c,0)==pdTRUE) { portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock); }
            if(command_pending) { command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock); }
            mode=0;radio_stats.mode=0;feedback_copy=feedback_total;
            ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(50));continue;
        }
        uint64_t loop_now=(uint64_t)esp_timer_get_time();
        if(mode && last_loop) {
            uint32_t gap=(uint32_t)(loop_now-last_loop);
            if(gap>radio_stats.tx_loop_max_us)radio_stats.tx_loop_max_us=gap;
            if(gap>5000u)++radio_stats.tx_loop_over5;
            if(gap>20000u)++radio_stats.tx_loop_over20;
        }
        last_loop=loop_now;
        if(wmp_coex_trace_active()) { int phase=wmp_coex_phase();if(phase!=traced_phase) { wmp_coex_trace_record(WMP_RT_PHASE,(unsigned)phase&255u,0,0);traced_phase=phase; } }
        int align_state=0;
        if(own_clock_local && (mode==2u || mode==0u))track_window((uint64_t)esp_timer_get_time());
        if(own_clock_local && mode==0u)align_state=align_tick((uint64_t)esp_timer_get_time());
        uint32_t complete;while(xQueueReceive(completed,&complete,0)==pdTRUE)if(complete<TX_BUFFERS && tx_buffers[complete].state==2u) {
            tx_buffer *b=&tx_buffers[complete];uint64_t now=(uint64_t)esp_timer_get_time();
            for(unsigned i=0;i<slot_count;i++) {
                slot *s=&slots[i];
                if(b->owner && s->active && s->waiting && s->message.owner==b->owner && s->next==b->copy+1u) {
                    s->waiting=0;
                    if(b->acked || s->next>=s->total) { s->next=s->total;s->active=(s->message.reference&WMP_AUDIO_REPAIR_REF)?2u:0u; }
                    else s->due=now-(uint32_t)((uint32_t)now-b->finished)+s->spacing;
                }
            }
            b->state=0;
        }
        /* One queue/slot owns each borrowed immutable audio reference. The
         * SDK buffer owns its byte copy until the completion callback. */
        for(unsigned i=0;i<slot_count;i++) {
            slot *s=&slots[i];
            if(s->active==2u) {
                int pending=0;for(unsigned j=0;j<TX_BUFFERS;j++)if(tx_buffers[j].state && tx_buffers[j].owner==s->message.owner) { pending=1;break; }
                if(!pending)s->active=0;
            }
            if(!s->active && s->message.reference) { wmp_audio_unshare(s->message.reference);s->message.reference=0; }
        }
        unsigned busy=0;uint32_t local_now=(uint32_t)esp_timer_get_time();
        for(unsigned i=0;i<TX_BUFFERS;i++) {
            tx_buffer *b=&tx_buffers[i];if(b->state)++busy;
            if(b->state==1u && local_now-b->submitted>1000000u) { b->state=3;++radio_stats.tx_timeout; }
        }
        if(busy>radio_stats.tx_busy_max)radio_stats.tx_busy_max=busy;
        unsigned active=0;for(unsigned i=0;i<slot_count;i++)active+=slots[i].active!=0u;
        if(!command_pending && xQueueReceive(commands,&pending_command,0)==pdTRUE) {
            command_pending=1;transition_started=(uint64_t)esp_timer_get_time();
            portENTER_CRITICAL(&publisher_lock);radio_stats.transition_target=pending_command.mode;radio_stats.transition_pending=1;portEXIT_CRITICAL(&publisher_lock);
        }
        if(command_pending && !active && !busy && uxQueueMessagesWaiting(source)==0u) {
            command cmd=pending_command;
            if(cmd.mode==2u && radio_stats.infrastructure_requested) {
                uint32_t epoch=0;uint64_t ready=0;
                if(!wmp_radio_clock_ready() || !wmp_source_clock_shared_ready(&epoch,&ready) || (uint64_t)esp_timer_get_time()<ready) {
                    command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock);
                    wmp_telemetry_printf("WMP_ROLE_RESOURCE target=2 status=%d network_unqualified=1\n",ESP_ERR_INVALID_STATE);continue;
                }
            }
            if(cmd.mode==11u || cmd.mode==12u || cmd.mode==13u) {
                unsigned network_mode=cmd.mode==13u?2u:(cmd.mode==11u?1u:0u);
                int result=apply_network(network_mode);radio_stats.infrastructure_status=result;infrastructure_pending=0;
                command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock);
                wmp_telemetry_printf("WMP_NETWORK_APPLIED enabled=%u anchor=%u status=%d\n",(unsigned)(network_mode!=0u),(unsigned)(network_mode==2u),result);continue;
            }
            if(cmd.mode==9u || cmd.mode==10u) {
                unsigned enabled=cmd.mode==9u?cmd.copies:0u;
                int result=ethernet_mode(enabled);
                command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock);
                wmp_telemetry_printf("WMP_ETH_APPLIED enabled=%u status=%d diagnostic=1\n",enabled,result);continue;
            }
            if(cmd.mode==7u || cmd.mode==8u) {
                int result=associate_leader(cmd.mode==7u);radio_stats.association_status=result;
                command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock);
                wmp_telemetry_printf("WMP_ASSOC_APPLIED requested=%u status=%d diagnostic=1\n",(unsigned)(cmd.mode==7u),result);continue;
            }
            if(cmd.mode==5u || cmd.mode==6u) {
                esp_err_t result=esp_wifi_set_promiscuous(cmd.mode==6u);
                if(result==ESP_OK) { radio_stats.rx_suspended=cmd.mode==5u;wmp_audio_prepare_peer(); }
                command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock);
                wmp_telemetry_printf("WMP_RX_PAUSE_APPLIED paused=%u status=%d\n",(unsigned)(cmd.mode==5u),result);continue;
            }
            if(!radio_stats.infrastructure_requested) {
                if(radio_stats.ethernet_enabled)ethernet_mode(0);
                if(radio_stats.direct_rx_enabled)wmp_radio_direct_rx(0);
            }
            if((cmd.mode==1u || cmd.mode==2u) && !slots) {
                slots=heap_caps_calloc(SLOTS,sizeof(*slots),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
                if(!slots) {
                    ++radio_stats.slot_resource_failures;command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock);
                    wmp_telemetry_printf("WMP_ROLE_RESOURCE target=%u status=%d\n",cmd.mode,ESP_ERR_NO_MEM);wait_transmitter((uint64_t)esp_timer_get_time()+10000u);continue;
                }
                slot_count=SLOTS;++radio_stats.slot_resource_starts;radio_stats.slot_resource_bytes=SLOTS*sizeof(*slots);
            }
            unsigned previous_mode=mode;mode=cmd.mode>=3u?0u:cmd.mode;pps=cmd.pps;copies=cmd.copies;payload=cmd.payload;
            /* Automatic ownership/codec changes retain the selected profile.
             * Resolve it here, not when queued, so a pending role request
             * cannot overwrite a newer SCHEDULE command. */
            if(!cmd.keep_schedule)schedule_config=cmd.spacing|(schedule_config&UINT32_C(0x80000000));
            spacing=schedule_config&UINT32_C(0x7fffffff);
            generation=((uint64_t)esp_random()<<32)|esp_random();if(!generation)generation=1;
            portENTER_CRITICAL(&copy_lock);drop_active=0;portEXIT_CRITICAL(&copy_lock);
            sequence=announcement_sequence=0;next=announce_at=0;
            native_receive_tick(2u,(uint64_t)esp_timer_get_time());
            if(!radio_stats.infrastructure_requested) {
                radio_stats.association_requested=radio_stats.association_connected=0;infrastructure_link_up=0;wmp_source_clock_enable(0);
            }
            if((previous_mode==2u && !mode) || cmd.mode==3u || cmd.mode==4u)wmp_audio_prepare_peer();
            if(radio_stats.infrastructure_requested) {
                /* The fixed network role, including a local ESP32 anchor,
                 * remains independent of Bluetooth audio leadership. */
                tx_interface=radio_stats.network_anchor?WIFI_IF_AP:WIFI_IF_STA;
                radio_stats.wifi_ap=radio_stats.network_anchor;
                if(!radio_stats.network_anchor)radio_stats.beacon_tu=0;
            } else if(pure_broadcast && !radio_stats.wifi_ap && cmd.mode!=3u && cmd.mode!=4u) {
                /* Pure broadcast role change: stay an unconnected station;
                 * raw broadcast TX uses the STA interface and its fixed rate,
                 * established when the passive interface was selected. */
                tx_interface=WIFI_IF_STA;memcpy(tx_mac,wmp_mac,6);radio_stats.beacon_tu=0;
            } else if(cmd.mode!=4u) {
                if(!radio_stats.wifi_ap)ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
                configure_ap(mode?100u:receiver_beacon_tu);tx_interface=WIFI_IF_AP;radio_stats.wifi_ap=1;
                ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_AP,tx_mac));
                /* A live rate change is refused while Bluetooth/audio is active.
                 * Report it; never abort the board on a role change. */
                int rate_status=wmp_radio_rate(selected_rate);
                if(rate_status!=ESP_OK)wmp_telemetry_printf("WMP_ROLE_RESOURCE target=%u rate_status=%d mbps=%u\n",cmd.mode,rate_status,selected_rate);
            } else {
                ESP_ERROR_CHECK(stop_wifi());ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
                tx_interface=WIFI_IF_STA;memcpy(tx_mac,wmp_mac,6);radio_stats.wifi_ap=0;radio_stats.beacon_tu=0;
                ESP_ERROR_CHECK(configure_rate_before_start(WIFI_IF_STA,selected_rate));
                ESP_ERROR_CHECK(start_wifi());ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
                ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
            }
            own_clock_local=pure_broadcast && !radio_stats.infrastructure_requested && !radio_stats.wifi_ap;
            if(mode && !radio_stats.infrastructure_requested)wmp_source_clock_enable(own_clock_local?4u:1u);
            {   /* Leadership start for first-broadcaster-wins precedence. */
                uint32_t epoch=0;int shared=mode==2u && (wmp_source_clock_shared_epoch(&epoch) || own_clock_local);uint64_t start=mode==2u?wmp_source_clock():0;
                portENTER_CRITICAL(&feedback_lock);own_leader_start=start;own_leader_shared=(uint8_t)(shared && start);portEXIT_CRITICAL(&feedback_lock);
            }
            portENTER_CRITICAL(&publisher_lock);publisher_generation=generation;source_ready_at=mode==2u?(uint64_t)esp_timer_get_time():0;
            radio_stats.mode=mode;radio_stats.preparing=0;portEXIT_CRITICAL(&publisher_lock);
            radio_stats.pps=pps;radio_stats.copies=copies;radio_stats.spacing_us=spacing;radio_stats.payload=payload;
            uint32_t elapsed=(uint32_t)((uint64_t)esp_timer_get_time()-transition_started);
            if(elapsed>radio_stats.transition_max_us)radio_stats.transition_max_us=elapsed;
            ++radio_stats.transition_count;command_pending=0;portENTER_CRITICAL(&publisher_lock);radio_stats.transition_pending=0;portEXIT_CRITICAL(&publisher_lock);
        }
        if(!mode && slots && !active && uxQueueMessagesWaiting(source)==0u) {
            heap_caps_free(slots);slots=NULL;slot_count=0;radio_stats.slot_resource_bytes=0;++radio_stats.slot_resource_stops;
        }
        uint64_t now=(uint64_t)esp_timer_get_time();
        if(!command_pending && !mode && !radio_stats.network_anchor && radio_stats.wifi_ap && radio_stats.beacon_tu!=receiver_beacon_tu)configure_ap(receiver_beacon_tu);
        native_receive_tick(mode,now);
        listen_tick(mode,now);
        unsigned audio_copies=mode==2u && original_copy_override?original_copy_override:copies;radio_stats.copies=audio_copies;
        spacing=schedule_config&UINT32_C(0x7fffffff);radio_stats.spacing_us=spacing;
        association_tick(now);
        station_keepalive(now);
        if(feedback_nack && feedback_copy<feedback_total && (mode || !replay_enabled))finish_nack(feedback_message.header.generation,feedback_message.header.sequence,now,0,32000u);
        if(mode || (feedback_nack && !replay_enabled))feedback_copy=feedback_total;
        if(!command_pending && !mode && feedback_copy>=feedback_total && replay_enabled) {
            nack_request request;
            if(xQueueReceive(nacks,&request,0)==pdTRUE) {
                suppression heard;portENTER_CRITICAL(&suppression_lock);heard=heard_nacks[request.sequence&7u];portEXIT_CRITICAL(&suppression_lock);
                uint64_t leader;portENTER_CRITICAL(&feedback_lock);leader=leader_generation;portEXIT_CRITICAL(&feedback_lock);
                if(request.generation==leader && pending_nack(request.generation,request.sequence,now) && !(heard.generation==request.generation && heard.sequence==request.sequence && heard.until>now)) {
                    feedback_message=(message){.header={.group=GROUP_ID,.generation=request.generation,.sequence=request.sequence,.type=WMP_SYNC_REPLY,.fragment_count=1,.payload_bytes=8,.sample_rate=request.rate,.sample_count=request.samples}};
                    /* The missing-sequence tracker already randomized its
                     * deadline. A second backoff wastes the repair window. */
                    memset(feedback_payload,0,sizeof(feedback_payload));feedback_message.payload=feedback_payload;feedback_payload[0]=2;feedback_payload[1]=1;feedback_nack=1;feedback_copy=0;feedback_due=now;
                    feedback_total=(uint8_t)nack_copies;
                    if(radio_stats.repair_unicast) { portENTER_CRITICAL(&feedback_lock);memcpy(feedback_message.repair_to,leader_mac,6);portEXIT_CRITICAL(&feedback_lock); }
                } else { finish_nack(request.generation,request.sequence,now,0,40000u);++radio_stats.nack_suppressed; }
            }
        }
        if(!mode && feedback_copy>=feedback_total && now-last_feedback>=5000000u) {
            uint64_t gen;uint32_t frames,rate,samples;
            portENTER_CRITICAL(&feedback_lock);gen=feedback_generation;frames=feedback_frames;rate=feedback_rate;samples=feedback_samples;
            if(frames) { feedback_frames=0; }
            portEXIT_CRITICAL(&feedback_lock);
            if(frames) {
                feedback_message=(message){.header={.group=GROUP_ID,.generation=gen,.sequence=feedback_sequence++,.type=WMP_SYNC_REPLY,.fragment_count=1,.payload_bytes=8,.sample_rate=rate,.sample_count=samples}};
                memset(feedback_payload,0,sizeof(feedback_payload));feedback_message.payload=feedback_payload;feedback_payload[0]=1;feedback_payload[2]=(uint8_t)frames;feedback_payload[3]=(uint8_t)(frames>>8);
                feedback_nack=0;feedback_copy=0;feedback_total=3;feedback_due=now+esp_random()%500000u;
            }
        }
        if(!mode && feedback_copy>=feedback_total && radio_stats.rx_active && now>=presence_next_us) {
            uint64_t gen;uint32_t rate,samples;
            portENTER_CRITICAL(&feedback_lock);gen=leader_generation;rate=leader_rate;samples=leader_samples;portEXIT_CRITICAL(&feedback_lock);
            presence_next_us=now+PRESENCE_PERIOD_US-100000u+esp_random()%200000u; /* jittered so followers do not align */
            if(gen && rate && samples) {
                feedback_message=(message){.header={.group=GROUP_ID,.generation=gen,.sequence=feedback_sequence++,.type=WMP_SYNC_REPLY,.fragment_count=1,.payload_bytes=8,.sample_rate=rate,.sample_count=samples}};
                memset(feedback_payload,0,sizeof(feedback_payload));feedback_message.payload=feedback_payload;
                feedback_payload[0]=3;feedback_payload[1]=1;feedback_payload[2]=(uint8_t)wmp_audio_speaker_channel();
                feedback_nack=0;feedback_copy=0;feedback_total=3;feedback_due=now;++presence_sent; /* 3 copies, 20 ms apart */
            }
        }
        radio_stats.nack_window_age_us=(uint32_t)now-feedback_rx_at;
        /* A late original/repair or a new epoch can retire queued feedback
         * while it waits for credit/window. Do not transmit that stale NACK. */
        if(feedback_nack && feedback_copy<feedback_total && !pending_nack(feedback_message.header.generation,feedback_message.header.sequence,now)) {
            finish_nack(feedback_message.header.generation,feedback_message.header.sequence,now,0,0u);feedback_copy=feedback_total;
            portENTER_CRITICAL(&nack_lock);++nack_stale_requests;portEXIT_CRITICAL(&nack_lock);
        }
        int nack_window_closed=feedback_nack && radio_stats.infrastructure_requested && radio_stats.direct_rx_enabled &&
            wmp_feedback_blocked(feedback_rx_at,(uint32_t)now,nack_gate_us);
        if(!mode && feedback_copy<feedback_total && now>=feedback_due && nack_window_closed) {
            ++radio_stats.nack_window_wait;if(!feedback_wait_since)feedback_wait_since=now;
            uint64_t waited=now-feedback_wait_since;
            if(waited>nack_gate_wait_max)nack_gate_wait_max=waited>UINT32_MAX?UINT32_MAX:(uint32_t)waited;
        } else feedback_wait_since=0;
        if(!mode && feedback_copy<feedback_total && now>=feedback_due && !nack_window_closed) {
            suppression heard;portENTER_CRITICAL(&suppression_lock);heard=heard_nacks[feedback_message.header.sequence&7u];portEXIT_CRITICAL(&suppression_lock);
            if(feedback_nack && heard.generation==feedback_message.header.generation && heard.sequence==feedback_message.header.sequence && heard.until>now) { finish_nack(feedback_message.header.generation,feedback_message.header.sequence,now,0,40000u);feedback_copy=feedback_total;++radio_stats.nack_suppressed; }
            else if(window_delay(now)) {
                uint32_t delay=window_delay(now);feedback_due=now+delay;++window_deferrals;if(delay>window_deferred_us_max)window_deferred_us_max=delay;
            }
            else {
                int sent=send_message(&feedback_message,feedback_copy);
                if(sent>0) {
                    if(feedback_nack) { if(feedback_copy+1u>=feedback_total)finish_nack(feedback_message.header.generation,feedback_message.header.sequence,(uint64_t)esp_timer_get_time(),1,0u);++radio_stats.nack_sent; }
                    else ++radio_stats.qos_sent;
                    ++feedback_copy;feedback_due=now+(feedback_nack?nack_copy_spacing_us:20000u);
                    if(feedback_copy==feedback_total && !feedback_nack)last_feedback=now;
                } else vTaskDelay(1);
            }
        }
        /* Pure broadcast source: stamp/send ANNOUNCE inside its own Wi-Fi
         * phase, so the stamp is not followed by a Bluetooth-phase wait. A
         * deferral is bounded: after 150 ms it is sent regardless. */
        int announce_window_ok=!(mode==2u && own_clock_local && window_period && window_len) || wmp_coex_phase()==0 || now>=announce_at+150000u;
        /* Stamp only with the transmit queue empty (bounded by the same 150 ms fallback, which is sent unflagged). */
        int tx_quiet=1;for(unsigned i=0;i<TX_BUFFERS;i++)if(tx_buffers[i].state)tx_quiet=0;
        int announce_quiet_ok=!(mode==2u && own_clock_local) || tx_quiet || now>=announce_at+150000u;
        if(mode && !command_pending && now>=announce_at && announce_window_ok && announce_quiet_ok) {
            message announcement={.header={.group=GROUP_ID,.generation=generation,.sequence=announcement_sequence,.type=WMP_ANNOUNCE,.fragment_count=1,.payload_bytes=ANNOUNCE_BYTES}};
            uint8_t announce_payload[ANNOUNCE_BYTES];announce_payload[0]=bt_stats.transport_started?1u:0u;memcpy(announce_payload+1,wmp_base_mac,6);
            uint64_t start;uint8_t shared;portENTER_CRITICAL(&feedback_lock);start=own_leader_start;shared=own_leader_shared;portEXIT_CRITICAL(&feedback_lock);
            announce_payload[7]=(uint8_t)((shared?ANNOUNCE_FLAG_SHARED:0u)|(own_clock_local?ANNOUNCE_FLAG_LOCAL:0u)|(tx_quiet?ANNOUNCE_FLAG_QUIET:0u));for(unsigned j=0;j<8u;j++)announce_payload[8u+j]=(uint8_t)(start>>(8u*j));memset(announce_payload+16,0,8);wmp_fx_announce(announce_payload+32);announcement.payload=announce_payload;
            if(send_message(&announcement,0)>0) { if(tx_quiet)++announce_quiet_sent;else ++announce_busy_sent;++announcement_sequence;announce_at=(uint64_t)esp_timer_get_time()+100000u;continue; }
            /* A busy ordinary credit must not block a due repair credit. */
            announce_at=now+1000u;
        }
        for(unsigned i=0;i<slot_count;i++)if(!slots[i].active) {
            int have=0;
            if(mode==1u && !command_pending && now>=next) {
                memset(&queued,0,sizeof(queued));queued.header.type=WMP_PROBE;queued.header.payload_bytes=(uint16_t)payload;
                queued.payload=NULL;
                queued.ready_us=now;have=1;if(!next)next=now;next+=1000000u/pps;
            }
            else if(xQueueReceive(source,&queued,0)==pdTRUE) {
                /* A send that raced a role transition belongs to a retired
                 * session: release its loan instead of broadcasting it. */
                if(queued.header.type!=WMP_PROBE && queued.header.generation!=generation) {
                    if(queued.reference)wmp_audio_unshare(queued.reference);
                    ++radio_stats.tx_unmatched;continue;
                }
                have=1;
            }
            if(!have)continue;
            if(queued.header.type==WMP_PROBE) {
                queued.header.group=GROUP_ID;queued.header.generation=generation;queued.header.sequence=sequence++;
                queued.header.fragment_count=1;queued.header.presentation_us=queued.ready_us+35000u;
            }
            slots[i].message=queued;if(!++next_slot_owner)++next_slot_owner;slots[i].message.owner=next_slot_owner;slots[i].next=slots[i].waiting=0;slots[i].paced=queued.copy_spacing?queued.repair_paced:(uint8_t)(schedule_config>>31);slots[i].spacing=queued.copy_spacing?queued.copy_spacing:spacing;slots[i].total=queued.total_copies?queued.total_copies:(uint8_t)audio_copies;slots[i].due=queued.ready_us;slots[i].active=1;++radio_stats.produced;
            slots[i].dither=(!slots[i].paced && queued.header.type==WMP_AUDIO && !(queued.header.flags&WMP_FLAG_REPLAY))?copy_dither_us:0u;
            if(slots[i].dither>slots[i].spacing/4u)slots[i].dither=slots[i].spacing/4u;
        }
        now=(uint64_t)esp_timer_get_time();
        uint32_t shed=tx_shed && mode==2u;
        if(shed) {
            /* Same 20 ms guard as repairs: a copy that late arrives after every receiver has given up. */
            uint64_t clock=wmp_source_clock();
            if(clock)for(unsigned i=0;i<slot_count;i++) {
                slot *s=&slots[i];
                if(s->active!=1u || s->waiting || (s->message.header.flags&WMP_FLAG_REPLAY) || (s->message.header.type!=WMP_AUDIO && s->message.header.type!=WMP_SILENCE))continue;
                if(!wmp_tx_original_expired(s->message.header.presentation_us,clock,20000u))continue;
                ++original_expired_trains;original_expired_copies+=s->total-s->next;if(!s->next)++original_expired_unsent;
                s->next=s->total;s->active=(s->message.reference&WMP_AUDIO_REPAIR_REF)?2u:0u;
            }
        }
        int selected=-1;uint64_t earliest=UINT64_MAX;
        for(unsigned i=0;i<slot_count;i++)if(slots[i].active==1u && !slots[i].waiting && (selected<0 || (shed?
            wmp_tx_precedes_copy(slots[i].due,(slots[i].message.header.flags&WMP_FLAG_REPLAY)!=0,slots[i].next,slots[i].message.header.presentation_us,earliest,(slots[selected].message.header.flags&WMP_FLAG_REPLAY)!=0,slots[selected].next,slots[selected].message.header.presentation_us,now):
            wmp_tx_precedes(slots[i].due,(slots[i].message.header.flags&WMP_FLAG_REPLAY)!=0,slots[i].message.header.presentation_us,earliest,(slots[selected].message.header.flags&WMP_FLAG_REPLAY)!=0,slots[selected].message.header.presentation_us,now)))) { selected=(int)i;earliest=slots[i].due; }
        now=(uint64_t)esp_timer_get_time();
        uint64_t due=earliest;if(due<copy_due)due=copy_due;
        if(selected>=0 && now>=due) {
            slot *s=&slots[selected];uint64_t presentation_now=s->message.header.type==WMP_PROBE?now:wmp_source_clock();
            int repair=(s->message.header.flags&WMP_FLAG_REPLAY)!=0;
            int disposition=wmp_tx_deadline(repair,s->message.header.presentation_us,presentation_now,20000u);
            if(disposition==WMP_TX_EXPIRED) {
                ++repair_expired_trains;repair_expired_copies+=s->total-s->next;
                /* Existing native buffers retain their copied bytes. Keep the
                 * repair loan pinned until all this slot's callbacks finish,
                 * exactly as for a normally completed repair train. */
                s->next=s->total;s->waiting=0;s->active=2u;continue;
            }
            if(disposition==WMP_TX_WAIT_CLOCK) { ++repair_clock_waits;s->due=now+10000u;continue; }
            /* Optional coexistence-phase gate: hold audio until the scheduler
             * is in an allowed (Wi-Fi) phase, unless the deadline is close. */
            uint32_t phase_mask=tx_phase_mask;
            if(phase_mask && s->message.header.type!=WMP_PROBE && presentation_now) {
                int phase=wmp_coex_phase();
                int allowed=phase>=0 && phase<32 && (phase_mask&(1u<<phase));
                int urgent=s->message.header.presentation_us<=presentation_now+tx_phase_urgent_us;
                if(!allowed && !urgent) { ++tx_phase_waits;wait_transmitter((uint64_t)esp_timer_get_time()+1000u);continue; }
                if(!allowed)++tx_phase_overrides;
            }
            { uint32_t following=0;uint64_t at;
              if(s->next+1u<s->total && !s->paced && wmp_copy_due(s->message.ready_us,s->message.header.generation^s->message.header.sequence,s->next+1u,s->spacing,s->dither,&at) && at>now)following=(uint32_t)(at-now);
              s->message.header.next_copy_us=following; }
            int sent=send_message(&s->message,s->next);
            if(sent>0) {
                copy_due=wmp_tx_after_call(copy_due,now,(uint64_t)esp_timer_get_time(),transmit_pace_us);
                uint64_t accepted_clock=s->message.header.type==WMP_PROBE?(uint64_t)esp_timer_get_time():wmp_source_clock();
                if(accepted_clock && accepted_clock>=s->message.header.presentation_us) {
                    ++radio_stats.tx_late;
                    if(s->message.header.type==WMP_AUDIO || s->message.header.type==WMP_SILENCE) { if(repair)++repair_late_submit;else ++original_late_submit; }
                }
                ++s->next;
                /* Accepted native buffers own their byte copies. The final
                 * submission no longer needs to pin the shared audio slab. */
                if(s->next>=s->total) { s->active=(s->message.reference&WMP_AUDIO_REPAIR_REF)?2u:0u;s->waiting=0; }
                else if(s->paced && sent==1)s->waiting=1;
                else { int ok=wmp_copy_due(s->message.ready_us,s->message.header.generation^s->message.header.sequence,s->next,s->spacing,s->dither,&s->due);configASSERT(ok); }
            } else { if(sent<0)s->due=now+1000u;wait_transmitter(now+1000u); }
        } else {
            /* Producer/TX-complete notifications wake this early. The timer
             * blocks the high-priority sender instead of spinning or delaying
             * every packet by a whole scheduler tick. */
            uint64_t wake=now+(wmp_coex_trace_active() || align_state==2?1000u:(own_clock_local?2000u:10000u));if(selected>=0 && due<wake)wake=due;
            if(mode && announce_at<wake)wake=announce_at>now?announce_at:now+1000u; /* a window-deferred announce polls, never spins */
            if(!mode && feedback_copy<feedback_total && !nack_window_closed && feedback_due<wake)wake=feedback_due;
            wait_transmitter(wake);
        }
    }
}
static message publisher_pending;
static uint64_t publisher_epoch,publisher_sequence,publisher_cursor,pending_input_first;
static uint8_t publisher_have_pending,publisher_audio_queued,publisher_silence;
void wmp_radio_publisher_idle(void) {
    /* Called only on the serialized host, just like offer_stream. */
    if((radio_stats.mode!=2u || radio_stats.transition_pending) && publisher_have_pending) {
        if(publisher_pending.reference)wmp_audio_unshare(publisher_pending.reference);
        publisher_pending.reference=0;publisher_have_pending=publisher_audio_queued=0;
    }
}
static int commit_publisher(void) {
    message *p=&publisher_pending;
    if(radio_stats.transition_pending || radio_stats.mode!=2u) { wmp_radio_publisher_idle();return 0; }
    if(!publisher_audio_queued) {
        p->reference=wmp_audio_share(&p->header,p->payload,&p->payload);if(!p->reference) { publisher_have_pending=0;return 0; }
        publisher_audio_queued=1;
    }
    portENTER_CRITICAL(&publisher_lock);
    int permitted=!radio_stats.transition_pending && radio_stats.mode==2u && p->header.generation==publisher_generation;
    portEXIT_CRITICAL(&publisher_lock);
    int sent=permitted && xQueueSend(source,p,0)==pdTRUE;
    if(!sent) { if(permitted)++radio_stats.queue_full;return 0; }
    publisher_cursor+=p->header.sample_count;++publisher_sequence;
    if(publisher_silence)++radio_stats.silence_queued;
    p->reference=0;publisher_have_pending=publisher_audio_queued=0;
    if(transmitter_task)xTaskNotifyGive(transmitter_task);
    return 1;
}
static int offer_stream(const wmp_audio_group *g,uint32_t rate,uint64_t enqueued_local_us,int silence) {
    if(radio_stats.mode!=2u)return 1;
    if(!wmp_radio_source_ready())return 0;
    if(!rate || (!silence && (!g || !g->frames || g->frames>WMP_MAX_GROUP_FRAMES || g->bytes>WMP_LIVE_PAYLOAD)))return 0;
    uint64_t generation;portENTER_CRITICAL(&publisher_lock);generation=publisher_generation;portEXIT_CRITICAL(&publisher_lock);
    if(!generation)return 0;
    if(publisher_epoch!=generation) {
        if(publisher_have_pending && publisher_pending.reference)wmp_audio_unshare(publisher_pending.reference);
        publisher_epoch=generation;publisher_sequence=publisher_cursor=0;publisher_have_pending=publisher_audio_queued=0;
        wmp_playout_init(&playout,requested_latency_us);applied_latency_version=latency_version;
    }
    if(publisher_have_pending) {
        int same=publisher_silence==silence && publisher_pending.header.sample_rate==rate;
        if(!silence)same=same && pending_input_first==g->first_sample && publisher_pending.header.sample_count==g->sample_count &&
            publisher_pending.header.payload_bytes==g->bytes && publisher_pending.header.frame_count==g->frames && !memcmp(publisher_pending.payload,g->payload,g->bytes);
        if(!same) { if(publisher_silence)commit_publisher();return 0; }
        return commit_publisher();
    }
    uint64_t clock=wmp_source_clock();if(!clock)return 0;
    uint64_t skipped=0;
    if(wmp_playout_recover(&playout,clock,20000u,&publisher_cursor,&skipped)) {
        ++radio_stats.timeline_recoveries;radio_stats.missing_source_samples+=skipped;
        if(silence)enqueued_local_us=(uint64_t)esp_timer_get_time();
    }
    publisher_pending=(message){.header={.group=GROUP_ID,.generation=generation,.sequence=publisher_sequence,
        .type=silence?WMP_SILENCE:WMP_AUDIO,.fragment_count=1,.first_sample=publisher_cursor,.sample_rate=rate,
        .sample_count=silence?512u:g->sample_count,.payload_bytes=silence?0u:g->bytes,.frame_count=silence?0u:g->frames,
        .codec=silence?0u:g->codec,.channels=silence || !g->codec?0u:g->channels},
        .ready_us=(uint64_t)esp_timer_get_time()};
    uint32_t version,delay,late;
    portENTER_CRITICAL(&publisher_lock);version=latency_version;delay=requested_latency_us;late=pending_late_frames;pending_late_frames=0;portEXIT_CRITICAL(&publisher_lock);
    if(version!=applied_latency_version) { wmp_playout_request(&playout,delay);applied_latency_version=version; }
    /* The master owns the timeline; slaves follow it (user instruction
     * 2026-11-17). Slave late reports are counted but do not move the shared
     * latency unless LATEGROWTH 1 is set for an experiment. */
    if(late) { ++late_reports_ignored;if(late_growth) { --late_reports_ignored;wmp_playout_report(&playout,late,publisher_pending.header.sample_count); } }
    uint64_t original_clock=wmp_ingress_clock(clock,(uint64_t)esp_timer_get_time(),enqueued_local_us);
    if(!playout.valid)original_clock=clock; /* A new timeline must start in the future. */
    if(!wmp_playout_plan(&playout,publisher_cursor,rate,original_clock,&publisher_pending.header.presentation_us))return 0;
    radio_stats.latency_us=playout.requested_us;radio_stats.latency_changes=playout.changes;
    radio_stats.latency_phase_us=(int32_t)(playout.phase_q16/65536);radio_stats.latency_target_phase_us=(int32_t)(playout.target_q16/65536);
    if(!silence) { publisher_pending.payload=g->payload;pending_input_first=g->first_sample; }
    publisher_silence=(uint8_t)silence;publisher_have_pending=1;publisher_audio_queued=0;
    return commit_publisher();
}
int wmp_radio_offer(const wmp_audio_group *g,uint32_t rate,uint64_t enqueued_local_us) {
    return offer_stream(g,rate,enqueued_local_us,0);
}
void wmp_radio_silence_tick(uint32_t rate) {
    if(!wmp_radio_source_ready() || !playout.valid || !rate || rate!=playout.rate)return;
    for(unsigned i=0;i<2;i++) {
        uint64_t next,clock=wmp_source_clock();if(!clock || !wmp_sample_time(playout.anchor_us,playout.first_sample,rate,publisher_cursor,&next))return;
        int64_t pts=(int64_t)next+playout.phase_q16/65536;
        uint64_t arrived;if(pts<0 || !wmp_silence_due((uint64_t)pts,clock,(uint64_t)esp_timer_get_time(),playout.requested_us,&arrived))return;
        if(!offer_stream(0,rate,arrived,1))return;
    }
}
void wmp_radio_nack_stats(unsigned long query) {
    unsigned active=0,pending=0,retries=0;uint32_t reservations,submissions,cancellations,stale,extended,peak,bounded,legacy;
    portENTER_CRITICAL(&nack_lock);
    for(unsigned i=0;missing_audio && i<WMP_NACK_SLOTS;i++)if(missing_audio->entries[i].valid) { ++active;pending+=missing_audio->entries[i].pending!=0;retries+=missing_audio->entries[i].attempts; }
    reservations=nack_reservations;submissions=nack_submissions;cancellations=nack_cancellations;stale=nack_stale_requests;
    extended=nack_extended_submissions;peak=nack_peak_attempts;
    bounded=nack_deadline_handoffs;legacy=nack_legacy_handoffs;
    portEXIT_CRITICAL(&nack_lock);
    wmp_telemetry_printf("WMP_NACK_TRACKER query=%08lx active=%u pending=%u retries=%u reserved=%lu submitted=%lu cancelled=%lu stale=%lu counts_api_acceptance=1\n",query,active,pending,retries,(unsigned long)reservations,(unsigned long)submissions,(unsigned long)cancellations,(unsigned long)stale);
    wmp_telemetry_printf("WMP_NACK_DEADLINE query=%08lx extended_submitted=%lu peak_attempts=%lu bounded_handoffs=%lu legacy_handoffs=%lu shared_limit=16 legacy_limit=8 presentation_guard_us=20000 entry_bytes=%u source_exact_deadline_checked=1\n",query,(unsigned long)extended,(unsigned long)peak,(unsigned long)bounded,(unsigned long)legacy,(unsigned)sizeof(wmp_nack_entry));
}
void wmp_radio_timing_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_NACK_GATE_STAT query=%08lx us=%lu max_wait_us=%lu\n",query,(unsigned long)nack_gate_us,(unsigned long)nack_gate_wait_max);
    wmp_telemetry_printf("WMP_TX_PHASE query=%08lx mask=%lu urgent_us=%lu waits=%lu overrides=%lu current=%d\n",query,(unsigned long)tx_phase_mask,(unsigned long)tx_phase_urgent_us,(unsigned long)tx_phase_waits,(unsigned long)tx_phase_overrides,wmp_coex_phase());
    uint64_t dwell,late;uint32_t completed,maximum,over20,over100,wakes,wake_max,over1,over5,credits;
    portENTER_CRITICAL(&duration_lock);
    dwell=original_dwell_us;completed=original_completions;maximum=original_dwell_max;over20=original_over20ms;over100=original_over100ms;
    late=wake_late_us;wakes=wake_calls;wake_max=wake_late_max;over1=wake_over1ms;over5=wake_over5ms;credits=credit_waits;
    portEXIT_CRITICAL(&duration_lock);
    wmp_telemetry_printf("WMP_TX_DWELL query=%08lx original_completions=%lu total_us=%llu max_us=%lu over20ms=%lu over100ms=%lu credit_waits=%lu callback_included=1\n",query,(unsigned long)completed,(unsigned long long)dwell,(unsigned long)maximum,(unsigned long)over20,(unsigned long)over100,(unsigned long)credits);
    wmp_telemetry_printf("WMP_TX_WAKE query=%08lx calls=%lu total_late_us=%llu max_late_us=%lu over1ms=%lu over5ms=%lu\n",query,(unsigned long)wakes,(unsigned long long)late,(unsigned long)wake_max,(unsigned long)over1,(unsigned long)over5);
}
void wmp_radio_memory_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_STORAGE query=%08lx message_bytes=%u slot_bytes=%u source_queue_bytes=%u rx_queue_bytes=%u tx_buffers_bytes=%u tx_stack_free=%u rx_stack_free=%u\n",query,
        (unsigned)sizeof(message),(unsigned)sizeof(slot),(unsigned)(SOURCE_QUEUE_DEPTH*sizeof(message)),(unsigned)(RX_QUEUE_DEPTH*sizeof(received)),(unsigned)(sizeof(tx_buffers)+sizeof(tx_scratch)),
        transmitter_task?(unsigned)(uxTaskGetStackHighWaterMark(transmitter_task)):0u,
        receiver_task?(unsigned)(uxTaskGetStackHighWaterMark(receiver_task)):0u);
}
int wmp_radio_command(unsigned mode,unsigned pps,unsigned copies,unsigned spacing,unsigned payload) {
    if(mode>2u || pps<1u || pps>500u || (copies!=3u && copies!=4u) || spacing<1000u || spacing>160000u || payload<4u || payload>WMP_LIVE_PAYLOAD)return 0;
    if(copy_dither_us>spacing/4u)return 0;
    command c={.mode=(uint8_t)mode,.pps=(uint16_t)pps,.copies=(uint8_t)copies,.spacing=spacing,.payload=(uint16_t)payload};
    int queued=xQueueSend(commands,&c,0)==pdTRUE;if(queued && transmitter_task)xTaskNotifyGive(transmitter_task);return queued;
}
static esp_err_t wifi_up_inner(void);
static esp_err_t wifi_up(void) {
    if(wifi_ready)return ESP_OK;
    int64_t t0=esp_timer_get_time();esp_err_t r=wifi_up_inner();
    wmp_telemetry_printf("WMP_WIFI_TIMING up_ms=%lu status=%d at_ms=%lu%c",(unsigned long)((esp_timer_get_time()-t0)/1000),(int)r,(unsigned long)(esp_timer_get_time()/1000),10);
    return r;
}
static esp_err_t wifi_up_inner(void) {
    if(!incoming) { incoming=xQueueCreate(RX_QUEUE_DEPTH,sizeof(received));if(!incoming)return ESP_ERR_NO_MEM; }
    incoming_retire=0;
    wifi_init_config_t config=WIFI_INIT_CONFIG_DEFAULT();config.sta_disconnected_pm=false;esp_err_t status;
    if((status=(esp_wifi_init(&config)))!=ESP_OK) { vQueueDelete(incoming);incoming=NULL;return status; }
    wifi_live=1;
    if((status=(esp_wifi_set_storage(WIFI_STORAGE_RAM)))!=ESP_OK)goto fail;
    if(pure_broadcast) {
        /* Pure broadcast (no SoftAP ever, user instruction): boot as an
         * unconnected station; raw broadcast TX uses the STA interface. */
        if((status=(esp_wifi_set_mode(WIFI_MODE_STA)))!=ESP_OK)goto fail;
        tx_interface=WIFI_IF_STA;radio_stats.wifi_ap=0;radio_stats.beacon_tu=0;
        if((status=(configure_rate_before_start(WIFI_IF_STA,selected_rate)))!=ESP_OK)goto fail;
    } else {
        if((status=(esp_wifi_set_mode(WIFI_MODE_AP)))!=ESP_OK)goto fail;
        /* Native AP beacons carry our hardware clock. The legacy 1-Mbit beacon
         * occupies much longer contiguous airtime than the configured OFDM audio;
         * measure the clock path with OFDM basic rates under active Classic/LE.
         * The public API must run before esp_wifi_start(), never on a live epoch. */
        if((status=(esp_wifi_config_11b_rate(WIFI_IF_AP,true)))!=ESP_OK)goto fail;
        configure_ap(receiver_beacon_tu);
        tx_interface=WIFI_IF_AP;radio_stats.wifi_ap=1;
        if((status=(configure_rate_before_start(WIFI_IF_AP,selected_rate)))!=ESP_OK)goto fail;
    }
    if((status=(start_wifi()))!=ESP_OK)goto fail;
    if((status=(esp_wifi_set_ps(WIFI_PS_NONE)))!=ESP_OK)goto fail;
    if((status=(wmp_radio_channel(selected_channel)))!=ESP_OK)goto fail;
    if((status=(esp_wifi_get_mac(WIFI_IF_STA,wmp_mac)))!=ESP_OK)goto fail;
    if((status=(esp_wifi_get_mac(pure_broadcast?WIFI_IF_STA:WIFI_IF_AP,tx_mac)))!=ESP_OK)goto fail;
    wifi_promiscuous_filter_t filter={.filter_mask=WIFI_PROMIS_FILTER_MASK_DATA|WIFI_PROMIS_FILTER_MASK_MGMT};
    if((status=(esp_wifi_set_promiscuous_filter(&filter)))!=ESP_OK)goto fail;
    if((status=(esp_wifi_set_promiscuous_rx_cb(rx_callback)))!=ESP_OK)goto fail;
    if((status=(esp_wifi_register_80211_tx_cb(tx_callback)))!=ESP_OK)goto fail;
    if((status=(esp_wifi_set_promiscuous(true)))!=ESP_OK)goto fail;
    if((status=(esp_wifi_set_tx_done_cb(ethernet_tx_done)))!=ESP_OK)goto fail;
    radio_stats.receive_window_enabled=0;(void)wmp_radio_receive_window(1);
    scan_state=0;wifi_ready=1;++wifi_ups;if(transmitter_task)xTaskNotifyGive(transmitter_task);
    return ESP_OK;
fail:
    wifi_live=0;esp_wifi_set_promiscuous(false);esp_wifi_stop();wifi_started=0;esp_wifi_deinit();
    incoming_retire=1;for(int i=0;i<100 && !incoming_parked;i++)vTaskDelay(pdMS_TO_TICKS(10));
    if(incoming_parked && incoming) { vQueueDelete(incoming);incoming=NULL; }
    return status;
}
static void wifi_down_inner(void);
static void wifi_down(void) {
    if(!wifi_ready)return;
    int64_t t0=esp_timer_get_time();wifi_down_inner();
    wmp_telemetry_printf("WMP_WIFI_TIMING down_ms=%lu parked=%u at_ms=%lu%c",(unsigned long)((esp_timer_get_time()-t0)/1000),(unsigned)incoming_parked,(unsigned long)(esp_timer_get_time()/1000),10);
}
static void wifi_down_inner(void) {
    wifi_ready=0;vTaskDelay(pdMS_TO_TICKS(60));      /* let the transmitter finish its iteration and park */
    wmp_source_clock_enable(0);vTaskDelay(pdMS_TO_TICKS(20));  /* sampler stops reading the TSF */
    /* The wake window is a registration inside the Wi-Fi library (8 bytes) that deinit does
     * not release: unregister it first, or every broadcast on/off cycle leaks it. */
    if(radio_stats.receive_window_enabled) { radio_stats.receive_window_status=esp_wifi_internal_configure_wake_window(false,&receive_wake_window);radio_stats.receive_window_enabled=0; }
    wifi_live=0;esp_wifi_set_promiscuous(false);esp_wifi_stop();wifi_started=0;esp_wifi_deinit();
    incoming_retire=1;
    for(int i=0;i<100 && !incoming_parked;i++)vTaskDelay(pdMS_TO_TICKS(10));
    if(incoming_parked && incoming) { vQueueDelete(incoming);incoming=NULL; }
    radio_stats.rx_active=0;radio_stats.rx_claim=0;scan_state=0;++wifi_downs;
}
int wmp_radio_wifi(unsigned on) { if(on>1u)return 0;wifi_wanted=(uint8_t)on;return 1; }
unsigned wmp_radio_wifi_ready(void) { return wifi_ready; }
/* Wi-Fi power runs from the console loop and, for a fast party start, directly from the
 * Bluetooth host when broadcast is switched on. The lock keeps the two from overlapping. */
static portMUX_TYPE wifi_power_mux=portMUX_INITIALIZER_UNLOCKED;static volatile uint8_t wifi_power_busy;
static int wifi_power_enter(void) { int ok=0;portENTER_CRITICAL(&wifi_power_mux);if(!wifi_power_busy) { wifi_power_busy=1;ok=1; }portEXIT_CRITICAL(&wifi_power_mux);return ok; }
static void wifi_power_exit(void) { portENTER_CRITICAL(&wifi_power_mux);wifi_power_busy=0;portEXIT_CRITICAL(&wifi_power_mux); }
void wmp_radio_wifi_service(void) {
    if(!wifi_power_enter())return;
    if(wifi_wanted && !wifi_ready) { esp_err_t r=wifi_up();wifi_status=r;if(r!=ESP_OK)wifi_wanted=0; }
    else if(!wifi_wanted && wifi_ready && radio_stats.mode!=2u && !radio_stats.transition_pending)wifi_down();
    wifi_power_exit();
}
void wmp_radio_wifi_start_now(void) {
    /* Start only (about 20-30 ms); stopping waits for tasks to park and stays on the console loop. */
    if(!wifi_power_enter())return;
    if(wifi_wanted && !wifi_ready) { esp_err_t r=wifi_up();wifi_status=r;if(r!=ESP_OK)wifi_wanted=0; }
    wifi_power_exit();
}
void wmp_radio_wifi_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_WIFI_POWER query=%08lx wanted=%u ready=%u status=%ld ups=%lu downs=%lu\n",query,(unsigned)wifi_wanted,(unsigned)wifi_ready,(long)wifi_status,(unsigned long)wifi_ups,(unsigned long)wifi_downs);
}
int wmp_radio_role(unsigned mode) {
    if(mode!=0u && mode!=2u)return 0;
    command c={.mode=(uint8_t)mode,.copies=4,.keep_schedule=1,.payload=476,.pps=90};
    int queued=xQueueSend(commands,&c,0)==pdTRUE;if(queued && transmitter_task)xTaskNotifyGive(transmitter_task);return queued;
}
void wmp_radio_init(void) {
    /* Eight admitted repairs must not consume all staging for new originals.
     * Queue only immutable references into the existing64-slot audio pool. */
    source=xQueueCreate(SOURCE_QUEUE_DEPTH,sizeof(message));commands=xQueueCreate(2,sizeof(command));completed=xQueueCreate(TX_BUFFERS,sizeof(uint32_t));nacks=xQueueCreate(4,sizeof(nack_request));
    configASSERT(source && commands && completed && nacks);
    replay_buffer=heap_caps_calloc(1,sizeof(*replay_buffer),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    missing_audio=heap_caps_calloc(1,sizeof(*missing_audio),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    configASSERT(replay_buffer && missing_audio);
    radio_stats.replay_enabled=replay_enabled;
    radio_stats.nack_delay_us=nack_delay_us;
    radio_stats.repair_unicast=0;
    /* No network interfaces or IP sockets exist in this raw transport. Keep
     * the SDK event loop, without allocating an unused lwIP TCP/IP thread. */
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_SCAN_DONE,listen_scan_done,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_CONNECTED,association_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,association_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_AP_START,association_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_AP_STOP,association_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_AP_STACONNECTED,association_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_AP_STADISCONNECTED,association_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_CONNECTIONLESS_MODULE_WAKE_INTERVAL_START,connectionless_wake,NULL));
    /* Wi-Fi itself starts only when broadcast is turned on (wifi_up). The STA MAC is the
     * broadcast identity and is read from eFuse, so it is known while Wi-Fi is down. */
    ESP_ERROR_CHECK(esp_read_mac(wmp_mac,ESP_MAC_WIFI_STA));memcpy(tx_mac,wmp_mac,6);
    radio_stats.phy_mbps=selected_rate;
    radio_stats.tx_pace_us=transmit_pace_us;
    radio_stats.tx_window=tx_window;
    radio_stats.tx_action_frames=audio_action_frames;
    radio_stats.latency_us=requested_latency_us;
    /* Receive handoff runs at24 on core1, above the Wi-Fi task (23); it only
     * validates and queues, then blocks, so it never starves Wi-Fi. */
    /* Full dual-input/BLE diagnostics left only396 bytes at3072; retain the
     * priority/core placement while restoring at least512 bytes of margin. */
    configASSERT(xTaskCreatePinnedToCore(receiver,"wmp_rx",3584,0,24,&receiver_task,1)==pdPASS);radio_stats.rx_priority=24;
    const esp_timer_create_args_t wake_args={.callback=wake_transmitter,.name="wmp_tx_wake"};
    ESP_ERROR_CHECK(esp_timer_create(&wake_args,&transmit_wakeup));
    configASSERT(xTaskCreatePinnedToCore(transmitter,"wmp_tx",2304,0,20,&transmitter_task,0)==pdPASS);
    /* Warm cycle before Bluetooth allocates: the Wi-Fi driver keeps ~0.4 KB after its first
     * init (calibration state). Doing that first init now puts it at the bottom of the heap,
     * so later broadcast on/off cycles do not split the large free block solo codecs use. */
    if(wifi_up()==ESP_OK)wifi_down();
    radio_stats.ready=1;
}
