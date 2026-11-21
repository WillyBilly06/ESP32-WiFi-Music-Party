#ifndef WMP_TELEMETRY_H
#define WMP_TELEMETRY_H
#include <stdint.h>
int wmp_telemetry_printf(const char *format,...) __attribute__((format(printf,1,2)));
int wmp_telemetry_begin(unsigned long query);
/* Call once from the console task before other tasks start printing. */
void wmp_telemetry_init(void);
void wmp_telemetry_counters(uint32_t *written,uint32_t *dropped,uint32_t *truncated);
void wmp_telemetry_end(unsigned long query);
#endif
