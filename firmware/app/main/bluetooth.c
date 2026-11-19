#include "wmp_codec.h"
#include "wmp_codecs.h"
#include "wmp_latm.h"
#include "node.h"
#include "wmp_sbc.h"
#include "wmp_sbc_queue.h"
#include "coex_trace.h"
#include "wmp_time.h"
#include "btstack.h"
#include "esp_random.h"
#include "btstack_port_esp32.h"
#include "ble/att_db_util.h"
#include "esp_timer.h"
#include "esp_bt.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <stdio.h>
#include <string.h>
volatile wmp_bt_stats bt_stats;
static QueueHandle_t commands;
static TaskHandle_t host_task;
static btstack_packet_callback_registration_t events;
static btstack_timer_source_t timer;
static portMUX_TYPE host_trace_lock=portMUX_INITIALIZER_UNLOCKED;
static unsigned host_trace_pending;
static unsigned long host_trace_query;
int wmp_bluetooth_host_trace(unsigned long query) {
    portENTER_CRITICAL(&host_trace_lock);
    int accepted=!host_trace_pending;
    if(accepted) { host_trace_query=query;host_trace_pending=1; }
    portEXIT_CRITICAL(&host_trace_lock);return accepted;
}
static void host_trace(void) {
    unsigned pending;unsigned long query;
    portENTER_CRITICAL(&host_trace_lock);pending=host_trace_pending;query=host_trace_query;host_trace_pending=0;portEXIT_CRITICAL(&host_trace_lock);
    if(!pending)return;
    /* The list belongs to this serialized host task. The UART task never
     * walks connection objects while callbacks can free or replace them. */
    unsigned count=0;btstack_linked_list_iterator_t it;hci_connections_get_iterator(&it);
    while(btstack_linked_list_iterator_has_next(&it)) {
        hci_connection_t *c=(hci_connection_t *)btstack_linked_list_iterator_next(&it);
        wmp_telemetry_printf("WMP_BTHOST query=%08lx handle=%u type=%u state=%u address=%02x:%02x:%02x:%02x:%02x:%02x\n",query,(unsigned)c->con_handle,(unsigned)c->address_type,(unsigned)c->state,c->address[0],c->address[1],c->address[2],c->address[3],c->address[4],c->address[5]);++count;
    }
    wmp_telemetry_printf("WMP_BTHOST_END query=%08lx count=%u hci_state=%u\n",query,count,(unsigned)hci_get_state());
}
/*16 blocks/8 subbands keep four-frame groups at512 samples. All advertised
 * channel modes and frequencies retain the same maximum bitpool/quality. */
static uint8_t codec_caps[4]={0xff,0x17,2,WMP_SBC_BITPOOL_CAP},codec_config[4],sdp[512];
static uint16_t phone_handle=HCI_CON_HANDLE_INVALID,ble_handle=HCI_CON_HANDLE_INVALID;
static uint16_t ble_link_units;static int64_t ble_link_next_us,ble_link_slow_since;static uint32_t ble_link_changes; /* see ble_link_tick() */
/* unit: byte-aligned copy of one AAC access unit taken out of LATM (master only, so it lives
 * in this lazily allocated block rather than in static RAM on every follower). */
typedef struct { wmp_sbc_queue input;wmp_audio_group pending,assembling;uint8_t unit[WMP_MAX_PAYLOAD]; } source_runtime;
static source_runtime *source_resources;
/* Original SBC frames per broadcast group (3..8). Fewer, larger groups halve
 * transmissions; a group still closes early at the raw-frame byte limit. */
static volatile uint8_t group_frames=4;
/* Codecs offered to the phone. The phone's original frames are rebroadcast unchanged.
 * Bit per WMP_CODEC_*; AAC/aptX stay off until every speaker can decode them. */
#ifndef WMP_CODEC_MASK
/* Codec endpoints registered at start. Which of them a phone may pick depends on the mode:
 * solo (broadcast off, Wi-Fi down) offers all of them; broadcast offers SBC and AAC-LC only
 * (aptX endpoints are reported in use), see broadcast_endpoints(). */
#define WMP_CODEC_MASK 0xfu /* SBC, AAC, aptX, aptX HD */
#endif
static volatile uint8_t active_codec=WMP_CODEC_SBC,active_channels=2;
static uint8_t aac_seid,aptx_seid,aptx_hd_seid;
static avdtp_stream_endpoint_t *aac_endpoint,*aptx_endpoint,*aptx_hd_endpoint;
/* Broadcast (Auracast-like) switch, off at every boot like Sony speakers: off = solo, Wi-Fi
 * down, every codec; on = Wi-Fi party, SBC (preferred) or AAC-LC. */
static volatile uint8_t broadcast_on;static uint32_t broadcast_changes;
unsigned wmp_bluetooth_broadcast(void) { return broadcast_on; }
/* Broadcast codec policy: SBC is preferred (every phone has it and every speaker decodes it
 * with the least RAM/CPU); BCODEC 1 / BLE opcode 6 also lets the phone choose AAC-LC. */
static volatile uint8_t broadcast_aac;
unsigned wmp_bluetooth_broadcast_aac(void) { return broadcast_aac; }
static int broadcast_allows(unsigned codec) { return codec==WMP_CODEC_SBC || (codec==WMP_CODEC_AAC && broadcast_aac); }
static uint8_t endpoint_hidden[3];
static void broadcast_endpoints(void) {
    /* Like a speaker that does not have the codec at all: while broadcasting, an endpoint the
     * party does not carry is unlinked from BTstack's endpoint list, so discovery never reports
     * it and no SET_CONFIGURATION can reach it. (Reporting it "in use" is not enough: a phone with
     * that codec pinned drops the link instead of falling back.) The endpoint memory is kept and
     * linked back for solo, so nothing is reallocated. Only an idle endpoint moves; a configured
     * one is BTstack's until the handover releases it. Runs on every host tick. */
    avdtp_stream_endpoint_t *e[3]={aac_endpoint,aptx_endpoint,aptx_hd_endpoint};
    uint8_t hide[3]={(uint8_t)(broadcast_on && !broadcast_aac),broadcast_on,broadcast_on};
    for(unsigned i=0;i<3u;i++) {
        if(!e[i] || hide[i]==endpoint_hidden[i] || e[i]->state!=AVDTP_STREAM_ENDPOINT_IDLE)continue;
        if(hide[i])btstack_linked_list_remove(avdtp_get_stream_endpoints(),(btstack_linked_item_t *)e[i]);
        else btstack_linked_list_add_tail(avdtp_get_stream_endpoints(),(btstack_linked_item_t *)e[i]);
        e[i]->sep.in_use=0;endpoint_hidden[i]=hide[i];
    }
}
static wmp_latm_state latm_state;static uint8_t codec_rtp_valid;static uint16_t codec_rtp_sequence;static uint64_t codec_next_sample;
static uint32_t codec_packets,codec_units,codec_bad,codec_unsupported;
unsigned wmp_bluetooth_codec(void) { return active_codec; }
unsigned wmp_bluetooth_channels(void) { return active_codec==WMP_CODEC_SBC?0u:active_channels; }
int wmp_bluetooth_group_frames(unsigned frames) { if(frames<3u || frames>WMP_MAX_GROUP_FRAMES)return 0;group_frames=(uint8_t)frames;return 1; }
static uint8_t have_rtp,tail_pending;
static uint32_t source_resource_starts,source_resource_stops,source_resource_failures;
static int64_t source_retry_us;
#define source_input (source_resources->input)
#define source_pending (source_resources->pending)
#define group (source_resources->assembling)
static void group_reset(void) {
    if(active_codec==WMP_CODEC_SBC)wmp_group_init(&group,group_frames);
    else { const wmp_codec_profile *p=wmp_codec_profile_get(active_codec);wmp_group_init_codec(&group,active_codec,p?p->group_units:1u);group.channels=active_channels; }
}
static int source_acquire(void) {
    if(source_resources)return 1;
    int64_t now=esp_timer_get_time();if(now<source_retry_us)return 0;
    source_resources=heap_caps_calloc(1,sizeof(*source_resources),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!source_resources) { ++source_resource_failures;source_retry_us=now+500000;return 0; }
    source_retry_us=0;
    wmp_sbc_queue_init(&source_input);group_reset();tail_pending=0;++source_resource_starts;return 1;
}
static int source_prepare(void) {
    if(!source_acquire())return 0;
    wmp_audio_preload();return 1;
}
static void source_release(void) {
    if(source_resources) { heap_caps_free(source_resources);source_resources=NULL;++source_resource_stops; }
}
static wmp_sbc_stream media_stream;
/* Standby suspend: a follower's own phone stream occupies the shared radio
 * (measured: 10-25x more follower loss) while its audio is discarded. Once a
 * peer has owned the party for over a second, suspend that stream. A phone
 * restart while the peer still owns it is suspended again; after the owner
 * stops, a restart is a normal takeover. */
static uint16_t a2dp_cid;static uint8_t a2dp_seid,standby_suspend_enabled=1;
/* Codec handover (Sony-style): broadcast switched on while the phone streams a codec the party
 * does not carry. Release the phone's A2DP signaling (the ACL stays), keep the codec's endpoint
 * hidden once idle, then connect back as initiator: BTstack's own configuration selects SBC.
 * A phone that reconnects first sees only the allowed endpoints. */
