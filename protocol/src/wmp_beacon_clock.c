#include "wmp_beacon_clock.h"
void wmp_beacon_clock_reset(wmp_beacon_clock *c) {
    if(!c)return;
    c->count=c->next=c->valid=c->jitter_us=c->fit_jitter_us=c->rejected=0;
    c->ref_local=c->ref_source=0;c->ratio=1.0;
}
int wmp_beacon_clock_push(wmp_beacon_clock *c,uint64_t local,uint64_t remote) {
    if(!c || !remote)return 0;
    if(!c->count) { c->ratio=1.0;c->valid=0; }
    if(c->count) {
        unsigned last=(c->next+127u)%128u;
        if(local<=c->local[last]) { ++c->rejected;return 0; }
        if(remote<=c->remote[last])wmp_beacon_clock_reset(c);
        else {
            int64_t step=(int64_t)(remote-c->remote[last])-(int64_t)(local-c->local[last]);
            /* Discontinuous radio clock epochs cannot be corrected by a20us
             * phase slew. Reacquire rather than retaining a seconds-wrong fit. */
            if(step>250000 || step< -250000)wmp_beacon_clock_reset(c);
        }
    }
    c->local[c->next]=local;c->remote[c->next]=remote;
    c->next=(c->next+1u)%128u;if(c->count<128u)++c->count;
    if(c->count<8u)return 0;
    int best[16];double value[16];unsigned oldest=0;
    for(unsigned b=0;b<16;b++) { best[b]=-1;value[b]=-1e30; }
    for(unsigned i=0;i<c->count;i++) {
        uint64_t age=local-c->local[i];unsigned b=(unsigned)(age/1000000u);if(b>=16u)continue;
        if(b>oldest)oldest=b;
        double delta=(double)((int64_t)c->remote[i]-(int64_t)remote)+c->ratio*(double)age;
        if(delta>value[b]) { value[b]=delta;best[b]=(int)i; }
    }
    double sx=0,sy=0,num=0,den=0;unsigned n=0;
    /* The oldest bucket is truncated by the ring boundary. Including it
     * biases the slope when its few observations all had long delivery delay. */
    for(unsigned b=0;b<oldest;b++)if(best[b]>=0) {
        unsigned i=(unsigned)best[b];sx+=(double)((int64_t)c->local[i]-(int64_t)local);
        sy+=(double)((int64_t)c->remote[i]-(int64_t)remote);++n;
    }
    double mx=n?sx/n:0,my=n?sy/n:0;
    for(unsigned b=0;b<oldest;b++)if(best[b]>=0) {
        unsigned i=(unsigned)best[b];double x=(double)((int64_t)c->local[i]-(int64_t)local)-mx;
        double y=(double)((int64_t)c->remote[i]-(int64_t)remote)-my;num+=x*y;den+=x*x;
    }
    if(n>=4u && den>0) {
        double ratio=num/den;
        if(ratio>=0.9995 && ratio<=1.0005)c->ratio+=0.05*(ratio-c->ratio);
        else ++c->rejected;
    }
    double estimate=-1e30;
    for(unsigned b=0;b<16;b++)if(best[b]>=0) {
        unsigned i=(unsigned)best[b];double at=(double)c->remote[i]+c->ratio*(double)(local-c->local[i]);
        if(at>estimate)estimate=at;
    }
    double worst=0,fit_worst=0;
    for(unsigned i=0;i<c->count;i++) {
        double error=estimate-((double)c->remote[i]+c->ratio*(double)(local-c->local[i]));
        if(error>worst)worst=error;
    }
    for(unsigned b=0;b<oldest;b++)if(best[b]>=0) {
        unsigned i=(unsigned)best[b];double error=estimate-((double)c->remote[i]+c->ratio*(double)(local-c->local[i]));
        if(error>fit_worst)fit_worst=error;
    }
    c->jitter_us=(uint32_t)worst;c->fit_jitter_us=(uint32_t)fit_worst;
    if(c->valid) {
        double predicted=(double)c->ref_source+c->ratio*(double)(local-c->ref_local);
        double phase=estimate-predicted;if(phase>20.0)phase=20.0;if(phase< -20.0)phase=-20.0;
        estimate=predicted+phase;
    }
    c->ref_source=(uint64_t)(estimate+0.5);c->ref_local=local;c->valid=1;return 1;
}
int wmp_beacon_clock_to_local(const wmp_beacon_clock *c,uint64_t remote,uint64_t *local) {
    if(!c || !c->valid || !local || c->ratio<0.9995 || c->ratio>1.0005)return 0;
    double at=(double)c->ref_local+(double)((int64_t)remote-(int64_t)c->ref_source)/c->ratio;
    if(at<0 || at>9e18)return 0;
    *local=(uint64_t)(at+0.5);return 1;
}
