#ifndef WMP_LATM_H
#define WMP_LATM_H
#include <stdint.h>
/* A2DP AAC carries MPEG-4 LATM AudioMuxElement(muxConfigPresent=1) per RTP packet
 * (ISO/IEC 14496-3 1.7). This parser extracts the raw AAC access units, which are then
 * rebroadcast byte-exact. Supported: audioMuxVersion 0/1 with audioMuxVersionA 0, one
 * program and layer, AAC-LC (object type 2), frameLengthType 0, no other-data or CRC,
 * 1024-sample frames, rate and channels matching the negotiated configuration. */
#define WMP_LATM_MAX_UNITS 16u
typedef struct { uint8_t configured,subframes; } wmp_latm_state;
typedef struct { uint32_t bit_offset; uint16_t bytes; } wmp_latm_unit;
/* Returns 1 with units, 0 malformed, -1 unsupported stream, -2 more units than capacity.
 * State is updated only on success; `units` is scratch (no stack copy) and valid only on success. */
int wmp_latm_parse(wmp_latm_state *state,uint32_t rate,uint8_t channels,const uint8_t *data,uint16_t size,
                   wmp_latm_unit *units,uint8_t capacity,uint8_t *count);
/* Copies one unit (which may start at any bit) into byte-aligned output. 1 ok, 0 invalid. */
int wmp_latm_copy(const uint8_t *data,uint16_t size,wmp_latm_unit unit,uint8_t *out,uint16_t capacity);
#endif