enum { HANDOVER_IDLE, HANDOVER_RELEASING, HANDOVER_WAIT, HANDOVER_CONNECTING };
static uint8_t handover_state,handover_tries,handover_status,a2dp_peer_valid,stream_open;static bd_addr_t a2dp_peer;
static int64_t handover_at,handover_retry_at;static uint32_t handovers,handover_done,handover_failures;
static void handover_tick(int64_t now);
static uint8_t wifi_deferred; /* broadcast on, Wi-Fi waits for the solo-only codec to be released */
static void wifi_release_deferred(void) { if(wifi_deferred && broadcast_on) { wifi_deferred=0;wmp_radio_wifi(1);wmp_radio_wifi_start_now(); } }
static uint8_t resume_pending,handover_was_playing,avrcp_self_connected;static int64_t resume_at,resume_deadline,avrcp_wait_until; /* AVRCP Play after a handover */
static uint32_t config_rejects;static uint8_t a2dp_trace_enabled=1;
/* Backstop for a phone that configures from cached capabilities without a fresh discovery:
 * while broadcasting, refuse SET_CONFIGURATION of any endpoint the party does not carry. */
static uint8_t config_validator(const avdtp_stream_endpoint_t *endpoint,const uint8_t *event,uint16_t size) {
    (void)event;(void)size;
    if(!broadcast_on)return 0;
    if((endpoint==aac_endpoint && !broadcast_aac) || endpoint==aptx_endpoint || endpoint==aptx_hd_endpoint) { ++config_rejects;return AVDTP_ERROR_CODE_SEP_IN_USE; }
    return 0;
}
static void endpoint_log(const char *what,unsigned seid) {
    const avdtp_stream_endpoint_t *e[3]={aac_endpoint,aptx_endpoint,aptx_hd_endpoint};unsigned flags=0;
    for(unsigned i=0;i<3u;i++)if(e[i])flags|=((unsigned)endpoint_hidden[i]<<(i*4u))|((unsigned)(e[i]->state&7u)<<(i*4u+1u));
    wmp_telemetry_printf("WMP_A2DP_CONFIG %s seid=%u codec=%u broadcast=%u handover=%u endpoints=%03x rejects=%lu%c",what,seid,(unsigned)active_codec,(unsigned)broadcast_on,(unsigned)handover_state,flags,(unsigned long)config_rejects,10);
}
static int64_t peer_owned_since,next_standby_suspend;static uint32_t standby_suspends,standby_suspend_status;
static volatile uint8_t party_on; /* defined with its initial value below */
static volatile uint8_t start_reject_enabled=1;static uint32_t start_rejects,start_accepts,start_reject_status;
/* Follower sniff: an idle (suspended) phone ACL in active mode is polled
 * every few tens of ms and takes the shared radio even in the Wi-Fi phase.
 * Sniff lets it wake only every ~0.4-0.5 s. Exit before any own streaming
 * or leadership. Mode 0 active, 2 sniff (HCI_EVENT_MODE_CHANGE). */
static uint8_t follower_sniff_enabled=1,classic_mode;static int64_t next_sniff_request;
static uint32_t sniff_requests,sniff_exits,sniff_events,sniff_status,sniff_interval;
int wmp_bluetooth_follower_sniff(unsigned enabled) { if(enabled>1u)return 0;follower_sniff_enabled=(uint8_t)enabled;next_sniff_request=0;return 1; }
void wmp_bluetooth_sniff_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_SNIFF query=%08lx enabled=%u mode=%u interval_slots=%lu requests=%lu exits=%lu events=%lu status=%lu\n",query,(unsigned)follower_sniff_enabled,(unsigned)classic_mode,(unsigned long)sniff_interval,(unsigned long)sniff_requests,(unsigned long)sniff_exits,(unsigned long)sniff_events,(unsigned long)sniff_status);
}
int wmp_bluetooth_start_reject(unsigned enabled) { if(enabled>1u)return 0;start_reject_enabled=(uint8_t)enabled;return 1; }
int wmp_bluetooth_standby_suspend(unsigned enabled) { if(enabled>1u)return 0;standby_suspend_enabled=(uint8_t)enabled;return 1; }
static int64_t last_media_us,candidate_at,leader_at;
static uint8_t own_audio_seen,role_request,requested_role;
static char classic_name[32],ble_name[32];
static int becoming_source(void) { return (role_request && requested_role==2u) || (radio_stats.transition_pending && radio_stats.transition_target==2u); }
static int party_network_ready(void) {
    if(!radio_stats.infrastructure_requested)return 1;
    uint32_t epoch=0;uint64_t ready=0;
    return wmp_radio_clock_ready() && wmp_source_clock_shared_ready(&epoch,&ready) && (uint64_t)esp_timer_get_time()>=ready;
}
static void queue_stats(void) {
    if(!source_resources) { bt_stats.source_queue_groups=bt_stats.source_queue_bytes=0;return; }
    bt_stats.source_queue_groups=source_input.count;bt_stats.source_queue_bytes=source_input.used;
    if(source_input.count>bt_stats.source_queue_max_groups)bt_stats.source_queue_max_groups=source_input.count;
    if(source_input.used>bt_stats.source_queue_max_bytes)bt_stats.source_queue_max_bytes=source_input.used;
}
/* Solo publisher: with broadcast off the groups go straight to this speaker's player on its
 * own clock (esp_timer), SOLO_LATENCY_US ahead of arrival. If the phone's clock drifts the
 * lead out of [SOLO_LEAD_MIN_US, SOLO_LEAD_MAX_US], a new generation re-anchors playback. */
#define SOLO_LATENCY_US 200000
#define SOLO_LEAD_MIN_US 40000
#define SOLO_LEAD_MAX_US 450000
static uint64_t solo_generation,solo_sequence,solo_cursor,solo_anchor_sample;static int64_t solo_anchor_us;static uint32_t solo_rate,solo_rebases,solo_deferred,solo_groups;
static void solo_drain(void) {
    if(!source_resources)return;
    for(unsigned i=0;i<WMP_SBC_QUEUE_GROUPS;i++) {
        uint32_t rate;uint64_t arrived;
        if(!wmp_sbc_queue_peek(&source_input,&source_pending,&rate,&arrived))break;
        int64_t now=esp_timer_get_time();
        int64_t pts=solo_generation && rate==solo_rate?solo_anchor_us+(int64_t)((solo_cursor-solo_anchor_sample)*1000000u/rate):0;
        if(!pts || pts-now<SOLO_LEAD_MIN_US || pts-now>SOLO_LEAD_MAX_US) {
            solo_generation=((uint64_t)esp_random()<<32)|esp_random();if(!solo_generation)solo_generation=1;
            solo_anchor_us=now+SOLO_LATENCY_US;solo_anchor_sample=solo_cursor;solo_rate=rate;pts=solo_anchor_us;++solo_rebases;
        }
        wmp_header h={.generation=solo_generation,.sequence=solo_sequence,.type=WMP_AUDIO,.fragment_count=1,.first_sample=solo_cursor,
            .presentation_us=(uint64_t)pts,.sample_rate=rate,.sample_count=source_pending.sample_count,.payload_bytes=source_pending.bytes,
            .frame_count=source_pending.frames,.codec=source_pending.codec,.channels=source_pending.codec?source_pending.channels:0u};
        if(!wmp_audio_offer(&h,source_pending.payload,1)) { ++solo_deferred;break; }   /* player pool full: retry next tick */
        ++solo_sequence;solo_cursor+=source_pending.sample_count;++solo_groups;
        configASSERT(wmp_sbc_queue_pop(&source_input));
    }
}
static void drain_source(void) {
    if(!broadcast_on) { solo_drain();queue_stats();return; }
    if(!source_resources) { queue_stats();return; }
    if(radio_stats.mode==2u && !wmp_radio_source_ready()) {
        for(unsigned i=0;i<source_input.count;i++)bt_stats.preparation_frames+=source_input.entries[(source_input.head+i)%WMP_SBC_QUEUE_GROUPS].frames;
        if(source_resources) { wmp_sbc_queue_init(&source_input); }bt_stats.preparation_frames+=group.frames;group_reset();tail_pending=0;queue_stats();return;
    }
    if(radio_stats.mode!=2u) {
        /* Keep the opening music while automatic ownership is being acquired.
         * A standby phone under an active peer deliberately has no DAC owner. */
        if(becoming_source()) { queue_stats();return; }
        /* Recent RTP activity may expire between START and the first packet,
         * or during an input gap. Keep this prepared queue until the actual
         * AVDTP transport ends or a peer takes ownership. */
        if(!((bt_stats.transport_started || bt_stats.streaming) && party_network_ready() && !radio_stats.rx_active && !radio_stats.rx_claim)) { wmp_sbc_queue_init(&source_input);source_release(); }
        queue_stats();return;
    }
    for(unsigned i=0;i<WMP_SBC_QUEUE_GROUPS;i++) {
        uint32_t rate;uint64_t arrived;
        if(!wmp_sbc_queue_peek(&source_input,&source_pending,&rate,&arrived))break;
        if(!wmp_radio_offer(&source_pending,rate,arrived)) { ++bt_stats.source_queue_deferred;break; }
        uint64_t age=(uint64_t)esp_timer_get_time()-arrived;
        if(age>bt_stats.source_queue_max_age_us)bt_stats.source_queue_max_age_us=(uint32_t)age;
        configASSERT(wmp_sbc_queue_pop(&source_input));++bt_stats.source_groups;own_audio_seen=1;
    }
    queue_stats();
}
static int flush_group(void) {
    if(!source_resources)return 1;
    const wmp_codec_profile *profile=wmp_codec_profile_get(group.codec);
    int full=profile && profile->stream_quantum?group.sample_count>=profile->group_samples:group.frames==group.target;
    if(!group.frames || (!full && bt_stats.streaming && !tail_pending))return 1;
    int eligible=radio_stats.mode==2u || becoming_source() || (bt_stats.streaming && party_network_ready() && !radio_stats.rx_active && !radio_stats.rx_claim);
    if(eligible && (!source_resources || !wmp_sbc_queue_push(&source_input,&group,bt_stats.rate,(uint64_t)esp_timer_get_time()))) {
        ++bt_stats.source_queue_full;return 0;
    }
    queue_stats();
    group_reset();tail_pending=0;return 1;
}
/* Phone input timing: arrival gaps and how far the delivered media runs
 * ahead of (burst/backlog) or behind (outage) wall time since the stream
 * (re)started. Separates phone/app stalls from board-side faults. */
