#ifndef WMP_SBC_H
#define WMP_SBC_H
#include <stddef.h>
#include <stdint.h>
typedef struct { uint32_t rate;uint16_t bytes,samples;uint8_t channels; } wmp_sbc_info;
/* Length/header validation only. The decoder still validates SBC CRC. */
int wmp_sbc_info_read(const uint8_t *frame,size_t available,wmp_sbc_info *out);
typedef struct { uint32_t timestamp;uint16_t sequence;uint8_t frames;uint32_t samples; } wmp_rtp_sbc;
typedef struct { uint64_t next_sample;uint32_t timestamp,packet_samples,timestamp_mismatches,last_delta,last_expected;uint16_t sequence;uint8_t valid; } wmp_sbc_stream;
void wmp_sbc_stream_reset(wmp_sbc_stream *state);
/* Contiguous RTP packets carry contiguous SBC frames even when a peer's RTP
 * timestamp is fixed, rounded or uses a different clock. Count actual samples.
 *0=duplicate,-1=invalid/overflow,1=accepted. Distance still exposes real loss. */
int wmp_sbc_stream_accept(wmp_sbc_stream *state,const wmp_rtp_sbc *packet,uint64_t *first_sample,uint16_t *distance);
/* Complete RTP packets only. Fragmented SBC payloads return-1, malformed0.
 * Packet/header bytes are never passed into the SBC decoder as audio. */
int wmp_rtp_sbc_read(const uint8_t *packet,size_t bytes,wmp_rtp_sbc *out,const uint8_t **frames,size_t *frame_bytes);
#endif
