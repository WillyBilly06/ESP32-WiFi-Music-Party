#ifndef WMP_VHCI_PROBE_H
#define WMP_VHCI_PROBE_H
#include "btstack_ring_buffer.h"
#include <stdint.h>
#include "esp_bt.h"
uint32_t wmp_vhci_observe_space(btstack_ring_buffer_t *ring,uint16_t bytes,const uint8_t *packet);
void wmp_vhci_stats(unsigned long query);
void wmp_vhci_send_packet(uint8_t *packet,uint16_t bytes);
void wmp_vhci_control_trace(unsigned long query);
/* Only force-included for the pinned ESP32 transport source. Its one free-
 * space query runs in host_recv_pkt_cb under the existing ring mutex. */
#ifdef WMP_VHCI_TRANSPORT_PROBE
#define esp_vhci_host_send_packet wmp_vhci_send_packet
#define btstack_ring_buffer_bytes_free(ring) wmp_vhci_observe_space((ring),len,data)
#endif
#endif