static int64_t input_prev_us,input_base_us;static uint64_t input_base_sample;
static uint32_t input_gap_max_us,input_gaps_50ms,input_gaps_200ms,input_rebases;static int32_t input_ahead_max_us,input_behind_max_us;
static void input_timing(int64_t now,uint64_t sample,uint32_t rate) {
    if(input_prev_us) {
        uint32_t gap=(uint32_t)(now-input_prev_us);if(gap>input_gap_max_us)input_gap_max_us=gap;
        if(gap>50000u)++input_gaps_50ms;
        if(gap>200000u)++input_gaps_200ms;
    }
    if(!input_base_us || !rate || now-input_prev_us>1000000 || sample<input_base_sample) { input_base_us=now;input_base_sample=sample;++input_rebases; }
    else {
        int64_t media=(int64_t)((sample-input_base_sample)*1000000u/rate),wall=now-input_base_us,ahead=media-wall;
        if(ahead>input_ahead_max_us)input_ahead_max_us=(int32_t)(ahead>INT32_MAX?INT32_MAX:ahead);
        if(-ahead>input_behind_max_us)input_behind_max_us=(int32_t)(-ahead>INT32_MAX?INT32_MAX:-ahead);
    }
    input_prev_us=now;
}
/* One unit (AAC access unit) or stream chunk (aptX) from the phone into broadcast groups,
 * with the same ownership rules as the SBC path. */
static int codec_owner_ready(void) {
    if(radio_stats.mode==2u && !wmp_radio_source_ready()) { ++bt_stats.preparation_frames;return 0; }
    if(radio_stats.mode!=2u && !becoming_source() && (radio_stats.rx_active || radio_stats.rx_claim || !party_network_ready())) {
        ++bt_stats.standby_frames;if(source_resources && group.frames)group_reset();return 0;
    }
    if(!source_resources && !source_acquire()) { ++bt_stats.source_blocked_frames;return 0; }
    if(!flush_group()) { ++bt_stats.source_blocked_frames;return 0; }
    return 1;
}
static void codec_push_unit(const uint8_t *payload,uint16_t size,wmp_latm_unit at,uint16_t samples) {
    if(!codec_owner_ready()) { codec_next_sample+=samples;return; }
    if(!wmp_latm_copy(payload,size,at,source_resources->unit,sizeof(source_resources->unit))) { ++codec_bad;codec_next_sample+=samples;return; }
    const uint8_t *unit=source_resources->unit;uint16_t bytes=at.bytes;
    int pushed=wmp_group_push_unit(&group,unit,bytes,codec_next_sample,samples);
    if((pushed==-3 || pushed==-2) && group.frames) { tail_pending=1;if(flush_group())pushed=wmp_group_push_unit(&group,unit,bytes,codec_next_sample,samples); }
    if(pushed<0)++bt_stats.source_blocked_frames;
    if(pushed==1)flush_group();
    codec_next_sample+=samples;++codec_units;
}
static void codec_push_stream(const uint8_t *data,uint32_t bytes) {
    const wmp_codec_profile *p=wmp_codec_profile_get(active_codec);
    if(!p || !p->stream_quantum || bytes%p->stream_quantum) { ++codec_bad;return; }
    while(bytes) {
        uint16_t chunk=(uint16_t)(bytes>4096u?4096u/p->stream_quantum*p->stream_quantum:bytes);
        if(!codec_owner_ready()) { codec_next_sample+=wmp_codec_stream_samples(p,chunk);data+=chunk;bytes-=chunk;continue; }
        uint16_t used=0;int pushed=wmp_group_push_stream(&group,data,chunk,codec_next_sample,&used);
        if(pushed==-3 && group.frames) { tail_pending=1;if(flush_group())pushed=wmp_group_push_stream(&group,data,chunk,codec_next_sample,&used); }
        if(pushed<0 || !used) { ++bt_stats.source_blocked_frames;codec_next_sample+=wmp_codec_stream_samples(p,chunk);break; }
        codec_next_sample+=wmp_codec_stream_samples(p,used);data+=used;bytes-=used;++codec_units;
        if(pushed==1)flush_group();
    }
}
static wmp_latm_unit latm_units[WMP_LATM_MAX_UNITS]; /* host task only; kept off its nearly full stack */
static void codec_media(const uint8_t *packet,uint16_t bytes) {
    const uint8_t *payload=packet;uint32_t size=bytes;
    if(active_codec!=WMP_CODEC_APTX) {
        /* AAC and aptX HD media carry an RTP header; classic aptX is bare codewords. */
        wmp_rtp_header h;
        if(!wmp_rtp_read(packet,bytes,&h,&payload,&size)) { ++bt_stats.rtp_bad;return; }
        if(codec_rtp_valid && h.sequence!=(uint16_t)(codec_rtp_sequence+1u)) {
            uint16_t lost=(uint16_t)(h.sequence-codec_rtp_sequence-1u);if(lost<0x8000u)bt_stats.rtp_gaps+=lost;
            ++bt_stats.group_discontinuity;if(source_resources && group.frames) { tail_pending=1;flush_group(); }
        }
        codec_rtp_valid=1;codec_rtp_sequence=h.sequence;
    }
    ++bt_stats.rtp_packets;++codec_packets;last_media_us=esp_timer_get_time();bt_stats.streaming=1;
    input_timing(last_media_us,codec_next_sample,bt_stats.rate);
    if(radio_stats.mode==2u)own_audio_seen=1;
    if(active_codec==WMP_CODEC_AAC) {
        wmp_latm_unit *units=latm_units;uint8_t count=0;
        int r=wmp_latm_parse(&latm_state,bt_stats.rate,active_channels,payload,(uint16_t)size,units,WMP_LATM_MAX_UNITS,&count);
        if(r!=1) { if(r==-1)++codec_unsupported;else ++codec_bad;return; }
        for(unsigned i=0;i<count;i++)codec_push_unit(payload,(uint16_t)size,units[i],1024u);
    } else codec_push_stream(payload,size);
    drain_source();
}
static int64_t media_prev_us;
static void media(uint8_t seid,uint8_t *packet,uint16_t bytes) {
    { int64_t t=esp_timer_get_time();
      if(media_prev_us && t-media_prev_us>100000)wmp_telemetry_printf("WMP_MEDIA_GAP ms=%lu at_ms=%lu ble=%u%c",(unsigned long)((t-media_prev_us)/1000),(unsigned long)(t/1000),(unsigned)bt_stats.ble,10);
      media_prev_us=t; }
    if(active_codec!=WMP_CODEC_SBC) { (void)seid;codec_media(packet,bytes);return; }
    (void)seid;wmp_rtp_sbc h;const uint8_t *frames;size_t available;
    int result=wmp_rtp_sbc_read(packet,bytes,&h,&frames,&available);
    if(result<0) { ++bt_stats.rtp_fragments;return; }
    if(!result) { ++bt_stats.rtp_bad;return; }
    ++bt_stats.rtp_packets;last_media_us=esp_timer_get_time();bt_stats.streaming=1;
    input_timing(last_media_us,media_stream.next_sample,bt_stats.rate);
    wmp_coex_trace_record(WMP_RT_RTP,h.frames,bytes,h.sequence);
    if(radio_stats.mode==2u)own_audio_seen=1;
    if(!have_rtp) { wmp_sbc_stream_reset(&media_stream);have_rtp=1; }
    uint16_t distance;uint64_t sample;
    int accepted=wmp_sbc_stream_accept(&media_stream,&h,&sample,&distance);
    if(!accepted)return;
    if(accepted<0) { ++bt_stats.rtp_bad;return; }
    if(distance && distance!=1u) {
        if(distance<0x8000u)bt_stats.rtp_gaps+=distance-1u;
        ++bt_stats.group_discontinuity;
        if(source_resources && group.frames) { tail_pending=1;flush_group(); }
    }
    size_t at=0;
    for(unsigned i=0;i<h.frames;i++) {
        wmp_sbc_info info;if(!wmp_sbc_info_read(frames+at,available-at,&info)) { ++bt_stats.rtp_bad;return; }
        ++bt_stats.sbc_frames;bt_stats.sbc_bytes+=info.bytes;bt_stats.rate=info.rate;
        if(radio_stats.mode==2u && !wmp_radio_source_ready()) {
            ++bt_stats.preparation_frames;at+=info.bytes;sample+=info.samples;continue;
        }
        if(radio_stats.mode!=2u && !becoming_source() && (radio_stats.rx_active || radio_stats.rx_claim || !party_network_ready())) {
            ++bt_stats.standby_frames;if(source_resources && group.frames) { group_reset();tail_pending=0; }
            at+=info.bytes;sample+=info.samples;continue;
        }
        /* Peer release can make this RTP burst eligible before the next host
         * timer. Acquire the opening FIFO once on that ownership edge; this
         * callback runs in the serialized host task, not the radio/ISR.
         * Codec and output-device preparation remain in source_prepare. */
        if(!source_resources && !source_acquire()) { ++bt_stats.source_blocked_frames;at+=info.bytes;sample+=info.samples;continue; }
        if(!flush_group()) { ++bt_stats.source_blocked_frames;at+=info.bytes;sample+=info.samples;continue; }
        int pushed=wmp_group_push(&group,frames+at,info.bytes,sample,info.samples);
        if(pushed==-3) {
            ++bt_stats.group_discontinuity;tail_pending=1;
            if(flush_group())pushed=wmp_group_push(&group,frames+at,info.bytes,sample,info.samples);
        }
        if(pushed==-2 && group.frames) {
            /* Byte limit reached before the frame target: close this group. */
            tail_pending=1;if(flush_group())pushed=wmp_group_push(&group,frames+at,info.bytes,sample,info.samples);
        }
        if(pushed<0)++bt_stats.source_blocked_frames;
        if(pushed==1)flush_group();
        at+=info.bytes;sample+=info.samples;
    }
    drain_source();
}
/* Phone (re)configured its stream. A different codec, rate or channel count makes every
 * byte still assembling or queued here belong to the old stream: drop it all in this one
 * place so the new stream never mixes with it or waits behind it. Groups already handed to
 * the radio carry their own codec id and still decode on every speaker. */
