#ifndef WMP_NODE_H
#define WMP_NODE_H
#include <stdint.h>
#include "telemetry.h"
#include "wmp_audio_group.h"
/* Groups close early when the next original SBC frame would not fit the raw
 * frame limit, so the live bound is the wire bound. Never recompress SBC. */
#define WMP_SBC_BITPOOL_CAP 53u
#define WMP_LIVE_PAYLOAD WMP_MAX_PAYLOAD
_Static_assert(WMP_LIVE_PAYLOAD<=WMP_MAX_PAYLOAD,"SBC groups must fit the wire bound");
/* Product defaults = the party settings tuned on hardware (previously sent by test scripts). */
#define WMP_DEFAULT_COPY_SPACING_US 33000u
/* Default native window is eight. Keep bounded diagnostic headroom without
 * retaining metadata for another sixteen unused in-flight submissions. */
#define WMP_RADIO_TX_CAPACITY 16u
#define WMP_AUDIO_REPAIR_REF 256u
typedef struct {
    uint32_t ready,mode,pps,copies,spacing_us,payload,produced,queue_full,slot_full;
    uint32_t tx_attempt,tx_accepted,tx_done,tx_fail,tx_api_error,tx_timeout,tx_late,tx_api_max_us,tx_callback_queue_full,tx_unmatched;
    uint32_t rx_callback,rx_rejected,rx_queue_full,rx_bad,rx_unique,rx_duplicate,rx_conflict,rx_stale,rx_bytes,rx_sbc_frames;
    uint32_t rx_max_gap_us,rx_callback_delay_max_us,rx_sessions,rx_first,rx_highest,rx_active,rx_claim,peer_id_hi,peer_id_lo;
    uint32_t clock_sample,clock_callback,phy_mbps,tx_phy_rate,rx_phy_rate;
    int32_t rx_rssi;
    uint32_t coex_preference;
    uint32_t rx_priority,rx_dispatch_max_us,rx_dispatch_over1,rx_dispatch_over5,rx_dispatch_over10,rx_dispatch_over20;
    uint32_t latency_us,latency_changes;
    uint32_t qos_sent,qos_received;
    uint32_t tx_pace_us,tx_loop_max_us,tx_loop_over5,tx_loop_over20,tx_busy_max,tx_action_frames,rx_native_flags,wifi_ap,beacon_tu,channel;
    int32_t noise_floor;
    uint32_t replay_enabled,nack_queued,nack_sent,nack_heard,nack_suppressed,replay_queued,replay_cache_miss,replay_deferred,replay_too_late,replay_received,replay_delivered;
    uint32_t nack_delay_us;
    uint32_t diagnostic_dropped_copies;
    uint32_t beacon_seen,beacon_bad,beacon_matched,beacon_matched_bad,beacon_rate,beacon_match_rate,beacon_queued,beacon_last_hi,beacon_last_lo,beacon_last_at,beacon_match_at;
    uint32_t silence_queued,silence_received,timeline_recoveries,preparing,rx_suspended;
    uint32_t transition_pending,transition_target,transition_count,transition_max_us;
    uint32_t repair_unicast,repair_unicast_sent,repair_unicast_done,repair_unicast_failed;
    uint32_t native_rx_requested,native_rx_active,native_rx_callbacks,native_rx_queued,native_rx_prefix,native_roc_calls,native_roc_done,native_roc_failed;
    uint32_t direct_rx_enabled,direct_rx_callbacks,direct_rx_queued;
    uint32_t direct_air_frames,direct_air_bad,direct_air_same_sequence,direct_air_last_sequence;
    uint32_t receive_window_enabled;
    int32_t receive_window_status;
    uint32_t receive_interval_ms;
    int32_t receive_interval_status;
    int32_t direct_rx_status;
    int32_t native_roc_status;
    uint32_t association_requested,association_connected,association_attempts;
    uint32_t infrastructure_requested;
    uint32_t network_anchor,anchor_active,anchor_peers;
    int32_t infrastructure_status;
    int32_t association_status;
    uint32_t association_connect_events,association_disconnect_events,association_disconnect_reason;
    uint32_t ethernet_enabled,ethernet_rx,ethernet_queued,ethernet_tx_done,ethernet_tx_unknown,ethernet_tx_prefix;
    int32_t ethernet_rate_status,tx_last_api_status;
    uint32_t ethernet_sniff_frames,ethernet_sniff_rate;
    int32_t ethernet_sniff_rssi;
    uint64_t tx_api_total_us;
    uint32_t tx_api_calls,tx_api_over1ms,tx_api_over5ms,tx_task_core;
    uint32_t tx_window;
    uint32_t listen_requested,listen_active,listen_starts,listen_done,listen_errors;
    int32_t listen_status;
    uint64_t missing_source_samples;
    int32_t latency_phase_us,latency_target_phase_us;
    uint32_t replay_attempted,replay_accepted,replay_completed,replay_failed,replay_invalid,nack_window_wait,nack_window_age_us;
    uint32_t slot_resource_bytes,slot_resource_starts,slot_resource_stops,slot_resource_failures,trace_resource_bytes;
} wmp_radio_stats;
typedef struct { uint32_t requested,working,streaming,transport_started,phone,ble,ble_writes,ble_max_us,rtp_packets,rtp_gaps,rtp_bad,rtp_fragments,sbc_frames,sbc_bytes,source_groups,source_blocked_frames,group_discontinuity,rate;
    uint32_t source_queue_groups,source_queue_bytes,source_queue_max_groups,source_queue_max_bytes,source_queue_deferred,source_queue_full,source_queue_max_age_us;
    uint32_t ble_interval,ble_latency,ble_timeout,ble_parameter_requests,ble_parameter_status;
    uint32_t preparation_frames,ssp_requests,pairing_status,authentication_status;
    uint32_t standby_frames,phone_peer_hi,phone_peer_lo,ble_peer_hi,ble_peer_lo,classic_visible;
    uint32_t classic_disconnects,classic_reason,ble_disconnects,ble_reason,connection_failures,last_connection_status;
    uint32_t follower_lock;
    uint32_t classic_role,role_policy,role_events,role_status,role_requests;
} wmp_bt_stats;
extern uint8_t wmp_mac[6],wmp_base_mac[6];
extern volatile wmp_radio_stats radio_stats;
extern volatile wmp_bt_stats bt_stats;
typedef struct { uint32_t queued,queue_full,decoded,codec_bad,pcm_bad,running,rate,concealed,concealed_missing,concealed_decode,last_missing_sample,late,write_errors,clock_pairs,clock_bad,clock_valid,clock_residual_us,underrun,output_frames;int32_t phase_us,correction_ppm;uint32_t clock_fit_us,arrivals,arrival_negative,arrival_under5;int32_t arrival_min_us,clock_ppm;uint32_t buffered_late,input_late,startup_skips,queue_wait_max_us,held_max,source_deferred,replay_played,silence_groups,silence_frames,preparing,preparation_skips,clock_expirations,clock_unavailable_skips;uint32_t realign_pad,realign_pad_ms,realign_skip,idle_stops; } wmp_audio_stats;
extern volatile wmp_audio_stats audio_stats;
void wmp_audio_dma_stats(unsigned long query);
int wmp_audio_priority(unsigned priority);
int wmp_audio_render_boost(unsigned us);
int wmp_audio_cursor_guard(unsigned on);
int wmp_audio_anchor_shift(unsigned ms);
void wmp_audio_init(void);
void wmp_audio_preload(void);
unsigned wmp_audio_volume(void);
int wmp_audio_set_volume(unsigned percent);
enum { WMP_SPEAKER_STEREO=0, WMP_SPEAKER_LEFT=1, WMP_SPEAKER_RIGHT=2 };
#define WMP_EQ_BANDS 7u /* 63 Hz shelf, 160, 400, 1k, 2.5k, 6.3k peaks, 16 kHz shelf */
unsigned wmp_audio_speaker_channel(void);
int wmp_audio_set_speaker_channel(unsigned channel);
void wmp_audio_party_size(unsigned speakers);
/* Follower: the master's volume from the clock trailer (-1 = absent). Used while fresh (2 s). */
void wmp_audio_party_volume(int percent);
int wmp_audio_capture_start(unsigned samples); /* PCMCAPTURE diagnostic */
int wmp_audio_capture_dump(void);
uint32_t wmp_audio_dsp_bench(void); /* DSPBENCH diagnostic */
unsigned wmp_audio_party(void);          /* 1, 2, 3 (=3+) or 0 unknown */
unsigned wmp_audio_output_channel(void); /* what this speaker plays now */
int wmp_audio_set_eq(const int8_t gains[WMP_EQ_BANDS]);
void wmp_audio_get_eq(int8_t gains[WMP_EQ_BANDS]);
void wmp_radio_presence_stats(unsigned long query);
/* Broadcast switch: Wi-Fi exists only while broadcast is on. */
int wmp_radio_wifi(unsigned on);
void wmp_radio_source_channel(void);
void wmp_radio_wifi_start_now(void); /* start Wi-Fi immediately if wanted (broadcast switched on) */ /* pick and hold the party channel before becoming the source */
unsigned wmp_radio_wifi_ready(void);
unsigned wmp_radio_wifi_live(void);   /* driver initialised: safe to call esp_wifi_* */
void wmp_radio_wifi_service(void);
void wmp_radio_wifi_stats(unsigned long query);
unsigned wmp_bluetooth_broadcast(void);
int wmp_bluetooth_broadcast_request(unsigned on);
/* Broadcast codec policy: 0 = SBC only (default), 1 = the phone may also pick AAC-LC. */
int wmp_bluetooth_broadcast_codec(unsigned aac);
unsigned wmp_bluetooth_broadcast_aac(void);
unsigned wmp_bluetooth_handover(void); /* nonzero while the phone is being moved to SBC */
/* User settings (volume, speaker channel, EQ) persisted in NVS. */
void wmp_settings_load(void);
void wmp_settings_changed(void);
void wmp_settings_service(void);
void wmp_settings_stats(unsigned long query);
void wmp_audio_memory_stats(unsigned long query);
int wmp_audio_offer(const wmp_header *header,const uint8_t *payload,int local_source);
unsigned wmp_audio_share(const wmp_header *header,const uint8_t *payload,const uint8_t **shared_payload);
unsigned wmp_audio_borrow(uint64_t generation,uint64_t sequence,wmp_header *header,const uint8_t **shared_payload);
void wmp_audio_unshare(unsigned reference);
int wmp_audio_cached(uint64_t generation,uint64_t sequence,wmp_header *header,uint8_t *payload,unsigned capacity);
int wmp_audio_beacon(uint64_t source_tsf,uint32_t local_mac);
int64_t wmp_audio_local_deadline(uint64_t presentation);
int wmp_audio_apll(unsigned enabled);
void wmp_audio_clock_trace(unsigned long query);
void wmp_audio_clock_reset(void);
void wmp_audio_prepare_peer(void);
void wmp_audio_prepare_stream(void);
uint64_t wmp_source_clock(void);
int wmp_source_clock_shared_epoch(uint32_t *epoch);
int wmp_source_clock_shared_ready(uint32_t *epoch,uint64_t *ready_us);
void wmp_source_clock_invalidate(void);
void wmp_source_clock_init(void);
void wmp_source_clock_enable(unsigned enabled);
void wmp_source_clock_stats(unsigned long query);
void wmp_radio_init(void);
void wmp_radio_memory_stats(unsigned long query);
void wmp_radio_rate_stats(unsigned long query);
void wmp_radio_network_stats(unsigned long query);
int wmp_radio_network_drop(void);
int wmp_bluetooth_host_trace(unsigned long query);
void wmp_radio_timing_stats(unsigned long query);
void wmp_radio_nack_stats(unsigned long query);
void wmp_radio_copy_trace(unsigned long query);
void wmp_radio_repair_trace(unsigned long query);
enum { WMP_D_SUBMIT=1,WMP_D_API,WMP_D_DONE,WMP_D_RX,WMP_D_RX_QUEUE,WMP_D_VALID,WMP_D_HANDOFF,WMP_D_DECODE,WMP_D_LATE,WMP_D_REQUEST,WMP_D_INJECT,WMP_D_CONTROL,WMP_D_RAW_DEDUP,WMP_D_DELIVERY_DEDUP,WMP_D_DISCARD };
int wmp_radio_delivery_arm(uint64_t generation,uint64_t sequence);
int wmp_radio_delivery_clear(void);
void wmp_radio_delivery_trace(unsigned long query);
void wmp_radio_delivery(const wmp_header *header,unsigned stage,int status);
void wmp_radio_repair_last(unsigned long query);
void wmp_radio_copy_summary(unsigned long query);
void wmp_radio_completion_trace(unsigned long query);
void wmp_radio_gap_stats(unsigned long query);
void wmp_radio_beacon_stats(unsigned long query);
void wmp_radio_gap_reset(void);
int wmp_radio_schedule(unsigned spacing_us,unsigned paced);
int wmp_radio_copy_dither(unsigned microseconds);
void wmp_radio_dither_stats(unsigned long query);
int wmp_radio_pace(unsigned microseconds);
int wmp_radio_path(unsigned action_frames);
int wmp_radio_receiver_mode(unsigned ap);
int wmp_radio_receiver_beacon(unsigned tu);
int wmp_radio_channel(unsigned channel);
int wmp_radio_replay(unsigned enabled);
int wmp_radio_repair_unicast(unsigned enabled);
int wmp_radio_native_rx(unsigned enabled);
int wmp_radio_direct_rx(unsigned enabled);
int wmp_radio_receive_window(unsigned enabled);
int wmp_radio_receive_window_size(unsigned milliseconds);
unsigned wmp_radio_receive_window_value(void);
int wmp_radio_receive_interval(unsigned milliseconds);
int wmp_radio_tx_wake(unsigned microseconds);
void wmp_radio_wake_stats(unsigned long query);
int wmp_radio_associate(unsigned enabled);
int wmp_radio_network(const char *ssid,const char *password);
int wmp_radio_anchor(const char *ssid,const char *password);
int wmp_radio_clock_ready(void);
int wmp_radio_ethernet(unsigned enabled);
int wmp_radio_power_save(unsigned mode);
int wmp_radio_tx_window(unsigned buffers);
int wmp_radio_copies(unsigned copies);
int wmp_radio_tx_shed(unsigned on);
int wmp_radio_scan_listen(unsigned enabled);
int wmp_radio_drop_test(unsigned groups_ahead);
int wmp_radio_drop_range(unsigned groups_ahead,unsigned groups);
void wmp_radio_drop_trace(unsigned long query);
int wmp_radio_drop_clear(void);
int wmp_radio_repair_pace(unsigned microseconds);
int wmp_radio_repair_dedup(unsigned enabled);
void wmp_radio_repair_gate_stats(unsigned long query);
int wmp_audio_borrow_repair(uint64_t generation,uint64_t sequence,wmp_header *header,const uint8_t **payload,unsigned *reference);
/* Cache-miss diagnosis: where a requested group stands in the playout pool.
 * state 0 not in any slot, 1 free (played or discarded, header kept), 2 pending,
 * 3 ready, 4 held, 5 retired, 6 other, 7 pool unloaded; +8 when not a local copy.
 * available/oldest/newest describe this generation's local ready|held|retired slots. */
