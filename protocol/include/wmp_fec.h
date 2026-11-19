#ifndef WMP_FEC_H
#define WMP_FEC_H
#include "wmp_wire.h"
#define WMP_FEC_PREFIX 24u
/* Negotiated SBC bitpool<=53 needs at most904 data bytes +72 header bytes. */
#define WMP_FEC_SHARD_MAX 976u
/* Two or four canonical wire packets, two independent GF(256) parity rows.
 * A repair preserves the entire original header, sample timeline and SBC bytes.
 * No allocation, ACKs, source codec change or decoder state inside this module. */
typedef struct {
    uint64_t group,generation,base;
    uint8_t p[WMP_FEC_SHARD_MAX],q[WMP_FEC_SHARD_MAX];
    uint32_t data_crc[4],parity_crc[2];
    uint16_t length[4];uint8_t mask,parity_mask,shards;
} wmp_fec;
void wmp_fec_bind(wmp_fec *state,uint64_t group,uint64_t generation,uint64_t base,unsigned shards);
int wmp_fec_data(wmp_fec *state,unsigned index,const uint8_t *canonical_wire,unsigned bytes);
int wmp_fec_emit(const wmp_fec *state,unsigned row,uint8_t *out,unsigned capacity);
int wmp_fec_receive(wmp_fec *state,const uint8_t *payload,unsigned bytes);
/* Produces at most one repair without mutating state. Caller MUST validate the
 * recovered wire CRC/stream/sequence, then admit it with wmp_fec_data, before
 * asking for the second repair. 0=not enough information; -1=invalid arguments. */
int wmp_fec_recover(const wmp_fec *state,uint8_t *wire,unsigned capacity,unsigned *index);
#endif