static uint32_t codec_switches;
static void codec_configure(uint8_t codec,uint8_t channels,uint32_t rate) {
    uint32_t previous_rate=bt_stats.rate;
    int changed=codec!=active_codec || channels!=active_channels || (previous_rate && rate!=previous_rate);
    bt_stats.rate=rate;active_codec=codec;active_channels=channels;
    have_rtp=0;codec_rtp_valid=0;latm_state=(wmp_latm_state){0};
    if(source_resources && (changed || radio_stats.mode!=2u)) {
        for(unsigned i=0;i<source_input.count;i++)bt_stats.preparation_frames+=source_input.entries[(source_input.head+i)%WMP_SBC_QUEUE_GROUPS].frames;
        bt_stats.preparation_frames+=group.frames;
        wmp_sbc_queue_init(&source_input);group_reset();
    }
    tail_pending=0;queue_stats();
    if(changed)++codec_switches;
    if(radio_stats.mode==2u && previous_rate && rate!=previous_rate)wmp_radio_role(2);
}
static void a2dp_event(uint8_t type,uint16_t channel,uint8_t *p,uint16_t n) {
    (void)channel;if(type!=HCI_EVENT_PACKET || n<3u || p[0]!=HCI_EVENT_A2DP_META)return;
    /* Signaling events are rare (never per media packet): keep a short ordered trace. */
    if(a2dp_trace_enabled)wmp_telemetry_printf("WMP_A2DP_EVENT sub=%u bytes=%u b3=%u b4=%u b5=%u b6=%u last=%u at_ms=%lu%c",(unsigned)p[2],(unsigned)n,(unsigned)(n>3u?p[3]:0u),(unsigned)(n>4u?p[4]:0u),(unsigned)(n>5u?p[5]:0u),(unsigned)(n>6u?p[6]:0u),(unsigned)p[n-1u],(unsigned long)(esp_timer_get_time()/1000),10);
    switch(p[2]) {
    case A2DP_SUBEVENT_SIGNALING_MEDIA_CODEC_SBC_CONFIGURATION: {
        codec_configure(WMP_CODEC_SBC,2,a2dp_subevent_signaling_media_codec_sbc_configuration_get_sampling_frequency(p));
        endpoint_log("sbc",a2dp_subevent_signaling_media_codec_sbc_configuration_get_local_seid(p));
        break;
    }
    case A2DP_SUBEVENT_SIGNALING_MEDIA_CODEC_MPEG_AAC_CONFIGURATION:
    case A2DP_SUBEVENT_SIGNALING_MEDIA_CODEC_OTHER_CONFIGURATION: {
        uint8_t codec=WMP_CODEC_SBC,channels=2;uint32_t rate=0;
        if(p[2]==A2DP_SUBEVENT_SIGNALING_MEDIA_CODEC_MPEG_AAC_CONFIGURATION) {
            codec=WMP_CODEC_AAC;rate=a2dp_subevent_signaling_media_codec_mpeg_aac_configuration_get_sampling_frequency(p);
            channels=a2dp_subevent_signaling_media_codec_mpeg_aac_configuration_get_num_channels(p);
        } else {
            const uint8_t *info=a2dp_subevent_signaling_media_codec_other_configuration_get_media_codec_information(p);
            uint16_t len=a2dp_subevent_signaling_media_codec_other_configuration_get_media_codec_information_len(p);
            uint32_t vendor=len>=7u?((uint32_t)info[0]|((uint32_t)info[1]<<8)|((uint32_t)info[2]<<16)|((uint32_t)info[3]<<24)):0u;
            unsigned id=len>=7u?((unsigned)info[4]|((unsigned)info[5]<<8)):0u;
            if(vendor==0x4fu && id==1u)codec=WMP_CODEC_APTX;else if(vendor==0xd7u && id==0x24u)codec=WMP_CODEC_APTX_HD;
            if(len>=7u)rate=(info[6]&0x10u)?48000u:44100u;
        }
        if(codec==WMP_CODEC_SBC || !rate || (channels!=1u && channels!=2u)) { ++codec_unsupported;break; }
        codec_configure(codec,channels,rate);endpoint_log("other",0);
        break;
    }
    case A2DP_SUBEVENT_START_STREAM_REQUESTED: {
        /* Follower ignores its own phone: while another board leads the party, refuse the
         * stream start so no A2DP media competes with the broadcast for the shared radio. */
        uint16_t cid=a2dp_subevent_start_stream_requested_get_a2dp_cid(p);uint8_t seid=a2dp_subevent_start_stream_requested_get_local_seid(p);
        int peer_leads=radio_stats.mode!=2u && (radio_stats.rx_active || radio_stats.rx_claim) && !becoming_source();
        if(start_reject_enabled && peer_leads) { start_reject_status=a2dp_sink_start_stream_reject(cid,seid);++start_rejects; }
        else { start_reject_status=a2dp_sink_start_stream_accept(cid,seid);++start_accepts; }
        break;
    }
    case A2DP_SUBEVENT_SIGNALING_CONNECTION_ESTABLISHED:
        if(!a2dp_subevent_signaling_connection_established_get_status(p)) {
            a2dp_cid=a2dp_subevent_signaling_connection_established_get_a2dp_cid(p);
            a2dp_subevent_signaling_connection_established_get_bd_addr(p,a2dp_peer);a2dp_peer_valid=1;
        } else if(handover_state==HANDOVER_CONNECTING) { handover_at=esp_timer_get_time()+1000000;handover_status=a2dp_subevent_signaling_connection_established_get_status(p); }
        break;
    case A2DP_SUBEVENT_SIGNALING_CONNECTION_RELEASED:
        a2dp_cid=0;a2dp_seid=0;stream_open=0;bt_stats.streaming=bt_stats.transport_started=0;have_rtp=0;
        if(handover_state==HANDOVER_RELEASING) { handover_state=HANDOVER_WAIT;handover_at=esp_timer_get_time()+150000; }
        break;
    case A2DP_SUBEVENT_STREAM_ESTABLISHED:
        if(!a2dp_subevent_stream_established_get_status(p)) {
            a2dp_cid=a2dp_subevent_stream_established_get_a2dp_cid(p);a2dp_seid=a2dp_subevent_stream_established_get_local_seid(p);stream_open=1;
            if(handover_state==HANDOVER_CONNECTING) { handover_state=HANDOVER_IDLE;++handover_done;
                if(handover_was_playing) { resume_pending=1;resume_at=esp_timer_get_time()+50000;resume_deadline=resume_at+8000000; } }
            endpoint_log("open",a2dp_seid);
        } else if(handover_state==HANDOVER_CONNECTING) { handover_at=esp_timer_get_time()+1000000;handover_status=a2dp_subevent_stream_established_get_status(p); }
        break;
    case A2DP_SUBEVENT_STREAM_STARTED:
        a2dp_cid=a2dp_subevent_stream_started_get_a2dp_cid(p);a2dp_seid=a2dp_subevent_stream_started_get_local_seid(p);wmp_crumb(1,a2dp_seid);
        /* Publish the active transport before draining: otherwise a first
         * stream event can allocate and immediately retire its own queue. */
        bt_stats.streaming=bt_stats.transport_started=1;have_rtp=0;
        if(!radio_stats.rx_active && !radio_stats.rx_claim)source_prepare();
        /* Pause/resume retains the party clock and output sample cursor. */
        if(source_resources && group.frames && group.frames<group.target)tail_pending=1;
        flush_group();drain_source();
        break;
    case A2DP_SUBEVENT_STREAM_RELEASED:
        stream_open=0;
        /* fall through */
    case A2DP_SUBEVENT_STREAM_SUSPENDED:
        bt_stats.streaming=bt_stats.transport_started=0;if(source_resources && group.frames)tail_pending=1;break;
    default:break;
    }
}
/* Music Party (PartyBoost-style): while the party is ON the speaker accepts
 * no new Classic connections (no inquiry or page scan); an existing phone
 * link is kept. To add a phone: party OFF, connect, party ON. Page/inquiry
 * scanning also takes the shared radio, so a party speaker stops it. BLE
 * stays connectable for the control app. */
/* PartyBoost-style automatic party: OFF at boot (phones may connect); ON while this speaker is the
 * master or plays another master's party; OFF again 5 s after the party ends. PARTY 0/1 = manual. */
