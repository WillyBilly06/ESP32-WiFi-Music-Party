#include "wmp_time.h"
int wmp_preparation_begin_at(wmp_preparation *s,uint64_t now,int shared,uint32_t epoch,int running,uint32_t rendered_epoch,uint64_t qualified_ready) {
    if(!s)return 0;
    int warm=shared && (qualified_ready?now>=qualified_ready:((running && rendered_epoch==epoch) || (s->warm && s->clock_epoch==epoch)));
    if(!++s->version)++s->version;
    s->clock_epoch=epoch;s->warm=(uint8_t)warm;
    s->ready_us=warm?now:(shared && qualified_ready?qualified_ready:(now>UINT64_MAX-2000000u?UINT64_MAX:now+2000000u));return warm;
}
int wmp_preparation_begin(wmp_preparation *s,uint64_t now,int shared,uint32_t epoch,int running,uint32_t rendered_epoch) { return wmp_preparation_begin_at(s,now,shared,epoch,running,rendered_epoch,0); }
int wmp_preparation_allows(const wmp_preparation *s,uint64_t now,int valid) { return s && valid && now>=s->ready_us; }
int wmp_preparation_keeps(const wmp_preparation *s,uint32_t version) { return s && s->version==version; }
void wmp_preparation_forget(wmp_preparation *s) {
    if(!s)return;
    s->warm=0;s->clock_epoch=0;s->ready_us=0;if(!++s->version)++s->version;
}
uint32_t wmp_election_delay(const uint8_t address[6]) {
    uint32_t hash=2166136261u;
    if(address)for(unsigned i=0;i<6u;i++)hash=(hash^address[i])*16777619u;
    return 20000u+hash%40000u;
}
unsigned wmp_replay_copies(uint64_t remaining,uint32_t spacing,uint32_t guard) {
    if(!spacing || remaining<=guard)return 0;
    uint64_t n=1u+(remaining-guard)/spacing;return n>4u?4u:(unsigned)n;
}
unsigned wmp_replay_plan(uint64_t remaining,uint32_t minimum,uint32_t guard,uint32_t urgent,uint32_t *spacing) {
    if(!spacing)return 0;
    *spacing=0;
    if(!minimum)return 0;
    if(remaining<=guard && urgent<guard) {
        if(remaining<=urgent)return 0;
        *spacing=minimum;return 1;
    }
    if(remaining<=guard)return 0;
    uint64_t available=remaining-guard,gaps=available/minimum;
    unsigned copies=gaps>=2u?3u:(gaps?2u:1u);
    /* A source pins this repair until its last native completion. Spreading
     * to the far deadline can consume every receiver retry while still
     * pending. Use the requested spacing, then release for another train if
     * these copies were lost. The guard still bounds every submission. */
    uint64_t spread=minimum;
    *spacing=spread>UINT32_MAX?UINT32_MAX:(uint32_t)spread;
    return copies;
}
uint64_t wmp_replay_start(uint64_t now,uint64_t next,uint64_t remaining,uint32_t urgent) {
    if(next<=now || remaining<=urgent)return now;
    return next-now>=remaining-urgent?now:next;
}
int wmp_wake_due(uint64_t now,uint64_t event,uint32_t interval,uint32_t span,uint64_t *due) {
    if(!due || !event || !interval || !span || span>=interval || now<event)return 0;
    uint64_t age=now-event;if(age>=(uint64_t)interval*2u)return 0;
    uint32_t phase=(uint32_t)(age%interval);
    if(phase<span) { *due=now;return 1; }
    uint32_t wait=interval-phase;if(now>UINT64_MAX-wait)return 0;
    *due=now+wait;return 1;
}
uint64_t wmp_tx_after_call(uint64_t previous,uint64_t start,uint64_t end,uint32_t period) {
    uint64_t next=wmp_tx_next(previous,start,period);
    return next>end?next:end;
}
uint64_t wmp_ingress_clock(uint64_t source_now,uint64_t local_now,uint64_t enqueued) {
    uint64_t age=local_now>=enqueued?local_now-enqueued:0;
    return source_now>age?source_now-age:0;
}
uint64_t wmp_tx_next(uint64_t previous,uint64_t now,uint32_t period) {
    if(!period)return now;
    if(now>UINT64_MAX-period)return UINT64_MAX;
    uint64_t next=previous && previous<=UINT64_MAX-period?previous+period:now+period;
    /* Preserve the grid for ordinary wake jitter. After an overrun start a
     * fresh interval instead of compressing all missed copies into a burst. */
    if(next<now+period/2u)next=now+period;
    return next;
}
void wmp_mac_reset(wmp_mac_unwrap *s) { if(s) { s->extended=0;s->previous=0;s->valid=0; } }
int wmp_mac_extend(wmp_mac_unwrap *s,uint32_t raw,uint64_t *out) {
    if(!s || !out)return 0;
    if(!s->valid) { s->extended=raw;s->previous=raw;s->valid=1;*out=s->extended;return 1; }
    uint32_t distance=raw-s->previous;
    if(distance>0x7fffffffu || s->extended>UINT64_MAX-distance)return 0;
    s->extended+=distance;s->previous=raw;*out=s->extended;return 1;
}
int wmp_sample_time(uint64_t anchor,uint64_t first,uint32_t rate,uint64_t sample,uint64_t *out) {
    if(!out || !rate || sample<first)return 0;
    uint64_t count=sample-first,seconds=count/rate;
    if(seconds>UINT64_MAX/1000000u)return 0;
    uint64_t offset=seconds*1000000u,rest=(count%rate)*1000000u/rate;
    if(offset>UINT64_MAX-rest || anchor>UINT64_MAX-offset-rest)return 0;
    *out=anchor+offset+rest;return 1;
}

