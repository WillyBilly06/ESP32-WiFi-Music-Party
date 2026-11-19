#ifndef WMP_CODECS_H
#define WMP_CODECS_H
#include <stdint.h>
/* Non-SBC decoders (AAC-LC via Helix, aptX/aptX HD via libfreeaptx), pinned from
 * WillyBilly06/esp32-hifi-a2dp-sink @ 7dcb71a0c3ffea973322fcec1f8f46abecd6b079.
 * Exactly one decoder exists at a time and only while its codec plays. The only heap it
 * holds is the library's own state (AAC about 20.6 KB, aptX about 4.4 KB); input is read
 * in place and output goes straight into the caller's PCM buffer. close() returns it all. */
int wmp_codecs_open(uint8_t codec,uint32_t rate,uint8_t channels);
void wmp_codecs_close(void);
/* Decodes one unit (AAC access unit or aptX codeword chunk) into `out` (`capacity` bytes) at
 * the codec's own precision, interleaved with `channels` samples per frame:
 *   *bits==16: int16 samples (AAC; Helix's native output)
 *   *bits==24: int32 samples holding signed 24-bit values (aptX, aptX HD; nothing truncated)
 * Returns 1 with frames/channels/bits set, 0 on a decode error (decoder state is reset). */
int wmp_codecs_decode(const uint8_t *unit,uint16_t bytes,void *out,uint32_t capacity,uint16_t *frames,uint8_t *channels,uint8_t *bits);
uint8_t wmp_codecs_active(void);          /* codec id currently open, 0 if none */
uint32_t wmp_codecs_bytes(void);          /* heap held by the open decoder (measured) */
uint32_t wmp_codecs_errors(void);
/* Open diagnostics: attempts, refused arguments, failed allocations and the largest free
 * internal block seen at the last failure. */
typedef struct { uint32_t opens,bad_args,no_memory,largest_at_fail; uint8_t last_codec,last_channels; uint32_t last_rate; } wmp_codecs_open_stats;
void wmp_codecs_stats(wmp_codecs_open_stats *out);
/* CODECBENCH diagnostic (aptX, hd 0/1): cycles for one group and its halves. 1 ok. */
int wmp_codecs_bench(int hd,uint32_t result[4]);
/* Reset-surviving breadcrumbs (RTC no-init RAM): the last 16 marks before a watchdog reset
 * that prints no panic. wmp_crumb is a few stores, safe from any task. */
void wmp_crumb(uint32_t code,uint32_t value);
/* Copies the marks left by the previous boot (oldest first); returns their count (0..16). */
unsigned wmp_crumbs_previous(uint32_t out[16][2]);
#endif