/* party_connect: a paired phone may still connect during a party (low-duty page scan only). */
static volatile uint8_t party_on=0,party_auto=1,party_connect=1;static uint32_t party_changes;static int64_t party_idle_since;
static void classic_visibility(void) {
    /* One phone per speaker: no scanning once a phone is connected. During a party only the
     * low-duty page scan stays on (reconnects), never inquiry scan (new pairing). */
    unsigned visible=!party_on && !bt_stats.phone?1u:0u,connectable=!bt_stats.phone && (!party_on || party_connect)?1u:0u;
    gap_discoverable_control((int)visible);gap_connectable_control((int)connectable);bt_stats.classic_visible=visible;
}
static void peer_identity(const uint8_t *wire,int le) {
    uint8_t address[6];reverse_bd_addr(wire,address);
    uint32_t hi=((uint32_t)address[0]<<24)|((uint32_t)address[1]<<16)|((uint32_t)address[2]<<8)|address[3];
    uint32_t lo=((uint32_t)address[4]<<8)|address[5];
    if(le) { bt_stats.ble_peer_hi=hi;bt_stats.ble_peer_lo=lo; }else { bt_stats.phone_peer_hi=hi;bt_stats.phone_peer_lo=lo; }
}
static void event(uint8_t type,uint16_t channel,uint8_t *p,uint16_t n) {
    (void)channel;if(type!=HCI_EVENT_PACKET || n<3u)return;
    if(p[0]==BTSTACK_EVENT_STATE) {
        bt_stats.working=btstack_event_state_get_state(p)==HCI_STATE_WORKING;
        if(bt_stats.working) {
            gap_set_page_scan_type(PAGE_SCAN_MODE_STANDARD);gap_set_page_scan_activity(0x0800,0x0012); /* 11.25 ms every 1.28 s */
            classic_visibility();
            uint8_t address[6]={0};gap_advertisements_set_params(320,320,0,0,address,7,0);
            /* 200 ms: a phone's low-power scan window always catches an advert, and its full-duty
             * initiating scan (which starves its own A2DP) ends within ~200 ms instead of ~1 s.
             * Cost: one 3-channel advert per 200 ms, about 0.6 % of airtime. */
            static uint8_t advertising[31];size_t length=strlen(ble_name);
            /* Dual mode: General Discoverable, with BR/EDR supported. Bit2
             * would falsely advertise this A2DP speaker as LE-only. */
            advertising[0]=2;advertising[1]=1;advertising[2]=2;advertising[3]=(uint8_t)(length+1);advertising[4]=9;memcpy(advertising+5,ble_name,length);
            gap_advertisements_set_data((uint8_t)(length+5),advertising);gap_advertisements_enable(1);
            bd_addr_t local;gap_local_bd_addr(local);wmp_telemetry_printf("WMP_BT_READY addr=%s\n",bd_addr_to_str(local));
        }
    } else if(p[0]==HCI_EVENT_CONNECTION_COMPLETE && n>=13u) {
        if(p[2]) { ++bt_stats.connection_failures;bt_stats.last_connection_status=p[2]; }
        else if(p[11]==1u) { phone_handle=little_endian_read_16(p,3);bt_stats.phone=1;peer_identity(p+5,0);classic_visibility(); }
    } else if(p[0]==HCI_EVENT_META_GAP && n>=33u && p[2]==GAP_SUBEVENT_LE_CONNECTION_COMPLETE) {
        unsigned status=gap_subevent_le_connection_complete_get_status(p);
        wmp_telemetry_printf("WMP_LE_CONNECTION status=%u handle=%u normalized=1\n",status,(unsigned)gap_subevent_le_connection_complete_get_connection_handle(p));
        if(status) { ++bt_stats.connection_failures;bt_stats.last_connection_status=status;return; }
        /* BTstack normalizes legacy and enhanced controller events here. */
        ble_handle=gap_subevent_le_connection_complete_get_connection_handle(p);bt_stats.ble=1;peer_identity(p+8,1);wmp_gatt_connection(ble_handle);
        wmp_telemetry_printf("WMP_BLE_CONNECT at_ms=%lu interval=%u%c",(unsigned long)(esp_timer_get_time()/1000),(unsigned)gap_subevent_le_connection_complete_get_conn_interval(p),10);
        bt_stats.ble_interval=gap_subevent_le_connection_complete_get_conn_interval(p);bt_stats.ble_latency=gap_subevent_le_connection_complete_get_conn_latency(p);bt_stats.ble_timeout=gap_subevent_le_connection_complete_get_supervision_timeout(p);
        /* Short physical anchors with idle skipping, rather than a500ms
         * interval through connection-update/discovery procedures. */
        /* Ask for 60-75 ms at once. Left at the phone's own 7.5-30 ms interval, service discovery
         * runs back-to-back connection events and the phone's A2DP stream to this speaker starves
         * (measured: 232 lost RTP packets and 51 underruns per app connect at 30 ms; 0 and 0 when
         * 60-75 ms was requested at connection). ble_link_tick() adapts the interval afterwards. */
        gap_request_connection_parameter_update(ble_handle,48,60,0,400);++bt_stats.ble_parameter_requests;
        ble_link_units=60;ble_link_slow_since=0;ble_link_next_us=esp_timer_get_time()+1000000;
    } else if(p[0]==HCI_EVENT_MODE_CHANGE && n>=8u) {
        ++sniff_events;sniff_status=p[2];
        if(!p[2] && little_endian_read_16(p,3)==phone_handle) { classic_mode=p[5];sniff_interval=little_endian_read_16(p,6); }
    } else if(p[0]==HCI_EVENT_ROLE_CHANGE && n>=10u) {
        ++bt_stats.role_events;bt_stats.role_status=p[2];
        if(!p[2])bt_stats.classic_role=p[9];
        wmp_telemetry_printf("WMP_CLASSIC_ROLE_EVENT status=%u role=%u\n",p[2],p[9]);
    } else if(p[0]==HCI_EVENT_LE_META && n>=12u && p[2]==HCI_SUBEVENT_LE_CONNECTION_UPDATE_COMPLETE) {
        bt_stats.ble_parameter_status=p[3];
        if(!p[3]) { bt_stats.ble_interval=little_endian_read_16(p,6);bt_stats.ble_latency=little_endian_read_16(p,8);bt_stats.ble_timeout=little_endian_read_16(p,10); }
        wmp_telemetry_printf("WMP_BLE_UPDATE status=%u interval=%u latency=%u at_ms=%lu%c",(unsigned)p[3],(unsigned)little_endian_read_16(p,6),(unsigned)little_endian_read_16(p,8),(unsigned long)(esp_timer_get_time()/1000),10);
        /* Android boosts the link to 7.5 ms while it discovers services (twice per app connect,
         * ~1 s each). An LE event every 7.5 ms leaves no room for 3.75 ms A2DP packets on the shared
         * radio: the phone retries past its flush deadline and drops them (~138 lost per connect).
         * Ask straight back for the current tier whenever the phone goes below 30 ms. */
        if(!p[3] && ble_handle!=HCI_CON_HANDLE_INVALID && little_endian_read_16(p,6)<24u) {
            uint16_t want=ble_link_units?ble_link_units:60u;
            gap_request_connection_parameter_update(ble_handle,(uint16_t)(want*4u/5u),want,0,400);++bt_stats.ble_parameter_requests;
        }
    } else if(p[0]==HCI_EVENT_IO_CAPABILITY_REQUEST) {
        ++bt_stats.ssp_requests;
    } else if(p[0]==HCI_EVENT_SIMPLE_PAIRING_COMPLETE && n>=9u) {
        bt_stats.pairing_status=p[2];
    } else if(p[0]==HCI_EVENT_AUTHENTICATION_COMPLETE && n>=5u) {
        bt_stats.authentication_status=p[2];
    } else if(p[0]==HCI_EVENT_DISCONNECTION_COMPLETE && n>=6u && !p[2]) {
        uint16_t handle=little_endian_read_16(p,3);
        if(handle==phone_handle) { classic_mode=0;phone_handle=HCI_CON_HANDLE_INVALID;bt_stats.phone=bt_stats.streaming=bt_stats.transport_started=0;have_rtp=0;++bt_stats.classic_disconnects;bt_stats.classic_reason=p[5];classic_visibility(); }
        if(handle==ble_handle) { ble_handle=HCI_CON_HANDLE_INVALID;bt_stats.ble=0;++bt_stats.ble_disconnects;bt_stats.ble_reason=p[5];wmp_gatt_connection(HCI_CON_HANDLE_INVALID); }
    }
}
/* AVRCP (like JBL/Sony speakers): controller sends Play to resume the phone after a codec
 * handover; target receives the phone's absolute volume, and reports speaker volume changes
 * made from the app so the phone's slider follows. */
