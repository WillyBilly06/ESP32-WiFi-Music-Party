#ifndef WMP_CODEC_H
#define WMP_CODEC_H
#include <stdint.h>
/* Codecs carried by the broadcast. The phone's original codec frames are rebroadcast
 * unchanged; every speaker decodes the same codec. SBC keeps codec id 0 so SBC frames
 * stay byte-identical on the wire. */
enum { WMP_CODEC_SBC=0, WMP_CODEC_AAC=1, WMP_CODEC_APTX=2, WMP_CODEC_APTX_HD=3, WMP_CODEC_COUNT=4 };

/* Per-codec buffer configuration. Each codec owns its own grouping, buffer and queue
 * sizes; nothing is shared or simply resized between codecs. Only the active codec's
 * working set is allocated (lazily, when its stream is configured); everything else
 * stays in flash as these const tables. */
typedef struct {
    uint8_t codec;
    const char *name;
    uint8_t group_units;      /* framed codecs: units per group (SBC frames, AAC access units); 0 = stream codec */
    uint8_t stream_quantum;   /* stream codecs: bytes per codeword of 4 stereo samples (aptX 4, aptX HD 6) */
    uint16_t group_samples;   /* samples per broadcast group (per channel) */
    uint16_t max_unit_bytes;  /* largest single unit accepted from the phone */
    uint16_t max_group_bytes; /* group payload bound, length prefixes included */
    uint16_t pcm_block_frames;/* decoder output block (stereo sample frames) */
    uint8_t queue_groups;     /* receive queue depth covering the 300 ms timeline plus margin */
    uint32_t decoder_bytes;   /* decoder working set allocated only while this codec plays */
} wmp_codec_profile;

const wmp_codec_profile *wmp_codec_profile_get(unsigned codec);
/* Receive payload arena for this codec: queue_groups typical groups, 4-byte aligned. */
uint32_t wmp_codec_payload_bytes(const wmp_codec_profile *profile);
/* Samples carried by a stream-codec chunk of `bytes` (aptX: 4 samples per quantum). 0 if not whole codewords. */
uint32_t wmp_codec_stream_samples(const wmp_codec_profile *profile,uint32_t bytes);
/* RTP (RFC 3550) header for A2DP media: returns 1 with the payload view, 0 malformed. */
typedef struct { uint16_t sequence; uint32_t timestamp; uint8_t marker,payload_type; } wmp_rtp_header;
int wmp_rtp_read(const uint8_t *packet,uint32_t bytes,wmp_rtp_header *out,const uint8_t **payload,uint32_t *payload_bytes);
#endif
