#ifndef WMP_PCM_BLOCKS_H
#define WMP_PCM_BLOCKS_H
#include <stdint.h>
#define WMP_PCM_DMA_MAX_FRAMES 256u
typedef struct { int16_t tail[WMP_PCM_DMA_MAX_FRAMES*2u];unsigned count,frames; } wmp_pcm_blocks;
typedef int (*wmp_pcm_writer)(void *context,const int16_t *stereo,unsigned frames);
int wmp_pcm_blocks_init(wmp_pcm_blocks *s,unsigned frames);
/* Write only complete DMA descriptors. Preserve the final partial descriptor
 * for the next codec group; no inserted silence, sample skip or per-call pad. */
int wmp_pcm_blocks_push(wmp_pcm_blocks *s,const int16_t *stereo,unsigned frames,wmp_pcm_writer writer,void *context);
uint32_t wmp_pcm_blocks_guard_us(const wmp_pcm_blocks *s,uint32_t rate,uint32_t refill_guard_us);
#endif