static uint16_t avrcp_cid;static uint8_t avrcp_volume_known,avrcp_last_volume;
static uint32_t avrcp_connects,avrcp_plays,avrcp_volume_sets,avrcp_play_status;
static uint8_t sdp_avrcp_ct[200],sdp_avrcp_tg[150];
static void avrcp_event(uint8_t type,uint16_t channel,uint8_t *p,uint16_t n) {
    (void)channel;(void)n;if(type!=HCI_EVENT_PACKET || hci_event_packet_get_type(p)!=HCI_EVENT_AVRCP_META)return;
    switch(hci_event_avrcp_meta_get_subevent_code(p)) {
    case AVRCP_SUBEVENT_CONNECTION_ESTABLISHED:
        if(avrcp_subevent_connection_established_get_status(p))break;
        avrcp_cid=avrcp_subevent_connection_established_get_avrcp_cid(p);++avrcp_connects;
        avrcp_target_support_event(avrcp_cid,AVRCP_NOTIFICATION_EVENT_VOLUME_CHANGED);
        /* The phone's volume-notification registration is answered with BTstack's stored
         * absolute volume, which starts at 0: seed it with the speaker's real volume first, or a
         * reconnecting phone (e.g. after a codec handover) adopts 0 and mutes its music stream. */
        avrcp_last_volume=(uint8_t)wmp_audio_volume();avrcp_volume_known=1;
        (void)avrcp_target_adjust_absolute_volume(avrcp_cid,(uint8_t)((avrcp_last_volume*127u+50u)/100u));
        break;
    case AVRCP_SUBEVENT_CONNECTION_RELEASED:avrcp_cid=0;avrcp_volume_known=0;break;
    default:break;
    }
}
static void avrcp_target_event(uint8_t type,uint16_t channel,uint8_t *p,uint16_t n) {
    (void)channel;(void)n;if(type!=HCI_EVENT_PACKET || hci_event_packet_get_type(p)!=HCI_EVENT_AVRCP_META)return;
    if(hci_event_avrcp_meta_get_subevent_code(p)==AVRCP_SUBEVENT_NOTIFICATION_VOLUME_CHANGED) {
        unsigned absolute=avrcp_subevent_notification_volume_changed_get_absolute_volume(p)&0x7fu;
        unsigned percent=(absolute*100u+63u)/127u;
        avrcp_last_volume=(uint8_t)percent;avrcp_volume_known=1;++avrcp_volume_sets;
        (void)wmp_audio_set_volume(percent);
        wmp_telemetry_printf("WMP_AVRCP volume=%u absolute=%u%c",percent,absolute,10);
    }
}
static void avrcp_controller_event(uint8_t type,uint16_t channel,uint8_t *p,uint16_t n) { (void)type;(void)channel;(void)p;(void)n; }
static void avrcp_tick(int64_t now) {
    if(avrcp_cid && avrcp_volume_known && wmp_audio_volume()!=avrcp_last_volume) {
        /* Volume changed from the app or console: tell the phone. */
        avrcp_last_volume=(uint8_t)wmp_audio_volume();
        (void)avrcp_target_volume_changed(avrcp_cid,(uint8_t)((avrcp_last_volume*127u+50u)/100u));
    }
    if(!resume_pending || now<resume_at)return;
    if(bt_stats.streaming || now>resume_deadline || !bt_stats.phone) { resume_pending=0;return; }
    if(avrcp_cid) { avrcp_play_status=avrcp_controller_play(avrcp_cid);++avrcp_plays;resume_pending=0;wmp_telemetry_printf("WMP_AVRCP play status=%lu%c",(unsigned long)avrcp_play_status,10);return; }
    /* Open AVRCP from here right away (a fresh connection; Android negotiates absolute volume on
     * it either way). Waiting for the phone to open it cost 1.5 s of every party start. Play is
     * sent on the next tick after it connects. */
    if(a2dp_peer_valid && !avrcp_self_connected) { uint16_t cid=0;(void)avrcp_connect(a2dp_peer,&cid);avrcp_self_connected=1; }
    resume_at=now+50000;
}
static void handover_tick(int64_t now) {
    /* Deferred Wi-Fi never waits on a handover that is not going to happen. */
    if(wifi_deferred && handover_state==HANDOVER_IDLE && !(stream_open && !broadcast_allows(active_codec)))wifi_release_deferred();
    switch(handover_state) {
    case HANDOVER_IDLE:
        if(broadcast_on && stream_open && a2dp_cid && a2dp_peer_valid && !broadcast_allows(active_codec) && now>=handover_retry_at) {
            handover_state=HANDOVER_RELEASING;handover_tries=0;handover_at=now+3000000;++handovers;handover_was_playing=bt_stats.streaming;
            avrcp_wait_until=0;avrcp_self_connected=0;
            wmp_telemetry_printf("WMP_HANDOVER start codec=%u%c",(unsigned)active_codec,10);
            /* Release AVRCP too: Android only (re)enables absolute volume when an AVRCP connection is
             * set up and the speaker registers for volume changes. Kept across the A2DP reconnect,
             * the phone fell back to scaling the audio itself ("abs vol not supported"), so phone
             * and speaker volume stacked and broadcast sounded at a different level than solo. */
            if(avrcp_cid)avrcp_disconnect(avrcp_cid);
            a2dp_sink_disconnect(a2dp_cid);
        }
        break;
    case HANDOVER_RELEASING:
        if(now>=handover_at) { handover_state=HANDOVER_IDLE;++handover_failures;handover_retry_at=now+5000000;wifi_release_deferred(); }
        break;
    case HANDOVER_WAIT:case HANDOVER_CONNECTING:
        if(a2dp_cid && stream_open) { handover_state=HANDOVER_IDLE;++handover_done;
            if(handover_was_playing) { resume_pending=1;resume_at=now+50000;resume_deadline=resume_at+8000000; }
            break; } /* the phone came back itself */
        if(handover_at && now<handover_at)break;
        if(handover_state==HANDOVER_CONNECTING && a2dp_cid)break; /* signaling up, configuration running */
        if(handover_tries>=4u) { handover_state=HANDOVER_IDLE;++handover_failures;handover_retry_at=now+5000000;wifi_release_deferred();break; }
        if(!handover_tries)wifi_release_deferred(); /* old stream released: Wi-Fi can start (see the broadcast command) */
        { uint16_t cid=0;++handover_tries;handover_status=a2dp_sink_establish_stream(a2dp_peer,&cid);
          handover_state=HANDOVER_CONNECTING;handover_at=now+(handover_status?1000000:8000000);
          wmp_telemetry_printf("WMP_HANDOVER connect try=%u status=%u%c",(unsigned)handover_tries,(unsigned)handover_status,10); }
        break;
    default:handover_state=HANDOVER_IDLE;break;
    }
}
/* Dynamic BLE responsiveness (app round trip 150 ms at best, 600 ms at worst):
 *  - best, 60-75 ms interval: the app is in use (GATT read/write in the last 8 s) or the speaker is
 *    not streaming;
 *  - middle, 120-150 ms: solo playback with the app idle;
 *  - worst, 240-300 ms: the app is idle while the speaker plays in a Wi-Fi party (radio busiest).
 * Speeding up is requested at once; slowing down waits 2 s and requests are at least 1 s apart. */
