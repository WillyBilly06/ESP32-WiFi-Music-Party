/* User settings that survive a reboot: volume, stereo-pair channel and EQ. Setters mark the
 * settings dirty; the main loop commits them to NVS once nothing has changed for a second, so
 * a slider dragged on the phone costs one flash write rather than dozens. */
#include "node.h"
#include "nvs.h"
#include "esp_timer.h"
#include <string.h>

#define SETTINGS_NAMESPACE "wmp_user"
#define SETTINGS_VERSION 2u /* 2: seven EQ bands; version 1 (five bands) is migrated on load */
typedef struct { uint8_t version,volume,channel,reserved;int8_t eq[WMP_EQ_BANDS]; } stored_settings;
typedef struct { uint8_t version,volume,channel,reserved;int8_t eq[5]; } stored_settings_v1;

static volatile int64_t dirty_since;
static volatile uint8_t loading;
static uint32_t saves,save_errors;

void wmp_settings_changed(void) { if(!loading && !dirty_since)dirty_since=esp_timer_get_time(); }
void wmp_settings_load(void) {
    nvs_handle_t h;stored_settings s;size_t bytes=sizeof(s);
    if(nvs_open(SETTINGS_NAMESPACE,NVS_READONLY,&h)!=ESP_OK)return;
    esp_err_t r=nvs_get_blob(h,"settings",&s,&bytes);nvs_close(h);
    if(r==ESP_OK && bytes==sizeof(stored_settings_v1) && s.version==1u) {
        /* 60/230/910/3600/14000 Hz -> 63/160/400/1k/2.5k/6.3k/16k: keep each old band, average between. */
        int8_t o[5];memcpy(o,s.eq,5);
        int8_t n[WMP_EQ_BANDS]={o[0],o[1],(int8_t)((o[1]+o[2])/2),o[2],o[3],(int8_t)((o[3]+o[4])/2),o[4]};
        memcpy(s.eq,n,sizeof(n));s.version=SETTINGS_VERSION;bytes=sizeof(s);
    }
    if(r!=ESP_OK || bytes!=sizeof(s) || s.version!=SETTINGS_VERSION)return;
    loading=1;
    (void)wmp_audio_set_volume(s.volume);(void)wmp_audio_set_speaker_channel(s.channel);(void)wmp_audio_set_eq(s.eq);
    loading=0;
}
void wmp_settings_service(void) {
    int64_t since=dirty_since;
    if(!since || esp_timer_get_time()-since<1000000)return;
    dirty_since=0;
    stored_settings s={.version=SETTINGS_VERSION,.volume=(uint8_t)wmp_audio_volume(),.channel=(uint8_t)wmp_audio_speaker_channel()};
    wmp_audio_get_eq(s.eq);
    nvs_handle_t h;
    if(nvs_open(SETTINGS_NAMESPACE,NVS_READWRITE,&h)!=ESP_OK) { ++save_errors;return; }
    if(nvs_set_blob(h,"settings",&s,sizeof(s))==ESP_OK && nvs_commit(h)==ESP_OK)++saves;else ++save_errors;
    nvs_close(h);
}
void wmp_settings_stats(unsigned long query) {
    int8_t eq[WMP_EQ_BANDS];wmp_audio_get_eq(eq);
    wmp_telemetry_printf("WMP_STEREO query=%08lx setting=%u output=%u party=%u volume=%u eq=%d,%d,%d,%d,%d,%d,%d saves=%lu save_errors=%lu\n",query,
        wmp_audio_speaker_channel(),wmp_audio_output_channel(),wmp_audio_party(),wmp_audio_volume(),eq[0],eq[1],eq[2],eq[3],eq[4],eq[5],eq[6],(unsigned long)saves,(unsigned long)save_errors);
}
