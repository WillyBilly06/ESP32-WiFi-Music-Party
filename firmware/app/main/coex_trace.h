#ifndef WMP_COEX_TRACE_H
#define WMP_COEX_TRACE_H
#include <stdint.h>
enum { WMP_RT_COEX_BT=1,WMP_RT_TX_SUBMIT,WMP_RT_TX_DONE,WMP_RT_RTP,WMP_RT_BLE,WMP_RT_RX,WMP_RT_HCI_IN,WMP_RT_PHASE,WMP_RT_HCI_OUT,WMP_RT_RX_BAD,WMP_RT_COEX_WIFI_REQ,WMP_RT_COEX_BT_REQ,WMP_RT_COEX_WIFI_REL,WMP_RT_COEX_BT_REL };
/* Safe from tasks, Wi-Fi/BT callbacks and ISRs; a no-op unless armed. */
void wmp_coex_trace_record(unsigned kind,unsigned a,unsigned b,uint32_t c);
int wmp_coex_trace_arm(unsigned enabled);
void wmp_coex_trace_dump(unsigned long query);
int wmp_coex_trace_active(void);
/* Current coexistence scheme phase index; task context only. */
int wmp_coex_phase(void);
/* Two-phase scheme override: wifi_percent 20..95 for phase 0 with wifi_type, 0 restores. */
int wmp_coex_override(unsigned wifi_percent,unsigned wifi_type);
void wmp_coex_override_poll(void);
void wmp_coex_state(void);
int wmp_coex_status_bit(unsigned type,unsigned bits,unsigned set);
void wmp_coex_requests(unsigned long query);
int wmp_coex_priority(unsigned event,unsigned priority);
#endif
