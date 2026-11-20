#include "telemetry.h"
#include "coex_trace.h"
#include "btstack_ring_buffer.h"
#include <stdio.h>
#include "esp_bt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
typedef struct { uint32_t at;uint16_t code;uint8_t direction,status,a,b; } control_record;
static control_record control[24];static unsigned control_next,control_count;
static portMUX_TYPE control_lock=portMUX_INITIALIZER_UNLOCKED;
static void record(unsigned direction,unsigned code,unsigned status,unsigned a,unsigned b) {
    control_record r={.at=(uint32_t)esp_timer_get_time(),.code=(uint16_t)code,.direction=(uint8_t)direction,.status=(uint8_t)status,.a=(uint8_t)a,.b=(uint8_t)b};
    portENTER_CRITICAL(&control_lock);control[control_next]=r;control_next=(control_next+1u)%24u;if(control_count<24u)++control_count;portEXIT_CRITICAL(&control_lock);
}
void wmp_vhci_send_packet(uint8_t *packet,uint16_t bytes) {
    /* Host flow-control credits (0x0c35, one per received ACL packet) are not traced: they would
     * fill the small ring a party master can afford. */
    if(bytes && !(packet[0]==1u && bytes>=3u && packet[1]==0x35u && packet[2]==0x0cu))wmp_coex_trace_record(WMP_RT_HCI_OUT,packet[0],bytes,bytes>=3u?((uint32_t)packet[1]|((uint32_t)packet[2]<<8)):0u);
    if(bytes>=4u && packet[0]==1u) {
        unsigned opcode=packet[1]|((unsigned)packet[2]<<8),a=0,b=0;
        if(opcode==0x042bu && bytes>=13u) { a=packet[10];b=packet[12]; }
        record(1,opcode,255,a,b); /* Never record link keys or other payloads. */
    }
    esp_vhci_host_send_packet(packet,bytes);
}
void wmp_vhci_control_trace(unsigned long query) {
    control_record copy[24];unsigned count;
    portENTER_CRITICAL(&control_lock);count=control_count;for(unsigned i=0;i<count;i++)copy[i]=control[(control_next+24u-count+i)%24u];portEXIT_CRITICAL(&control_lock);
    for(unsigned i=0;i<count;i++)wmp_telemetry_printf("WMP_HCI_CONTROL query=%08lx at_us32=%lu direction=%u code=%u status=%u a=%u b=%u\n",query,(unsigned long)copy[i].at,(unsigned)copy[i].direction,(unsigned)copy[i].code,(unsigned)copy[i].status,(unsigned)copy[i].a,(unsigned)copy[i].b);
    wmp_telemetry_printf("WMP_HCI_CONTROL_END query=%08lx count=%u\n",query,count);
}
static volatile uint32_t packets,acl,events,dropped,dropped_bytes,min_space=UINT32_MAX,max_packet;
uint32_t wmp_vhci_payload_space(btstack_ring_buffer_t *ring) {
    uint32_t free=btstack_ring_buffer_bytes_free(ring);return free>=2u?free-2u:0;
}
uint32_t wmp_vhci_observe_space(btstack_ring_buffer_t *ring,uint16_t bytes,const uint8_t *packet) {
    uint8_t type=packet[0];
    wmp_coex_trace_record(WMP_RT_HCI_IN,type,bytes,bytes>=3u?((uint32_t)packet[1]|((uint32_t)packet[2]<<8)):0u);
    uint32_t space=wmp_vhci_payload_space(ring);++packets;
    if(type==2u)++acl;
    else if(type==4u) {
        ++events;
        if(bytes>=7u && packet[1]==0x0eu)record(2,packet[4]|((unsigned)packet[5]<<8),packet[6],0x0e,0);
        else if(bytes>=7u && packet[1]==0x0fu)record(2,packet[5]|((unsigned)packet[6]<<8),packet[3],0x0f,0);
        else if(bytes>=3u && packet[1]!=0x13u) {
            unsigned status=255,a=0,b=0;
            if(bytes>=4u && (packet[1]==3u || packet[1]==6u || packet[1]==0x36u || packet[1]==0x0bu || packet[1]==0x23u))status=packet[3];
            if(bytes>=7u && packet[1]==5u)status=packet[6];
            if(bytes>=12u && packet[1]==0x32u) { a=packet[9];b=packet[11]; }
            if(bytes>=8u && packet[1]==0x23u) { a=packet[6];b=packet[7]; }
            record(3,packet[1],status,a,b);
        }
    }
    if(space<min_space)min_space=space;
    if(bytes>max_packet)max_packet=bytes;
    if(space<bytes) { ++dropped;dropped_bytes+=bytes; }
    return space;
}
void wmp_vhci_stats(unsigned long query) {
    wmp_telemetry_printf("WMP_VHCI query=%08lx packets=%lu acl=%lu events=%lu dropped=%lu dropped_bytes=%lu min_space=%lu max_packet=%lu\n",query,
        (unsigned long)packets,(unsigned long)acl,(unsigned long)events,(unsigned long)dropped,(unsigned long)dropped_bytes,(unsigned long)min_space,(unsigned long)max_packet);
}
