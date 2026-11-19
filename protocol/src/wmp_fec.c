#include "wmp_fec.h"
static uint8_t xtime(uint8_t a) { return (uint8_t)((a<<1)^((a&128u)?0x1du:0u)); }
static uint8_t coefficient(uint8_t a,unsigned index) { while(index--)a=xtime(a);return a; }
static uint8_t multiply(uint8_t a,uint8_t b) {
    uint8_t out=0;while(b) { if(b&1u)out^=a;a=xtime(a);b>>=1; }return out;
}
static uint8_t inverse(uint8_t a) {
    uint8_t out=1;unsigned power=254;
    while(power) { if(power&1u)out=multiply(out,a);a=multiply(a,a);power>>=1; }return out;
}
void wmp_fec_bind(wmp_fec *s,uint64_t group,uint64_t generation,uint64_t base,unsigned shards) {
    if(!s)return;
    s->shards=(shards==2u || shards==4u)?(uint8_t)shards:0;
    s->group=group;s->generation=generation;s->base=base&~(uint64_t)(shards?shards-1u:0u);s->mask=s->parity_mask=0;
    for(unsigned i=0;i<WMP_FEC_SHARD_MAX;i++)s->p[i]=s->q[i]=0;
    for(unsigned i=0;i<4;i++) { s->length[i]=0;s->data_crc[i]=0; }
    s->parity_crc[0]=s->parity_crc[1]=0;
}
int wmp_fec_data(wmp_fec *s,unsigned index,const uint8_t *wire,unsigned n) {
    if(!s || !wire || !s->shards || index>=s->shards || n<WMP_HEADER_BYTES || n>WMP_FEC_SHARD_MAX)return -1;
    uint32_t crc=wmp_logical_crc(wire,n);unsigned bit=1u<<index;
    if(s->mask&bit)return s->length[index]==n && s->data_crc[index]==crc?0:-1;
    if(s->length[index] && s->length[index]!=n)return -1;
    for(unsigned i=0;i<n;i++) { s->p[i]^=wire[i];s->q[i]^=coefficient(wire[i],index); }
    s->length[index]=(uint16_t)n;s->data_crc[index]=crc;s->mask|=(uint8_t)bit;return 1;
}
int wmp_fec_emit(const wmp_fec *s,unsigned row,uint8_t *out,unsigned capacity) {
    if(!s || !out || !s->shards || row>=2u || s->mask!=((1u<<s->shards)-1u) || s->parity_mask)return -1;
    unsigned n=0;for(unsigned i=0;i<4;i++)if(s->length[i]>n)n=s->length[i];
    if(n>WMP_FEC_SHARD_MAX || capacity<n+WMP_FEC_PREFIX)return -1;
    out[0]='W';out[1]='F';out[2]='P';out[3]='1';out[4]=1;out[5]=s->shards;out[6]=(uint8_t)row;out[7]=0;
    for(unsigned i=0;i<8;i++)out[8+i]=(uint8_t)(s->base>>(8u*i));
    for(unsigned i=0;i<4;i++) { out[16+i*2]=(uint8_t)s->length[i];out[17+i*2]=(uint8_t)(s->length[i]>>8); }
    const uint8_t *data=row?s->q:s->p;for(unsigned i=0;i<n;i++)out[WMP_FEC_PREFIX+i]=data[i];
    return (int)(n+WMP_FEC_PREFIX);
}
int wmp_fec_receive(wmp_fec *s,const uint8_t *p,unsigned n) {
    if(!s || !p || n<WMP_FEC_PREFIX+WMP_HEADER_BYTES || n>WMP_MAX_PAYLOAD ||
       p[0]!='W' || p[1]!='F' || p[2]!='P' || p[3]!='1' || p[4]!=1 || !s->shards || p[5]!=s->shards || p[6]>1 || p[7])return -1;
    uint64_t base=0;for(unsigned i=0;i<8;i++)base|=(uint64_t)p[8+i]<<(8u*i);
    if(base!=s->base)return -1;
    unsigned max=0;uint16_t lengths[4];
    for(unsigned i=0;i<4;i++) {
        unsigned len=p[16+i*2]|((unsigned)p[17+i*2]<<8);
        if(i>=s->shards) { if(len)return -1;lengths[i]=0;continue; }
        if(len<WMP_HEADER_BYTES || len>WMP_FEC_SHARD_MAX || (s->length[i] && s->length[i]!=len))return -1;
        lengths[i]=(uint16_t)len;if(len>max)max=len;
    }
    if(max+WMP_FEC_PREFIX!=n)return -1;
    unsigned row=p[6],bit=1u<<row;uint32_t crc=wmp_crc32(p,n);
    if(s->parity_mask&bit)return s->parity_crc[row]==crc?0:-1;
    for(unsigned i=0;i<4;i++)s->length[i]=lengths[i];
    uint8_t *data=row?s->q:s->p;for(unsigned i=0;i<max;i++)data[i]^=p[WMP_FEC_PREFIX+i];
    s->parity_crc[row]=crc;s->parity_mask|=(uint8_t)bit;return 1;
}
int wmp_fec_recover(const wmp_fec *s,uint8_t *wire,unsigned capacity,unsigned *index) {
    if(!s || !wire || !index)return -1;
    unsigned missing[4],count=0;
    if(!s->shards)return -1;
    for(unsigned i=0;i<s->shards;i++)if(!(s->mask&(1u<<i)))missing[count++]=i;
    if(!count || count>2u || !s->parity_mask || (count==2u && s->parity_mask!=3u))return 0;
    unsigned a=missing[0],n=s->length[a];if(n<WMP_HEADER_BYTES || n>capacity)return -1;
    uint8_t div=count==2u?inverse((uint8_t)((1u<<a)^(1u<<missing[1]))):inverse((uint8_t)(1u<<a));
    for(unsigned i=0;i<n;i++) {
        if(count==1u)wire[i]=(s->parity_mask&1u)?s->p[i]:multiply(s->q[i],div);
        else wire[i]=multiply((uint8_t)(s->q[i]^coefficient(s->p[i],missing[1])),div);
    }
    *index=a;return (int)n;
}
