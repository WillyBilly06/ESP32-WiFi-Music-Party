#include "node.h"
#include "wmp_codecs.h"
#include "esp_heap_caps.h"
#include "wmp_frame_pool.h"
#include "wmp_sbc_queue.h"
#include "coex_trace.h"
void wmp_vhci_stats(unsigned long query);
void wmp_vhci_control_trace(unsigned long query);
#include "esp_bt.h"
#include "esp_coexist.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#ifdef CONFIG_HEAP_TRACING_STANDALONE
#include "esp_heap_trace.h"
static heap_trace_record_t trace_records[120];static uint8_t trace_ready;
#endif
uint8_t wmp_base_mac[6];
#ifndef WMP_BUILD_ID
#define WMP_BUILD_ID "unversioned"
#endif
static int bt_sleep_requested,bt_sleep_applied=-1,bt_sleep_result=ESP_ERR_INVALID_STATE,bt_power_attempted;
static int a2dp_profile_enabled=1,a2dp_profile_attempted=-1,a2dp_profile_applied=-1,a2dp_profile_result=ESP_ERR_INVALID_STATE;
static int role_preference_attempted=-1,role_preference_result=ESP_ERR_INVALID_STATE;
/* Follower coexistence share: esp_coex_preference_set() is a stub in the
 * pinned libcoexist, so a follower replaces the 40/60 Wi-Fi/BT table with a
 * Wi-Fi-heavy two-phase table (A/B: conceals 44-92 -> 0-16 per minute, BLE
 * writes roughly halved). A source always restores the library table so the
 * phone's A2DP stream keeps its BT share. 0 disables. */
