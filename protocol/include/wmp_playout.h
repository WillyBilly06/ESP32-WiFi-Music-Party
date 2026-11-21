#ifndef WMP_PLAYOUT_H
#define WMP_PLAYOUT_H
#include <stdint.h>
/* Group-wide playout reserve bounds. The receive/payload pools are sized for
 * the maximum, so automatic growth may never exceed it. */
#define WMP_PLAYOUT_MIN_US 120000u
#define WMP_PLAYOUT_MAX_US 300000u
#define WMP_PLAYOUT_DECAY_WINDOWS 12u
#define WMP_PLAYOUT_DECAY_STEP_US 10000u
/* Source-owned common timeline. Slew only future packet timestamps; published
 * packets and their repair copies keep their immutable presentation time. */
typedef struct {
    uint64_t first_sample,anchor_us,last_sample,window_us;
    int64_t phase_q16,target_q16,min_slack,max_slack;
    uint32_t rate,requested_us,changes;
    uint64_t feedback_window_us;
    uint32_t pending_late,packet_samples,clean_windows;
    uint8_t valid;
    /* Explicitly requested reserve. Automatic growth after late reports decays
     * back toward it after WMP_PLAYOUT_DECAY_WINDOWS clean feedback windows. */
    uint32_t baseline_us;
    /* Common forward timeline steps applied for late-report growth. */
    uint32_t steps;
    /* Consecutive drift windows above the band, and their smallest minimum. */
    uint32_t high_windows;
    int64_t high_min;
} wmp_playout;
void wmp_playout_init(wmp_playout *s,uint32_t delay_us);
int wmp_playout_plan(wmp_playout *s,uint64_t sample,uint32_t rate,uint64_t now_us,uint64_t *presentation_us);
int wmp_playout_request(wmp_playout *s,uint32_t delay_us);
void wmp_playout_report(wmp_playout *s,uint32_t late_frames,uint32_t packet_samples);
/* Drift controller: minimum-slack window (1000-60000 ms), common slew rate
 * (50-1200 ppm) and consecutive early windows before pulling the timeline back
 * (1-120). Returns 0 and changes nothing when out of range. */
int wmp_playout_tuning(uint32_t window_ms,uint32_t slew_ppm,uint32_t hold_windows);
void wmp_playout_tuning_get(uint32_t *window_ms,uint32_t *slew_ppm,uint32_t *hold_windows);
/* A producer outage can leave its cursor behind samples already rendered as
 * missing input. Resume on future sample positions without discarding SBC,
 * changing the session, or trying to broadcast already elapsed silence. */
int wmp_playout_recover(wmp_playout *s,uint64_t now_us,uint32_t guard_us,uint64_t *sample,uint64_t *skipped);
#endif
