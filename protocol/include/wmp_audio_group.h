#ifndef WMP_AUDIO_GROUP_H
#define WMP_AUDIO_GROUP_H
#include "wmp_wire.h"
#include "wmp_codec.h"
typedef struct {
    uint8_t payload[WMP_MAX_PAYLOAD];
    uint64_t first_sample,next_sample;
    uint32_t sample_count;
    uint16_t bytes,frames;
    uint8_t target;
    uint8_t codec;            /* WMP_CODEC_*; 0 = SBC */
    uint8_t channels;         /* non-SBC: channels negotiated with the phone when the group was built; SBC 0 */
} wmp_audio_group;
void wmp_group_init(wmp_audio_group *group,uint8_t target);
/* Codec-aware init: framed codecs use `target` units (SBC 3..8, AAC 1..8); stream codecs
 * (aptX) ignore it and fill one unit up to the profile's group_samples. */
void wmp_group_init_codec(wmp_audio_group *group,uint8_t codec,uint8_t target);
/* Length-prefixed, byte-exact SBC frames.0=accepted partial,1=ready,
 * -1=invalid,-2=full/too large,-3=nonconsecutive samples. No hidden flush. */
int wmp_group_push(wmp_audio_group *group,const uint8_t *sbc,uint16_t bytes,uint64_t first_sample,uint16_t samples);
/* Framed codec unit (SBC frame or AAC access unit), same return codes as wmp_group_push. */
int wmp_group_push_unit(wmp_audio_group *group,const uint8_t *unit,uint16_t bytes,uint64_t first_sample,uint16_t samples);
/* Stream codec (aptX/aptX HD): appends whole codewords from `data` into the group's single unit
 * until group_samples is reached. *consumed gets the bytes taken. Returns 1 when the group is
 * ready (caller flushes, then pushes the remainder), 0 when all input was taken, -1 invalid,
 * -3 nonconsecutive samples. */
int wmp_group_push_stream(wmp_audio_group *group,const uint8_t *data,uint16_t bytes,uint64_t first_sample,uint16_t *consumed);
/* Reader preserves frame boundaries and refuses incomplete/malformed groups. */
int wmp_group_frame(const uint8_t *payload,size_t bytes,uint16_t frames,uint16_t index,const uint8_t **frame,uint16_t *frame_bytes);
/* Codec-aware reader: SBC frames must start with the SBC syncword; other codecs only need
 * consistent length prefixes. */
int wmp_group_frame_codec(uint8_t codec,const uint8_t *payload,size_t bytes,uint16_t frames,uint16_t index,const uint8_t **frame,uint16_t *frame_bytes);
/* Planning only: this does not reserve RF or claim actual transmission. */
int wmp_copy_plan(uint64_t ready_us,uint64_t presentation_us,uint32_t spacing_us,uint8_t copies,uint64_t due_us[4]);
#endif
