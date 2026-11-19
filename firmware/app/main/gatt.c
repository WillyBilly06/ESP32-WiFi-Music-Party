#include "node.h"
#include "btstack.h"
#include "bluetooth_gatt.h"
#include "ble/att_db_util.h"
#include "esp_timer.h"
#include "coex_trace.h"
#include <string.h>
#include <stdio.h>

static hci_con_handle_t connection=HCI_CON_HANDLE_INVALID;
static uint16_t control_handle,status_handle,volume_handle,latency_handle,speaker_handle,eq_handle,broadcast_handle,changed_handle;
static uint16_t status_cccd,changed_cccd;
static uint8_t send_pending,changed_pending,changed_announced;
static int64_t notified_at;
/* STATUS notification period; shorter periods are a BLE radio-load test. */
static volatile uint32_t notify_period_us=1000000u;
int wmp_gatt_notify_period(unsigned ms) { if(ms<10u || ms>1000u)return 0;notify_period_us=ms*1000u;return 1; }
typedef struct { uint8_t opcode;uint16_t value;int8_t eq[WMP_EQ_BANDS]; } setting;
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
}
void wmp_gatt_tick(void) {
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
        default: ok=0;break;
        }
        if(ok)++applied;else ++rejected;
    }
    if(connection!=HCI_CON_HANDLE_INVALID && !send_pending && ((changed_pending && changed_cccd==2u) || (status_cccd==1u && esp_timer_get_time()-notified_at>=(int64_t)notify_period_us))) {
        send_pending=1;att_server_request_can_send_now_event(connection);
    }
}
void wmp_gatt_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_GATT query=%08lx services=4 queued=%lu applied=%lu rejected=%lu notifications=%lu pending=%u subscribed=%u notify_period_us=%lu\n",query,
        (unsigned long)accepted,(unsigned long)applied,(unsigned long)rejected,(unsigned long)notifications,count,(unsigned)(status_cccd==1u),(unsigned long)notify_period_us);
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
    att_db_util_add_service_uuid16(ORG_BLUETOOTH_SERVICE_GENERIC_ACCESS);
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_GAP_DEVICE_NAME,name);
    uint8_t appearance[2]={0,0};att_db_util_add_characteristic_uuid16(ORG_BLUETOOTH_CHARACTERISTIC_GAP_APPEARANCE,ATT_PROPERTY_READ,ATT_SECURITY_NONE,ATT_SECURITY_NONE,appearance,2);
    att_db_util_add_service_uuid16(ORG_BLUETOOTH_SERVICE_GENERIC_ATTRIBUTE);
    changed_handle=att_db_util_add_characteristic_uuid16(ORG_BLUETOOTH_CHARACTERISTIC_GATT_SERVICE_CHANGED,ATT_PROPERTY_INDICATE|ATT_PROPERTY_DYNAMIC,ATT_SECURITY_NONE,ATT_SECURITY_NONE,0,0);
    att_db_util_add_service_uuid16(ORG_BLUETOOTH_SERVICE_DEVICE_INFORMATION);
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_MANUFACTURER_NAME_STRING,"WMP development");
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_MODEL_NUMBER_STRING,"ESP32 Wi-Fi Music Party");
    char serial[13];snprintf(serial,sizeof(serial),"%02X%02X%02X%02X%02X%02X",wmp_base_mac[0],wmp_base_mac[1],wmp_base_mac[2],wmp_base_mac[3],wmp_base_mac[4],wmp_base_mac[5]);
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_SERIAL_NUMBER_STRING,serial);
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_FIRMWARE_REVISION_STRING,build);
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_HARDWARE_REVISION_STRING,"ESP32 / PCM5102A");
    text_characteristic(ORG_BLUETOOTH_CHARACTERISTIC_SOFTWARE_REVISION_STRING,"BTstack1.8.3 / IDF6.1");
    att_server_init(att_db_util_get_address(),read_value,write_value);att_server_register_packet_handler(event);
}