static void ble_link_tick(int64_t now) {
    if(ble_handle==HCI_CON_HANDLE_INVALID) { ble_link_units=0;ble_link_slow_since=0;return; }
    int interacting=now-wmp_gatt_last_activity()<8000000;
    int party=wmp_radio_wifi_ready() && (radio_stats.mode==2u || radio_stats.rx_active || radio_stats.rx_claim);
    int playing=bt_stats.streaming || party;
    uint16_t want=(interacting || !playing)?60u:(party?240u:120u);
    if(want==ble_link_units) { ble_link_slow_since=0;return; }
    if(want>ble_link_units) { if(!ble_link_slow_since)ble_link_slow_since=now;if(now-ble_link_slow_since<2000000)return; }
    if(now<ble_link_next_us)return;
    gap_request_connection_parameter_update(ble_handle,(uint16_t)(want*4u/5u),want,0,400);
    ++bt_stats.ble_parameter_requests;++ble_link_changes;ble_link_units=want;ble_link_next_us=now+1000000;ble_link_slow_since=0;
}
static void tick(btstack_timer_source_t *t) {
    host_trace();wmp_gatt_tick();unsigned cmd;
    if(xQueueReceive(commands,&cmd,0)==pdTRUE) {
        if(cmd&0x80000000u) {
            if(ble_handle!=HCI_CON_HANDLE_INVALID) { gap_request_connection_parameter_update(ble_handle,(cmd>>8)&65535u,(cmd>>8)&65535u,cmd&255u,400);++bt_stats.ble_parameter_requests; }
        }
        else if(cmd==1u) { bt_stats.requested=1;hci_power_control(HCI_POWER_ON); }
        else if(cmd==3u) { bt_stats.requested=0;hci_power_control(HCI_POWER_OFF); }
        else if(cmd==5u || cmd==6u) { bt_stats.role_policy=cmd==5u?0u:1u;hci_set_master_slave_policy((uint8_t)bt_stats.role_policy); }
        else if(cmd==14u || cmd==15u) { party_connect=cmd==15u?1u:0u;classic_visibility(); }
        else if(cmd==20u || cmd==21u) {
            unsigned on=cmd==21u;
            if(on!=broadcast_on) {
                broadcast_on=(uint8_t)on;++broadcast_changes;broadcast_endpoints();solo_generation=0;
                if(on) {
                    /* Start Wi-Fi now, unless the phone first has to leave a solo-only codec: then
                     * request it once that stream is released (handover_tick), so Wi-Fi's ~45 KB is
                     * not allocated on top of the aptX decoder and its 32-bit DMA (master minimum heap
                     * fell to 12.9 KB). */
                    if(stream_open && !broadcast_allows(active_codec))wifi_deferred=1;
                    else { wmp_radio_wifi(1);wmp_radio_wifi_start_now(); }
                }
                else { wifi_deferred=0;if(radio_stats.mode==2u || role_request)wmp_radio_role(0);role_request=0;candidate_at=0;wmp_radio_wifi(0); }
                /* Any half-built group or queued group belongs to the previous path. */
                if(source_resources) { wmp_sbc_queue_init(&source_input);group_reset();tail_pending=0; }
            }
            wmp_telemetry_printf("WMP_BROADCAST on=%u changes=%lu at_ms=%lu%c",(unsigned)broadcast_on,(unsigned long)broadcast_changes,(unsigned long)(esp_timer_get_time()/1000),10);
        }
        else if(cmd==22u || cmd==23u) { broadcast_aac=cmd==23u;wmp_telemetry_printf("WMP_BCODEC aac=%u%c",(unsigned)broadcast_aac,10); }
        else if(cmd==13u) { party_auto=1;party_idle_since=0;wmp_telemetry_printf("WMP_PARTY auto=1 on=%u%c",(unsigned)party_on,10); }
        else if(cmd==11u || cmd==12u) { party_auto=0;party_on=cmd==12u?1u:0u;++party_changes;classic_visibility();wmp_telemetry_printf("WMP_PARTY on=%u phone=%u%c",(unsigned)party_on,(unsigned)bt_stats.phone,10); }
        else if(cmd==7u && bt_stats.phone) {
            bd_addr_t peer;peer[0]=(uint8_t)(bt_stats.phone_peer_hi>>24);peer[1]=(uint8_t)(bt_stats.phone_peer_hi>>16);peer[2]=(uint8_t)(bt_stats.phone_peer_hi>>8);peer[3]=(uint8_t)bt_stats.phone_peer_hi;peer[4]=(uint8_t)(bt_stats.phone_peer_lo>>8);peer[5]=(uint8_t)bt_stats.phone_peer_lo;
            ++bt_stats.role_requests;bt_stats.role_status=gap_request_role(peer,HCI_ROLE_MASTER);
        }
        else if((cmd==2u || cmd==4u) && bt_stats.working && !bt_stats.phone) {
            /* Reconnect a known phone without any address in the code: PHONECONNECT = the phone this
             * speaker last streamed from (else the first bonded one), PHONECONNECT 2 = the second
             * bonded phone. */
            bd_addr_t phone;int found=0;
            if(cmd==2u && a2dp_peer_valid) { memcpy(phone,a2dp_peer,6);found=1; }
            else {
                btstack_link_key_iterator_t it;unsigned want=cmd==4u?1u:0u,index=0;link_key_t key;link_key_type_t type;
                if(gap_link_key_iterator_init(&it)) {
                    while(gap_link_key_iterator_get_next(&it,phone,key,&type)) { if(index++==want) { found=1;break; } }
                    gap_link_key_iterator_done(&it);
                }
            }
            uint16_t cid;
            if(found)wmp_telemetry_printf("WMP_PHONE_CONNECT status=%u\n",a2dp_sink_establish_stream(phone,&cid));
            else wmp_telemetry_printf("WMP_PHONE_CONNECT status=none\n");
        }
    }
    bt_stats.classic_role=gap_get_role(phone_handle);
    int64_t now=esp_timer_get_time();
    broadcast_endpoints();handover_tick(now);avrcp_tick(now);ble_link_tick(now);
    /* A declined lazy source allocation leaves the old role intact. Retry from
     * the host timer with backoff instead of retaining an unfulfillable latch. */
    if(role_request && !radio_stats.transition_pending && radio_stats.mode!=requested_role) { role_request=0;candidate_at=now+500000; }
    if(bt_stats.streaming && last_media_us && now-last_media_us>500000)bt_stats.streaming=0;
    if(radio_stats.mode==2u) {
        if(!leader_at)leader_at=now;
        candidate_at=0;
        /* Two boards became source together: the one that started
         * broadcasting first on the shared clock keeps the party; the MAC
         * only breaks exact ties or decides when no shared clock exists. */
        int simultaneous_winner=radio_stats.rx_claim && now-leader_at<1000000 && wmp_radio_peer_precedes();
        /* A missing RTP burst is an input fault, not a user pause. Only the
         * actual AVDTP transport stop may yield an established party owner. */
        if(!role_request && ((!bt_stats.transport_started && radio_stats.rx_claim) || simultaneous_winner)) {
            role_request=(uint8_t)wmp_radio_role(0);requested_role=0;
        }
    } else {
        if(leader_at) { leader_at=0;own_audio_seen=role_request=0; }
        if(broadcast_on && wmp_radio_wifi_ready() && handover_state==HANDOVER_IDLE && broadcast_allows(active_codec) && !bt_stats.follower_lock && bt_stats.streaming && party_network_ready() && now-last_media_us<100000 && !radio_stats.rx_active && !radio_stats.rx_claim) {
            if(!candidate_at)candidate_at=now+wmp_election_delay(wmp_base_mac);
            if(now>=candidate_at && !role_request && source_prepare()) { wmp_radio_source_channel();role_request=(uint8_t)wmp_radio_role(2);requested_role=2; }
        } else { candidate_at=0;role_request=0; }
    }
    if(role_request && radio_stats.mode==requested_role)role_request=0;
    { static uint8_t last_mode,last_rx;uint8_t rx=(uint8_t)(radio_stats.rx_active?1u:0u);
      if(radio_stats.mode!=last_mode || rx!=last_rx) { wmp_telemetry_printf("WMP_ROLE_TIMING mode=%u rx_active=%u at_ms=%lu%c",(unsigned)radio_stats.mode,(unsigned)rx,(unsigned long)(now/1000),10);last_mode=(uint8_t)radio_stats.mode;last_rx=rx; } }
    int peer_owns=radio_stats.mode!=2u && (radio_stats.rx_active || radio_stats.rx_claim) && !becoming_source();
    if(!peer_owns)peer_owned_since=0;else if(!peer_owned_since)peer_owned_since=now;
    if(party_auto) {
        int in_party=radio_stats.mode==2u || peer_owns;
        if(in_party) { party_idle_since=0;if(!party_on) { party_on=1;++party_changes;classic_visibility();wmp_telemetry_printf("WMP_PARTY on=1 phone=%u auto=1%c",(unsigned)bt_stats.phone,10); } }
        else if(party_on) {
            if(!party_idle_since)party_idle_since=now;
            else if(now-party_idle_since>5000000) { party_on=0;party_idle_since=0;++party_changes;classic_visibility();wmp_telemetry_printf("WMP_PARTY on=0 phone=%u auto=1%c",(unsigned)bt_stats.phone,10); }
        }
    }
    if(standby_suspend_enabled && peer_owns && now-peer_owned_since>1000000 && bt_stats.transport_started && a2dp_cid && now>=next_standby_suspend) {
        standby_suspend_status=avdtp_sink_suspend(a2dp_cid,a2dp_seid);++standby_suspends;next_standby_suspend=now+2000000;
    }
    if(phone_handle!=HCI_CON_HANDLE_INVALID) {
        int want_sniff=follower_sniff_enabled && peer_owns && now-peer_owned_since>2000000 && !bt_stats.transport_started && !bt_stats.streaming;
        if(want_sniff && classic_mode==0u && now>=next_sniff_request) {
            /* 400-500 ms (slots of 0.625 ms), 4 attempt slots, 1 timeout slot. */
            sniff_status=gap_sniff_mode_enter(phone_handle,640,800,4,1);++sniff_requests;next_sniff_request=now+5000000;
        } else if(!want_sniff && classic_mode==2u && now>=next_sniff_request) {
            sniff_status=gap_sniff_mode_exit(phone_handle);++sniff_exits;next_sniff_request=now+500000;
        }
    }
    wmp_radio_publisher_idle();
    if(radio_stats.mode==2u || (bt_stats.streaming && party_network_ready() && !radio_stats.rx_active && !radio_stats.rx_claim))source_prepare();
    flush_group();drain_source();
    if(radio_stats.mode==2u && !bt_stats.streaming && (!source_resources || (!source_input.count && !group.frames)))wmp_radio_silence_tick(bt_stats.rate);
    btstack_run_loop_set_timer(t,10);btstack_run_loop_add_timer(t);
}
static void host(void *unused) {
    (void)unused;btstack_init();l2cap_init();sm_init();sdp_init();
    events.callback=event;hci_add_event_handler(&events);
    snprintf(classic_name,sizeof(classic_name),"WMP-Sink-%02X%02X",wmp_base_mac[4],wmp_base_mac[5]);gap_set_local_name(classic_name);
    snprintf(ble_name,sizeof(ble_name),"WMP-Control-%02X%02X",wmp_base_mac[4],wmp_base_mac[5]);
    gap_set_class_of_device(0x200414);gap_ssp_set_io_capability(SSP_IO_CAPABILITY_NO_INPUT_NO_OUTPUT);gap_ssp_set_auto_accept(1);
    /* Diagnostic for the documented ESP32 BR/EDR SC interoperability path.
     * SSP pairing and link encryption remain enabled. */
    gap_secure_connections_enable(false);
    /* Keep BR/EDR scheduling local when the peer accepts role negotiation. */
    gap_set_default_link_policy_settings(LM_LINK_POLICY_ENABLE_ROLE_SWITCH|LM_LINK_POLICY_ENABLE_SNIFF_MODE);
    hci_set_master_slave_policy(1);bt_stats.role_policy=1;bt_stats.classic_role=HCI_ROLE_INVALID;
    sm_set_io_capabilities(IO_CAPABILITY_NO_INPUT_NO_OUTPUT);
    a2dp_sink_init();a2dp_sink_register_packet_handler(a2dp_event);a2dp_sink_register_media_handler(media);a2dp_sink_register_media_config_validator(config_validator);
    configASSERT(a2dp_sink_create_stream_endpoint(AVDTP_AUDIO,AVDTP_CODEC_SBC,codec_caps,sizeof(codec_caps),codec_config,sizeof(codec_config)));
    /* AAC: MPEG-2/4 AAC-LC, 44.1/48 kHz, mono/stereo, VBR up to 320 kbps. */
    static const uint8_t aac_caps[6]={0xc0,0x01,0x8c,0x84,0xe2,0x00};static uint8_t aac_config[6];
    /* aptX: vendor 0x0000004F codec 0x0001; aptX HD: vendor 0x000000D7 codec 0x0024; 44.1/48 kHz stereo. */
    static const uint8_t aptx_caps[7]={0x4f,0,0,0,0x01,0x00,0x32};static uint8_t aptx_config[7];
    static const uint8_t aptx_hd_caps[11]={0xd7,0,0,0,0x24,0x00,0x32,0,0,0,0};static uint8_t aptx_hd_config[11];
    avdtp_stream_endpoint_t *endpoint;
    if((WMP_CODEC_MASK&(1u<<WMP_CODEC_AAC)) && (endpoint=a2dp_sink_create_stream_endpoint(AVDTP_AUDIO,AVDTP_CODEC_MPEG_2_4_AAC,aac_caps,sizeof(aac_caps),aac_config,sizeof(aac_config)))) { aac_seid=avdtp_local_seid(endpoint);aac_endpoint=endpoint; }
    if((WMP_CODEC_MASK&(1u<<WMP_CODEC_APTX_HD)) && (endpoint=a2dp_sink_create_stream_endpoint(AVDTP_AUDIO,AVDTP_CODEC_NON_A2DP,aptx_hd_caps,sizeof(aptx_hd_caps),aptx_hd_config,sizeof(aptx_hd_config)))) { aptx_hd_seid=avdtp_local_seid(endpoint);aptx_hd_endpoint=endpoint; }
    if((WMP_CODEC_MASK&(1u<<WMP_CODEC_APTX)) && (endpoint=a2dp_sink_create_stream_endpoint(AVDTP_AUDIO,AVDTP_CODEC_NON_A2DP,aptx_caps,sizeof(aptx_caps),aptx_config,sizeof(aptx_config)))) { aptx_seid=avdtp_local_seid(endpoint);aptx_endpoint=endpoint; }
    broadcast_endpoints();
    a2dp_sink_create_sdp_record(sdp,0x10001,AVDTP_SINK_FEATURE_MASK_SPEAKER,"Wi-Fi Music Party","WMP");sdp_register_service(sdp);
    avrcp_init();avrcp_controller_init();avrcp_target_init();
    avrcp_register_packet_handler(avrcp_event);avrcp_controller_register_packet_handler(avrcp_controller_event);avrcp_target_register_packet_handler(avrcp_target_event);
    avrcp_controller_create_sdp_record(sdp_avrcp_ct,0x10002,1u<<AVRCP_CONTROLLER_SUPPORTED_FEATURE_CATEGORY_PLAYER_OR_RECORDER,NULL,NULL);sdp_register_service(sdp_avrcp_ct);
    avrcp_target_create_sdp_record(sdp_avrcp_tg,0x10003,1u<<AVRCP_TARGET_SUPPORTED_FEATURE_CATEGORY_MONITOR_OR_AMPLIFIER,NULL,NULL);sdp_register_service(sdp_avrcp_tg);
    wmp_gatt_init(ble_name,WMP_BUILD_ID);
    btstack_run_loop_set_timer_handler(&timer,tick);btstack_run_loop_set_timer(&timer,10);btstack_run_loop_add_timer(&timer);
    bt_stats.requested=1;hci_power_control(HCI_POWER_ON);
    btstack_run_loop_execute();
}
/* BTstack host: L2CAP/AVDTP/A2DP callbacks run the master's grouping and radio hand-off on
 * this stack. 3072 left 72 bytes free while streaming and overflowed (COM7 2026-11-18). */
