#ifndef WMP_PEER_LEASE_H
#define WMP_PEER_LEASE_H
#include <stdint.h>
typedef struct { uint64_t peer,selected_us,active_us;uint8_t valid,playing; } wmp_peer_lease;
void wmp_peer_lease_init(wmp_peer_lease *state);
int wmp_peer_claimed(const wmp_peer_lease *state,uint64_t now);
/* Select a live owner before binding its generation. An unrelated idle
 * announcement cannot replace an active owner or reset its playback. */
int wmp_peer_announce(wmp_peer_lease *state,uint64_t peer,int playing,uint64_t now);
/* Leadership precedence: the board that started broadcasting first keeps the
 * party. Start times are only comparable on one shared clock; within the tie
 * window, or without a shared clock, the lower identity wins. 1=a precedes b. */
#define WMP_LEADER_TIE_US 2000u
int wmp_leader_precedes(uint64_t a_id,uint64_t a_start,uint64_t b_id,uint64_t b_start,int comparable);
/* As wmp_peer_announce, but a startup contest between two playing owners is
 * decided by wmp_leader_precedes instead of identity alone. The caller keeps
 * the current owner's start time (owner_start) beside the lease. */
int wmp_peer_announce_at(wmp_peer_lease *state,uint64_t peer,int playing,uint64_t now,uint64_t start,uint64_t owner_start,int comparable);
void wmp_peer_audio(wmp_peer_lease *state,uint64_t now);
void wmp_peer_beacon(wmp_peer_lease *state,uint64_t now);
void wmp_peer_idle(wmp_peer_lease *state);
#endif
