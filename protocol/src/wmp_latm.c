#include "wmp_latm.h"
typedef struct { const uint8_t *data; uint32_t bits,pos; int bad; } reader;
static uint32_t bits(reader *r,unsigned n) {
    if(n>32u || n>r->bits-r->pos) { r->bad=1;return 0; }
    uint32_t v=0;
    for(unsigned i=0;i<n;i++) { v=(v<<1)|((r->data[r->pos>>3]>>(7u-(r->pos&7u)))&1u);++r->pos; }
    return v;
}
/* LatmGetValue(): 2-bit byte count minus one, then that many bytes. */
static uint32_t latm_value(reader *r) { unsigned n=bits(r,2)+1u;return bits(r,8u*n); }
static uint32_t index_rate(unsigned index) {
    static const uint32_t rates[13]={96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350};
    return index<13u?rates[index]:0;
}
int wmp_latm_parse(wmp_latm_state *state,uint32_t rate,uint8_t channels,const uint8_t *data,uint16_t size,
                   wmp_latm_unit *units,uint8_t capacity,uint8_t *count) {
    if(!state || !data || !size || !units || !capacity || !count || (channels!=1u && channels!=2u))return 0;
    reader r={data,(uint32_t)size*8u,0,0};
    wmp_latm_state next=*state;
    if(!bits(&r,1)) {                                   /* useSameStreamMux == 0: StreamMuxConfig follows */
        unsigned version=bits(&r,1);
        if(version) { if(bits(&r,1))return r.bad?0:-1;  /* audioMuxVersionA must be 0 */
                      (void)latm_value(&r); }           /* taraBufferFullness */
        unsigned same_framing=bits(&r,1),subframes=bits(&r,6)+1u,programs=bits(&r,4),layers=bits(&r,3);
        if(r.bad)return 0;
        if(!same_framing || programs || layers)return -1;
        uint32_t end_of_config=r.bits;
        if(version) { uint32_t asc=latm_value(&r);if(r.bad || asc>r.bits-r.pos)return 0;r.bits=r.pos+asc; }
        unsigned object=bits(&r,5),index=bits(&r,4);
        uint32_t asc_rate=index==15u?bits(&r,24):index_rate(index);
        unsigned asc_channels=bits(&r,4),frame_length=bits(&r,1),core=bits(&r,1),extension=bits(&r,1);
        if(r.bad)return 0;
        if(object!=2u || asc_rate!=rate || asc_channels!=channels || frame_length || core || extension)return -1;
        if(version) { uint32_t pad=r.bits-r.pos;if(pad>7u || bits(&r,(unsigned)pad))return -1;r.bits=end_of_config; }
        unsigned frame_length_type=bits(&r,3);(void)bits(&r,8);     /* latmBufferFullness */
        unsigned other=bits(&r,1),crc=bits(&r,1);
        if(r.bad)return 0;
        if(frame_length_type || other || crc)return -1;
        next.configured=1;next.subframes=(uint8_t)subframes;
    } else if(!next.configured)return 0;
    if(!next.subframes)return 0;
    if(next.subframes>capacity)return -2;
    for(unsigned i=0;i<next.subframes;i++) {         /* PayloadLengthInfo then PayloadMux, per subframe */
        uint32_t length=0,part;
        do { part=bits(&r,8);length+=part;if(r.bad || length>768u*channels)return 0; } while(part==255u);
        if(!length || length*8u>r.bits-r.pos)return 0;
        units[i].bit_offset=r.pos;units[i].bytes=(uint16_t)length;r.pos+=length*8u;
    }
    uint32_t tail=r.bits-r.pos;                        /* only byte-alignment padding may remain */
    if(tail>7u || bits(&r,(unsigned)tail) || r.bad)return 0;
    *count=next.subframes;*state=next;return 1;
}
int wmp_latm_copy(const uint8_t *data,uint16_t size,wmp_latm_unit unit,uint8_t *out,uint16_t capacity) {
    if(!data || !out || !unit.bytes || unit.bytes>capacity || unit.bit_offset>(uint32_t)size*8u ||
       (uint32_t)unit.bytes*8u>(uint32_t)size*8u-unit.bit_offset)return 0;
    unsigned shift=unit.bit_offset&7u,at=unit.bit_offset>>3;
    for(unsigned i=0;i<unit.bytes;i++) {
        unsigned v=(unsigned)data[at+i]<<8;
        if(shift && at+i+1u<size)v|=data[at+i+1u];
        out[i]=(uint8_t)(v>>(8u-shift));
    }
    return 1;
}
