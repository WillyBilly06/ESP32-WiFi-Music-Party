/* Freestanding Windows floating-point marker, not part of ESP32 firmware. */
int _fltused;
#include "../firmware/app/main/node.h"
#include "wmp_sbc_queue.h"
#include "wmp_beacon_clock.h"
#include "wmp_pcm_blocks.h"
#include "wmp_peer_lease.h"
unsigned wmp_live_payload_capacity(void) { return WMP_LIVE_PAYLOAD; }
/* Host-only sizes for the hand-mirrored ctypes structs in test_protocol.py. */
unsigned wmp_host_sizeof_dedup(void) { return (unsigned)sizeof(wmp_dedup); }
unsigned wmp_host_sizeof_audio_group(void) { return (unsigned)sizeof(wmp_audio_group); }
unsigned wmp_host_sizeof_sbc_queue(void) { return (unsigned)sizeof(wmp_sbc_queue); }
unsigned wmp_host_sizeof_beacon_clock(void) { return (unsigned)sizeof(wmp_beacon_clock); }
unsigned wmp_host_sizeof_pcm_blocks(void) { return (unsigned)sizeof(wmp_pcm_blocks); }
unsigned wmp_host_sizeof_peer_lease(void) { return (unsigned)sizeof(wmp_peer_lease); }