static unsigned follower_coex_percent=90;
static void controller_profile(void) {
    if(esp_bt_controller_get_status()!=ESP_BT_CONTROLLER_STATUS_ENABLED) { a2dp_profile_attempted=a2dp_profile_applied=role_preference_attempted=-1;return; }
    /* On a follower the real output arrives over Wi-Fi; its own phone's SBC
     * is intentionally ignored. Do not reserve A2DP streaming priority for
     * that discarded input. Keep the true RTP activity state for takeover. */
    int peer_output=a2dp_profile_enabled!=2 && radio_stats.mode!=2u && (radio_stats.rx_active || radio_stats.rx_claim);
    int preference=peer_output?1:0;
    if(role_preference_attempted!=preference) { role_preference_result=wmp_radio_preference((unsigned)preference);role_preference_attempted=preference;wmp_coex_override(preference?follower_coex_percent:0u,3u);wmp_telemetry_printf("WMP_FOLLOWER_COEX follower=%d wifi_pct=%u\n",preference,preference?follower_coex_percent:0u); }
    /* RF packet silence is not an AVDTP Suspend. Keep the real active stream
     * priority while recovering reception; ownership still uses recent RTP. */
    int wanted=a2dp_profile_enabled && !peer_output?(bt_stats.transport_started?1:(bt_stats.phone?2:0)):0;
    if(a2dp_profile_attempted==wanted)return;
    int clear=esp_coex_status_bit_clear(ESP_COEX_ST_TYPE_BT,ESP_COEX_BT_ST_A2DP_PAUSED);
    int clear_streaming=esp_coex_status_bit_clear(ESP_COEX_ST_TYPE_BT,ESP_COEX_BT_ST_A2DP_STREAMING);
    if(!clear)clear=clear_streaming;
    int status=ESP_OK;
    if(wanted)status=esp_coex_status_bit_set(ESP_COEX_ST_TYPE_BT,wanted==1?ESP_COEX_BT_ST_A2DP_STREAMING:ESP_COEX_BT_ST_A2DP_PAUSED);
    a2dp_profile_result=clear?clear:(status?status:role_preference_result);a2dp_profile_applied=a2dp_profile_result==ESP_OK?wanted:-1;a2dp_profile_attempted=wanted;
    wmp_telemetry_printf("WMP_A2DP_COEX enabled=%d streaming=%lu applied=%d status=%d\n",a2dp_profile_enabled,(unsigned long)bt_stats.streaming,a2dp_profile_applied,a2dp_profile_result);
}
static void controller_power(void) {
    if(esp_bt_controller_get_status()!=ESP_BT_CONTROLLER_STATUS_ENABLED) { bt_power_attempted=0;bt_sleep_applied=-1;return; }
    if(bt_power_attempted)return;
    bt_sleep_result=bt_sleep_requested?esp_bt_sleep_enable():esp_bt_sleep_disable();bt_power_attempted=1;
    if(bt_sleep_result==ESP_OK)bt_sleep_applied=bt_sleep_requested;
    wmp_telemetry_printf("WMP_POWER requested=%d applied=%d status=%d\n",bt_sleep_requested,bt_sleep_applied,bt_sleep_result);
}
static void info(void) {
    wmp_telemetry_printf("WMP_INFO build=%s base_mac=%02x:%02x:%02x:%02x:%02x:%02x radio=%lu audio_qualified=0\n",WMP_BUILD_ID,wmp_base_mac[0],wmp_base_mac[1],wmp_base_mac[2],wmp_base_mac[3],wmp_base_mac[4],wmp_base_mac[5],(unsigned long)radio_stats.ready);
}
static void stats(unsigned long query) {
    wmp_radio_stats r=radio_stats;wmp_bt_stats b=bt_stats;wmp_audio_stats a=audio_stats;
    int64_t clock_local=esp_timer_get_time();uint64_t source_tsf=wmp_source_clock();int64_t clock_span=esp_timer_get_time()-clock_local;
    wmp_telemetry_printf("WMP_RADIO query=%08lx time_us=%"PRId64" mode=%lu pps=%lu copies=%lu spacing=%lu produced=%lu qfull=%lu attempt=%lu accepted=%lu done=%lu fail=%lu api_error=%lu timeout=%lu late=%lu api_max=%lu callback_qfull=%lu\n",query,esp_timer_get_time(),(unsigned long)r.mode,(unsigned long)r.pps,(unsigned long)r.copies,(unsigned long)r.spacing_us,(unsigned long)r.produced,(unsigned long)r.queue_full,(unsigned long)r.tx_attempt,(unsigned long)r.tx_accepted,(unsigned long)r.tx_done,(unsigned long)r.tx_fail,(unsigned long)r.tx_api_error,(unsigned long)r.tx_timeout,(unsigned long)r.tx_late,(unsigned long)r.tx_api_max_us,(unsigned long)r.tx_callback_queue_full);
    wmp_telemetry_printf("WMP_RX query=%08lx callbacks=%lu rejected=%lu qfull=%lu bad=%lu unique=%lu duplicate=%lu conflict=%lu stale=%lu bytes=%lu sbc_frames=%lu max_gap_us=%lu callback_delay_max=%lu sessions=%lu first=%lu highest=%lu mac_rx=%lu mac_callback=%lu\n",query,(unsigned long)r.rx_callback,(unsigned long)r.rx_rejected,(unsigned long)r.rx_queue_full,(unsigned long)r.rx_bad,(unsigned long)r.rx_unique,(unsigned long)r.rx_duplicate,(unsigned long)r.rx_conflict,(unsigned long)r.rx_stale,(unsigned long)r.rx_bytes,(unsigned long)r.rx_sbc_frames,(unsigned long)r.rx_max_gap_us,(unsigned long)r.rx_callback_delay_max_us,(unsigned long)r.rx_sessions,(unsigned long)r.rx_first,(unsigned long)r.rx_highest,(unsigned long)r.clock_sample,(unsigned long)r.clock_callback);
    wmp_telemetry_printf("WMP_BT query=%08lx requested=%lu working=%lu controller=%u transport_started=%lu phone=%lu streaming=%lu ble=%lu ble_writes=%lu ble_max=%lu rtp=%lu rtp_gaps=%lu rtp_bad=%lu fragments=%lu frames=%lu bytes=%lu groups=%lu blocked_frames=%lu discontinuity=%lu rate=%lu\n",query,(unsigned long)b.requested,(unsigned long)b.working,esp_bt_controller_get_status(),(unsigned long)b.transport_started,(unsigned long)b.phone,(unsigned long)b.streaming,(unsigned long)b.ble,(unsigned long)b.ble_writes,(unsigned long)b.ble_max_us,(unsigned long)b.rtp_packets,(unsigned long)b.rtp_gaps,(unsigned long)b.rtp_bad,(unsigned long)b.rtp_fragments,(unsigned long)b.sbc_frames,(unsigned long)b.sbc_bytes,(unsigned long)b.source_groups,(unsigned long)b.source_blocked_frames,(unsigned long)b.group_discontinuity,(unsigned long)b.rate);
    wmp_telemetry_printf("WMP_AUDIO query=%08lx running=%lu rate=%lu queued=%lu qfull=%lu decoded=%lu codec_bad=%lu pcm_bad=%lu concealed=%lu late=%lu write_errors=%lu clock_pairs=%lu clock_bad=%lu clock_valid=%lu clock_residual_us=%lu underrun=%lu output_frames=%lu phase_us=%ld correction_ppm=%ld realign_pad=%lu realign_pad_ms=%lu realign_skip=%lu idle_stops=%lu\n",query,(unsigned long)a.running,(unsigned long)a.rate,(unsigned long)a.queued,(unsigned long)a.queue_full,(unsigned long)a.decoded,(unsigned long)a.codec_bad,(unsigned long)a.pcm_bad,(unsigned long)a.concealed,(unsigned long)a.late,(unsigned long)a.write_errors,(unsigned long)a.clock_pairs,(unsigned long)a.clock_bad,(unsigned long)a.clock_valid,(unsigned long)a.clock_residual_us,(unsigned long)a.underrun,(unsigned long)a.output_frames,(long)a.phase_us,(long)a.correction_ppm,(unsigned long)a.realign_pad,(unsigned long)a.realign_pad_ms,(unsigned long)a.realign_skip,(unsigned long)a.idle_stops);
    wmp_telemetry_printf("WMP_PHY query=%08lx selected_mbps=%lu tx_rate=%lu rx_rate=%lu rssi=%ld unmatched=%lu source_tsf=%llu clock_local_us=%lld clock_span_us=%lld\n",query,(unsigned long)r.phy_mbps,(unsigned long)r.tx_phy_rate,(unsigned long)r.rx_phy_rate,(long)r.rx_rssi,(unsigned long)r.tx_unmatched,(unsigned long long)source_tsf,(long long)clock_local,(long long)clock_span);
    wmp_telemetry_printf("WMP_CLOCK_QUALITY query=%08lx fit_us=%lu ppm=%ld arrivals=%lu negative=%lu under5=%lu min_us=%ld lead_us=%lu\n",query,(unsigned long)a.clock_fit_us,(long)a.clock_ppm,(unsigned long)a.arrivals,(unsigned long)a.arrival_negative,(unsigned long)a.arrival_under5,(long)a.arrival_min_us,(unsigned long)r.latency_us);
    wmp_telemetry_printf("WMP_LATENCY query=%08lx requested_us=%lu phase_us=%ld target_phase_us=%ld changes=%lu\n",query,(unsigned long)r.latency_us,(long)r.latency_phase_us,(long)r.latency_target_phase_us,(unsigned long)r.latency_changes);
    wmp_telemetry_printf("WMP_QOS query=%08lx reports_sent=%lu reports_received=%lu\n",query,(unsigned long)r.qos_sent,(unsigned long)r.qos_received);
    wmp_telemetry_printf("WMP_PREPARATION query=%08lx source_wait=%lu receiver_wait=%lu source_skipped_frames=%lu receiver_skipped_groups=%lu\n",query,(unsigned long)r.preparing,(unsigned long)a.preparing,(unsigned long)b.preparation_frames,(unsigned long)a.preparation_skips);
    wmp_telemetry_printf("WMP_CLOCK_HEALTH query=%08lx expirations=%lu unavailable_skips=%lu\n",query,(unsigned long)a.clock_expirations,(unsigned long)a.clock_unavailable_skips);
    wmp_telemetry_printf("WMP_TRANSITION query=%08lx pending=%lu target=%lu applied=%lu max_us=%lu\n",query,(unsigned long)r.transition_pending,(unsigned long)r.transition_target,(unsigned long)r.transition_count,(unsigned long)r.transition_max_us);
    wmp_radio_session_stats(query,r.mode==2u);
    wmp_radio_repair_gate_stats(query);
    wmp_radio_dither_stats(query);
    wmp_telemetry_printf("WMP_CONSOLE query=%08lx buffered=1 rx_bytes=256 tx_bytes=1024\n",query);
    wmp_telemetry_printf("WMP_RADIO_RESOURCES query=%08lx source_slots_bytes=%lu starts=%lu stops=%lu failures=%lu history_bytes=%lu\n",query,(unsigned long)r.slot_resource_bytes,(unsigned long)r.slot_resource_starts,(unsigned long)r.slot_resource_stops,(unsigned long)r.slot_resource_failures,(unsigned long)r.trace_resource_bytes);
    wmp_telemetry_printf("WMP_REPAIR_AIR query=%08lx attempted=%lu accepted=%lu completed=%lu failed=%lu invalid=%lu window_wait=%lu peer_age_us=%lu\n",query,(unsigned long)r.replay_attempted,(unsigned long)r.replay_accepted,(unsigned long)r.replay_completed,(unsigned long)r.replay_failed,(unsigned long)r.replay_invalid,(unsigned long)r.nack_window_wait,(unsigned long)r.nack_window_age_us);
    wmp_telemetry_printf("WMP_REPAIR_LINK query=%08lx unicast=%lu submitted=%lu completed=%lu failed=%lu\n",query,(unsigned long)r.repair_unicast,(unsigned long)r.repair_unicast_sent,(unsigned long)r.repair_unicast_done,(unsigned long)r.repair_unicast_failed);
    wmp_telemetry_printf("WMP_NATIVE_RX query=%08lx requested=%lu active=%lu callbacks=%lu queued=%lu prefix=%lu calls=%lu done=%lu failed=%lu status=%ld copy_timestamp_is_capture=%u\n",query,(unsigned long)r.native_rx_requested,(unsigned long)r.native_rx_active,(unsigned long)r.native_rx_callbacks,(unsigned long)r.native_rx_queued,(unsigned long)r.native_rx_prefix,(unsigned long)r.native_roc_calls,(unsigned long)r.native_roc_done,(unsigned long)r.native_roc_failed,(long)r.native_roc_status,r.native_rx_active?0u:1u);
    wmp_telemetry_printf("WMP_DIRECT_RX query=%08lx enabled=%lu callbacks=%lu queued=%lu status=%ld copy_timestamp_is_capture=0 diagnostic=1\n",query,(unsigned long)r.direct_rx_enabled,(unsigned long)r.direct_rx_callbacks,(unsigned long)r.direct_rx_queued,(long)r.direct_rx_status);
    wmp_telemetry_printf("WMP_DIRECT_AIR query=%08lx frames=%lu bad=%lu same_sequence=%lu last_sequence=%lu diagnostic=1\n",query,(unsigned long)r.direct_air_frames,(unsigned long)r.direct_air_bad,(unsigned long)r.direct_air_same_sequence,(unsigned long)r.direct_air_last_sequence);
    wmp_telemetry_printf("WMP_RECEIVE_WINDOW query=%08lx enabled=%lu status=%ld window_ms=%u interval_ms=%lu interval_status=%ld diagnostic=1\n",query,(unsigned long)r.receive_window_enabled,(long)r.receive_window_status,wmp_radio_receive_window_value(),(unsigned long)r.receive_interval_ms,(long)r.receive_interval_status);
    wmp_radio_wake_stats(query);
    wmp_telemetry_printf("WMP_TIMELINE query=%08lx recoveries=%lu missing_source_samples=%llu\n",query,(unsigned long)r.timeline_recoveries,(unsigned long long)r.missing_source_samples);
    wmp_telemetry_printf("WMP_SILENCE query=%08lx queued=%lu received=%lu played_groups=%lu played_frames=%lu\n",query,(unsigned long)r.silence_queued,(unsigned long)r.silence_received,(unsigned long)a.silence_groups,(unsigned long)a.silence_frames);
    wmp_telemetry_printf("WMP_REPLAY query=%08lx enabled=%lu nack_queued=%lu nack_sent=%lu nack_heard=%lu suppressed=%lu queued=%lu cache_miss=%lu deferred=%lu too_late=%lu received=%lu delivered=%lu played=%lu nack_delay_us=%lu fec=0\n",query,(unsigned long)r.replay_enabled,(unsigned long)r.nack_queued,(unsigned long)r.nack_sent,(unsigned long)r.nack_heard,(unsigned long)r.nack_suppressed,(unsigned long)r.replay_queued,(unsigned long)r.replay_cache_miss,(unsigned long)r.replay_deferred,(unsigned long)r.replay_too_late,(unsigned long)r.replay_received,(unsigned long)r.replay_delivered,(unsigned long)a.replay_played,(unsigned long)r.nack_delay_us);
    wmp_telemetry_printf("WMP_DIAGNOSTIC query=%08lx intentionally_dropped_copies=%lu\n",query,(unsigned long)r.diagnostic_dropped_copies);
    wmp_telemetry_printf("WMP_CLASSIC_ROLE query=%08lx current=%lu requested_policy=%lu events=%lu status=%lu requests=%lu central_is_0=1\n",query,(unsigned long)b.classic_role,(unsigned long)b.role_policy,(unsigned long)b.role_events,(unsigned long)b.role_status,(unsigned long)b.role_requests);
    wmp_telemetry_printf("WMP_BT_LINKS query=%08lx classic_disconnects=%lu classic_reason=%lu ble_disconnects=%lu ble_reason=%lu connection_failures=%lu last_status=%lu\n",query,(unsigned long)b.classic_disconnects,(unsigned long)b.classic_reason,(unsigned long)b.ble_disconnects,(unsigned long)b.ble_reason,(unsigned long)b.connection_failures,(unsigned long)b.last_connection_status);
    wmp_telemetry_printf("WMP_SECURITY query=%08lx ssp_requests=%lu pairing_status=%lu authentication_status=%lu rx_suspended=%lu\n",query,(unsigned long)b.ssp_requests,(unsigned long)b.pairing_status,(unsigned long)b.authentication_status,(unsigned long)r.rx_suspended);
    wmp_telemetry_printf("WMP_BLE_LINK query=%08lx interval_units=%lu latency=%lu timeout_units=%lu requests=%lu status=%lu\n",query,(unsigned long)b.ble_interval,(unsigned long)b.ble_latency,(unsigned long)b.ble_timeout,(unsigned long)b.ble_parameter_requests,(unsigned long)b.ble_parameter_status);
    wmp_telemetry_printf("WMP_PEERS query=%08lx classic_hi=%lu classic_lo=%lu ble_hi=%lu ble_lo=%lu classic_visible=%lu standby_frames=%lu\n",query,(unsigned long)b.phone_peer_hi,(unsigned long)b.phone_peer_lo,(unsigned long)b.ble_peer_hi,(unsigned long)b.ble_peer_lo,(unsigned long)b.classic_visible,(unsigned long)b.standby_frames);
    wmp_audio_resource_stats(query);
    wmp_telemetry_printf("WMP_SOURCE_QUEUE query=%08lx groups=%lu bytes=%lu max_groups=%lu max_bytes=%lu deferred=%lu full=%lu max_age_us=%lu playback_deferred=%lu capacity_bytes=%u capacity_groups=%u\n",query,(unsigned long)b.source_queue_groups,(unsigned long)b.source_queue_bytes,(unsigned long)b.source_queue_max_groups,(unsigned long)b.source_queue_max_bytes,(unsigned long)b.source_queue_deferred,(unsigned long)b.source_queue_full,(unsigned long)b.source_queue_max_age_us,(unsigned long)a.source_deferred,(unsigned)WMP_SBC_QUEUE_BYTES,(unsigned)WMP_SBC_QUEUE_GROUPS);
    wmp_telemetry_printf("WMP_BUFFER query=%08lx queued_on_time_late=%lu input_late=%lu startup_skips=%lu queue_wait_max_us=%lu held_max=%lu capacity=%u\n",query,(unsigned long)a.buffered_late,(unsigned long)a.input_late,(unsigned long)a.startup_skips,(unsigned long)a.queue_wait_max_us,(unsigned long)a.held_max,(unsigned)WMP_FRAME_SLOTS);
    wmp_telemetry_printf("WMP_DISPATCH query=%08lx priority=%lu max_us=%lu over1=%lu over5=%lu over10=%lu over20=%lu\n",query,(unsigned long)r.rx_priority,(unsigned long)r.rx_dispatch_max_us,(unsigned long)r.rx_dispatch_over1,(unsigned long)r.rx_dispatch_over5,(unsigned long)r.rx_dispatch_over10,(unsigned long)r.rx_dispatch_over20);
    wmp_telemetry_printf("WMP_TX_SCHEDULE query=%08lx period_us=%lu loop_max_us=%lu over5=%lu over20=%lu busy_max=%lu buffers=32 scratch_frames=1 priority=20\n",query,(unsigned long)r.tx_pace_us,(unsigned long)r.tx_loop_max_us,(unsigned long)r.tx_loop_over5,(unsigned long)r.tx_loop_over20,(unsigned long)r.tx_busy_max);
    wmp_radio_beacon_stats(query);wmp_radio_copy_summary(query);wmp_radio_repair_last(query);wmp_gatt_stats(query);wmp_settings_stats(query);wmp_radio_presence_stats(query);wmp_radio_wifi_stats(query);wmp_vhci_stats(query);
    wmp_radio_gap_stats(query);
    wmp_telemetry_printf("WMP_CONCEAL query=%08lx missing=%lu decode=%lu last_missing_sample=%lu\n",query,(unsigned long)a.concealed_missing,(unsigned long)a.concealed_decode,(unsigned long)a.last_missing_sample);
    wmp_audio_dma_stats(query);
    wmp_radio_timing_stats(query);
    wmp_radio_nack_stats(query);
    wmp_telemetry_printf("WMP_RAW_PATH query=%08lx action_frames=%lu flagged_rx=%lu esp_now=0\n",query,(unsigned long)r.tx_action_frames,(unsigned long)r.rx_native_flags);
    wmp_telemetry_printf("WMP_WIFI_MODE query=%08lx ap=%lu beacon_tu=%lu channel=%lu noise_floor=%ld no_association=%u\n",query,(unsigned long)r.wifi_ap,(unsigned long)r.beacon_tu,(unsigned long)r.channel,(long)r.noise_floor,r.association_connected?0u:1u);
    wmp_telemetry_printf("WMP_ASSOC query=%08lx requested=%lu connected=%lu attempts=%lu status=%ld diagnostic=1\n",query,(unsigned long)r.association_requested,(unsigned long)r.association_connected,(unsigned long)r.association_attempts,(long)r.association_status);
    wmp_telemetry_printf("WMP_ASSOC_EVENTS query=%08lx connected=%lu disconnected=%lu reason=%lu\n",query,(unsigned long)r.association_connect_events,(unsigned long)r.association_disconnect_events,(unsigned long)r.association_disconnect_reason);
    int network_rssi=0,network_rssi_status=wmp_radio_wifi_live()?esp_wifi_sta_get_rssi(&network_rssi):ESP_ERR_WIFI_NOT_INIT;
    wmp_telemetry_printf("WMP_NETWORK query=%08lx infrastructure=%lu status=%ld rssi_status=%d rssi=%d diagnostic=1\n",query,(unsigned long)r.infrastructure_requested,(long)r.infrastructure_status,network_rssi_status,network_rssi);
    wmp_telemetry_printf("WMP_ETH query=%08lx enabled=%lu rx=%lu queued=%lu tx_done=%lu tx_unknown=%lu tx_prefix=%lu copy_timestamp_is_capture=0 tx_phy_rate_known=0 diagnostic=1\n",query,(unsigned long)r.ethernet_enabled,(unsigned long)r.ethernet_rx,(unsigned long)r.ethernet_queued,(unsigned long)r.ethernet_tx_done,(unsigned long)r.ethernet_tx_unknown,(unsigned long)r.ethernet_tx_prefix);
    wmp_telemetry_printf("WMP_ETH_RATE query=%08lx configured_mbps=%lu native_auto=%u ampdu=%u status=%ld last_send_status=%ld\n",query,(unsigned long)((r.infrastructure_requested || WIFI_AMPDU_TX_ENABLED)?0u:r.phy_mbps),(unsigned)(r.infrastructure_requested || WIFI_AMPDU_TX_ENABLED),(unsigned)WIFI_AMPDU_TX_ENABLED,(long)r.ethernet_rate_status,(long)r.tx_last_api_status);
    wmp_telemetry_printf("WMP_TX_API query=%08lx core=%lu calls=%lu total_us=%llu over1ms=%lu over5ms=%lu\n",query,(unsigned long)r.tx_task_core,(unsigned long)r.tx_api_calls,(unsigned long long)r.tx_api_total_us,(unsigned long)r.tx_api_over1ms,(unsigned long)r.tx_api_over5ms);
    wmp_telemetry_printf("WMP_TX_WINDOW query=%08lx active_limit=%lu allocated=%u raw_limit=%u scratch_frames=1\n",query,(unsigned long)r.tx_window,(unsigned)WMP_RADIO_TX_CAPACITY,(unsigned)WMP_RADIO_TX_CAPACITY);
    wmp_telemetry_printf("WMP_LISTEN query=%08lx requested=%lu active=%lu starts=%lu done=%lu errors=%lu status=%ld duration_ms=80 gap_ms=20 channel=%lu diagnostic=1\n",query,(unsigned long)r.listen_requested,(unsigned long)r.listen_active,(unsigned long)r.listen_starts,(unsigned long)r.listen_done,(unsigned long)r.listen_errors,(long)r.listen_status,(unsigned long)r.channel);
    wmp_telemetry_printf("WMP_ROLE_POLICY query=%08lx follower_lock=%lu diagnostic_only=%lu\n",query,(unsigned long)b.follower_lock,(unsigned long)b.follower_lock);
    wifi_ps_type_t wifi_ps=WIFI_PS_NONE;bool wifi_sniffer=false;
    int live=wmp_radio_wifi_live();
    int wifi_ps_status=live?esp_wifi_get_ps(&wifi_ps):ESP_ERR_WIFI_NOT_INIT,wifi_sniffer_status=live?esp_wifi_get_promiscuous(&wifi_sniffer):ESP_ERR_WIFI_NOT_INIT;
    uint8_t primary_channel=0;wifi_second_chan_t secondary_channel=WIFI_SECOND_CHAN_NONE;
    int channel_status=live?esp_wifi_get_channel(&primary_channel,&secondary_channel):ESP_ERR_WIFI_NOT_INIT;
    wmp_telemetry_printf("WMP_WIFI_CHANNEL query=%08lx status=%d primary=%u secondary=%u configured=%lu\n",query,channel_status,(unsigned)primary_channel,(unsigned)secondary_channel,(unsigned long)r.channel);
    wmp_telemetry_printf("WMP_WIFI_STATE query=%08lx ps_status=%d ps=%u sniffer_status=%d sniffer=%u eth_sniff_frames=%lu eth_sniff_rate=%lu eth_sniff_rssi=%ld\n",query,wifi_ps_status,(unsigned)wifi_ps,wifi_sniffer_status,(unsigned)wifi_sniffer,(unsigned long)r.ethernet_sniff_frames,(unsigned long)r.ethernet_sniff_rate,(long)r.ethernet_sniff_rssi);
    wmp_source_clock_stats(query);
    wmp_telemetry_printf("WMP_POWER_STAT query=%08lx requested=%d applied=%d status=%d\n",query,bt_sleep_requested,bt_sleep_applied,bt_sleep_result);
    wmp_telemetry_printf("WMP_PROFILE_STAT query=%08lx enabled=%d streaming=%lu applied=%d status=%d\n",query,a2dp_profile_enabled,(unsigned long)b.streaming,a2dp_profile_applied,a2dp_profile_result);
    wmp_radio_memory_stats(query);wmp_radio_rate_stats(query);wmp_radio_network_stats(query);wmp_audio_memory_stats(query);wmp_bluetooth_memory_stats(query);
    wmp_telemetry_printf("WMP_MEMORY query=%08lx free=%lu minimum=%lu largest=%lu internal=%lu main_stack_free=%u\n",query,(unsigned long)esp_get_free_heap_size(),(unsigned long)esp_get_minimum_free_heap_size(),(unsigned long)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),(unsigned long)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),(unsigned)(uxTaskGetStackHighWaterMark(NULL)));
    wmp_telemetry_printf("WMP_STAT_END query=%08lx heap=%lu minimum_heap=%lu\n",query,(unsigned long)esp_get_free_heap_size(),(unsigned long)esp_get_minimum_free_heap_size());
}
static void command(char *line) {
    unsigned mode,pps,copies,spacing,payload;unsigned long query;unsigned long long focus_gen,focus_seq;
    char network_name[33],network_key[64];
    if(!strcmp(line,"INFO"))info();
    else if(sscanf(line,"PING %lx",&query)==1)wmp_telemetry_printf("WMP_PONG nonce=%08lx\n",query);
    else if(sscanf(line,"STAT %lx",&query)==1) { if(wmp_telemetry_begin(query)) { stats(query);wmp_telemetry_end(query); } }
    else if(sscanf(line,"CLOCK %lx",&query)==1)wmp_audio_clock_trace(query);
    else if(sscanf(line,"HCICTRACE %lx",&query)==1)wmp_vhci_control_trace(query);
    else if(sscanf(line,"BTHOST %lx",&query)==1) { if(!wmp_bluetooth_host_trace(query))wmp_telemetry_printf("WMP_BTHOST_END query=%08lx rejected=1\n",query); }
    else if(sscanf(line,"REPAIRTRACE %lx",&query)==1)wmp_radio_repair_trace(query);
    else if(sscanf(line,"DELIVERYTRACE %lx",&query)==1)wmp_radio_delivery_trace(query);
    else if(sscanf(line,"FOCUS %llu %llu",&focus_gen,&focus_seq)==2)wmp_telemetry_printf("WMP_FOCUS accepted=%d generation=%llu seq=%llu\n",wmp_radio_delivery_arm((uint64_t)focus_gen,(uint64_t)focus_seq),focus_gen,focus_seq);
    else if(!strcmp(line,"FOCUSCLEAR"))wmp_telemetry_printf("WMP_FOCUS_CLEAR accepted=%d\n",wmp_radio_delivery_clear());
    else if(sscanf(line,"FAULTTRACE %lx",&query)==1)wmp_audio_fault_trace(query);
    else if(sscanf(line,"DROPTRACE %lx",&query)==1)wmp_radio_drop_trace(query);
    else if(!strcmp(line,"DROPCLEAR"))wmp_telemetry_printf("WMP_DROP_CLEAR accepted=%d\n",wmp_radio_drop_clear());
    else if(sscanf(line,"REPAIRPACE %u",&mode)==1)wmp_telemetry_printf("WMP_REPAIR_PACE accepted=%d us=%u\n",wmp_radio_repair_pace(mode),mode);
    else if(sscanf(line,"REPAIRDEDUP %u",&mode)==1)wmp_telemetry_printf("WMP_REPAIR_DEDUP accepted=%d enabled=%u\n",wmp_radio_repair_dedup(mode),mode);
    else if(sscanf(line,"REPAIRLIMIT %u",&mode)==1)wmp_telemetry_printf("WMP_REPAIR_LIMIT accepted=%d limit=%u\n",wmp_audio_repair_limit(mode),mode);
    else if(sscanf(line,"NACKDELAY %u",&mode)==1)wmp_telemetry_printf("WMP_NACK_DELAY accepted=%d us=%u\n",wmp_radio_nack_delay(mode),mode);
    else if(sscanf(line,"NACKGATE %u",&mode)==1)wmp_telemetry_printf("WMP_NACK_GATE accepted=%d us=%u\n",wmp_radio_nack_gate(mode),mode);
    else if(sscanf(line,"REPAIRCFG %u %u %u %u %u",&mode,&pps,&copies,&spacing,&payload)==5)wmp_telemetry_printf("WMP_REPAIR_CONFIG accepted=%d\n",wmp_radio_repair_config(mode,pps,copies,spacing,payload));
    else if(sscanf(line,"TRACE %lx",&query)==1)wmp_radio_copy_trace(query);
    else if(sscanf(line,"RADIOTRACE %lx",&query)==1)wmp_coex_trace_dump(query);
    else if(sscanf(line,"TXPHASE %u %u",&mode,&spacing)==2)wmp_telemetry_printf("WMP_TX_PHASE_REQUEST accepted=%d mask=%u urgent_ms=%u\n",wmp_radio_tx_phase(mode,spacing),mode,spacing);
    else if(sscanf(line,"GROUPFRAMES %u",&mode)==1)wmp_telemetry_printf("WMP_GROUP_FRAMES_REQUEST accepted=%d frames=%u\n",wmp_bluetooth_group_frames(mode),mode);
    else if(sscanf(line,"LIVECHANNEL %u",&mode)==1) { unsigned elapsed=0;int status=wmp_radio_channel_live(mode,&elapsed);wmp_telemetry_printf("WMP_LIVECHANNEL status=%d channel=%u elapsed_us=%u%c",status,mode,elapsed,10); }
    else if(sscanf(line,"APLL %u",&mode)==1)wmp_telemetry_printf("WMP_APLL_REQUEST accepted=%d enabled=%u%c",wmp_audio_apll(mode),mode,10);
    else if(sscanf(line,"PARTYCONNECT %u",&mode)==1)wmp_telemetry_printf("WMP_PARTYCONNECT_REQUEST accepted=%d on=%u%c",wmp_bluetooth_party_connect(mode),mode,10);
    else if(strcmp(line,"PARTYAUTO 1")==0)wmp_telemetry_printf("WMP_PARTYAUTO_REQUEST accepted=%d%c",wmp_bluetooth_party_auto(),10);
    else if(sscanf(line,"PARTY %u",&mode)==1)wmp_telemetry_printf("WMP_PARTY_REQUEST accepted=%d on=%u%c",wmp_bluetooth_party(mode),mode,10);
    else if(sscanf(line,"LATEGROWTH %u",&mode)==1)wmp_telemetry_printf("WMP_LATEGROWTH accepted=%d enabled=%u%c",wmp_radio_late_growth(mode),mode,10);
    else if(sscanf(line,"COEXALIGN %u",&mode)==1)wmp_telemetry_printf("WMP_COEXALIGN accepted=%d enabled=%u%c",wmp_radio_coex_align(mode),mode,10);
    else if(sscanf(line,"NACKCOPIES %u %u",&mode,&spacing)==2)wmp_telemetry_printf("WMP_NACK_COPIES accepted=%d copies=%u spacing_us=%u%c",wmp_radio_nack_copies(mode,spacing),mode,spacing,10);
    else if(sscanf(line,"SNIFF %u",&mode)==1)wmp_telemetry_printf("WMP_SNIFF_REQUEST accepted=%d enabled=%u\n",wmp_bluetooth_follower_sniff(mode),mode);
    else if(sscanf(line,"STARTREJECT %u",&mode)==1)wmp_telemetry_printf("WMP_START_REJECT_REQUEST accepted=%d enabled=%u%c",wmp_bluetooth_start_reject(mode),mode,10);
    else if(sscanf(line,"STANDBYSUSPEND %u",&mode)==1)wmp_telemetry_printf("WMP_STANDBY_REQUEST accepted=%d enabled=%u\n",wmp_bluetooth_standby_suspend(mode),mode);
    else if(sscanf(line,"BLENOTIFY %u",&mode)==1)wmp_telemetry_printf("WMP_BLE_NOTIFY_REQUEST accepted=%d period_ms=%u\n",wmp_gatt_notify_period(mode),mode);
    else if(strcmp(line,"COEXSTATE")==0)wmp_coex_state();
    else if(sscanf(line,"COEXREQ %lx",&query)==1)wmp_coex_requests(query);
    else if(sscanf(line,"COEXPRIO %u %u",&mode,&spacing)==2)wmp_telemetry_printf("WMP_COEXPRIO accepted=%d event=%u prio=%u%c",wmp_coex_priority(mode,spacing),mode,spacing,10);
    else if(sscanf(line,"COEXBIT %u %x %u",&mode,&spacing,&copies)==3) { wmp_telemetry_printf("WMP_COEXBIT accepted=%d\n",wmp_coex_status_bit(mode,spacing,copies));wmp_coex_state(); }
    else if(sscanf(line,"TXPOWER %u",&mode)==1 && mode>=8u && mode<=84u && wmp_radio_wifi_live()) { int status=esp_wifi_set_max_tx_power((int8_t)mode);int8_t power=0;esp_wifi_get_max_tx_power(&power);wmp_telemetry_printf("WMP_TXPOWER accepted=%d status=%d quarter_dbm=%d\n",status==ESP_OK,status,(int)power); }
#ifdef CONFIG_HEAP_TRACING_STANDALONE
    else if(sscanf(line,"HEAPTRACE %u",&mode)==1) {
        if(!trace_ready) { ESP_ERROR_CHECK(heap_trace_init_standalone(trace_records,120));trace_ready=1; }
        if(mode) { heap_trace_start(HEAP_TRACE_LEAKS);wmp_telemetry_printf("WMP_HEAPTRACE started%c",10); }
        else { heap_trace_stop();heap_trace_dump();wmp_telemetry_printf("WMP_HEAPTRACE done%c",10); }
    }
#endif
    else if(sscanf(line,"VOLUME %u",&mode)==1)wmp_telemetry_printf("WMP_VOLUME_REQUEST accepted=%d volume=%u\n",wmp_audio_set_volume(mode),wmp_audio_volume());
    else if(sscanf(line,"PCMCAPTURE %u",&mode)==1)wmp_telemetry_printf("WMP_PCMCAPTURE accepted=%d%c",wmp_audio_capture_start(mode),10);
    else if(!strcmp(line,"DSPBENCH"))wmp_telemetry_printf("WMP_DSPBENCH cycles_per_sample=%lu\n",(unsigned long)wmp_audio_dsp_bench());
    else if(!strcmp(line,"PCMDUMP")) { if(!wmp_audio_capture_dump())wmp_telemetry_printf("WMP_PCM_NOT_READY%c",10); }
    else if(sscanf(line,"BCODEC %u",&mode)==1)wmp_telemetry_printf("WMP_BCODEC_REQUEST accepted=%d\n",wmp_bluetooth_broadcast_codec(mode));
    else if(sscanf(line,"BROADCAST %u",&mode)==1)wmp_telemetry_printf("WMP_BROADCAST_REQUEST accepted=%d\n",wmp_bluetooth_broadcast_request(mode));
    else if(sscanf(line,"SPEAKER %u",&mode)==1)wmp_telemetry_printf("WMP_SPEAKER_REQUEST accepted=%d setting=%u output=%u party=%u\n",wmp_audio_set_speaker_channel(mode),wmp_audio_speaker_channel(),wmp_audio_output_channel(),wmp_audio_party());
    else if(!strncmp(line,"EQ ",3)) {
        int g[WMP_EQ_BANDS];int8_t e[WMP_EQ_BANDS];int ok=sscanf(line+3,"%d %d %d %d %d %d %d",&g[0],&g[1],&g[2],&g[3],&g[4],&g[5],&g[6])==7;
        for(unsigned i=0;ok && i<WMP_EQ_BANDS;i++) { if(g[i]<-12 || g[i]>12)ok=0;else e[i]=(int8_t)g[i]; }
        wmp_telemetry_printf("WMP_EQ_REQUEST accepted=%d\n",ok && wmp_audio_set_eq(e));
    }
    else if(sscanf(line,"CODECBENCH %u",&mode)==1 && mode<=1u) {
        uint32_t r[4]={0};int ok=wmp_codecs_bench((int)mode,r);
        wmp_telemetry_printf("WMP_CODECBENCH accepted=%d hd=%u group_cycles=%lu group_us=%lu qmf_cycles=%lu prediction_cycles=%lu out_bytes=%lu%c",ok,mode,(unsigned long)r[0],(unsigned long)(r[0]/240u),(unsigned long)r[1],(unsigned long)r[2],(unsigned long)r[3],10);
    }
    else if(strcmp(line,"HWSTAT")==0) {
        /* MAC hardware RX/TX counters, register map recovered from libpp dbg_lmac_hw_statis_dump. */
        static const struct { const char *name; uint32_t addr; } regs[]={
            {"rxbufbk",0x3ff73094u},{"rxcckerr",0x3ff7328cu},{"ofdmerr",0x3ff73290u},{"rx_agc",0x3ff73294u},
            {"rxsf",0x3ff73298u},{"rx_abort",0x3ff7329cu},{"rx_fcs",0x3ff732a0u},{"fifofull",0x3ff732a4u},
            {"afull",0x3ff732a8u},{"rx_oth",0x3ff732b0u},{"tkiperr",0x3ff732b4u},{"samebm",0x3ff732b8u},
            {"ack_int",0x3ff732bcu},{"rts_int",0x3ff732c0u},{"cts_int",0x3ff732c4u},{"rifs_int",0x3ff732c8u},
            {"blockerr",0x3ff732e0u},{"hoperr",0x3ff732d8u},{"txrts",0x3ff73d58u},{"txcts",0x3ff73d5cu},
            {"track",0x3ff73d60u},{"trcts",0x3ff73d64u},{"trigger",0x3ff73d68u},{"txhung",0x3ff73d6cu},{"panic",0x3ff73d70u}};
        char out[640];size_t used=(size_t)snprintf(out,sizeof(out),"WMP_HWSTAT at=%lld",(long long)esp_timer_get_time());
        for(size_t i=0;i<sizeof(regs)/sizeof(regs[0]) && used<sizeof(out);++i)
            used+=(size_t)snprintf(out+used,sizeof(out)-used," %s=%lu",regs[i].name,(unsigned long)*(volatile uint32_t*)regs[i].addr);
        wmp_telemetry_printf("%s%c",out,10);
    }
    else if(sscanf(line,"WIFISTAT %lx",&query)==1 && wmp_radio_wifi_live()) { esp_wifi_statis_dump((uint32_t)query);wmp_telemetry_printf("WMP_WIFISTAT_END mask=%lx%c",query,10); }
    else if(sscanf(line,"RFAWAKE %u",&mode)==1)wmp_telemetry_printf("WMP_RFAWAKE accepted=%d held=%u\n",wmp_radio_rf_awake(mode),wmp_radio_rf_awake_held());
    else if(sscanf(line,"RXFILTER %u",&mode)==1) { int ok=wmp_radio_rx_filter(mode);unsigned long any,leader;wmp_radio_rx_fcs(&any,&leader);wmp_telemetry_printf("WMP_RXFILTER accepted=%d classes=%u fcs_any=%lu fcs_leader=%lu\n",ok,wmp_radio_rx_filter_classes(),any,leader); }
    else if(sscanf(line,"FOLLOWERCOEX %u",&mode)==1 && (mode==0u || (mode>=20u && mode<=95u))) { follower_coex_percent=mode;role_preference_attempted=-1;controller_profile();wmp_coex_state(); }
    else if(sscanf(line,"COEXTABLE %u %u",&mode,&spacing)==2) { wmp_telemetry_printf("WMP_COEXTABLE accepted=%d wifi_pct=%u type=%u\n",wmp_coex_override(mode,spacing),mode,spacing);wmp_coex_state(); }
    else if(sscanf(line,"RADIOLOG %x",&mode)==1)wmp_telemetry_printf("WMP_RADIOLOG accepted=%d enabled=%u\n",wmp_coex_trace_arm(mode),mode);
    else if(!strcmp(line,"GAPRESET")) { wmp_radio_gap_reset();wmp_telemetry_printf("WMP_GAP_RESET accepted=1\n"); }
    else if(sscanf(line,"SCHEDULE %u %u",&spacing,&mode)==2)wmp_telemetry_printf("WMP_SCHEDULE accepted=%d spacing=%u paced=%u\n",wmp_radio_schedule(spacing,mode),spacing,mode);
    else if(sscanf(line,"TXDITHER %u",&spacing)==1)wmp_telemetry_printf("WMP_TX_DITHER_REQUEST accepted=%d us=%u\n",wmp_radio_copy_dither(spacing),spacing);
    else if(sscanf(line,"TXPACE %u",&spacing)==1)wmp_telemetry_printf("WMP_TXPACE accepted=%d us=%u\n",wmp_radio_pace(spacing),spacing);
    else if(sscanf(line,"RAWPATH %u",&mode)==1)wmp_telemetry_printf("WMP_RAW_PATH_REQUEST accepted=%d action_frames=%u\n",wmp_radio_path(mode),mode);
    else if(sscanf(line,"REPLAY %u",&mode)==1)wmp_telemetry_printf("WMP_REPLAY_REQUEST accepted=%d enabled=%u\n",wmp_radio_replay(mode),mode);
    else if(sscanf(line,"REPAIRCAST %u",&mode)==1)wmp_telemetry_printf("WMP_REPAIR_CAST accepted=%d unicast=%u\n",wmp_radio_repair_unicast(mode),mode);
    else if(sscanf(line,"NATIVERX %u",&mode)==1)wmp_telemetry_printf("WMP_NATIVE_RX_REQUEST accepted=%d enabled=%u\n",wmp_radio_native_rx(mode),mode);
    else if(sscanf(line,"DIRECTRX %u",&mode)==1)wmp_telemetry_printf("WMP_DIRECT_RX_REQUEST accepted=%d enabled=%u\n",wmp_radio_direct_rx(mode),mode);
    else if(sscanf(line,"COMPLETION %lx",&query)==1)wmp_radio_completion_trace(query);
    else if(sscanf(line,"RXWINDOW %u",&mode)==1)wmp_telemetry_printf("WMP_RECEIVE_WINDOW_REQUEST accepted=%d enabled=%u\n",wmp_radio_receive_window(mode),mode);
    else if(sscanf(line,"RXWINDOWMS %u",&mode)==1)wmp_telemetry_printf("WMP_RECEIVE_SIZE_REQUEST accepted=%d window_ms=%u\n",wmp_radio_receive_window_size(mode),mode);
    else if(sscanf(line,"TXWAKE %u",&mode)==1)wmp_telemetry_printf("WMP_TX_WAKE_REQUEST accepted=%d span_us=%u\n",wmp_radio_tx_wake(mode),mode);
    else if(sscanf(line,"WAKESTAT %lx",&query)==1)wmp_radio_wake_stats(query);
    else if(sscanf(line,"RXINTERVAL %u",&mode)==1)wmp_telemetry_printf("WMP_RECEIVE_INTERVAL status=%d interval_ms=%u\n",wmp_radio_receive_interval(mode),mode);
    else if(!strcmp(line,"NETDROP"))wmp_telemetry_printf("WMP_NETWORK_DROP status=%d diagnostic=1\n",wmp_radio_network_drop());
    else if(!strcmp(line,"NETWORK OFF"))wmp_telemetry_printf("WMP_NETWORK_REQUEST accepted=%d\n",wmp_radio_network(NULL,NULL));
    else if(sscanf(line,"NETWORK %32s %63s",network_name,network_key)==2)wmp_telemetry_printf("WMP_NETWORK_REQUEST accepted=%d\n",wmp_radio_network(network_name,network_key));
    else if(sscanf(line,"ANCHOR %32s %63s",network_name,network_key)==2)wmp_telemetry_printf("WMP_ANCHOR_REQUEST accepted=%d\n",wmp_radio_anchor(network_name,network_key));
    else if(sscanf(line,"ASSOC %u",&mode)==1)wmp_telemetry_printf("WMP_ASSOC_REQUEST accepted=%d enabled=%u diagnostic=1\n",wmp_radio_associate(mode),mode);
    else if(sscanf(line,"ETHMODE %u",&mode)==1)wmp_telemetry_printf("WMP_ETH_REQUEST accepted=%d enabled=%u diagnostic=1\n",wmp_radio_ethernet(mode),mode);
    else if(sscanf(line,"WIFIPS %u",&mode)==1)wmp_telemetry_printf("WMP_WIFI_PS status=%d mode=%u\n",wmp_radio_power_save(mode),mode);
    else if(sscanf(line,"TXWINDOW %u",&mode)==1)wmp_telemetry_printf("WMP_TX_WINDOW_REQUEST accepted=%d limit=%u\n",wmp_radio_tx_window(mode),mode);
    else if(sscanf(line,"COPIES %u",&mode)==1)wmp_telemetry_printf("WMP_COPIES_REQUEST accepted=%d count=%u\n",wmp_radio_copies(mode),mode);
    else if(sscanf(line,"LISTEN %u",&mode)==1)wmp_telemetry_printf("WMP_LISTEN_REQUEST accepted=%d enabled=%u diagnostic=1\n",wmp_radio_scan_listen(mode),mode);
    else if(sscanf(line,"FOLLOWLOCK %u",&mode)==1)wmp_telemetry_printf("WMP_FOLLOWLOCK_REQUEST accepted=%d enabled=%u diagnostic=1\n",wmp_bluetooth_follower_lock(mode),mode);
    else if(sscanf(line,"DROPONE %u",&mode)==1)wmp_telemetry_printf("WMP_DROP_TEST accepted=%d ahead=%u diagnostic=1\n",wmp_radio_drop_test(mode),mode);
    else if(sscanf(line,"DROPRANGE %u %u",&mode,&copies)==2)wmp_telemetry_printf("WMP_DROP_TEST accepted=%d ahead=%u groups=%u diagnostic=1\n",wmp_radio_drop_range(mode,copies),mode,copies);
    else if(sscanf(line,"RXMODE %u",&mode)==1)wmp_telemetry_printf("WMP_RXMODE accepted=%d ap=%u\n",wmp_radio_receiver_mode(mode),mode);
    else if(sscanf(line,"RXBEACON %u",&mode)==1)wmp_telemetry_printf("WMP_RXBEACON accepted=%d tu=%u\n",wmp_radio_receiver_beacon(mode),mode);
    else if(sscanf(line,"CHANNEL %u",&mode)==1)wmp_telemetry_printf("WMP_CHANNEL status=%d channel=%u\n",wmp_radio_channel(mode),mode);
    else if(sscanf(line,"COPYLOG %u",&mode)==1)wmp_telemetry_printf("WMP_TRACE_RESOURCE accepted=%d enabled=%u\n",wmp_radio_trace_resources(mode),mode);
    else if(sscanf(line,"FAULTLOG %u",&mode)==1)wmp_telemetry_printf("WMP_FAULT_RESOURCE accepted=%d enabled=%u\n",wmp_audio_fault_resources(mode),mode);
    else if(sscanf(line,"RXPRIO %u",&mode)==1)wmp_telemetry_printf("WMP_RXPRIO accepted=%d priority=%u\n",wmp_radio_rx_priority(mode),mode);
    else if(sscanf(line,"AUDIOPRIO %u",&mode)==1)wmp_telemetry_printf("WMP_AUDIOPRIO accepted=%d priority=%u\n",wmp_audio_priority(mode),mode);
    else if(sscanf(line,"LATENCY %u",&mode)==1)wmp_telemetry_printf("WMP_LATENCY_REQUEST accepted=%d ms=%u\n",wmp_radio_latency(mode),mode);
    else if(sscanf(line,"QOSREPORT %u",&mode)==1)wmp_telemetry_printf("WMP_QOS_REQUEST accepted=%d frames=%u diagnostic=1\n",wmp_radio_test_report(mode),mode);
    else if(sscanf(line,"COEX %u",&mode)==1)wmp_telemetry_printf("WMP_COEX status=%d requested=%u\n",wmp_radio_preference(mode),mode);
    else if(sscanf(line,"BTSLEEP %u",&mode)==1 && mode<=1u) { bt_sleep_requested=(int)mode;bt_power_attempted=0;controller_power(); }
    else if(sscanf(line,"A2DPCOEX %u",&mode)==1 && mode<=2u) { a2dp_profile_enabled=(int)mode;a2dp_profile_attempted=-1;controller_profile(); }
    else if(!strcmp(line,"BT ON"))wmp_telemetry_printf("WMP_BT_REQUEST accepted=%d\n",wmp_bluetooth_command(1));
    else if(!strcmp(line,"BT OFF"))wmp_telemetry_printf("WMP_BT_REQUEST accepted=%d\n",wmp_bluetooth_command(3));
    else if(sscanf(line,"AUTOCHANNEL %u",&mode)==1)wmp_telemetry_printf("WMP_AUTOCHANNEL_REQUEST accepted=%d enabled=%u%c",wmp_radio_auto_channel(mode),mode,10);
    else if(sscanf(line,"CLOCKCOPY %u",&mode)==1)wmp_telemetry_printf("WMP_CLOCKCOPY accepted=%d mode=%u%c",wmp_radio_clock_copy(mode),mode,10);
    else if(sscanf(line,"TXRATE %x",&mode)==1) { int ht=0;int st=wmp_radio_tx_rate_live(mode,&ht);wmp_telemetry_printf("WMP_TXRATE status=%d ht_status=%d code=%x%c",st,ht,mode,10); }
    else if(sscanf(line,"RATE %u",&pps)==1)wmp_telemetry_printf("WMP_RATE status=%d mbps=%u\n",wmp_radio_rate(pps),pps);
    else if(sscanf(line,"RXPAUSE %u",&mode)==1)wmp_telemetry_printf("WMP_RX_PAUSE_REQUEST accepted=%d\n",wmp_radio_receive_pause(mode));
    else if(sscanf(line,"BLEPARAMS %u %u",&pps,&copies)==2)wmp_telemetry_printf("WMP_BLE_PARAMS accepted=%d\n",wmp_bluetooth_parameters(pps,copies));
    else if(!strcmp(line,"PHONECONNECT 2"))wmp_telemetry_printf("WMP_PHONE_REQUEST accepted=%d\n",wmp_bluetooth_command(4));
    else if(sscanf(line,"ROLEPOLICY %u",&mode)==1 && mode<=1u)wmp_telemetry_printf("WMP_ROLE_POLICY_REQUEST accepted=%d central=%u\n",wmp_bluetooth_command(mode?6u:5u),(unsigned)(mode==0u));
    else if(!strcmp(line,"ROLECENTRAL"))wmp_telemetry_printf("WMP_ROLE_CENTRAL_REQUEST accepted=%d\n",wmp_bluetooth_command(7u));
    else if(!strcmp(line,"PHONECONNECT"))wmp_telemetry_printf("WMP_PHONE_REQUEST accepted=%d\n",wmp_bluetooth_command(2));
    else if(!strcmp(line,"AUDIO ON"))wmp_telemetry_printf("WMP_AUDIO_REQUEST accepted=%d\n",wmp_radio_role(2));
    else if(!strcmp(line,"STOP"))wmp_telemetry_printf("WMP_STOP_REQUEST accepted=%d\n",wmp_radio_role(0));
    else if(sscanf(line,"LOAD %u %u %u %u %u",&mode,&pps,&copies,&spacing,&payload)==5)wmp_telemetry_printf("WMP_LOAD_REQUEST accepted=%d\n",wmp_radio_command(mode,pps,copies,spacing,payload));
    else wmp_telemetry_printf("WMP_BAD_COMMAND\n");
}
void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());ESP_ERROR_CHECK(esp_read_mac(wmp_base_mac,ESP_MAC_BASE));
    /* The default console polls the UART FIFO. Use the supported buffered
     * driver so long diagnostic responses wait on I/O instead of spinning. */
    ESP_ERROR_CHECK(uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM,256,1024,0,NULL,0));
    uart_vfs_dev_use_driver(CONFIG_ESP_CONSOLE_UART_NUM);
    int flags=fcntl(fileno(stdin),F_GETFL,0);configASSERT(flags>=0);configASSERT(fcntl(fileno(stdin),F_SETFL,flags|O_NONBLOCK)>=0);
    wmp_source_clock_init();wmp_audio_init();wmp_settings_load();wmp_radio_init();wmp_bluetooth_start();info();wmp_telemetry_printf("WMP_READY build=%s\n",WMP_BUILD_ID);
    { uint32_t c[16][2];unsigned n=wmp_crumbs_previous(c);wmp_telemetry_printf("WMP_CRUMBS reset=%d count=%u\n",(int)esp_reset_reason(),n);
      for(unsigned i=0;i<n;i++)wmp_telemetry_printf("WMP_CRUMB i=%u code=%lu ms=%lu value=%lu\n",i,(unsigned long)(c[i][0]>>24),(unsigned long)(c[i][0]&0xffffffu),(unsigned long)c[i][1]); }
    char line[96];unsigned used=0;
    for(;;) { wmp_source_clock_service();wmp_coex_override_poll();wmp_radio_channel_service();wmp_radio_wifi_service();wmp_settings_service();wmp_audio_service_resources();controller_power();controller_profile();int c=getchar();if(c==EOF) { clearerr(stdin);vTaskDelay(1);continue; }if(c=='\r')continue;
        if(c=='\n') { line[used]=0;command(line);used=0; }
        else if(used<sizeof(line)-1u)line[used++]=(char)c;else used=0;
    }
}
