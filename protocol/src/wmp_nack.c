#include "wmp_nack.h"
_Static_assert(sizeof(wmp_nack_entry)==32u,"Deadline metadata must not grow tracker RAM");
static int live(const wmp_nack_entry *e,uint64_t now) {
    return e->valid && now>=e->first_us && now-e->first_us<=e->lifetime_us;
}
static uint64_t after(uint64_t now,uint32_t delay) {
    return now>UINT64_MAX-delay?UINT64_MAX:now+delay;
}
static wmp_nack_entry *find(wmp_nack *s,uint64_t sequence) {
    for(unsigned i=0;i<WMP_NACK_SLOTS;i++)if(s->entries[i].valid && s->entries[i].sequence==sequence)return &s->entries[i];
    return 0;
}
void wmp_nack_reset(wmp_nack *s) {
    if(!s)return;
    s->valid=0;s->generation=s->highest=0;
    for(unsigned i=0;i<WMP_NACK_SLOTS;i++)s->entries[i].valid=s->entries[i].pending=0;
}
void wmp_nack_observe(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now,uint32_t backoff) {
    wmp_nack_observe_after(s,gen,seq,now,backoff,120000u);
}
static void observe(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now,uint32_t backoff,uint32_t initial_delay,uint32_t lifetime,unsigned limit,int allow_new) {
    if(!s || !gen)return;
    if(!s->valid || s->generation!=gen) {
        wmp_nack_reset(s);s->valid=1;s->generation=gen;s->highest=seq;return;
    }
    wmp_nack_entry *got=find(s,seq);if(got)got->valid=0;
    if(seq<=s->highest)return;
    if(!allow_new) { s->highest=seq;return; }
    uint64_t begin=s->highest+1u;if(seq-begin>WMP_NACK_SLOTS)begin=seq-WMP_NACK_SLOTS;
    for(uint64_t missing=begin;missing<seq;missing++) {
        if(find(s,missing))continue;
        wmp_nack_entry *e=0;
        for(unsigned i=0;i<WMP_NACK_SLOTS;i++) {
            wmp_nack_entry *candidate=&s->entries[i];
            if(!live(candidate,now)) { e=candidate;break; }
        }
        /* Preserve live earlier deadlines when the bounded tracker is full. */
        if(!e)continue;
        *e=(wmp_nack_entry){.sequence=missing,.first_us=now,.next_us=after(after(now,initial_delay),backoff%20000u),.valid=1,.limit=(uint8_t)limit,.lifetime_us=lifetime};
    }
    s->highest=seq;
}
void wmp_nack_observe_after(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now,uint32_t backoff,uint32_t initial_delay) {
    observe(s,gen,seq,now,backoff,initial_delay,400000u,8u,1);
}
void wmp_nack_observe_until(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now,uint32_t backoff,uint32_t initial_delay,uint64_t until) {
    uint64_t span=until>now?until-now:0;
    uint32_t lifetime=span?(span-1u>UINT32_MAX?UINT32_MAX:(uint32_t)(span-1u)):0;
    /* A nearly due gap must not spend its entire useful life waiting for
     * another original. Preserve at least half the remaining recovery window. */
    uint64_t delay=(uint64_t)initial_delay+backoff%20000u;
    if(delay>span/2u)delay=span/2u;
    if(delay>UINT32_MAX)delay=UINT32_MAX;
    observe(s,gen,seq,now,0,(uint32_t)delay,lifetime,16u,span!=0);
}
unsigned wmp_nack_attempts(const wmp_nack *s,uint64_t gen,uint64_t seq) {
    if(!s || !s->valid || s->generation!=gen)return 0;
    for(unsigned i=0;i<WMP_NACK_SLOTS;i++)if(s->entries[i].valid && s->entries[i].sequence==seq)return s->entries[i].attempts;
    return 0;
}
int wmp_nack_plan(wmp_nack *s,uint64_t now,uint64_t *sequence) {
    if(!s || !sequence)return 0;
    wmp_nack_entry *best=0;
    for(unsigned i=0;i<WMP_NACK_SLOTS;i++) {
        wmp_nack_entry *e=&s->entries[i];if(!e->valid)continue;
        if(!live(e,now)) { e->valid=0;continue; }
        if(e->pending || e->attempts>=e->limit || now<e->next_us)continue;
        if(!best || e->sequence<best->sequence)best=e;
    }
    if(!best)return 0;
    *sequence=best->sequence;return 1;
}
void wmp_nack_commit(wmp_nack *s,uint64_t seq,uint64_t now) {
    if(!s)return;
    wmp_nack_entry *e=find(s,seq);
    if(e && live(e,now) && e->attempts<e->limit) { e->pending=0;++e->attempts;e->next_us=after(now,32000u); }
}
int wmp_nack_reserve(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now) {
    if(!s || !s->valid || s->generation!=gen)return 0;
    wmp_nack_entry *e=find(s,seq);
    if(!e || !live(e,now) || e->pending || e->attempts>=e->limit || now<e->next_us)return 0;
    e->pending=1;return 1;
}
int wmp_nack_pending(const wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now) {
    if(!s || !s->valid || s->generation!=gen)return 0;
    for(unsigned i=0;i<WMP_NACK_SLOTS;i++) {
        const wmp_nack_entry *e=&s->entries[i];
        if(e->pending && e->sequence==seq && live(e,now))return 1;
    }
    return 0;
}
int wmp_nack_submit(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now) {
    if(!wmp_nack_pending(s,gen,seq,now))return 0;
    wmp_nack_commit(s,seq,now);return 1;
}
int wmp_nack_cancel(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now,uint32_t defer_us) {
    if(!s || !s->valid || s->generation!=gen)return 0;
    wmp_nack_entry *e=find(s,seq);if(!e || !e->pending)return 0;
    e->pending=0;uint64_t next=after(now,defer_us);
    if(next>e->next_us)e->next_us=next;
    return 1;
}
void wmp_nack_heard(wmp_nack *s,uint64_t gen,uint64_t seq,uint64_t now) {
    if(!s || s->generation!=gen)return;
    wmp_nack_entry *e=find(s,seq);
    uint64_t next=after(now,40000u);if(e && e->next_us<next)e->next_us=next;
}
