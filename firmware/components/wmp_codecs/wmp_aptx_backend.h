#ifndef WMP_APTX_BACKEND_H
#define WMP_APTX_BACKEND_H
#include <stddef.h>
#include <stdint.h>
struct aptx_context;
/* Plain (fast) decode of whole codewords into signed 24-bit values in int32. Returns the
 * input bytes consumed; fewer than offered with room left means a parity failure. */
size_t wmp_aptx_decode24(struct aptx_context *ctx,const uint8_t *in,size_t bytes,int32_t *out,size_t capacity_samples,size_t *samples);
/* Recovery decode of one codeword (see wmp_aptx_backend.c). */
size_t wmp_aptx_recover24(struct aptx_context *ctx,const uint8_t *in,size_t bytes,int32_t *out,size_t capacity_samples,size_t *samples,uint8_t *run,int *ready);
/* Same result as wmp_aptx_decode24, with the right channel decoded by a helper task on
 * another core (started with wmp_aptx_parallel_start; without it both run here). */
size_t wmp_aptx_decode24_parallel(struct aptx_context *ctx,const uint8_t *in,size_t bytes,int32_t *out,size_t capacity_samples,size_t *samples);
int wmp_aptx_parallel_start(unsigned core,unsigned priority);
void wmp_aptx_parallel_stop(void);
/* CODECBENCH diagnostic: cycles [full group, QMF part, prediction part], output bytes. */
int wmp_aptx_bench(int hd,uint32_t result[4]);
#endif