typedef struct { unsigned state,available; uint64_t oldest,newest,pts; } wmp_audio_miss;
void wmp_audio_miss_probe(uint64_t generation,uint64_t sequence,wmp_audio_miss *out);
int wmp_audio_repair_limit(unsigned limit);
unsigned wmp_audio_repair_limit_value(void);
int wmp_radio_nack_delay(unsigned microseconds);
int wmp_radio_nack_gate(unsigned microseconds);
int wmp_radio_repair_config(unsigned gap,unsigned guard,unsigned budget,unsigned spacing,unsigned paced);
void wmp_radio_session_stats(unsigned long query,unsigned source_role);
int wmp_audio_fault_resources(unsigned enabled);
void wmp_audio_fault_trace(unsigned long query);
int wmp_radio_rx_priority(unsigned priority);
int wmp_radio_latency(unsigned milliseconds);
void wmp_radio_late_report(uint64_t generation,unsigned late_frames,unsigned rate,unsigned samples);
int wmp_radio_test_report(unsigned frames);
int wmp_radio_preference(unsigned preference);
int wmp_radio_command(unsigned mode,unsigned pps,unsigned copies,unsigned spacing_us,unsigned payload);
int wmp_radio_role(unsigned mode);
int wmp_radio_peer_precedes(void);
int wmp_radio_rx_filter(unsigned classes);
unsigned wmp_radio_rx_filter_classes(void);
int wmp_radio_rf_awake(unsigned hold);
int wmp_radio_nack_copies(unsigned copies,unsigned spacing_us);
int wmp_radio_channel_live(unsigned channel,unsigned *elapsed_us);
int wmp_radio_coex_align(unsigned enabled);
int wmp_radio_late_growth(unsigned enabled);
unsigned wmp_radio_rf_awake_held(void);
void wmp_radio_rx_fcs(unsigned long *any,unsigned long *leader);
int wmp_radio_tx_phase(unsigned mask,unsigned urgent_ms);
int wmp_radio_offer(const wmp_audio_group *group,uint32_t rate,uint64_t enqueued_local_us);
void wmp_radio_silence_tick(uint32_t rate);
void wmp_radio_publisher_idle(void);
int wmp_radio_receive_pause(unsigned paused);
int wmp_radio_source_ready(void);
int wmp_radio_rate(unsigned mbps);
int wmp_radio_tx_rate_live(unsigned code,int *status_ht);
int wmp_radio_clock_copy(unsigned mode);
int wmp_radio_auto_channel(unsigned enabled);
int wmp_bluetooth_party_auto(void);
unsigned wmp_bluetooth_codec(void);
unsigned wmp_bluetooth_channels(void);
int wmp_bluetooth_party_connect(unsigned on);
void wmp_radio_channel_service(void);
void wmp_bluetooth_start(void);
void wmp_bluetooth_memory_stats(unsigned long query);
int wmp_bluetooth_command(unsigned command);
int wmp_bluetooth_parameters(unsigned interval,unsigned latency);
int wmp_bluetooth_follower_lock(unsigned enabled);
int wmp_bluetooth_standby_suspend(unsigned enabled);
int wmp_bluetooth_start_reject(unsigned enabled);
int wmp_bluetooth_follower_sniff(unsigned enabled);
int wmp_bluetooth_party(unsigned on);
unsigned wmp_bluetooth_party_on(void);
void wmp_bluetooth_sniff_stats(unsigned long query);
int wmp_bluetooth_group_frames(unsigned frames);