#define WMP_HOST_STACK 5632
void wmp_bluetooth_start(void) { commands=xQueueCreate(4,sizeof(unsigned));configASSERT(commands);configASSERT(xTaskCreatePinnedToCore(host,"wmp_host",WMP_HOST_STACK,0,6,&host_task,0)==pdPASS); }
int wmp_bluetooth_command(unsigned command) { return (command>=1u && command<=7u) && xQueueSend(commands,&command,0)==pdTRUE; }
int wmp_bluetooth_party_connect(unsigned on) { if(on>1u)return 0;unsigned command=on?15u:14u;return xQueueSend(commands,&command,0)==pdTRUE; }
int wmp_bluetooth_party_auto(void) { unsigned command=13u;return xQueueSend(commands,&command,0)==pdTRUE; }
int wmp_bluetooth_broadcast_request(unsigned on) { if(on>1u)return 0;unsigned command=on?21u:20u;return xQueueSend(commands,&command,0)==pdTRUE; }
int wmp_bluetooth_broadcast_codec(unsigned aac) { if(aac>1u)return 0;unsigned command=aac?23u:22u;return xQueueSend(commands,&command,0)==pdTRUE; }
unsigned wmp_bluetooth_handover(void) { return handover_state; }
int wmp_bluetooth_party(unsigned on) { if(on>1u)return 0;unsigned command=on?12u:11u;return xQueueSend(commands,&command,0)==pdTRUE; }
unsigned wmp_bluetooth_party_on(void) { return party_on; }
int wmp_bluetooth_follower_lock(unsigned enabled) { if(enabled>1u || (enabled && (radio_stats.mode || radio_stats.transition_pending)))return 0;bt_stats.follower_lock=enabled;return 1; }

void wmp_bluetooth_memory_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_RTP_TIMING query=%08lx counted_samples=%llu timestamp_mismatches=%lu last_delta=%lu expected_delta=%lu packet_samples=%lu input_gap_max_us=%lu input_gaps_50ms=%lu input_gaps_200ms=%lu input_ahead_max_us=%ld input_behind_max_us=%ld input_rebases=%lu\n",query,(unsigned long long)media_stream.next_sample,(unsigned long)media_stream.timestamp_mismatches,(unsigned long)media_stream.last_delta,(unsigned long)media_stream.last_expected,(unsigned long)media_stream.packet_samples,(unsigned long)input_gap_max_us,(unsigned long)input_gaps_50ms,(unsigned long)input_gaps_200ms,(long)input_ahead_max_us,(long)input_behind_max_us,(unsigned long)input_rebases);
    wmp_telemetry_printf("WMP_STANDBY query=%08lx suspend_enabled=%u suspends=%lu last_status=%lu peer_owned=%u start_reject=%u start_rejects=%lu start_accepts=%lu start_status=%lu\n",query,(unsigned)standby_suspend_enabled,(unsigned long)standby_suspends,(unsigned long)standby_suspend_status,(unsigned)(peer_owned_since!=0),(unsigned)start_reject_enabled,(unsigned long)start_rejects,(unsigned long)start_accepts,(unsigned long)start_reject_status);
    wmp_bluetooth_sniff_stats(query);
    wmp_telemetry_printf("WMP_BROADCAST_STATE query=%08lx on=%u changes=%lu wifi=%u solo_groups=%lu solo_rebases=%lu solo_deferred=%lu aac=%u handover=%u handovers=%lu handover_done=%lu handover_failures=%lu handover_status=%u%c",query,(unsigned)broadcast_on,(unsigned long)broadcast_changes,wmp_radio_wifi_ready(),(unsigned long)solo_groups,(unsigned long)solo_rebases,(unsigned long)solo_deferred,
        (unsigned)broadcast_aac,(unsigned)handover_state,(unsigned long)handovers,(unsigned long)handover_done,(unsigned long)handover_failures,(unsigned)handover_status,10);
    wmp_telemetry_printf("WMP_AVRCP_STATE query=%08lx cid=%u connects=%lu plays=%lu play_status=%lu volume_sets=%lu volume_known=%u last_volume=%u resume_pending=%u%c",query,(unsigned)avrcp_cid,(unsigned long)avrcp_connects,(unsigned long)avrcp_plays,(unsigned long)avrcp_play_status,(unsigned long)avrcp_volume_sets,(unsigned)avrcp_volume_known,(unsigned)avrcp_last_volume,(unsigned)resume_pending,10);
    wmp_telemetry_printf("WMP_PARTY_STATE query=%08lx auto=%u connect=%u on=%u changes=%lu classic_visible=%lu phone=%lu%c",query,(unsigned)party_auto,(unsigned)party_connect,(unsigned)party_on,(unsigned long)party_changes,(unsigned long)bt_stats.classic_visible,(unsigned long)bt_stats.phone,10);
    wmp_telemetry_printf("WMP_BT_RESOURCES query=%08lx source_bytes=%u starts=%lu stops=%lu failures=%lu assembler_bytes=%u\n",query,source_resources?(unsigned)sizeof(*source_resources):0u,(unsigned long)source_resource_starts,(unsigned long)source_resource_stops,(unsigned long)source_resource_failures,source_resources?(unsigned)sizeof(group):0u);
    wmp_telemetry_printf("WMP_BT_CODEC query=%08lx codec=%u channels=%u switches=%lu packets=%lu units=%lu bad=%lu unsupported=%lu%c",query,(unsigned)active_codec,(unsigned)active_channels,(unsigned long)codec_switches,(unsigned long)codec_packets,(unsigned long)codec_units,(unsigned long)codec_bad,(unsigned long)codec_unsupported,10);
    wmp_telemetry_printf("WMP_BT_MEMORY query=%08lx host_stack_free=%u hci_command_ready=%u hci_buffer_reserved=%u vhci_send_available=%u sdp_ready=%u br_sc_active=%u\n",query,host_task?(unsigned)uxTaskGetStackHighWaterMark(host_task):0u,
        (unsigned)hci_can_send_command_packet_now(),(unsigned)hci_is_packet_buffer_reserved(),bt_stats.working?(unsigned)esp_vhci_host_check_send_available():0u,(unsigned)sdp_client_ready(),(unsigned)gap_secure_connections_active());
}

int wmp_bluetooth_parameters(unsigned interval,unsigned latency) {
    if(interval<24u || interval>400u || latency>9u || (uint64_t)interval*(latency+1u)*2500u>=4000000u)return 0;
    unsigned command=0x80000000u|(interval<<8)|latency;return xQueueSend(commands,&command,0)==pdTRUE;
}
