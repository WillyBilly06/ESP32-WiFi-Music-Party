#include "wmp_payload_pool.h"
static unsigned aligned(unsigned bytes) { return (bytes+3u)&~3u; }
void wmp_payload_pool_init(wmp_payload_pool *s) {
    if(!s)return;
    s->offset[0]=0;s->length[0]=WMP_PAYLOAD_BYTES;s->count=1;s->available=WMP_PAYLOAD_BYTES;
}
static void remove_extent(wmp_payload_pool *s,unsigned i) {
    for(unsigned j=i+1u;j<s->count;j++) { s->offset[j-1u]=s->offset[j];s->length[j-1u]=s->length[j]; }
    --s->count;
}
int wmp_payload_alloc(wmp_payload_pool *s,unsigned bytes) {
    if(!s || !bytes || bytes>WMP_PAYLOAD_BYTES)return -1;
    unsigned size=aligned(bytes);
    for(unsigned i=0;i<s->count;i++)if(s->length[i]>=size) {
        unsigned offset=s->offset[i];s->offset[i]=(uint16_t)(offset+size);s->length[i]=(uint16_t)(s->length[i]-size);
        s->available=(uint16_t)(s->available-size);if(!s->length[i])remove_extent(s,i);return (int)offset;
    }
    return -1;
}
int wmp_payload_free(wmp_payload_pool *s,unsigned offset,unsigned bytes) {
    if(!s || !bytes || bytes>WMP_PAYLOAD_BYTES || offset%4u || offset>=WMP_PAYLOAD_BYTES)return 0;
    unsigned size=aligned(bytes);if(size>WMP_PAYLOAD_BYTES-offset)return 0;
    unsigned i=0;while(i<s->count && s->offset[i]<offset)++i;
    if((i && s->offset[i-1u]+s->length[i-1u]>offset) || (i<s->count && offset+size>s->offset[i]))return 0;
    int left=i && s->offset[i-1u]+s->length[i-1u]==offset;
    int right=i<s->count && offset+size==s->offset[i];
    if(left) {
        s->length[i-1u]=(uint16_t)(s->length[i-1u]+size);
        if(right) { s->length[i-1u]=(uint16_t)(s->length[i-1u]+s->length[i]);remove_extent(s,i); }
    } else if(right) { s->offset[i]=(uint16_t)offset;s->length[i]=(uint16_t)(s->length[i]+size); }
    else {
        if(s->count==WMP_PAYLOAD_EXTENTS)return 0;
        for(unsigned j=s->count;j>i;j--) { s->offset[j]=s->offset[j-1u];s->length[j]=s->length[j-1u]; }
        s->offset[i]=(uint16_t)offset;s->length[i]=(uint16_t)size;++s->count;
    }
    s->available=(uint16_t)(s->available+size);return 1;
}
