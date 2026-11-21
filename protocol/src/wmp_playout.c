#include "wmp_playout.h"
#include "wmp_time.h"
#include "wmp_wire.h"
/* Drift-tracking controller tuning (one source-owned timeline). Defaults are
 * the measured 2026-11-17 behaviour: one-second minimum ingress slack, slewed
 * at 1200 ppm, pulled back after a single early window. Changeable at run time
 * for a same-session A/B. hold_windows>1: input arriving late (slack below the
 * band) still retargets at once, but the timeline is only pulled earlier after
 * that many consecutive windows above the band, using their smallest minimum,
 * so one late burst no longer causes a forward step and a reversal. */
static uint32_t tuning_window_us=1000000u,tuning_slew_ppm=1200u,tuning_hold_windows=1u;
int wmp_playout_tuning(uint32_t window_ms,uint32_t slew_ppm,uint32_t hold_windows) {
    if(window_ms<1000u || window_ms>60000u || slew_ppm<50u || slew_ppm>1200u || !hold_windows || hold_windows>120u)return 0;
    tuning_window_us=window_ms*1000u;tuning_slew_ppm=slew_ppm;tuning_hold_windows=hold_windows;return 1;
}
void wmp_playout_tuning_get(uint32_t *window_ms,uint32_t *slew_ppm,uint32_t *hold_windows) {
    *window_ms=tuning_window_us/1000u;*slew_ppm=tuning_slew_ppm;*hold_windows=tuning_hold_windows;
}
void wmp_playout_init(wmp_playout *s,uint32_t delay) {
    if(!s)return;
    s->valid=0;s->requested_us=delay;s->baseline_us=delay;s->changes=0;s->phase_q16=s->target_q16=0;
    s->min_slack=INT64_MAX;s->max_slack=INT64_MIN;
    s->pending_late=s->packet_samples=s->clean_windows=0;s->feedback_window_us=0;s->steps=0;
    s->high_windows=0;s->high_min=INT64_MAX;
}
void wmp_playout_report(wmp_playout *s,uint32_t late,uint32_t samples) {
    if(!s || !samples || samples>WMP_MAX_GROUP_SAMPLES || !late)return;
    if(late>32u)late=32u;
    if(late>s->pending_late)s->pending_late=late;
    s->packet_samples=samples;
}
int wmp_playout_recover(wmp_playout *s,uint64_t now,uint32_t guard,uint64_t *sample,uint64_t *skipped) {
    if(!s || !s->valid || !sample || !skipped || !s->rate || now>UINT64_MAX-s->requested_us)return 0;
    uint64_t nominal;if(!wmp_sample_time(s->anchor_us,s->first_sample,s->rate,*sample,&nominal))return 0;
    int64_t pts=(int64_t)nominal+s->phase_q16/65536;
    if(pts>=0 && (uint64_t)pts>=now && (uint64_t)pts-now>=guard)return 0;
    uint64_t target=now+s->requested_us;
    if(pts<0 || target<(uint64_t)pts)return 0;
    uint64_t gap=target-(uint64_t)pts;
    if(gap>(UINT64_MAX-999999u)/s->rate)return 0;
    uint64_t count=(gap*s->rate+999999u)/1000000u;
    if(!count || *sample>UINT64_MAX-count)return 0;
    *sample+=count;*skipped=count;return 1;
}
static int set_requested(wmp_playout *s,uint32_t delay) {
    if(!s || delay<WMP_PLAYOUT_MIN_US || delay>WMP_PLAYOUT_MAX_US)return 0;
    if(s->valid)s->target_q16=s->phase_q16+((int64_t)delay-(int64_t)s->requested_us)*65536;
    s->requested_us=delay;++s->changes;return 1;
}
int wmp_playout_request(wmp_playout *s,uint32_t delay) {
    if(!set_requested(s,delay))return 0;
    s->baseline_us=delay;s->clean_windows=0;return 1;
}
int wmp_playout_plan(wmp_playout *s,uint64_t sample,uint32_t rate,uint64_t now,uint64_t *out) {
    if(!s || !out || !rate || (s->valid && sample<s->last_sample))return 0;
    if(!s->valid || s->rate!=rate) {
        s->first_sample=s->last_sample=sample;s->anchor_us=now+s->requested_us;s->window_us=now;
        s->rate=rate;s->phase_q16=s->target_q16=0;s->valid=1;
        s->feedback_window_us=now;
        s->min_slack=INT64_MAX;s->max_slack=INT64_MIN;
        s->high_windows=0;s->high_min=INT64_MAX;
    }
    uint64_t elapsed=sample-s->last_sample;s->last_sample=sample;
    /* A bounded common slew, with feed-forward and phase correction at sinks. */
    int64_t step=(int64_t)(elapsed*tuning_slew_ppm*65536u/rate),difference=s->target_q16-s->phase_q16;
    if(difference>step)difference=step;
    if(difference< -step)difference= -step;
    s->phase_q16+=difference;
    uint64_t nominal;if(!wmp_sample_time(s->anchor_us,s->first_sample,rate,sample,&nominal))return 0;
    int64_t pts=(int64_t)nominal+s->phase_q16/65536;if(pts<0)return 0;*out=(uint64_t)pts;
    int64_t slack=pts-(int64_t)now;
    if(slack<s->min_slack)s->min_slack=slack;
    if(slack>s->max_slack)s->max_slack=slack;
    if(now>=s->window_us && now-s->window_us>=tuning_window_us) {
        /* Track producer-clock drift while bounding actual queued headroom.
         * This is group-wide; followers never grow independent delays. */
        /* +/-30ms: phone A2DP delivery (and BLE activity on the source) moves
         * one-second minima by ~20ms; a 15ms band retargeted every few
         * seconds and kept every sink resampling at +/-1200ppm. */
        int64_t floor=(int64_t)s->requested_us-30000,ceiling=(int64_t)s->requested_us+30000;
        /* A2DP arrival bursts widen max-min. Pulling the maximum down to a
         * target can destroy the minimum delivery reserve. Control only the
         * worst-case original-ingress headroom, never our dequeue residence. */
        int64_t measured=s->min_slack;int retarget=0;
        if(measured<floor) { retarget=1;s->high_windows=0;s->high_min=INT64_MAX; }
        else if(measured>ceiling) {
            if(measured<s->high_min)s->high_min=measured;
            if(++s->high_windows>=tuning_hold_windows) { measured=s->high_min;retarget=1;s->high_windows=0;s->high_min=INT64_MAX; }
        } else { s->high_windows=0;s->high_min=INT64_MAX; }
        if(retarget) {
            int64_t correction=(int64_t)s->requested_us-measured;
            if(correction>50000)correction=50000;
            if(correction< -50000)correction= -50000;
            s->target_q16=s->phase_q16+correction*65536;++s->changes;
        }
        s->window_us=now;s->min_slack=INT64_MAX;s->max_slack=INT64_MIN;
    }
    if(now>=s->feedback_window_us && now-s->feedback_window_us>=5000000u) {
        if(s->pending_late && s->packet_samples) {
            uint32_t frames=s->pending_late>2u?2u:s->pending_late;
            uint32_t increase=(uint32_t)((uint64_t)frames*s->packet_samples*1000000u/rate);
            uint32_t target=s->requested_us+increase;if(target>WMP_PLAYOUT_MAX_US)target=WMP_PLAYOUT_MAX_US;
            /* Late reports mean sinks are already behind: apply the growth as
             * one common forward step rather than a 1.2ms/s slew that would
             * keep every sink late for a minute. Sinks realign with a single
             * faded fill; reductions still slew. */
            if(target!=s->requested_us && set_requested(s,target) && s->target_q16>s->phase_q16) { s->phase_q16=s->target_q16;++s->steps; }
            s->clean_windows=0;
        } else if(++s->clean_windows>=WMP_PLAYOUT_DECAY_WINDOWS && s->requested_us>s->baseline_us) {
            /* A transient fault must not hold the whole group at the maximum
             * forever: step back toward the requested reserve after a clean
             * minute. A renewed late report grows it again. */
            uint32_t step=s->requested_us-s->baseline_us;if(step>WMP_PLAYOUT_DECAY_STEP_US)step=WMP_PLAYOUT_DECAY_STEP_US;
            set_requested(s,s->requested_us-step);s->clean_windows=0;
        }
        /* A short clean interval cannot establish that rare long gaps are
         * gone, so decay waits a full minute and moves 10 ms at a time. */
        s->pending_late=0;s->feedback_window_us=now;
    }
    return 1;
}