int wmp_silence_due(uint64_t pts,uint64_t source,uint64_t local,uint32_t reserve,uint64_t *ingress) {
    if(!ingress || source>UINT64_MAX-reserve || pts>source+reserve)return 0;
    uint64_t age=source+reserve-pts;*ingress=age<local?local-age:0;return 1;
}

int wmp_tx_precedes(uint64_t candidate_due,int candidate_repair,uint64_t candidate_deadline,
                    uint64_t selected_due,int selected_repair,uint64_t selected_deadline,uint64_t now) {
    int candidate_ready=candidate_due<=now,selected_ready=selected_due<=now;
    if(candidate_ready!=selected_ready)return candidate_ready;
    if(candidate_ready) {
        uint64_t c=candidate_deadline?candidate_deadline:UINT64_MAX,s=selected_deadline?selected_deadline:UINT64_MAX;
        if(c!=s)return c<s;
        if(candidate_repair!=selected_repair)return !candidate_repair;
    }
    return candidate_due<selected_due;
}
int wmp_tx_deadline(int repair,uint64_t presentation,uint64_t clock,uint32_t guard) {
    if(!repair)return WMP_TX_SEND;
    if(!clock)return WMP_TX_WAIT_CLOCK;
    return presentation>clock && presentation-clock>guard?WMP_TX_SEND:WMP_TX_EXPIRED;
}
int wmp_tx_original_expired(uint64_t presentation,uint64_t clock,uint32_t guard) {
    return clock && presentation && (presentation<=clock || presentation-clock<=guard);
}
int wmp_tx_precedes_copy(uint64_t candidate_due,int candidate_repair,unsigned candidate_copy,uint64_t candidate_deadline,
                         uint64_t selected_due,int selected_repair,unsigned selected_copy,uint64_t selected_deadline,uint64_t now) {
    if(candidate_due<=now && selected_due<=now && !candidate_repair && !selected_repair && candidate_copy!=selected_copy)
        return candidate_copy<selected_copy;
    return wmp_tx_precedes(candidate_due,candidate_repair,candidate_deadline,selected_due,selected_repair,selected_deadline,now);
}
int wmp_feedback_window(uint32_t last_rx,uint32_t now,uint32_t window_us) {
    return last_rx && window_us && (uint32_t)(now-last_rx)<window_us;
}
int wmp_feedback_blocked(uint32_t last_rx,uint32_t now,uint32_t gate_us) {
    return gate_us && !wmp_feedback_window(last_rx,now,gate_us);
}

int wmp_copy_due(uint64_t ready,uint64_t identity,unsigned copy,uint32_t spacing,uint32_t jitter,uint64_t *out) {
    if(!out || copy>3u || !spacing || jitter>spacing/4u)return 0;
    uint64_t offset=(uint64_t)copy*spacing;
    if(copy && jitter) {
        uint32_t h=(uint32_t)identity^(uint32_t)(identity>>32)^(copy*UINT32_C(0x9e3779b9));
        h^=h>>16;h*=UINT32_C(0x7feb352d);h^=h>>15;h*=UINT32_C(0x846ca68b);h^=h>>16;
        int64_t delta=(int64_t)(h%((uint64_t)jitter*2u+1u))-(int64_t)jitter;
        offset=delta<0?offset-(uint64_t)(-delta):offset+(uint64_t)delta;
    }
    if(ready>UINT64_MAX-offset)return 0;
    *out=ready+offset;return 1;
}
