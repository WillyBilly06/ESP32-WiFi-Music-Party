#ifndef WMP_BEACON_CLOCK_H
#define WMP_BEACON_CLOCK_H
#include <stdint.h>
/* One-way timestamps contain variable positive delivery delay. Fit frequency
 * to the least-delayed observation in each second, rather than rejecting an
 * entire window because some beacons waited for RF. Absolute fixed delay is
 * still unknown: this is not a physical synchronization accuracy guarantee. */
typedef struct {
    uint64_t local[128],remote[128],ref_local,ref_source;
    double ratio;
    uint32_t count,next,valid,jitter_us,fit_jitter_us,rejected;
} wmp_beacon_clock;
void wmp_beacon_clock_reset(wmp_beacon_clock *clock);
int wmp_beacon_clock_push(wmp_beacon_clock *clock,uint64_t local,uint64_t remote);
int wmp_beacon_clock_to_local(const wmp_beacon_clock *clock,uint64_t remote,uint64_t *local);
#endif
