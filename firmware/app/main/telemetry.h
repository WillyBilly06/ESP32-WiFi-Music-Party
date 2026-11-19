#ifndef WMP_TELEMETRY_H
#define WMP_TELEMETRY_H
int wmp_telemetry_printf(const char *format,...) __attribute__((format(printf,1,2)));
int wmp_telemetry_begin(unsigned long query);
void wmp_telemetry_end(unsigned long query);
#endif
