#include "telemetry.h"
#include "wmp_wire.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
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
    if(!owner || owner!=xTaskGetCurrentTaskHandle())result=vprintf(format,args);
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
