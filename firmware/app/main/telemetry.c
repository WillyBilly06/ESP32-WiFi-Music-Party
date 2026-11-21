#include "telemetry.h"
#include "wmp_wire.h"
#include "esp_heap_caps.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

/* Only the serial command task formats a STAT body. Other task logs keep
 * ordinary vprintf semantics and never touch its buffer or checksum. The
 * scratch allocation is diagnostic-only, outside all radio/media callbacks. */
static TaskHandle_t volatile owner;
static char *scratch;
static uint32_t checksum,body_bytes,valid;
#define STAT_LINE_BYTES 768u

/* A STAT body keeps the 1 KB UART transmit buffer full for ~1.2 s at 115200.
 * Event lines from any other task (Bluetooth host, radio, audio) must never
 * wait for that: they are formatted into one shared line and written only if
 * the whole line fits in the transmit buffer now, else dropped and counted.
 * Only the console task may wait for a STAT body to drain. */
static TaskHandle_t console;
static SemaphoreHandle_t event_lock;
static char event_line[256];
static volatile uint32_t events_written,events_dropped,events_truncated;

void wmp_telemetry_init(void) {
    console=xTaskGetCurrentTaskHandle();
    if(!event_lock)event_lock=xSemaphoreCreateMutex();
    configASSERT(event_lock);
}
void wmp_telemetry_counters(uint32_t *written,uint32_t *dropped,uint32_t *truncated) {
    *written=events_written;*dropped=events_dropped;*truncated=events_truncated;
}
static int event_vprintf(const char *format,va_list args) {
    /* Interrupts and the scheduler-less start-up path keep the old behaviour. */
    if(!event_lock || xPortInIsrContext() || xTaskGetSchedulerState()!=taskSCHEDULER_RUNNING)return vprintf(format,args);
    if(xSemaphoreTake(event_lock,0)!=pdTRUE) { ++events_dropped;return 0; }
    int result=vsnprintf(event_line,sizeof(event_line),format,args);
    size_t length=result<0?0u:(unsigned)result>=sizeof(event_line)?sizeof(event_line)-1u:(unsigned)result;
    if(result>=0 && (unsigned)result>=sizeof(event_line)) { ++events_truncated;event_line[length-1u]='\n'; }
    size_t room=0;
    if(length && uart_get_tx_buffer_free_size(CONFIG_ESP_CONSOLE_UART_NUM,&room)==ESP_OK && room>=length) {
        /* No fflush(stdout): its FILE lock can be held by the console mid-STAT.
         * The driver's TX mutex is per write, so the residual wait is bounded by
         * the console's current write, not by a STAT body. */
        uart_write_bytes(CONFIG_ESP_CONSOLE_UART_NUM,event_line,length);++events_written;
    } else if(length)++events_dropped;
    xSemaphoreGive(event_lock);return result;
}
int wmp_telemetry_begin(unsigned long query) {
    if(owner)return 0;
    scratch=heap_caps_malloc(STAT_LINE_BYTES,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!scratch) { printf("WMP_STAT_CRC query=%08lx valid=0 bytes=0 crc=0 allocation_failed=1\n",query);return 0; }
    checksum=body_bytes=0;valid=1;
    printf("WMP_STAT_BEGIN query=%08lx schema=2\n",query);
    owner=xTaskGetCurrentTaskHandle();return 1;
}
int wmp_telemetry_printf(const char *format,...) {
    va_list args;va_start(args,format);int result;
    TaskHandle_t self=xTaskGetCurrentTaskHandle();
    if(!owner || owner!=self)result=console && self!=console?event_vprintf(format,args):vprintf(format,args);
    else {
        result=vsnprintf(scratch,STAT_LINE_BYTES,format,args);
        if(result<0 || (unsigned)result>=STAT_LINE_BYTES)valid=0;
        size_t length=result<0?0u:(unsigned)result>=STAT_LINE_BYTES?STAT_LINE_BYTES-1u:(unsigned)result;
        checksum=wmp_crc32_extend(checksum,(const uint8_t *)scratch,length);body_bytes+=(uint32_t)length;
        fwrite(scratch,1,length,stdout);
    }
    va_end(args);return result;
}
void wmp_telemetry_end(unsigned long query) {
    if(owner!=xTaskGetCurrentTaskHandle())return;
    owner=NULL;heap_caps_free(scratch);scratch=NULL;
    printf("WMP_STAT_CRC query=%08lx valid=%lu bytes=%lu crc=%lu allocation_failed=0\n",query,(unsigned long)valid,(unsigned long)body_bytes,(unsigned long)checksum);
}
