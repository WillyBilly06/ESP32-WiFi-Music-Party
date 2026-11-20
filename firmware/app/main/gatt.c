#include "node.h"
#include "btstack.h"
#include "bluetooth_gatt.h"
#include "ble/att_db_util.h"
#include "esp_timer.h"
#include "coex_trace.h"
#include <string.h>
#include <stdio.h>
#include "esp_random.h"
#include "psa/crypto.h"
#include "identity_key.h" /* public default key; use your own for your own speakers */

static hci_con_handle_t connection=HCI_CON_HANDLE_INVALID;
static uint16_t identity_handle,control_handle,status_handle,volume_handle,latency_handle,speaker_handle,eq_handle,broadcast_handle,changed_handle,firmware_handle,fx_handle;
static uint16_t status_cccd,changed_cccd;
static uint8_t send_pending,changed_pending,changed_announced;
static int64_t notified_at;
/* STATUS notification period; shorter periods are a BLE radio-load test. */
static volatile uint32_t notify_period_us=1000000u;
int wmp_gatt_notify_period(unsigned ms) { if(ms<10u || ms>1000u)return 0;notify_period_us=ms*1000u;return 1; }
typedef struct { uint8_t opcode;uint16_t value;int8_t eq[WMP_EQ_BANDS];uint8_t fx[WMP_FX_BYTES]; } setting;
/* ATT callbacks and this FIFO's consumer run on the serialized BTstack host. */
static setting pending[4];static unsigned head,count;
static volatile uint32_t accepted,applied,rejected,notifications;

static void status_value(uint8_t value[20]) {
    memset(value,0,20);value[0]=1;value[1]=radio_stats.mode==2u?2u:(audio_stats.running?1u:0u);
    value[2]=(bt_stats.phone?1u:0u)|(bt_stats.ble?2u:0u);value[3]=(bt_stats.streaming?1u:0u)|(radio_stats.rx_active?2u:0u);
    if(radio_stats.preparing || audio_stats.preparing)value[3]|=4u;
    little_endian_store_32(value,4,audio_stats.rate);little_endian_store_16(value,8,(uint16_t)(radio_stats.latency_us/1000u));
    value[10]=(uint8_t)wmp_audio_volume();value[11]=(uint8_t)(radio_stats.mode==2u?audio_stats.running:audio_stats.clock_valid);
    little_endian_store_32(value,12,audio_stats.concealed);little_endian_store_32(value,16,audio_stats.late);
}
/* Identity handshake (characteristic aefb0009), mutual, three ATT operations:
 *   app writes  [1, app_nonce(8)]
 *   app reads   [state=1, key_id, speaker_nonce(8), speaker_tag(10)]
 *               speaker_tag = HMAC-SHA256(key, "WMP-S" app_nonce speaker_nonce)[0..9]
 *   app writes  [2, app_tag(10)]
 *               app_tag     = HMAC-SHA256(key, "WMP-A" speaker_nonce app_nonce)[0..9]
 * The app trusts the speaker only after checking speaker_tag; the speaker accepts every other
 * write only after checking app_tag. One proof attempt per challenge; a connection that has
 * not proven itself within WMP_IDENTITY_DEADLINE_US is dropped, so a foreign app cannot hold
 * the BLE link (and its airtime next to A2DP). */