void wmp_gatt_init(const char *name,const char *build);
void wmp_gatt_connection(uint16_t handle);
/* Karaoke effects (fx.c). Settings, 11 bytes: flags (1 vocal remover, 2 echo, 4 reverb, 8 chorus),
 * vocal 0..100 %, echo delay 5..25 x10 ms, echo feedback 0..75 %, echo mix 0..100 %, reverb size,
 * damping, mix (0..100 each), chorus rate 1..50 x0.1 Hz, chorus depth 0..100 %, chorus mix 0..100 %. */
#define WMP_FX_BYTES 11u
int wmp_fx_set(const uint8_t cfg[WMP_FX_BYTES]);
void wmp_fx_load(const uint8_t cfg[WMP_FX_BYTES]);
void wmp_fx_get(uint8_t out[WMP_FX_BYTES+1u]);     /* settings + status byte */
void wmp_fx_party(const uint8_t cfg[WMP_FX_BYTES]); /* from the party master's announce */
void wmp_fx_announce(uint8_t out[1u+WMP_FX_BYTES]); /* version byte + this speaker's settings */
void wmp_fx_service(void);                          /* main loop: allocates/frees effect memory */
void wmp_fx_process(int32_t *pcm,unsigned frames,uint32_t rate); /* audio task, in place */
int wmp_fx_active(void);
void wmp_radio_ram(void);void wmp_audio_ram(void);void wmp_bluetooth_ram(void); /* STACKS: heap held per module */
void wmp_fx_stats(unsigned long query);
void wmp_gatt_tick(void);
int64_t wmp_gatt_last_activity(void); /* esp_timer us of the last GATT read/write (or connection) */
void wmp_gatt_stats(unsigned long query);
int wmp_gatt_notify_period(unsigned ms);

void wmp_source_clock_service(void);
int wmp_radio_trace_resources(unsigned enabled);
void wmp_audio_service_resources(void);
void wmp_audio_resource_stats(unsigned long query);

#endif
