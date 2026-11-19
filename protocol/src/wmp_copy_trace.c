#include "wmp_copy_trace.h"
_Static_assert(sizeof(wmp_copy_record)==56u,"Routing uses existing trace-record padding, not more RAM");
void wmp_copy_trace_reset_gaps(wmp_copy_trace *s) {
    if(!s)return;
    s->gap_valid=0;
    for(unsigned i=0;i<4;i++)s->gap_first[i]=s->gap_last[i]=s->gap_max[i]=s->gap_events[i]=0;
}
static void gap(wmp_copy_trace *s,unsigned channel,uint32_t at) {
    if(s->gap_valid&(1u<<channel)) {
        uint32_t delta=at-s->gap_last[channel];
        if(delta<0x80000000u && delta>s->gap_max[channel])s->gap_max[channel]=delta;
    } else { s->gap_valid|=(uint8_t)(1u<<channel);s->gap_first[channel]=at; }
    s->gap_last[channel]=at;++s->gap_events[channel];
}
static wmp_copy_record *seed(wmp_copy_trace *s,uint64_t seq) {
    wmp_copy_record *r=&s->records[seq&63u];
    if(r->valid && r->sequence==seq)return r;
    if(r->valid && r->sequence>seq)return 0;
    if(r->valid)++s->retired[r->seen&15u];
    r->sequence=seq;r->presentation_us=0;r->seen=r->accepted=r->completed=r->failed=r->repaired=r->injected=r->route=0;r->valid=1;
    for(unsigned i=0;i<4;i++)r->a[i]=r->b[i]=0;
    return r;
}
void wmp_copy_trace_event(wmp_copy_trace *s,uint64_t gen,uint64_t seq,uint64_t pts,unsigned stage,unsigned copy,uint32_t a,uint32_t b) {
    if(!s || !gen || stage>WMP_COPY_INJECT || (copy>=4u && stage!=WMP_COPY_REPAIR))return;
    unsigned kind=stage<=WMP_COPY_COMPLETE || stage==WMP_COPY_TX_FAIL || stage==WMP_COPY_INJECT?1u:2u;
    if(!s->valid || s->generation!=gen || s->kind!=kind) {
        s->generation=gen;s->first=s->highest=seq;s->kind=(uint8_t)kind;s->valid=1;s->repeated=0;
        wmp_copy_trace_reset_gaps(s);
        for(unsigned i=0;i<64;i++)s->records[i].valid=0;
        for(unsigned i=0;i<16;i++)s->retired[i]=0;
        for(unsigned row=0;row<3;row++)for(unsigned i=0;i<4;i++)s->counts[row][i]=0;
    }
    if(stage==WMP_COPY_SUBMIT || stage==WMP_COPY_RX)gap(s,0,a);
    else if(stage==WMP_COPY_ACCEPT) { gap(s,1,a);if(copy==0u)gap(s,3,a); }
    else if(stage==WMP_COPY_COMPLETE)gap(s,2,b);
    if(seq>s->highest) {
        uint64_t begin=s->highest+1u;
        if(seq-begin>63u) { s->retired[0]+=(uint32_t)(seq-begin-63u);begin=seq-63u; }
        for(uint64_t i=begin;i<seq;i++)seed(s,i);
        s->highest=seq;
    }
    wmp_copy_record *r=seed(s,seq);if(!r)return;
    if(pts)r->presentation_us=pts;
    unsigned bit=stage==WMP_COPY_REPAIR?0u:1u<<copy;
    if(stage==WMP_COPY_SUBMIT || stage==WMP_COPY_RX) {
        ++s->counts[0][copy];if(r->seen&bit)++s->repeated;
        else { r->a[copy]=a;if(stage==WMP_COPY_RX)r->b[copy]=b; }
        r->seen|=(uint8_t)bit;
    } else if(stage==WMP_COPY_ACCEPT || stage==WMP_COPY_VALID) { ++s->counts[1][copy];r->accepted|=(uint8_t)bit; }
    else if(stage==WMP_COPY_COMPLETE) { ++s->counts[2][copy];r->completed|=(uint8_t)bit;r->b[copy]=b; }
    else if(stage==WMP_COPY_TX_FAIL)r->failed|=(uint8_t)bit;
    else if(stage==WMP_COPY_INJECT)r->injected|=(uint8_t)bit;
    else r->repaired=1;
}
int wmp_copy_trace_route(wmp_copy_trace *s,uint64_t gen,uint64_t seq,unsigned bits) {
    if(!s || !s->valid || s->kind!=2u || s->generation!=gen || !bits || bits>255u)return 0;
    wmp_copy_record *r=&s->records[seq&63u];
    if(!r->valid || r->sequence!=seq)return 0;
    r->route|=(uint8_t)bits;return 1;
}