#define WMP_IDENTITY_TAG 10u
#define WMP_IDENTITY_DEADLINE_US 10000000
static uint8_t identity_state,app_nonce[8],speaker_nonce[8],speaker_tag[WMP_IDENTITY_TAG];
static int64_t connected_at;
static volatile uint32_t identity_proven,identity_failed,identity_dropped;
static int hmac_sha256(const uint8_t *message,size_t length,uint8_t out[32]) {
    uint8_t block[64+32];size_t used=0;
    if(length>32u)return 0;
    for(unsigned i=0;i<64u;i++)block[i]=(uint8_t)((i<32u?wmp_identity_key[i]:0u)^0x36u);
    memcpy(block+64,message,length);
    if(psa_hash_compute(PSA_ALG_SHA_256,block,64u+length,out,32,&used)!=PSA_SUCCESS || used!=32u)return 0;
    for(unsigned i=0;i<64u;i++)block[i]=(uint8_t)((i<32u?wmp_identity_key[i]:0u)^0x5cu);
    memcpy(block+64,out,32);
    return psa_hash_compute(PSA_ALG_SHA_256,block,96,out,32,&used)==PSA_SUCCESS && used==32u;
}
static int identity_tag(char who,const uint8_t first[8],const uint8_t second[8],uint8_t tag[WMP_IDENTITY_TAG]) {
    uint8_t message[5+16],digest[32];memcpy(message,"WMP-",4);message[4]=(uint8_t)who;
    memcpy(message+5,first,8);memcpy(message+13,second,8);
    if(!hmac_sha256(message,sizeof(message),digest))return 0;
    memcpy(tag,digest,WMP_IDENTITY_TAG);return 1;
}
static int identity_write(const uint8_t *buffer,uint16_t bytes) {
    if(bytes==9u && buffer[0]==1u) {
        if(identity_state==2u)return 0; /* already proven on this connection */
        memcpy(app_nonce,buffer+1,8);esp_fill_random(speaker_nonce,8);
        if(!identity_tag('S',app_nonce,speaker_nonce,speaker_tag)) { identity_state=0;return ATT_ERROR_UNLIKELY_ERROR; }
        identity_state=1;return 0;
    }
    if(bytes==1u+WMP_IDENTITY_TAG && buffer[0]==2u) {
        if(identity_state==2u)return 0;
        if(identity_state!=1u)return ATT_ERROR_VALUE_NOT_ALLOWED;
        uint8_t expected[WMP_IDENTITY_TAG],difference=0;identity_state=0; /* one attempt per challenge */
        if(!identity_tag('A',speaker_nonce,app_nonce,expected))return ATT_ERROR_UNLIKELY_ERROR;
        for(unsigned i=0;i<WMP_IDENTITY_TAG;i++)difference|=(uint8_t)(expected[i]^buffer[1+i]);
        if(difference) { ++identity_failed;wmp_telemetry_printf("WMP_IDENTITY result=fail%c",10);return ATT_ERROR_INSUFFICIENT_AUTHORIZATION; }
        identity_state=2;++identity_proven;
        wmp_telemetry_printf("WMP_IDENTITY result=proven after_ms=%lu%c",(unsigned long)((esp_timer_get_time()-connected_at)/1000),10);
        return 0;
    }
    return ATT_ERROR_VALUE_NOT_ALLOWED;
}
static unsigned identity_read(uint8_t value[20]) {
    value[0]=identity_state;value[1]=(uint8_t)WMP_IDENTITY_KEY_ID;
    if(identity_state==1u) { memcpy(value+2,speaker_nonce,8);memcpy(value+10,speaker_tag,WMP_IDENTITY_TAG);return 2u+8u+WMP_IDENTITY_TAG; }
    return 2u;
}
static volatile int64_t gatt_activity_us;
int64_t wmp_gatt_last_activity(void) { return gatt_activity_us; }
static uint16_t read_value(hci_con_handle_t con,uint16_t handle,uint16_t offset,uint8_t *buffer,uint16_t capacity) {
    (void)con;uint8_t value[20];unsigned bytes=0;gatt_activity_us=esp_timer_get_time();
    if(!offset)wmp_telemetry_printf("WMP_GATT_EVENT read handle=%u at_ms=%lu%c",(unsigned)handle,(unsigned long)(gatt_activity_us/1000),10);
    if(handle==status_handle || handle==control_handle) { status_value(value);bytes=20; }
    else if(handle==volume_handle) { value[0]=(uint8_t)wmp_audio_volume();bytes=1; }
    else if(handle==latency_handle) { little_endian_store_16(value,0,(uint16_t)(radio_stats.latency_us/1000u));bytes=2; }
    else if(handle==speaker_handle) { value[0]=(uint8_t)wmp_audio_speaker_channel();value[1]=(uint8_t)wmp_audio_output_channel();value[2]=(uint8_t)wmp_audio_party();bytes=3; }
    else if(handle==broadcast_handle) { value[0]=(uint8_t)wmp_bluetooth_broadcast();value[1]=(uint8_t)wmp_radio_wifi_ready();value[2]=(uint8_t)wmp_audio_party();value[3]=(uint8_t)wmp_bluetooth_broadcast_aac();value[4]=(uint8_t)wmp_bluetooth_handover();bytes=5; }
    else if(handle==identity_handle)bytes=identity_read(value);
    else if(handle==fx_handle) { wmp_fx_get(value);bytes=WMP_FX_BYTES+1u; }
    else if(handle==eq_handle) { int8_t eq[WMP_EQ_BANDS];wmp_audio_get_eq(eq);memcpy(value,eq,WMP_EQ_BANDS);bytes=WMP_EQ_BANDS; }
    else if(handle==status_handle+1u) { little_endian_store_16(value,0,status_cccd);bytes=2; }
    else if(handle==changed_handle+1u) { little_endian_store_16(value,0,changed_cccd);bytes=2; }
    else if(handle==changed_handle) { value[0]=1;value[1]=0;value[2]=value[3]=255;bytes=4; }
    return att_read_callback_handle_blob(value,(uint16_t)bytes,offset,buffer,capacity);
}
static int write_value(hci_con_handle_t con,uint16_t handle,uint16_t transaction,uint16_t offset,uint8_t *buffer,uint16_t bytes) {
    (void)con;int64_t begin=esp_timer_get_time();gatt_activity_us=begin;
    wmp_telemetry_printf("WMP_GATT_EVENT write handle=%u bytes=%u at_ms=%lu%c",(unsigned)handle,(unsigned)bytes,(unsigned long)(begin/1000),10);
    if(transaction!=ATT_TRANSACTION_MODE_NONE || offset || !buffer || !bytes)return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
    if(handle==identity_handle)return identity_write(buffer,bytes);
    if(identity_state!=2u) { ++rejected;return ATT_ERROR_INSUFFICIENT_AUTHORIZATION; } /* not our app (yet) */
    if(handle==status_handle+1u || handle==changed_handle+1u) {
        if(bytes!=2u)return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
        uint16_t value=little_endian_read_16(buffer,0);
        if(handle==status_handle+1u) { if(value>1u)return ATT_ERROR_VALUE_NOT_ALLOWED;status_cccd=value; }
        else { if(value!=0u && value!=2u)return ATT_ERROR_VALUE_NOT_ALLOWED;changed_cccd=value;if(value==2u && !changed_announced)changed_pending=1; }
        return 0;
    }
    setting command={0};
    if(handle==volume_handle && bytes==1u) { command.opcode=1;command.value=buffer[0]; }
    else if(handle==latency_handle && bytes==2u) { command.opcode=2;command.value=little_endian_read_16(buffer,0); }
    else if(handle==speaker_handle && bytes==1u) { command.opcode=3;command.value=buffer[0]; }
    else if(handle==broadcast_handle && bytes==1u) { command.opcode=5;command.value=buffer[0]; }
    else if(handle==eq_handle && bytes==WMP_EQ_BANDS) { command.opcode=4;memcpy(command.eq,buffer,WMP_EQ_BANDS); }
    else if(handle==fx_handle && bytes==WMP_FX_BYTES) { command.opcode=7;memcpy(command.fx,buffer,WMP_FX_BYTES); }
    else if(handle==control_handle && bytes<=20u) {
        command.opcode=buffer[0];
        if(command.opcode==0x10u)command.value=0; /* Existing bounded BLE load command. */
        else if(command.opcode==0x11u && bytes==3u)command.value=little_endian_read_16(buffer,1); /* STATUS notify period, ms. */
        else if(command.opcode==1u && bytes==2u)command.value=buffer[1];
        else if(command.opcode==2u && bytes==3u)command.value=little_endian_read_16(buffer,1);
        else if(command.opcode==3u && bytes==2u)command.value=buffer[1];                  /* speaker channel */
        else if(command.opcode==5u && bytes==2u)command.value=buffer[1];                  /* broadcast on/off */
        else if(command.opcode==6u && bytes==2u)command.value=buffer[1];                  /* broadcast codec: 0 SBC, 1 +AAC-LC */
        else if(command.opcode==4u && bytes==1u+WMP_EQ_BANDS)memcpy(command.eq,buffer+1,WMP_EQ_BANDS); /* EQ dB */
        else if(command.opcode==7u && bytes==1u+WMP_FX_BYTES)memcpy(command.fx,buffer+1,WMP_FX_BYTES); /* karaoke effects */
        else { ++rejected;return ATT_ERROR_VALUE_NOT_ALLOWED; }
    } else { ++rejected;return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH; }
    if((command.opcode==1u && command.value>100u) ||
       (command.opcode==2u && (command.value<120u || command.value>300u || radio_stats.mode!=2u)) ||
       (command.opcode==0x11u && (command.value<10u || command.value>1000u)) ||
       (command.opcode==3u && command.value>WMP_SPEAKER_RIGHT) || ((command.opcode==5u || command.opcode==6u) && command.value>1u)) { ++rejected;return ATT_ERROR_VALUE_NOT_ALLOWED; }
    if(command.opcode==4u)for(unsigned i=0;i<WMP_EQ_BANDS;i++)if(command.eq[i]<-12 || command.eq[i]>12) { ++rejected;return ATT_ERROR_VALUE_NOT_ALLOWED; }
    if(count==4u) { ++rejected;return ATT_ERROR_INSUFFICIENT_RESOURCES; }
    pending[(head+count)%4u]=command;++count;++accepted;++bt_stats.ble_writes;
    wmp_coex_trace_record(WMP_RT_BLE,command.opcode,bytes,accepted);
    uint32_t elapsed=(uint32_t)(esp_timer_get_time()-begin);if(elapsed>bt_stats.ble_max_us)bt_stats.ble_max_us=elapsed;
    return 0;
}
static void event(uint8_t type,uint16_t channel,uint8_t *packet,uint16_t size) {
    (void)channel;if(type!=HCI_EVENT_PACKET || size<2u || packet[0]!=ATT_EVENT_CAN_SEND_NOW)return;
    send_pending=0;if(connection==HCI_CON_HANDLE_INVALID)return;
    if(changed_pending && changed_cccd==2u) {
        uint8_t range[4]={1,0,255,255};if(!att_server_indicate(connection,changed_handle,range,4)) { changed_pending=0;changed_announced=1; }
    } else if(status_cccd==1u) {
        uint8_t value[20];status_value(value);if(!att_server_notify(connection,status_handle,value,20)) { ++notifications;notified_at=esp_timer_get_time(); }
    }
}
void wmp_gatt_connection(uint16_t handle) {
    gatt_activity_us=esp_timer_get_time(); /* a new connection is the app opening: start responsive */
    connection=handle;status_cccd=changed_cccd=0;send_pending=changed_pending=changed_announced=0;
    identity_state=0;connected_at=handle==HCI_CON_HANDLE_INVALID?0:gatt_activity_us;
}
void wmp_gatt_tick(void) {
    if(connection!=HCI_CON_HANDLE_INVALID && identity_state!=2u && connected_at && esp_timer_get_time()-connected_at>WMP_IDENTITY_DEADLINE_US) {
        ++identity_dropped;connected_at=0;wmp_telemetry_printf("WMP_IDENTITY result=dropped%c",10);gap_disconnect(connection);
    }
    if(count) {
        setting command=pending[head];head=(head+1u)%4u;--count;
        int ok;
        switch(command.opcode) {
        case 0x10u: ok=1;break;
        case 0x11u: ok=wmp_gatt_notify_period(command.value);break;
        case 1u: ok=wmp_audio_set_volume(command.value);break;
        case 2u: ok=wmp_radio_latency(command.value);break;
        case 3u: ok=wmp_audio_set_speaker_channel(command.value);break;
        case 4u: ok=wmp_audio_set_eq(command.eq);break;
        case 5u: ok=wmp_bluetooth_broadcast_request(command.value);break;
        case 6u: ok=wmp_bluetooth_broadcast_codec(command.value);break;
        case 7u: ok=wmp_fx_set(command.fx);break;
        default: ok=0;break;
        }
        if(ok)++applied;else ++rejected;
    }
    if(connection!=HCI_CON_HANDLE_INVALID && !send_pending && ((changed_pending && changed_cccd==2u) || (status_cccd==1u && esp_timer_get_time()-notified_at>=(int64_t)notify_period_us))) {
        send_pending=1;att_server_request_can_send_now_event(connection);
    }
}
void wmp_gatt_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_GATT query=%08lx services=2 queued=%lu applied=%lu rejected=%lu notifications=%lu pending=%u subscribed=%u notify_period_us=%lu identity=%u proven=%lu failed=%lu dropped=%lu key=%u\n",query,
        (unsigned long)accepted,(unsigned long)applied,(unsigned long)rejected,(unsigned long)notifications,count,(unsigned)(status_cccd==1u),(unsigned long)notify_period_us,
        (unsigned)identity_state,(unsigned long)identity_proven,(unsigned long)identity_failed,(unsigned long)identity_dropped,(unsigned)WMP_IDENTITY_KEY_ID);
}
static void text_characteristic(uint16_t uuid,const char *value) {
    att_db_util_add_characteristic_uuid16(uuid,ATT_PROPERTY_READ,ATT_SECURITY_NONE,ATT_SECURITY_NONE,(uint8_t *)value,(uint16_t)strlen(value));
}
void wmp_gatt_init(const char *name,const char *build) {
    static const uint8_t service[16]={0xae,0xfb,0,1,0x2c,0x51,0x4b,0x0d,0xb1,0,0xec,0x5b,0,0,0,1};
    uint8_t uuid[16];memcpy(uuid,service,16);att_db_util_init();
    /* Retain the original control service and value handle3 for existing clients. */
    att_db_util_add_service_uuid128(service);uuid[3]=2;
    control_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_WRITE_WITHOUT_RESPONSE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    uuid[3]=3;status_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_NOTIFY|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    uuid[3]=4;volume_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    uuid[3]=5;latency_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    /* Appended after the existing characteristics so their handles do not move. */
    uuid[3]=6;speaker_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    uuid[3]=7;eq_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    uuid[3]=8;broadcast_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    uuid[3]=9;identity_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    /* Firmware revision lives here, not in a Device Information service, and there is no Generic
     * Attribute service: Android rediscovers this database on every connection while holding the link
     * at a 7.5 ms interval, and on a party master each discovery round trip costs ~60 ms of airtime
     * taken from the phone's A2DP stream. Control service + Generic Access is ~10 round trips. */
    uuid[3]=10;firmware_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ,ATT_SECURITY_NONE,ATT_SECURITY_NONE,(uint8_t *)build,(uint16_t)strlen(build));
    uuid[3]=11;fx_handle=att_db_util_add_characteristic_uuid128(uuid,ATT_PROPERTY_READ|ATT_PROPERTY_WRITE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    changed_handle=0xfff0u; /* no Service Changed characteristic: matches no real attribute */
    att_db_util_add_service_uuid16(ORG_BLUETOOTH_SERVICE_GENERIC_ACCESS);
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_GAP_DEVICE_NAME,name);
    uint8_t appearance[2]={0,0};att_db_util_add_characteristic_uuid16(ORG_BLUETOOTH_CHARACTERISTIC_GAP_APPEARANCE,ATT_PROPERTY_READ,ATT_SECURITY_NONE,ATT_SECURITY_NONE,appearance,2);
    att_server_init(att_db_util_get_address(),read_value,write_value);att_server_register_packet_handler(event);
}
