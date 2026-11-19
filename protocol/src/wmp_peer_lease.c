#include "wmp_peer_lease.h"
void wmp_peer_lease_init(wmp_peer_lease *s) { if(s)*s=(wmp_peer_lease){0}; }
int wmp_peer_claimed(const wmp_peer_lease *s,uint64_t now) {
    return s && s->valid && s->playing && now>=s->active_us && now-s->active_us<1500000u;
}
int wmp_leader_precedes(uint64_t a_id,uint64_t a_start,uint64_t b_id,uint64_t b_start,int comparable) {
    if(comparable && a_start && b_start) {
        uint64_t gap=a_start>b_start?a_start-b_start:b_start-a_start;
        if(gap>WMP_LEADER_TIE_US)return a_start<b_start;
    }
    return a_id<b_id;
}
static int announce(wmp_peer_lease *s,uint64_t peer,int playing,uint64_t now,uint64_t start,uint64_t owner_start,int comparable) {
    if(!s || !peer)return 0;
    int active=wmp_peer_claimed(s,now);
    if(s->valid && s->peer!=peer && active) {
        int startup=playing && wmp_leader_precedes(peer,start,s->peer,owner_start,comparable) && now>=s->selected_us && now-s->selected_us<1000000u;
        if(!startup)return 0;
    }
    if(!s->valid || s->peer!=peer || (!active && playing))s->selected_us=now;
    s->peer=peer;s->valid=1;s->playing=playing!=0;
    if(playing)s->active_us=now;
    return 1;
}
int wmp_peer_announce(wmp_peer_lease *s,uint64_t peer,int playing,uint64_t now) { return announce(s,peer,playing,now,0,0,0); }
int wmp_peer_announce_at(wmp_peer_lease *s,uint64_t peer,int playing,uint64_t now,uint64_t start,uint64_t owner_start,int comparable) { return announce(s,peer,playing,now,start,owner_start,comparable); }
void wmp_peer_audio(wmp_peer_lease *s,uint64_t now) { if(s && s->valid && s->playing && now>=s->active_us)s->active_us=now; }
void wmp_peer_beacon(wmp_peer_lease *s,uint64_t now) { wmp_peer_audio(s,now); }
void wmp_peer_idle(wmp_peer_lease *s) { if(s)s->playing=0; }
