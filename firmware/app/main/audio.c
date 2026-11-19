#include "wmp_codecs.h"
#include "wmp_codec.h"
#include "node.h"
#include "wmp_sbc.h"
#include "wmp_time.h"
#include "wmp_beacon_clock.h"
#include "wmp_frame_pool.h"
#include "wmp_payload_pool.h"
#include "wmp_pcm_blocks.h"
#include "classic/btstack_sbc.h"
#include "classic/btstack_sbc_bluedroid.h"
#include "driver/i2s_std.h"
#include "hal/clk_tree_ll.h"
#include "soc/rtc.h"
#include "esp_chip_info.h"
#include "esp_wifi.h"
#include "esp_private/wifi.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_cpu.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
volatile wmp_audio_stats audio_stats;
static volatile unsigned volume_percent=25;
static volatile unsigned repair_loan_limit=8;
int wmp_audio_repair_limit(unsigned limit) { if(limit<1u || limit>16u)return 0;repair_loan_limit=limit;return 1; }
unsigned wmp_audio_repair_limit_value(void) { return repair_loan_limit; }
unsigned wmp_audio_volume(void) { return volume_percent; }
/* Audio-taper volume: gain = (percent/100)^2 in Q24, so 10 % is -40 dB, 25 % -24 dB, 50 % -12 dB,
 * 100 % 0 dB. A linear percentage made the low steps far too loud (10 % was only -20 dB). */
static volatile int32_t volume_gain_q24=1048576; /* 25 % */
static int32_t volume_curve(unsigned percent) { return (int32_t)(((uint64_t)percent*percent*16777216u+5000u)/10000u); }
/* Party volume: a follower plays at the master's volume (JBL-style) while the master's frames
 * carry it; its own setting returns in solo. */
static volatile int32_t party_gain_q24=-1;static volatile int64_t party_gain_at;static int32_t current_gain_q24=1048576;
void wmp_audio_party_volume(int percent) {
    if(percent<0 || percent>100)return;
    party_gain_q24=volume_curve((unsigned)percent);party_gain_at=esp_timer_get_time();
}
static int32_t effective_gain(void) { return party_gain_q24>=0 && esp_timer_get_time()-party_gain_at<2000000?party_gain_q24:volume_gain_q24; }
int wmp_audio_set_volume(unsigned percent) { if(percent>100u)return 0;volume_percent=percent;volume_gain_q24=volume_curve(percent);wmp_settings_changed();return 1; }
/* Stereo pair (JBL-style). Each speaker has a channel setting; it takes effect only while the
 * party has exactly two speakers (size from the master's clock trailers, or the master's own
 * count), otherwise every speaker plays full stereo. */
static volatile uint8_t speaker_channel=WMP_SPEAKER_STEREO,party_size;
static volatile int64_t party_size_at;
unsigned wmp_audio_speaker_channel(void) { return speaker_channel; }
int wmp_audio_set_speaker_channel(unsigned channel) { if(channel>WMP_SPEAKER_RIGHT)return 0;speaker_channel=(uint8_t)channel;wmp_settings_changed();return 1; }
void wmp_audio_party_size(unsigned speakers) { party_size=(uint8_t)(speakers>3u?3u:speakers);party_size_at=esp_timer_get_time(); }
unsigned wmp_audio_party(void) { return esp_timer_get_time()-party_size_at<2000000?party_size:0u; }
unsigned wmp_audio_output_channel(void) { return wmp_audio_party()==2u?speaker_channel:WMP_SPEAKER_STEREO; }
/* Seven-band EQ, integer only (no floating point anywhere in the audio path): 63 Hz low shelf,
 * peaks at 160 Hz / 400 Hz / 1 kHz / 2.5 kHz / 6.3 kHz (Q 1.1), 16 kHz high shelf, -12..+12 dB.
 * Coefficients are Q28 tables generated off-target (tools/gen_eq_tables.py) for 16/32/44.1/48 kHz;
 * an unknown rate bypasses the EQ. Direct form I with a 64-bit accumulator; the 28 discarded
 * fraction bits are carried into the next sample (error feedback), so even the 63 Hz shelf adds
 * no rounding noise floor. A band at 0 dB is an exact identity and is skipped. */
static volatile int8_t eq_gain[WMP_EQ_BANDS];
static volatile uint32_t eq_version;
#include "eq_tables.inc"
typedef struct { int32_t x1,x2,y1,y2;uint32_t fraction; } eq_history;
static const int32_t *eq_coef[WMP_EQ_BANDS];          /* table row per active band */
static eq_history eq_h[WMP_EQ_BANDS][2];
static uint8_t eq_band_list[WMP_EQ_BANDS],eq_band_count;
static int32_t eq_preamp=1<<30;                         /* Q30: half the largest boost, in dB */
static uint32_t eq_applied_version=UINT32_MAX,eq_rate;
static uint8_t eq_active;
int wmp_audio_set_eq(const int8_t gains[WMP_EQ_BANDS]) {
    for(unsigned i=0;i<WMP_EQ_BANDS;i++)if(gains[i]<-12 || gains[i]>12)return 0;
    for(unsigned i=0;i<WMP_EQ_BANDS;i++)eq_gain[i]=gains[i];
    ++eq_version;wmp_settings_changed();return 1;
}
void wmp_audio_get_eq(int8_t gains[WMP_EQ_BANDS]) { for(unsigned i=0;i<WMP_EQ_BANDS;i++)gains[i]=eq_gain[i]; }
static void eq_select(uint32_t rate) {
    int r=-1;for(unsigned i=0;i<WMP_EQ_RATE_COUNT;i++)if(eq_rates[i]==rate)r=(int)i;
    int peak=0;eq_band_count=0;
    for(unsigned b=0;r>=0 && b<WMP_EQ_BANDS;b++) {
        int g=eq_gain[b];if(!g)continue;if(g>peak)peak=g;
        eq_coef[b]=eq_table[r][b][g+12];eq_band_list[eq_band_count++]=(uint8_t)b;
    }
    eq_active=eq_band_count!=0;eq_preamp=eq_preamp_q30[peak];eq_rate=rate;
}
typedef struct { wmp_header header;int64_t arrived;uint8_t local;uint8_t *payload;uint32_t preparation; } audio_item;
typedef struct { uint64_t source;uint32_t local; } beacon;
static QueueHandle_t timing;
static audio_item *items;
static wmp_frame_pool packet_pool;
static wmp_payload_pool payload_pool;
static uint8_t *payload_storage;
static portMUX_TYPE packet_lock=portMUX_INITIALIZER_UNLOCKED;
typedef struct { uint64_t generation,sample,pts;int64_t at,arrived;int32_t lead_us;uint16_t frames,ready,held;uint8_t kind; } audio_fault;
static audio_fault *fault_history;
static unsigned fault_next,fault_count;
static int64_t local_deadline(uint64_t source,int own);
/* This speaker is the audio source: party master, or solo (broadcast off). */
static inline int local_role(void) { return radio_stats.mode==2u || !wmp_bluetooth_broadcast(); }
static void apll_capture_nominal(void);
static int apll_steer(int32_t ppm);
static void fault_record(unsigned kind,uint64_t gen,uint64_t sample,uint64_t pts,int64_t arrived,unsigned frames) {
    if(!fault_history)return;
    int64_t now=esp_timer_get_time(),deadline=local_deadline(pts,local_role());
    portENTER_CRITICAL(&packet_lock);
    if(fault_history) {
        fault_history[fault_next]=(audio_fault){.generation=gen,.sample=sample,.pts=pts,.at=now,.arrived=arrived,.lead_us=(int32_t)(deadline-now),.frames=(uint16_t)frames,
            .ready=(uint16_t)__builtin_popcountll(packet_pool.ready),.held=(uint16_t)__builtin_popcountll(packet_pool.held),.kind=(uint8_t)kind};
        fault_next=(fault_next+1u)%16u;if(fault_count<16u)++fault_count;
    }
    portEXIT_CRITICAL(&packet_lock);
}
int wmp_audio_fault_resources(unsigned enabled) {
    if(enabled>1u)return 0;
    if(enabled) {
        if(fault_history)return 1;
        audio_fault *created=heap_caps_calloc(16,sizeof(*created),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);if(!created)return 0;
        portENTER_CRITICAL(&packet_lock);if(!fault_history) { fault_history=created;created=NULL;fault_next=fault_count=0; }portEXIT_CRITICAL(&packet_lock);heap_caps_free(created);return 1;
    }
    portENTER_CRITICAL(&packet_lock);audio_fault *old=fault_history;fault_history=NULL;fault_next=fault_count=0;portEXIT_CRITICAL(&packet_lock);heap_caps_free(old);return 1;
}
void wmp_audio_fault_trace(unsigned long query) {
    unsigned count,next;portENTER_CRITICAL(&packet_lock);count=fault_count;next=fault_next;portEXIT_CRITICAL(&packet_lock);
    for(unsigned i=0;i<count;i++) {
        audio_fault f={0};portENTER_CRITICAL(&packet_lock);if(fault_history)f=fault_history[(next+16u-count+i)%16u];portEXIT_CRITICAL(&packet_lock);
        wmp_telemetry_printf("WMP_FAULT query=%08lx kind=%u generation=%llu sample=%llu pts=%llu at_us=%lld arrived_us=%lld lead_us=%ld frames=%u ready=%u held=%u\n",query,f.kind,(unsigned long long)f.generation,(unsigned long long)f.sample,(unsigned long long)f.pts,(long long)f.at,(long long)f.arrived,(long)f.lead_us,f.frames,f.ready,f.held);
    }
    wmp_telemetry_printf("WMP_FAULT_END query=%08lx count=%u loaded=%u\n",query,count,(unsigned)(fault_history!=NULL));
}
static wmp_preparation peer_preparation;
static uint32_t renderer_clock_epoch,preparation_warm,preparation_cold,preparation_retries;
static unsigned renderer_shared_clock;
static int native_clock_network(void) {
    /* A qualified AP anchor and an associated station use the same native
     * clock route. No audio path may infer readiness from STA association. */
    return (radio_stats.ethernet_enabled || radio_stats.direct_rx_enabled) && wmp_radio_clock_ready();
}
static btstack_sbc_decoder_bluedroid_t *decoder_context;
static const btstack_sbc_decoder_t *decoder;
/* Packet-loss concealment: a gap repeats the last good audio while fading it
 * out, and the next real audio fades back in, instead of hard zeros (which
 * cut the waveform mid-sample and are heard as a click/pop). */
#define PLC_HISTORY 256u
/* Concealment: continue the last detected period, fade over ~46 ms, then
 * crossfade back into real audio. A single lost 4-frame group ends near
 * 75% level instead of a hard dropout. */
#define PLC_FADE_OUT 2048u
#define PLC_FADE_IN 128u
#define PLC_WINDOW 64u
#define PLC_MIN_LAG 32u
/* Playback PCM keeps every codec's own precision: interleaved stereo int32 holding signed
 * 24-bit values (16-bit codecs are widened exactly, aptX / aptX HD keep all 24 bits). The
 * DAC clock follows the timeline through the APLL, so there is no resampler copy; the same
 * buffer is packed in place to the I2S width just before DMA. */
typedef struct { int32_t pcm[WMP_MAX_GROUP_SAMPLES*2];int32_t history[PLC_HISTORY*2];int32_t drift_ppm_frames;uint16_t history_frames,plc_position,plc_lag;uint8_t fade_in; } pcm_runtime;
static pcm_runtime *signal_runtime;
#define pcm (signal_runtime->pcm)
static volatile unsigned resources_ready,resources_wanted,resources_stop,resources_starting;
static volatile uint32_t last_offer_us;
static int64_t resource_retry_us,last_codec_us;
static uint32_t resource_failures,resource_starts,resource_stops,codec_starts,codec_stops;
static volatile uint32_t audio_stack_free;
/* Aggregate timing only; never retain or print per-codec-frame records. */
static uint64_t decode_wall_us;
static uint32_t decode_calls,decode_max_us,decode_over1ms,decode_min_us=UINT32_MAX; /* min ~ unpreempted cost */
static volatile unsigned requested_priority=24;
/* The Wi-Fi driver task is pinned to this core (1) at priority 23. Rendering (DMA refill and
 * timing) stays above it; decoding a group runs just below it, so Wi-Fi TX/RX and the radio
 * receiver (24) preempt the decoder. Measured 2026-11-18: decoding aptX at 24 held the Wi-Fi
 * task off (TX completions 21 ms, late copies on every speaker); decoding unpinned at 5
 * starved the master's own playback. aptX also decodes its right channel on core 0. */
#define WMP_DECODE_PRIORITY 22u

static unsigned pcm_frames;
static uint32_t pcm_rate;
static i2s_chan_handle_t dac;
static volatile uint32_t dma_version,dma_sent;
static volatile int64_t dma_time;
static uint64_t written;
static uint32_t output_rate;
/* I2S sample width: 16 for 16-bit codecs, 32 (24 valid bits) while a 24-bit codec plays.
 * frame_bytes is one stereo frame in DMA (4 or 8). */
static uint8_t output_bits=16,frame_bytes=4;
static uint32_t drift_drops,drift_repeats,width_switches;static wmp_pcm_blocks *dma_blocks;
static int64_t startup_deadline;
static volatile uint32_t dma_overflows,dma_events,dma_gap_max_us,dma_started_us;
static uint32_t dma_deficit_max;
static uint32_t dma_block_frames,dma_descriptors;
static TaskHandle_t renderer_task;
int wmp_audio_priority(unsigned priority) {
    if(priority<7u || priority>24u)return 0;
    requested_priority=priority;return 1;
}
static wmp_mac_unwrap clock_unwrap;
static wmp_beacon_clock peer_clock;
static uint64_t trace_pairs[16][2];
static unsigned trace_next,trace_count;
static portMUX_TYPE trace_lock=portMUX_INITIALIZER_UNLOCKED;
static int64_t last_beacon_at;
static volatile uint32_t clock_reset_requested;
void wmp_audio_clock_reset(void) { ++clock_reset_requested; }
static void prepare_peer(int stream) {
    /* A new active stream is real demand for the lazy playback arena. This
     * runs in the receive/control task, never the Wi-Fi callback or ISR. */
    if(stream)wmp_audio_preload();
    uint32_t epoch=0;uint64_t ready=0;
    int shared=native_clock_network() && wmp_source_clock_shared_ready(&epoch,&ready);
    portENTER_CRITICAL(&packet_lock);
    int warm=wmp_preparation_begin_at(&peer_preparation,(uint64_t)esp_timer_get_time(),resources_ready && shared,epoch,audio_stats.running && renderer_shared_clock,renderer_clock_epoch,stream?ready:0);
    if(warm) { ++preparation_warm;audio_stats.clock_valid=1; }else { ++preparation_cold;renderer_shared_clock=0; }
    audio_stats.preparing=!warm;portEXIT_CRITICAL(&packet_lock);
    if(!warm)wmp_audio_clock_reset();
}
void wmp_audio_prepare_peer(void) { prepare_peer(0); }
void wmp_audio_prepare_stream(void) { prepare_peer(1); }
/* Period of the most recent audio: the lag whose preceding segment best
 * matches the last PLC_WINDOW frames (normalized cross-correlation on a mono
 * mix). Repeating that period continues the waveform without a jump. */
static uint16_t plc_find_lag(const pcm_runtime *r) {
    unsigned n=r->history_frames;if(n<PLC_WINDOW+PLC_MIN_LAG)return (uint16_t)n;
    const int32_t *h=r->history;unsigned best=n,last=n-PLC_WINDOW;int64_t best_num=0,best_den=1;
    for(unsigned lag=PLC_MIN_LAG;lag<=last;lag++) {
        int64_t corr=0,energy=0;
        for(unsigned j=0;j<PLC_WINDOW;j++) {
            /* Period search only: 16-bit-scale mono keeps the products in range. */
            int32_t a=(h[(last+j)*2u]+h[(last+j)*2u+1u])>>9,b=(h[(last+j-lag)*2u]+h[(last+j-lag)*2u+1u])>>9;
            corr+=(int64_t)a*b;energy+=(int64_t)b*b;
        }
        if(corr<=0 || !energy)continue;
        /* Compare corr^2/energy without division of large products. */
        int64_t num=(corr>>16)*(corr>>16);
        if(num*best_den>best_num*(energy>>16?energy>>16:1)) { best_num=num;best_den=energy>>16?energy>>16:1;best=lag; }
    }
    return (uint16_t)best;
}
static void plc_sample(const pcm_runtime *r,unsigned position,int32_t *left,int32_t *right) {
    unsigned n=r->history_frames;*left=*right=0;
    if(!n || position>=PLC_FADE_OUT)return;
    unsigned lag=r->plc_lag && r->plc_lag<=n?r->plc_lag:n;
    const int32_t *h=&r->history[((n-lag)+(position%lag))*2u];int64_t gain=(int64_t)(PLC_FADE_OUT-position);
    *left=(int32_t)(h[0]*gain/(int64_t)PLC_FADE_OUT);*right=(int32_t)(h[1]*gain/(int64_t)PLC_FADE_OUT);
}
static void plc_conceal(unsigned frames) {
    pcm_runtime *r=signal_runtime;
    if(!r->plc_position)r->plc_lag=plc_find_lag(r);
    for(unsigned i=0;i<frames;i++) {
        int32_t left,right;plc_sample(r,r->plc_position+i,&left,&right);
        pcm[i*2u]=left;pcm[i*2u+1u]=right;
    }
    r->plc_position=(uint16_t)(r->plc_position+frames>PLC_FADE_OUT?PLC_FADE_OUT:r->plc_position+frames);r->fade_in=1;
}
static void plc_audio(unsigned frames) {
    pcm_runtime *r=signal_runtime;
    if(r->fade_in) {
        /* Crossfade from the concealment continuation into the real audio. */
        unsigned ramp=frames<PLC_FADE_IN?frames:PLC_FADE_IN;
        for(unsigned i=0;i<ramp;i++) {
            int32_t cl,cr;plc_sample(r,r->plc_position+i,&cl,&cr);int32_t k=(int32_t)(i+1u),rest=(int32_t)ramp-k;
            pcm[i*2u]=(int32_t)(((int64_t)pcm[i*2u]*k+(int64_t)cl*rest)/(int32_t)ramp);
            pcm[i*2u+1u]=(int32_t)(((int64_t)pcm[i*2u+1u]*k+(int64_t)cr*rest)/(int32_t)ramp);
        }
        r->fade_in=0;
    }
    unsigned keep=frames<PLC_HISTORY?frames:PLC_HISTORY;
    memcpy(r->history,&pcm[(frames-keep)*2u],keep*8u);r->history_frames=(uint16_t)keep;r->plc_position=0;
}
/* Codec switch: forget the previous codec's tail so concealment never replays it; the new
 * codec's first block fades in from silence. */
static uint8_t play_codec=0xffu;
static void plc_restart(void) {
    pcm_runtime *r=signal_runtime;r->history_frames=0;r->plc_position=0;r->plc_lag=0;r->fade_in=1;
}
/* Decoder-output signal statistics, before volume/EQ (diagnostic, integer only):
 * peak, power, first-difference power (high-frequency share) and how many 24-bit samples use
 * their low 8 bits (0 for any 16-bit source). Reset when reported. */
static uint64_t sig_power,sig_diff_power;static int32_t sig_peak,sig_prev[2];static uint32_t sig_samples,sig_low_bits;
/* PCMDUMP: capture the left channel of the decoder output (pre-volume, top 16 of 24 bits) for a
 * host-side spectrum. Diagnostic only; the buffer exists only between request and dump. */
static int16_t *volatile capture_buf;static volatile uint32_t capture_len,capture_pos;
int wmp_audio_capture_start(unsigned samples) {
    if(capture_buf || samples<256u || samples>16384u)return 0;
    int16_t *b=heap_caps_malloc(samples*sizeof(int16_t),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);if(!b)return 0;
    capture_len=samples;capture_pos=0;__atomic_store_n(&capture_buf,b,__ATOMIC_RELEASE);return 1;
}
int wmp_audio_capture_dump(void) {
    int16_t *b=capture_buf;if(!b || capture_pos<capture_len)return 0;
    wmp_telemetry_printf("WMP_PCM_BEGIN samples=%lu rate=%lu%c",(unsigned long)capture_len,(unsigned long)output_rate,10);
    static char line[4*64+1]; /* static: the console task has little stack */
    for(uint32_t i=0;i<capture_len;i+=64u) {
        unsigned n=0;for(uint32_t k=i;k<i+64u && k<capture_len;k++)n+=(unsigned)snprintf(line+n,sizeof(line)-n,"%04x",(unsigned)(uint16_t)b[k]);
        wmp_telemetry_printf("WMP_PCM %s%c",line,10);
    }
    wmp_telemetry_printf("WMP_PCM_END%c",10);
    __atomic_store_n(&capture_buf,(int16_t *)NULL,__ATOMIC_RELEASE);heap_caps_free(b);return 1;
}
static void signal_note(const int32_t *x,unsigned frames) {
    int16_t *cb=capture_buf;
    if(cb)for(unsigned i=0;i<frames && capture_pos<capture_len;i++)cb[capture_pos++]=(int16_t)(x[i*2u]>>8);
    for(unsigned i=0;i<frames;i++)for(unsigned c=0;c<2u;c++) {
        int32_t v=x[i*2u+c],a=v<0?-v:v;if(a>sig_peak)sig_peak=a;
        int32_t f=v>>4,d=(v-sig_prev[c])>>4;sig_power+=(uint64_t)((int64_t)f*f);sig_diff_power+=(uint64_t)((int64_t)d*d);sig_prev[c]=v;
        if(v&0xff)++sig_low_bits;
    }
    sig_samples+=frames*2u;
}
/* Volume on a 24-bit value (Q16 gain, 64-bit product, rounded). */
/* Volume moved after the EQ (output_process): decoders hand over unscaled 24-bit PCM. */
static inline int32_t gain24(int32_t v) { return v; }
/* A decoder wrote `frames` frames of `channels` interleaved samples at &pcm[pcm_frames*2]:
 * bits 16 = int16 samples (widened exactly to 24-bit), bits 24 = int32 24-bit samples.
 * Volume is applied and mono widened to stereo in place, walking backwards so a source
 * sample is always read before its bytes are overwritten. */
static void decoded_in_place(unsigned frames,unsigned channels,unsigned bits,uint32_t rate) {
    if(!frames || (channels!=1u && channels!=2u) || (bits!=16u && bits!=24u) || (bits==24u && channels!=2u) ||
       pcm_frames+frames>WMP_MAX_GROUP_SAMPLES || (pcm_frames && pcm_rate!=rate)) { ++audio_stats.pcm_bad;return; }
    int32_t *o=&pcm[pcm_frames*2u];current_gain_q24=effective_gain();
    if(bits==16u) {
        const int16_t *in=(const int16_t *)o;
        for(unsigned i=frames;i-->0;) { int32_t l=in[i*channels],r=in[i*channels+channels-1u];o[i*2u]=l*256;o[i*2u+1u]=r*256; }
    }
    signal_note(o,frames);
    for(unsigned i=0;i<frames*2u;i++)o[i]=gain24(o[i]);
    pcm_rate=rate;pcm_frames+=frames;
}
static void decoded(int16_t *data,int samples,int channels,int rate,void *context) {
    (void)context;
    if(samples<0 || (channels!=1 && channels!=2) || pcm_frames+(unsigned)samples>WMP_MAX_GROUP_SAMPLES || (pcm_frames && pcm_rate!=(uint32_t)rate)) { ++audio_stats.pcm_bad;return; }
    pcm_rate=(uint32_t)rate;current_gain_q24=effective_gain();
    for(int i=0;i<samples;i++) { pcm[(pcm_frames+i)*2]=(int32_t)data[i*channels]*256;pcm[(pcm_frames+i)*2+1]=(int32_t)data[i*channels+channels-1]*256; }
    signal_note(&pcm[pcm_frames*2u],(unsigned)samples);
    for(int i=0;i<samples*2;i++)pcm[pcm_frames*2u+(unsigned)i]=gain24(pcm[pcm_frames*2u+(unsigned)i]);
    pcm_frames+=(unsigned)samples;
}
static bool IRAM_ATTR sent(i2s_chan_handle_t channel,i2s_event_data_t *event,void *context) {
    (void)channel;(void)context;int64_t now=esp_timer_get_time();
    ++dma_version;
    if(dma_time) { uint32_t gap=(uint32_t)(now-dma_time);if(gap>dma_gap_max_us)dma_gap_max_us=gap; }
    dma_sent+=(uint32_t)(event->size/frame_bytes);dma_time=now;++dma_events;++dma_version;return false;
}
static bool IRAM_ATTR send_overflow(i2s_chan_handle_t channel,i2s_event_data_t *event,void *context) {
    (void)channel;(void)event;(void)context;++dma_overflows;return false;
}
void wmp_audio_apll_stats(unsigned long query);void wmp_audio_signal_stats(unsigned long query);
void wmp_audio_dma_stats(unsigned long query) {
    wmp_audio_apll_stats(query);wmp_audio_signal_stats(query);
    wmp_telemetry_printf("WMP_DMA query=%08lx submitted=%lu emitted=%lu pending=%u deficit_max=%lu queue_overflow=%lu events=%lu gap_max_us=%lu start_us32=%lu last_us32=%lu block_frames=%lu block_us=%lu descriptors=%lu priority=%u output_bits=%u width_switches=%lu drift_drops=%lu drift_repeats=%lu\n",query,(unsigned long)audio_stats.output_frames,(unsigned long)dma_sent,dma_blocks?dma_blocks->count:0,(unsigned long)dma_deficit_max,(unsigned long)dma_overflows,(unsigned long)dma_events,(unsigned long)dma_gap_max_us,(unsigned long)dma_started_us,(unsigned long)(uint32_t)dma_time,(unsigned long)dma_block_frames,(unsigned long)(output_rate?(uint64_t)dma_block_frames*1000000u/output_rate:0),(unsigned long)dma_descriptors,renderer_task?(unsigned)uxTaskPriorityGet(renderer_task):0,(unsigned)output_bits,(unsigned long)width_switches,(unsigned long)drift_drops,(unsigned long)drift_repeats);
}
static void learn_clock(void) {
    if(native_clock_network()) {
        /* Associated STA TSF is synchronized by the Wi-Fi hardware to the
         * known source AP. The bounded background getter exposes its span. */
        audio_stats.clock_valid=wmp_source_clock()!=0;
        audio_stats.clock_residual_us=audio_stats.clock_fit_us=0;
        return;
    }
    static uint32_t applied_reset;
    beacon b;
    if(applied_reset!=clock_reset_requested) {
        applied_reset=clock_reset_requested;wmp_mac_reset(&clock_unwrap);wmp_beacon_clock_reset(&peer_clock);
        last_beacon_at=0;audio_stats.clock_valid=0;
        while(xQueueReceive(timing,&b,0)==pdTRUE){}
    }
    while(xQueueReceive(timing,&b,0)==pdTRUE) {
        uint64_t local;
        if(!wmp_mac_extend(&clock_unwrap,b.local,&local)) {
            ++audio_stats.clock_bad;wmp_mac_reset(&clock_unwrap);wmp_beacon_clock_reset(&peer_clock);audio_stats.clock_valid=0;
            if(!wmp_mac_extend(&clock_unwrap,b.local,&local))continue;
        }
        last_beacon_at=esp_timer_get_time();
        portENTER_CRITICAL(&trace_lock);
        trace_pairs[trace_next][0]=local;trace_pairs[trace_next][1]=b.source;
        trace_next=(trace_next+1u)%16u;if(trace_count<16u)++trace_count;
        portEXIT_CRITICAL(&trace_lock);
        ++audio_stats.clock_pairs;
        uint32_t rejected=peer_clock.rejected;wmp_beacon_clock_push(&peer_clock,local,b.source);
        audio_stats.clock_valid=peer_clock.valid;if(peer_clock.rejected>=rejected)audio_stats.clock_bad+=peer_clock.rejected-rejected;
        audio_stats.clock_residual_us=peer_clock.jitter_us;audio_stats.clock_fit_us=peer_clock.fit_jitter_us;
        audio_stats.clock_ppm=(int32_t)((peer_clock.ratio-1.0)*1000000.0);
    }
    if(radio_stats.mode!=2u && audio_stats.clock_valid && last_beacon_at && esp_timer_get_time()-last_beacon_at>15000000) {
        ++audio_stats.clock_expirations;audio_stats.clock_valid=0;last_beacon_at=0;
        wmp_mac_reset(&clock_unwrap);wmp_beacon_clock_reset(&peer_clock);
        portENTER_CRITICAL(&packet_lock);wmp_preparation_begin(&peer_preparation,(uint64_t)esp_timer_get_time(),0,0,0,0);++preparation_cold;portEXIT_CRITICAL(&packet_lock);
    }

}
static int64_t local_deadline(uint64_t source,int own) {
    int64_t now=esp_timer_get_time();
    if(own && !wmp_bluetooth_broadcast())return (int64_t)source; /* solo: timestamps are esp_timer */
    if(own || native_clock_network()) { uint64_t clock=wmp_source_clock();return clock?now+(int64_t)source-(int64_t)clock:0; }
    if(!audio_stats.clock_valid || esp_timer_get_time()-last_beacon_at>15000000)return 0;
    if(!wmp_radio_wifi_live())return 0;
    uint32_t raw=esp_wifi_internal_get_mac_clock_time();
    int64_t local_now=(int64_t)peer_clock.ref_local+(int32_t)(raw-(uint32_t)peer_clock.ref_local);
    uint64_t target;if(!wmp_beacon_clock_to_local(&peer_clock,source,&target))return 0;
    return now+(int64_t)target-local_now;
}
/* Follower: esp_timer time at which a source presentation time plays (0 if
 * no valid clock). Task context; reads the fitted clock without locking,
 * as local_deadline() does for playback. */
int64_t wmp_audio_local_deadline(uint64_t presentation) { return local_deadline(presentation,0); }
void wmp_audio_clock_trace(unsigned long query) {
    uint64_t copy[16][2];unsigned count,next;
    portENTER_CRITICAL(&trace_lock);memcpy(copy,trace_pairs,sizeof(copy));count=trace_count;next=trace_next;portEXIT_CRITICAL(&trace_lock);
    for(unsigned j=0;j<count;j++) { unsigned i=(next+16u-count+j)%16u;wmp_telemetry_printf("WMP_CLOCK query=%08lx local=%llu source=%llu\n",query,(unsigned long long)copy[i][0],(unsigned long long)copy[i][1]); }
    wmp_telemetry_printf("WMP_CLOCK_END query=%08lx count=%u\n",query,count);
}
static void configure(uint32_t rate,int local_source,unsigned bits) {
    wmp_crumb(2,(rate<<8)|bits);
    /* The source's128-frame DMA budget is shorter than a native Wi-Fi TX
     * burst. Keep that renderer above Wi-Fi; followers retain RX priority. */
    vTaskPrioritySet(NULL,requested_priority);
    if(dac) { if(audio_stats.running)i2s_channel_disable(dac);i2s_del_channel(dac);dac=0; }
    audio_stats.running=0;output_rate=rate;
    uint32_t epoch=0;int shared=native_clock_network() && wmp_source_clock_shared_epoch(&epoch);
    portENTER_CRITICAL(&packet_lock);renderer_shared_clock=shared;renderer_clock_epoch=epoch;portEXIT_CRITICAL(&packet_lock);
    if(decoder_context)decoder->configure(decoder_context,SBC_MODE_STANDARD,decoded,0);
    i2s_chan_config_t channel=I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0,I2S_ROLE_MASTER);
    /* Receiver output is approximately5ms per descriptor;44.1kHz requires
     * an integer221 samples (5.011ms). Aggregated SBC packets stay unchanged. */
    (void)local_source;dma_block_frames=(rate*5u+500u)/1000u; /* 5 ms blocks x 6 = 30 ms, longer than an AAC group */
    /* Per-codec I2S: 32-bit only for aptX HD (24-bit codec); SBC, AAC and aptX use 16-bit, so
     * the DMA ring is sized for the playing codec and party mode (SBC/AAC-LC) never pays for 32. */
    output_bits=bits==32u?32u:16u;frame_bytes=output_bits==32u?8u:4u;
    /* A 32-bit frame is twice the bytes: half-size descriptors, twice as many, keep the DMA
     * ring at the same duration and the packer block (counted in 4-byte units) unchanged. */
    unsigned hw_frames=output_bits==32u?dma_block_frames/2u:dma_block_frames;
    dma_descriptors=output_bits==32u?12u:6u;
    channel.dma_desc_num=dma_descriptors;channel.dma_frame_num=hw_frames;channel.auto_clear=true;
    ESP_ERROR_CHECK(i2s_new_channel(&channel,&dac,0));
    const i2s_data_bit_width_t width=output_bits==32u?I2S_DATA_BIT_WIDTH_32BIT:I2S_DATA_BIT_WIDTH_16BIT;
    i2s_std_config_t config={.clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(rate),.slot_cfg=I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(width,I2S_SLOT_MODE_STEREO),.gpio_cfg={.mclk=I2S_GPIO_UNUSED,.bclk=27,.ws=25,.dout=26,.din=I2S_GPIO_UNUSED}};
    config.clk_cfg.clk_src=I2S_CLK_SRC_APLL;ESP_ERROR_CHECK(i2s_channel_init_std_mode(dac,&config));
    i2s_event_callbacks_t events={.on_sent=sent,.on_send_q_ovf=send_overflow};ESP_ERROR_CHECK(i2s_channel_register_event_callback(dac,&events,0));
    dma_sent=dma_version=0;dma_time=0;written=0;dma_overflows=dma_events=dma_gap_max_us=dma_started_us=dma_deficit_max=0;
    startup_deadline=0;configASSERT(wmp_pcm_blocks_init(dma_blocks,hw_frames*(frame_bytes/4u)));audio_stats.rate=rate;signal_runtime->drift_ppm_frames=0;
    apll_capture_nominal();
}
/* APLL clock steering. I2S runs from the APLL; instead of resampling PCM,
 * scale the APLL fractional coefficient so the DAC clock itself follows the
 * source timeline. apll = xtal*(4 + N/65536)/(2*(o_div+2)) with
 * N = sdm2<<16 | sdm1<<8 | sdm0 (24-bit fraction), so one sdm0 step is about
 * 1-2.5 ppm. Rev0 silicon ignores sdm0/sdm1: keep the resampler there. */
static volatile uint32_t apll_enabled=1,apll_valid,apll_o_div,apll_n_nominal,apll_updates;static volatile int32_t apll_applied_ppm;static int64_t apll_last_us;
static uint32_t apll_sdm_written; /* the fractional word currently in the APLL */
int wmp_audio_apll(unsigned enabled) { if(enabled>1u)return 0;apll_enabled=enabled;return 1; }
static void apll_capture_nominal(void) {
    esp_chip_info_t chip;esp_chip_info(&chip);
    uint32_t o,s0,s1,s2;clk_ll_apll_get_config(&o,&s0,&s1,&s2);
    apll_o_div=o;apll_n_nominal=(s2<<16)|(s1<<8)|s0;apll_sdm_written=apll_n_nominal;apll_valid=chip.revision>=100u;apll_applied_ppm=0;apll_last_us=0;
}
static int apll_steer(int32_t ppm) {
    if(!apll_enabled || !apll_valid)return 0;
    int64_t now=esp_timer_get_time();
    int32_t delta=ppm-apll_applied_ppm;if(delta<0)delta=-delta;
    /* At most every 100 ms and only for a 2 ppm change: the clock moves in rare, tiny steps. */
    if(delta<2 || (apll_last_us && now-apll_last_us<100000))return 1;
    int64_t base=(int64_t)4*65536+apll_n_nominal;
    int64_t target=base+(base*(int64_t)ppm+(ppm>=0?500000:-500000))/1000000-(int64_t)4*65536;
    if(target<0 || target>((int64_t)63<<16|0xffff))return 0;
    /* Write only the sigma-delta bytes that changed. rtc_clk_apll_coeff_set() also stops and
     * restarts the modulator and recalibrates the APLL (spinning until relock) on every call: done
     * up to 100 times a second that disturbed the DAC master clock continuously, heard as a grainy,
     * low-bitrate sound with every counter clean. The fractional divider retunes in place. */
    uint32_t n=(uint32_t)target,old=apll_sdm_written;
    if(((n>>16)&63u)!=((old>>16)&63u))REGI2C_WRITE_MASK(I2C_APLL,I2C_APLL_DSDM2,(n>>16)&63u);
    if(((n>>8)&255u)!=((old>>8)&255u))REGI2C_WRITE_MASK(I2C_APLL,I2C_APLL_DSDM1,(n>>8)&255u);
    if((n&255u)!=(old&255u))REGI2C_WRITE_MASK(I2C_APLL,I2C_APLL_DSDM0,n&255u);
    apll_sdm_written=n;
    apll_applied_ppm=ppm;apll_last_us=now;++apll_updates;return 1;
}
static int32_t out_gain; /* defined with the output stage below */
static volatile uint32_t dsp_calls,dsp_us_total,dsp_us_max,dsp_frames;
void wmp_audio_signal_stats(unsigned long query) {
    uint64_t mean=sig_samples?sig_power/sig_samples:0u;uint32_t rms=0;
    for(uint32_t bit=1u<<30;bit;bit>>=1)if((uint64_t)(rms|bit)*(rms|bit)<=mean)rms|=bit;   /* integer sqrt */
    rms<<=4;uint32_t hf=sig_power?(uint32_t)(sig_diff_power*1000u/sig_power):0u;
    wmp_telemetry_printf("WMP_SIGNAL query=%08lx samples=%lu peak=%ld rms=%ld hf_permille=%lu low_bits_permille=%lu gain_q24=%ld out_gain_q30=%ld output_bits=%u rate=%lu dsp_calls=%lu dsp_us_total=%lu dsp_us_max=%lu dsp_frames=%lu eq_active=%u%c",query,(unsigned long)sig_samples,(long)sig_peak,(long)rms,
        (unsigned long)hf,(unsigned long)(sig_samples?(uint64_t)sig_low_bits*1000u/sig_samples:0u),(long)current_gain_q24,(long)out_gain,(unsigned)output_bits,(unsigned long)output_rate,
        (unsigned long)dsp_calls,(unsigned long)dsp_us_total,(unsigned long)dsp_us_max,(unsigned long)dsp_frames,(unsigned)eq_active,10);
    dsp_calls=dsp_us_total=dsp_us_max=dsp_frames=0;
    sig_power=sig_diff_power=0;sig_peak=0;sig_samples=sig_low_bits=0;
}
void wmp_audio_apll_stats(unsigned long query) {
    uint32_t o,s0,s1,s2;clk_ll_apll_get_config(&o,&s0,&s1,&s2);
    wmp_telemetry_printf("WMP_APLL query=%08lx enabled=%lu valid=%lu o_div=%lu n_nominal=%lu applied_ppm=%ld updates=%lu sdm_written=%lu sdm_read=%lu%c",query,(unsigned long)apll_enabled,(unsigned long)apll_valid,(unsigned long)apll_o_div,(unsigned long)apll_n_nominal,(long)apll_applied_ppm,(unsigned long)apll_updates,(unsigned long)apll_sdm_written,(unsigned long)((s2<<16)|(s1<<8)|s0),10);
}
static int submit_dma(void *context,const int16_t *data,unsigned frames) {
    int64_t deadline=*(const int64_t *)context;
    size_t consumed=0,bytes=frames*4u;
    if(!audio_stats.running) {
        if(i2s_channel_preload_data(dac,data,bytes,&consumed)!=ESP_OK || consumed!=bytes) { ++audio_stats.write_errors;return 0; }
        while(deadline-esp_timer_get_time()>1500)vTaskDelay(1);
        int64_t remaining=deadline-esp_timer_get_time();if(remaining>0)esp_rom_delay_us((uint32_t)remaining);else ++audio_stats.late;
        dma_started_us=(uint32_t)esp_timer_get_time();ESP_ERROR_CHECK(i2s_channel_enable(dac));audio_stats.running=1;
    } else if(i2s_channel_write(dac,data,bytes,&consumed,100)!=ESP_OK || consumed!=bytes) { ++audio_stats.write_errors;return 0; }
    written+=consumed/frame_bytes;audio_stats.output_frames=(uint32_t)written;return 1;
}
/* Output stage, in place on the 24-bit stereo PCM just before it is packed for I2S. */
/* One band over one channel of the block (stride 2), coefficients and history in registers.
 * Branch-free split accumulator: the five products' high words (mulsh) are summed as int32 and
 * their low words (mull) >> 4 as uint32, so no 64-bit carry chain is built. y = hi*16 + lo>>24;
 * lo's low 24 bits are the carried fraction (error feedback). Dropping each product's 4 lowest
 * bits is about 2^-24 of an output LSB (host check: 121 dB below the 64-bit kernel's output). */
static inline int32_t mul_hi(int32_t a,int32_t b) { return (int32_t)(((int64_t)a*b)>>32); }
static inline uint32_t mul_lo4(int32_t a,int32_t b) { return ((uint32_t)a*(uint32_t)b)>>4; }
static void IRAM_ATTR eq_run(const int32_t *k,eq_history *h,int32_t *p,unsigned frames) {
    const int32_t b0=k[0],b1=k[1],b2=k[2],na1=-k[3],na2=-k[4];
    int32_t x1=h->x1,x2=h->x2,y1=h->y1,y2=h->y2;uint32_t fraction=h->fraction;
    for(int32_t *end=p+2u*frames;p<end;p+=2) {
        int32_t x=*p;
        int32_t hi=mul_hi(b0,x)+mul_hi(b1,x1)+mul_hi(b2,x2)+mul_hi(na1,y1)+mul_hi(na2,y2);
        uint32_t lo=fraction+mul_lo4(b0,x)+mul_lo4(b1,x1)+mul_lo4(b2,x2)+mul_lo4(na1,y1)+mul_lo4(na2,y2);
        int32_t y=hi*16+(int32_t)(lo>>24);fraction=lo&0x00ffffffu;
        x2=x1;x1=x;y2=y1;y1=y;*p=y;
    }
    h->x1=x1;h->x2=x2;h->y1=y1;h->y2=y2;h->fraction=fraction;
}
/* DSPBENCH (diagnostic): cycles for one band over 512 stereo frames, interrupts off, on a
 * scratch buffer; the result is cycles per sample (one channel, one band). */
uint32_t wmp_audio_dsp_bench(void) {
    const unsigned frames=512u;int32_t *buf=heap_caps_malloc(frames*2u*sizeof(int32_t),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);if(!buf)return 0;
    for(unsigned i=0;i<frames*2u;i++)buf[i]=(int32_t)((i*2654435761u)>>9)-4194304;
    eq_history h={0};const int32_t *k=eq_table[2][0][12+7];
    eq_run(k,&h,buf,frames);                                  /* warm the cache */
    static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
    portENTER_CRITICAL(&mux);uint32_t c0=esp_cpu_get_cycle_count();eq_run(k,&h,buf,frames);uint32_t c1=esp_cpu_get_cycle_count();portEXIT_CRITICAL(&mux);
    heap_caps_free(buf);return (c1-c0)/frames;
}
/* Output gain (volume curve x EQ preamp, Q30), ramped over 20 ms so a change never clicks. */
static int32_t out_gain_target,out_gain_step;static uint32_t out_gain_left;static uint8_t out_gain_ready;
static uint32_t dither_state=0x1234567u;static int32_t shape_error[2];
static void output_process_inner(unsigned frames);
static void output_process(unsigned frames) {
    int64_t t0=esp_timer_get_time();output_process_inner(frames);uint32_t us=(uint32_t)(esp_timer_get_time()-t0);
    ++dsp_calls;dsp_us_total+=us;dsp_frames+=frames;if(us>dsp_us_max)dsp_us_max=us;
}
/* EQ in place, then a single fused pass: gain, 24-bit saturation, stereo-pair channel, and the
 * pack to the I2S width. 16-bit output is noise-shaped (first order) with TPDF dither whenever
 * gain or EQ changed the samples; unity volume with a flat EQ stays bit-exact. */
static void IRAM_ATTR output_process_inner(unsigned frames) {
    uint32_t version=eq_version;uint32_t rate=pcm_rate?pcm_rate:44100u;
    if(version!=eq_applied_version || eq_rate!=rate) { eq_select(rate);eq_applied_version=version;memset(eq_h,0,sizeof(eq_h)); }
    for(unsigned k=0;k<eq_band_count;k++) { unsigned b=eq_band_list[k];eq_run(eq_coef[b],&eq_h[b][0],&pcm[0],frames);eq_run(eq_coef[b],&eq_h[b][1],&pcm[1],frames); }
    int32_t target=(int32_t)(((int64_t)effective_gain()*(eq_active?eq_preamp:(1<<30))+(1<<23))>>24); /* Q24 x Q30 -> Q30 */
    if(!out_gain_ready) { out_gain=out_gain_target=target;out_gain_left=0;out_gain_ready=1; }
    else if(target!=out_gain_target) { uint32_t ramp=rate/50u;out_gain_target=target;out_gain_left=ramp;out_gain_step=(int32_t)(((int64_t)target-out_gain)/(int64_t)ramp); }
    unsigned channel=wmp_audio_output_channel();
    int unity=out_gain==(1<<30) && !out_gain_left,dither=!unity || eq_active;
    int32_t g=out_gain,e0=shape_error[0],e1=shape_error[1];if(!dither)e0=e1=0;
    uint32_t seed=dither_state;int32_t *in=pcm;int16_t *d16=(int16_t *)pcm;
    for(unsigned i=0;i<frames;i++) {
        if(out_gain_left) { g+=out_gain_step;if(--out_gain_left==0)g=out_gain_target; }
        int32_t l=in[i*2u],r=in[i*2u+1u];
        if(!unity) { l=(int32_t)(((int64_t)l*g+(1<<29))>>30);r=(int32_t)(((int64_t)r*g+(1<<29))>>30); }
        l=l>8388607?8388607:(l<-8388608?-8388608:l);r=r>8388607?8388607:(r<-8388608?-8388608:r);
        if(channel==WMP_SPEAKER_LEFT)r=l;else if(channel==WMP_SPEAKER_RIGHT)l=r;
        if(output_bits==16u) {
            int32_t sl=l-e0,sr=r-e1,tl=sl,tr=sr;
            if(dither) { seed=seed*1664525u+1013904223u;tl+=(int32_t)(seed&255u)+(int32_t)((seed>>8)&255u)-255;tr+=(int32_t)((seed>>16)&255u)+(int32_t)(seed>>24)-255; }
            int32_t ql=(tl+128)>>8,qr=(tr+128)>>8;
            ql=ql>32767?32767:(ql<-32768?-32768:ql);qr=qr>32767?32767:(qr<-32768?-32768:qr);
            if(dither) { e0=ql*256-sl;e1=qr*256-sr;e0=e0>2048?2048:(e0<-2048?-2048:e0);e1=e1>2048?2048:(e1<-2048?-2048:e1); }
            d16[i*2u]=(int16_t)ql;d16[i*2u+1u]=(int16_t)qr;   /* only bytes already read */
        } else { in[i*2u]=(int32_t)((uint32_t)l<<8);in[i*2u+1u]=(int32_t)((uint32_t)r<<8); }
    }
    out_gain=g;shape_error[0]=e0;shape_error[1]=e1;dither_state=seed;
}
static void write_pcm(unsigned samples,int64_t deadline,int32_t schedule_ppm) {
    uint32_t sent_count=0,a,b;int64_t stamp=0;
    do { a=dma_version;sent_count=dma_sent;stamp=dma_time;b=dma_version; }while((a&1u)||a!=b);
    if(audio_stats.running && stamp) {
        int64_t pending=(int64_t)dma_blocks->count*4/(int64_t)frame_bytes; /* packer counts 4-byte units */
        int64_t prediction=stamp+((int64_t)written-(int64_t)sent_count+pending)*1000000/(int64_t)output_rate;
        int32_t error=(int32_t)(prediction-deadline);audio_stats.phase_us=error;
        int32_t correction=schedule_ppm+error/10;if(correction>2400)correction=2400;if(correction< -2400)correction= -2400;
        audio_stats.correction_ppm=correction;
        /* Move the DAC clock (APLL) so PCM is never resampled. Where the APLL cannot be
         * steered (rev0 silicon), drop or repeat a single frame as the drift accumulates. */
        if(!apll_steer(correction)) {
            pcm_runtime *r=signal_runtime;r->drift_ppm_frames+=correction*(int32_t)samples;
            while(r->drift_ppm_frames>=1000000 && samples>1u) { --samples;r->drift_ppm_frames-=1000000;++drift_drops; }
            if(r->drift_ppm_frames<=-1000000 && samples && samples<WMP_MAX_GROUP_SAMPLES) {
                pcm[samples*2u]=pcm[(samples-1u)*2u];pcm[samples*2u+1u]=pcm[(samples-1u)*2u+1u];++samples;r->drift_ppm_frames+=1000000;++drift_repeats;
            }
            if(r->drift_ppm_frames< -4000000)r->drift_ppm_frames= -4000000;
        }
        if(sent_count>written) {
            /* The DMA ran dry and played auto-cleared silence. Those frames are gone: count them as
             * consumed so later blocks neither report a false underrun nor skew the playout clock. */
            ++audio_stats.underrun;uint32_t deficit=(uint32_t)(sent_count-written);if(deficit>dma_deficit_max)dma_deficit_max=deficit;
            written=sent_count;
        }
    }
    output_process(samples); /* EQ, gain and the pack to the I2S width (see output_process_inner) */
    unsigned count=samples*(frame_bytes/4u);
    if(!audio_stats.running && !startup_deadline)startup_deadline=deadline;
    int64_t start=audio_stats.running?deadline:startup_deadline;
    wmp_pcm_blocks_push(dma_blocks,(const int16_t *)pcm,count,submit_dma,&start);
}
static void release_payload(unsigned index) {
    audio_item *item=&items[index];
    if((packet_pool.free&(UINT64_C(1)<<index)) && item->payload) {
        int ok=wmp_payload_free(&payload_pool,(unsigned)(item->payload-payload_storage),item->header.payload_bytes);configASSERT(ok);item->payload=NULL;
    }
}
static void release_item(unsigned index,uint64_t *occupied) {
    portENTER_CRITICAL(&packet_lock);int ok=wmp_frame_pool_release(&packet_pool,index);if(ok)release_payload(index);portEXIT_CRITICAL(&packet_lock);
    configASSERT(ok);*occupied&=~(UINT64_C(1)<<index);
}
static void release_all(uint64_t *occupied) {
    for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if(*occupied&(UINT64_C(1)<<i)) { wmp_radio_delivery(&items[i].header,WMP_D_DISCARD,0);release_item(i,occupied); }
}
static void classify_late(const audio_item *item,uint64_t next_sample,uint32_t rate,unsigned samples) {
    wmp_radio_delivery(&item->header,WMP_D_LATE,0);
    fault_record(1u,item->header.generation,item->header.first_sample,item->header.presentation_us,item->arrived,item->header.sample_count);
    int64_t deadline=local_deadline(item->header.presentation_us,item->local);
    if(deadline && item->arrived<=deadline-2000)++audio_stats.buffered_late;
    else ++audio_stats.input_late;
    if(!item->local && samples && next_sample>item->header.first_sample) {
        uint64_t age=(next_sample-item->header.first_sample+samples-1u)/samples;
        wmp_radio_late_report(item->header.generation,(unsigned)(age>32u?32u:age),rate,samples);
    }
}
static int codec_load(void) {
    if(wmp_codecs_active())wmp_codecs_close(); /* one decoder at a time */
    if(decoder_context) { last_codec_us=esp_timer_get_time();return 1; }
    btstack_sbc_decoder_bluedroid_t *state=heap_caps_calloc(1,sizeof(*state),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!state) { ++resource_failures;return 0; }
    decoder_context=state;decoder=btstack_sbc_decoder_bluedroid_init_instance(state);
    decoder->configure(state,SBC_MODE_STANDARD,decoded,0);last_codec_us=esp_timer_get_time();++codec_starts;return 1;
}
static void codec_release(void) {
    if(decoder_context) { heap_caps_free(decoder_context);decoder_context=NULL;decoder=NULL;++codec_stops; }
}
static void player(void *unused) {
    (void)unused;uint64_t occupied=0;uint32_t applied_preparation=0;
    int64_t last_arrival_us=esp_timer_get_time();
    uint64_t generation=0,queue_epoch=0,anchor_sample=0,anchor_time=0,next_sample=0,previous_pts=0,previous_sample=0;uint32_t group_samples=512,frame_samples=128,rate=0;int own=0,was_silent=0;int32_t schedule_ppm=0;
    while(!resources_ready)ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
    for(;;) {
        audio_stack_free=(uint32_t)uxTaskGetStackHighWaterMark(0);
        if(uxTaskPriorityGet(NULL)!=requested_priority)vTaskPrioritySet(NULL,requested_priority);
        portENTER_CRITICAL(&packet_lock);unsigned stopping=resources_stop;if(stopping)resources_ready=0;portEXIT_CRITICAL(&packet_lock);
        if(stopping) {
            if(dac) { if(audio_stats.running)i2s_channel_disable(dac);i2s_del_channel(dac);dac=NULL; }
            audio_stats.running=0;release_all(&occupied);
            for(;;) { portENTER_CRITICAL(&packet_lock);int i=wmp_frame_pool_take(&packet_pool);portEXIT_CRITICAL(&packet_lock);if(i<0)break;wmp_radio_delivery(&items[i].header,WMP_D_DISCARD,3);release_item((unsigned)i,&occupied); }
            portENTER_CRITICAL(&packet_lock);int quiescent=wmp_frame_pool_quiescent(&packet_pool);portEXIT_CRITICAL(&packet_lock);
            if(!quiescent) { vTaskDelay(1);continue; }
            codec_release();wmp_codecs_close();play_codec=0xffu;heap_caps_free(signal_runtime);heap_caps_free(dma_blocks);heap_caps_free(payload_storage);heap_caps_free(items);
            portENTER_CRITICAL(&packet_lock);signal_runtime=NULL;dma_blocks=NULL;payload_storage=NULL;items=NULL;renderer_shared_clock=0;wmp_preparation_forget(&peer_preparation);wmp_frame_pool_init(&packet_pool);wmp_payload_pool_init(&payload_pool);portEXIT_CRITICAL(&packet_lock);
            ++resource_stops;audio_stack_free=0;resources_stop=0;audio_stats.clock_valid=0;wmp_audio_clock_reset();__atomic_store_n(&renderer_task,NULL,__ATOMIC_RELEASE);vTaskDelete(NULL);return;
        }
        if(decoder_context && esp_timer_get_time()-last_codec_us>2000000)codec_release();
        /* Free an idle decoder after 2 s, or after 100 ms once broadcast is on (a solo-only codec is
         * being handed over to SBC and the party needs that RAM). */
        if(wmp_codecs_active() && esp_timer_get_time()-last_codec_us>(wmp_bluetooth_broadcast()?100000:2000000))wmp_codecs_close();
        learn_clock();
        uint32_t requested;uint64_t ready;
        portENTER_CRITICAL(&packet_lock);requested=peer_preparation.version;ready=peer_preparation.ready_us;portEXIT_CRITICAL(&packet_lock);
        if(requested!=applied_preparation) {
            applied_preparation=requested;if(dac && audio_stats.running)i2s_channel_disable(dac);
            audio_stats.running=0;generation=queue_epoch=0;
            /* Packets admitted after the reset request can already be held
             * here. Purge old admissions, preserving the new epoch's opening. */
            for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if((occupied&(UINT64_C(1)<<i)) && items[i].preparation!=requested) { wmp_radio_delivery(&items[i].header,WMP_D_DISCARD,1);release_item(i,&occupied); }
        }
        audio_stats.preparing=radio_stats.mode==2u?radio_stats.preparing:(local_role()?0u:((uint64_t)esp_timer_get_time()<ready || !audio_stats.clock_valid));
        /* Always expose every ready packet to the reorder search. Future
         * packets cannot hide an urgent recovery behind a full6-slot array. */
        for(unsigned intake=0;intake<WMP_FRAME_SLOTS;intake++) {
            portENTER_CRITICAL(&packet_lock);int index=wmp_frame_pool_take(&packet_pool);uint32_t latest=peer_preparation.version;portEXIT_CRITICAL(&packet_lock);
            if(index<0)break;
            unsigned i=(unsigned)index;audio_item *incoming=&items[i];
            if(incoming->preparation!=latest) { wmp_radio_delivery(&incoming->header,WMP_D_DISCARD,1);release_item(i,&occupied);++audio_stats.preparation_skips;continue; }
            /* Preload only SBC and only if no other decoder is open: never close a decoder whose
             * groups are still queued ahead of this one (that would reallocate it per packet). */
            if(incoming->header.type==WMP_AUDIO && !incoming->header.codec && !wmp_codecs_active())codec_load();
            if((incoming->local!=0)!=local_role()) { wmp_radio_delivery(&incoming->header,WMP_D_DISCARD,2);release_item(i,&occupied);continue; }
            if(queue_epoch && incoming->header.generation!=queue_epoch) {
                if(dac && audio_stats.running)i2s_channel_disable(dac);
                audio_stats.running=0;generation=0;release_all(&occupied);
            }
            queue_epoch=incoming->header.generation;last_arrival_us=esp_timer_get_time();
            int64_t waited=esp_timer_get_time()-incoming->arrived;
            if(waited>audio_stats.queue_wait_max_us)audio_stats.queue_wait_max_us=(uint32_t)waited;
            int64_t when=local_deadline(incoming->header.presentation_us,incoming->local);
            if(when) {
                int64_t slack=when-incoming->arrived;
                ++audio_stats.arrivals;if(slack<0)++audio_stats.arrival_negative;
                if(slack<5000)++audio_stats.arrival_under5;
                if(slack<audio_stats.arrival_min_us || audio_stats.arrivals==1u)audio_stats.arrival_min_us=(int32_t)slack;
            }
            occupied|=UINT64_C(1)<<i;
        }
        portENTER_CRITICAL(&packet_lock);int preparation_changed=peer_preparation.version!=applied_preparation;portEXIT_CRITICAL(&packet_lock);
        if(preparation_changed)continue;
        if(!local_role() && audio_stats.preparing) {
            if(dac && audio_stats.running)i2s_channel_disable(dac);
            audio_stats.running=0;generation=queue_epoch=0;
            audio_stats.preparation_skips+=(unsigned)__builtin_popcountll(occupied);
            release_all(&occupied);vTaskDelay(1);continue;
        }
        unsigned held=(unsigned)__builtin_popcountll(occupied);if(held>audio_stats.held_max)audio_stats.held_max=held;
        /* No master: once nothing has arrived for 0.5 s and nothing is queued, stop the output and
         * re-anchor on the next packet instead of concealing silence for seconds. A paused phone is
         * unaffected: the master keeps sending silence groups. */
        if(generation && !occupied && radio_stats.mode!=2u && esp_timer_get_time()-last_arrival_us>500000) {
            if(dac && audio_stats.running)i2s_channel_disable(dac);
            audio_stats.running=0;generation=queue_epoch=0;++audio_stats.idle_stops;vTaskDelay(1);continue;
        }
        /* Role changes must be observed before consulting the old clock.
         * In particular the old source AP clock disappears on withdrawal. */
        if(generation && own!=local_role()) {
            if(dac && audio_stats.running)i2s_channel_disable(dac);
            audio_stats.running=0;generation=0;
            for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if((occupied&(UINT64_C(1)<<i)) && (items[i].local!=0)!=local_role()) { wmp_radio_delivery(&items[i].header,WMP_D_DISCARD,2);release_item(i,&occupied); }
        }
        int earliest=-1;uint64_t first=UINT64_MAX;
        for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if((occupied&(UINT64_C(1)<<i)) && items[i].header.first_sample<first) { earliest=(int)i;first=items[i].header.first_sample; }
        if(!generation) {
            if(earliest<0) { vTaskDelay(1);continue; }
            audio_item *item=&items[earliest];wmp_header *h=&item->header;
            int64_t deadline=local_deadline(h->presentation_us,item->local);
            if(!deadline) { wmp_radio_delivery(h,WMP_D_DISCARD,4);release_item((unsigned)earliest,&occupied);++audio_stats.clock_unavailable_skips;continue; }
            /* A later group may arrive first during takeover/late joining.
             * Arming its DAC start now blocks intake while earlier, on-time
             * copies arrive. Keep the opening reorderable until50ms before
             * that group's unchanged presentation time; codec state is already
             * preloaded and this leaves DMA/configuration headroom. */
            if(!item->local && deadline-esp_timer_get_time()>50000) { vTaskDelay(1);continue; }
            if(deadline<esp_timer_get_time()+3000) { wmp_radio_delivery(h,WMP_D_LATE,1);release_item((unsigned)earliest,&occupied);++audio_stats.late;++audio_stats.startup_skips;continue; }
            generation=h->generation;anchor_sample=next_sample=h->first_sample;anchor_time=h->presentation_us;group_samples=h->sample_count;rate=h->sample_rate;own=item->local;
            configure(rate,own,h->codec==WMP_CODEC_APTX_HD?32u:16u);audio_stats.phase_us=0;
            previous_pts=previous_sample=0;schedule_ppm=0;frame_samples=128;
        }
        int selected=-1;
        for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if(occupied&(UINT64_C(1)<<i)) {
            if(items[i].header.generation!=generation) { generation=0;break; }
            if(items[i].header.first_sample<next_sample) { classify_late(&items[i],next_sample,rate,group_samples);release_item(i,&occupied);++audio_stats.late;continue; }
            if(items[i].header.first_sample==next_sample)selected=(int)i;
        }
        if(!generation)continue;
        /* Follow the source's immutable per-packet presentation time. A common
         * latency slew must not be replaced by an old nominal-rate anchor. */
        if(selected>=0) {
            uint64_t pts=items[selected].header.presentation_us;
            if(previous_pts && pts>previous_pts && next_sample>previous_sample) {
                int64_t nominal=(int64_t)((next_sample-previous_sample)*1000000u/rate),actual=(int64_t)(pts-previous_pts);
                /* A common timeline step is handled by realignment, not as a
                 * rate change: keep it out of the feed-forward estimate. */
                int64_t feed=nominal>0 && llabs(nominal-actual)<5000?(nominal-actual)*1000000/nominal:schedule_ppm;
                if(feed>2000)feed=2000;
                if(feed< -2000)feed= -2000;
                schedule_ppm+=(int32_t)(feed-schedule_ppm)/4;
            }
            previous_pts=pts;previous_sample=next_sample;anchor_sample=next_sample;anchor_time=pts;
        }
        uint64_t source_time;
        if(!wmp_sample_time(anchor_time,anchor_sample,rate,next_sample,&source_time)) { generation=0;continue; }
        int64_t deadline=local_deadline(source_time,own);
        if(!deadline) {
            if(dac && audio_stats.running)i2s_channel_disable(dac);
            audio_stats.running=0;generation=0;release_all(&occupied);vTaskDelay(1);continue;
        }
        /* Fill ahead whenever real audio is available. For a missing group,
         * allow recovery copies until close to its actual presentation time. */
        /* The retained tail belongs to the preceding, already received audio.
         * It starts before this missing group's timestamp, so waiting until
         * only2ms before that timestamp can starve the DMA queue first. */
        uint32_t refill_guard=wmp_pcm_blocks_guard_us(dma_blocks,rate,2000u+(uint32_t)((uint64_t)dma_block_frames*1000000u/rate));
        if(selected<0 && deadline-esp_timer_get_time()>(int64_t)refill_guard) { vTaskDelay(1);continue; }
        /* Timeline realignment. Resampling corrects at most 2.4ms/s, so a
         * common timeline step (late-report growth) would otherwise leave
         * this output on the old schedule for tens of seconds while every
         * arriving group is already behind the playback cursor. Beyond the
         * thresholds, step once: fill with the faded concealment when output
         * is early, drop one group when it is late. Every sink follows the
         * same presentation times, so all sinks step together. */
        if(audio_stats.running && audio_stats.phase_us< -20000) {
            uint32_t us=(uint32_t)-audio_stats.phase_us;if(us>100000u)us=100000u;
            unsigned frames=(unsigned)((uint64_t)us*rate/1000000u);if(frames>WMP_MAX_GROUP_SAMPLES)frames=WMP_MAX_GROUP_SAMPLES;
            if(frames) {
                plc_conceal(frames);write_pcm(frames,deadline,schedule_ppm);
                uint32_t padded=(uint32_t)((uint64_t)frames*1000000u/rate);audio_stats.phase_us+=(int32_t)padded;
                ++audio_stats.realign_pad;audio_stats.realign_pad_ms+=padded/1000u;continue;
            }
        }
        if(audio_stats.running && audio_stats.phase_us>30000) {
            unsigned skip=selected>=0?group_samples:frame_samples;
            if(selected>=0) { wmp_radio_delivery(&items[selected].header,WMP_D_DISCARD,5);release_item((unsigned)selected,&occupied); }
            /* Bridge the cut with a short faded continuation; the following
             * audio fades in, so the step is not a hard discontinuity. */
            unsigned bridge=skip>256u?128u:skip/2u;next_sample+=skip;
            if(bridge) { plc_conceal(bridge);write_pcm(bridge,deadline,schedule_ppm); }
            audio_stats.phase_us-=(int32_t)((uint64_t)(skip-bridge)*1000000u/rate);++audio_stats.realign_skip;continue;
        }
        pcm_frames=0;pcm_rate=rate;
        int decode_lowered=selected>=0 && requested_priority>WMP_DECODE_PRIORITY;
        if(decode_lowered)vTaskPrioritySet(NULL,WMP_DECODE_PRIORITY); /* loop top restores it on any early continue */
        if(selected>=0) {
            audio_item *item=&items[selected];const uint8_t *frame;uint16_t bytes;
            group_samples=item->header.sample_count;
            if(item->header.type==WMP_AUDIO && item->header.codec!=play_codec) {
                /* Codec switch at the play position: drop the previous decoder and its
                 * concealment history, so the new codec starts clean and fades in. */
                if(item->header.codec)codec_release();else wmp_codecs_close();
                plc_restart();play_codec=item->header.codec;
                unsigned bits=play_codec==WMP_CODEC_APTX_HD?32u:16u; /* per-codec I2S width, see configure */
                if(dac && bits!=output_bits) { configure(rate,own,bits);++width_switches; }
            }
            if(item->header.type==WMP_SILENCE) {
                /* Deliberate silence after audio fades out like a gap would. */
                if(signal_runtime->fade_in)memset(pcm,0,group_samples*8u);else plc_conceal(group_samples);
                pcm_frames=group_samples;++audio_stats.silence_groups;audio_stats.silence_frames+=group_samples;was_silent=1;
            } else if(item->header.codec) {
                /* AAC / aptX: the phone's original units, decoded by this codec's lazily opened decoder. */
                uint8_t codec=item->header.codec;
                if(!wmp_codecs_open(codec,item->header.sample_rate,item->header.channels))++audio_stats.codec_bad;
                else {
                    last_codec_us=esp_timer_get_time();was_silent=0;
                    for(unsigned f=0;f<item->header.frame_count;f++) {
                        if(!wmp_group_frame_codec(codec,item->payload,item->header.payload_bytes,item->header.frame_count,f,&frame,&bytes)) { ++audio_stats.codec_bad;break; }
                        uint16_t frames=0;uint8_t channels=0;int64_t decode_at=esp_timer_get_time();
                        uint8_t bits=0;
                        int ok=wmp_codecs_decode(frame,bytes,&pcm[pcm_frames*2u],(WMP_MAX_GROUP_SAMPLES-pcm_frames)*8u,&frames,&channels,&bits);
                        uint32_t decode_us=(uint32_t)(esp_timer_get_time()-decode_at);
                        portENTER_CRITICAL(&packet_lock);++decode_calls;decode_wall_us+=decode_us;if(decode_us>decode_max_us)decode_max_us=decode_us;if(decode_us<decode_min_us)decode_min_us=decode_us;if(decode_us>1000u)++decode_over1ms;portEXIT_CRITICAL(&packet_lock);
                        if(ok) { decoded_in_place(frames,channels,bits,item->header.sample_rate);++audio_stats.decoded; }
                        else ++audio_stats.codec_bad;
                    }
                }
            } else if(!codec_load()) {
                vTaskDelay(1);continue;
            } else if(was_silent) {
                decoder->configure(decoder_context,SBC_MODE_STANDARD,decoded,0);was_silent=0;
            }
            for(unsigned f=0;!item->header.codec && f<item->header.frame_count;f++) {
                if(!wmp_group_frame(item->payload,item->header.payload_bytes,item->header.frame_count,f,&frame,&bytes)) { ++audio_stats.codec_bad;break; }
                wmp_sbc_info info;if(wmp_sbc_info_read(frame,bytes,&info))frame_samples=info.samples;
                int before=decoder_context->good_frames_nr;int64_t decode_at=esp_timer_get_time();
                decoder->decode_signed_16(decoder_context,0,frame,bytes);
                uint32_t decode_us=(uint32_t)(esp_timer_get_time()-decode_at);
                portENTER_CRITICAL(&packet_lock);++decode_calls;decode_wall_us+=decode_us;if(decode_us>decode_max_us)decode_max_us=decode_us;if(decode_us<decode_min_us)decode_min_us=decode_us;if(decode_us>1000u)++decode_over1ms;portEXIT_CRITICAL(&packet_lock);
                if(decoder_context->good_frames_nr==before)++audio_stats.codec_bad;else ++audio_stats.decoded;
            }
        }
        if(decode_lowered)vTaskPrioritySet(NULL,requested_priority);
        unsigned advance=selected<0?frame_samples:group_samples;
        if(selected<0) {
            /* A source discontinuity may be fewer than four codec frames.
             * Fill exactly that gap, rather than skipping past every future
             * packet forever on a misaligned512-sample grid. */
            for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if((occupied&(UINT64_C(1)<<i)) && items[i].header.first_sample>next_sample && items[i].header.first_sample-next_sample<advance)advance=(unsigned)(items[i].header.first_sample-next_sample);
        }
        int decoded_intact=pcm_frames==advance && pcm_rate==rate;
        if(!decoded_intact) {
            if(selected<0) { ++audio_stats.concealed_missing;audio_stats.last_missing_sample=(uint32_t)next_sample;fault_record(0u,generation,next_sample,source_time,0,advance); }
            else ++audio_stats.concealed_decode;
            plc_conceal(advance);pcm_frames=advance;++audio_stats.concealed;
        } else if(selected>=0 && items[selected].header.type==WMP_AUDIO) {
            plc_audio(pcm_frames);
            if(items[selected].header.flags&WMP_FLAG_REPLAY)++audio_stats.replay_played;
        }
        if(selected>=0)wmp_radio_delivery(&items[selected].header,WMP_D_DECODE,decoded_intact);
        portENTER_CRITICAL(&packet_lock);preparation_changed=peer_preparation.version!=applied_preparation;portEXIT_CRITICAL(&packet_lock);
        if(preparation_changed)continue;
        write_pcm(pcm_frames,deadline,schedule_ppm);
        if(selected>=0)release_item((unsigned)selected,&occupied);
        next_sample+=advance;
    }
}
unsigned wmp_audio_borrow(uint64_t generation,uint64_t sequence,wmp_header *header,const uint8_t **payload) {
    if(!items || !resources_ready || !header || !payload)return 0;
    int index=-1;
    portENTER_CRITICAL(&packet_lock);
    if(!resources_ready || !items) { portEXIT_CRITICAL(&packet_lock);return 0; }
    uint64_t available=packet_pool.ready|packet_pool.held|packet_pool.retired;
    for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if((available&(UINT64_C(1)<<i)) && items[i].local &&
       items[i].header.generation==generation && items[i].header.sequence==sequence && wmp_frame_pool_pin(&packet_pool,i)) { index=(int)i;break; }
    portEXIT_CRITICAL(&packet_lock);
    if(index<0)return 0;
    *header=items[index].header;*payload=items[index].payload;return (unsigned)index+1u;
}
void wmp_audio_unshare(unsigned reference) {
    unsigned repair=reference&WMP_AUDIO_REPAIR_REF;reference&=~WMP_AUDIO_REPAIR_REF;
    configASSERT(reference && reference<=WMP_FRAME_SLOTS);
    portENTER_CRITICAL(&packet_lock);int ok=repair?wmp_frame_pool_unpin_repair(&packet_pool,reference-1u):wmp_frame_pool_unpin(&packet_pool,reference-1u);if(ok)release_payload(reference-1u);portEXIT_CRITICAL(&packet_lock);configASSERT(ok);
}
int wmp_audio_borrow_repair(uint64_t generation,uint64_t sequence,wmp_header *header,const uint8_t **payload,unsigned *reference) {
    if(!header || !payload || !reference)return 0;
    int result=0;*reference=0;
    portENTER_CRITICAL(&packet_lock);
    if(resources_ready && items) {
        uint64_t available=packet_pool.ready|packet_pool.held|packet_pool.retired;
        for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)if((available&(UINT64_C(1)<<i)) && items[i].local && items[i].header.generation==generation && items[i].header.sequence==sequence) {
            result=wmp_frame_pool_pin_repair_limit(&packet_pool,i,repair_loan_limit);
            if(result==1) { *header=items[i].header;*payload=items[i].payload;*reference=(i+1u)|WMP_AUDIO_REPAIR_REF; }
            break;
        }
    }
    portEXIT_CRITICAL(&packet_lock);return result;
}
int wmp_audio_cached(uint64_t generation,uint64_t sequence,wmp_header *header,uint8_t *payload,unsigned capacity) {
    const uint8_t *shared;unsigned reference=wmp_audio_borrow(generation,sequence,header,&shared);if(!reference)return 0;
    int ok=header->payload_bytes<=capacity;if(ok && header->payload_bytes)memcpy(payload,shared,header->payload_bytes);
    wmp_audio_unshare(reference);return ok;
}
static unsigned offer_packet(const wmp_header *h,const uint8_t *payload,int local,int share,const uint8_t **shared_payload) {
    if(!h || !h->sample_count || h->sample_count>WMP_MAX_GROUP_SAMPLES || h->payload_bytes>WMP_LIVE_PAYLOAD)return 0;
    portENTER_CRITICAL(&packet_lock);
    if(!resources_ready) { resources_wanted=1;portEXIT_CRITICAL(&packet_lock);wmp_audio_service_resources();return 0; }
    last_offer_us=(uint32_t)esp_timer_get_time();resources_stop=0;
    portEXIT_CRITICAL(&packet_lock);
    if(!local) {
        portENTER_CRITICAL(&packet_lock);int allowed=wmp_preparation_allows(&peer_preparation,(uint64_t)esp_timer_get_time(),audio_stats.clock_valid);portEXIT_CRITICAL(&packet_lock);
        if(!allowed) { ++audio_stats.preparation_skips;return 2; }
    }
    if(h->type==WMP_AUDIO) { if(!payload || !h->payload_bytes || !h->frame_count || h->frame_count>WMP_MAX_GROUP_FRAMES)return 0; }
    else if(h->type!=WMP_SILENCE || h->payload_bytes || h->frame_count)return 0;
    portENTER_CRITICAL(&packet_lock);
    if(!resources_ready) { resources_wanted=1;portEXIT_CRITICAL(&packet_lock);return 0; }
    uint32_t admitted_preparation=peer_preparation.version;
    /* Leave two additional receive slots beyond the source62-packet limit.
     * Source DMA and follower clock estimates need not release the same slot
     * on the same microsecond. This reserve prevents that skew losing copies. */
    unsigned bytes=h->payload_bytes,aligned_bytes=(bytes+3u)&~3u;
    int reserve=local && (__builtin_popcountll(packet_pool.free)<=2 || (bytes && payload_pool.available<3u*aligned_bytes));
    int index=reserve?-1:wmp_frame_pool_acquire(&packet_pool),offset=-1;
    if(index>=0 && bytes) {
        offset=wmp_payload_alloc(&payload_pool,bytes);
        if(offset<0) { int cancelled=wmp_frame_pool_cancel(&packet_pool,(unsigned)index);configASSERT(cancelled);index=-1; }
    }
    portEXIT_CRITICAL(&packet_lock);
    if(index<0) { if(local)++audio_stats.source_deferred;else ++audio_stats.queue_full;return 0; }
    audio_item *item=&items[index];item->header=*h;item->arrived=esp_timer_get_time();item->local=(uint8_t)local;item->payload=bytes?payload_storage+offset:NULL;item->preparation=admitted_preparation;
    if(h->payload_bytes)memcpy(item->payload,payload,h->payload_bytes);
    portENTER_CRITICAL(&packet_lock);
    if(!resources_ready || !wmp_preparation_keeps(&peer_preparation,admitted_preparation)) {
        int cancelled=wmp_frame_pool_cancel(&packet_pool,(unsigned)index);configASSERT(cancelled);release_payload((unsigned)index);++preparation_retries;portEXIT_CRITICAL(&packet_lock);return 0;
    }
    int published=wmp_frame_pool_publish(&packet_pool,(unsigned)index);
    int pinned=!share || wmp_frame_pool_pin(&packet_pool,(unsigned)index);portEXIT_CRITICAL(&packet_lock);configASSERT(published && pinned);
    if(share)*shared_payload=item->payload;
    ++audio_stats.queued;return share?(unsigned)index+1u:1u;
}
int wmp_audio_offer(const wmp_header *h,const uint8_t *payload,int local) { return (int)offer_packet(h,payload,local,0,0); }
unsigned wmp_audio_share(const wmp_header *h,const uint8_t *payload,const uint8_t **shared_payload) {
    return shared_payload?offer_packet(h,payload,1,1,shared_payload):0u;
}
void wmp_audio_memory_stats(unsigned long query) {
    unsigned counts[9]={0},payload_available,payload_largest=0,payload_extents;
    uint64_t total_decode,oldest_sequence=0;uint32_t calls,maximum,minimum,over1ms,oldest_original_us=0,oldest_retired_us=0,original_refs=0;
    portENTER_CRITICAL(&packet_lock);int64_t now=esp_timer_get_time();
    counts[0]=items?(unsigned)__builtin_popcountll(packet_pool.free):0;counts[1]=(unsigned)__builtin_popcountll(packet_pool.pending);
    counts[2]=(unsigned)__builtin_popcountll(packet_pool.ready);counts[3]=(unsigned)__builtin_popcountll(packet_pool.held);
    counts[4]=(unsigned)__builtin_popcountll(packet_pool.borrowed);counts[5]=(unsigned)__builtin_popcountll(packet_pool.retired);
    for(unsigned i=0;i<WMP_FRAME_SLOTS;i++)counts[6]+=packet_pool.references[i];
    counts[7]=(unsigned)__builtin_popcountll(packet_pool.repair_pending);counts[8]=(unsigned)__builtin_popcountll(packet_pool.retired&packet_pool.repair_pending);
    payload_available=items?payload_pool.available:0;payload_extents=items?payload_pool.count:0;
    for(unsigned i=0;items && i<payload_pool.count;i++)if(payload_pool.length[i]>payload_largest)payload_largest=payload_pool.length[i];
    for(unsigned i=0;items && i<WMP_FRAME_SLOTS;i++)if(packet_pool.references[i]) {
        unsigned repairs=(unsigned)((packet_pool.repair_pending>>i)&1u),originals=packet_pool.references[i]-repairs;
        if(originals) {
            original_refs+=originals;uint64_t elapsed=(uint64_t)(now-items[i].arrived);uint32_t age=elapsed>UINT32_MAX?UINT32_MAX:(uint32_t)elapsed;
            if(age>oldest_original_us) { oldest_original_us=age;oldest_sequence=items[i].header.sequence; }
            if((packet_pool.retired&(UINT64_C(1)<<i)) && age>oldest_retired_us)oldest_retired_us=age;
        }
    }
    total_decode=decode_wall_us;calls=decode_calls;maximum=decode_max_us;minimum=decode_min_us==UINT32_MAX?0u:decode_min_us;over1ms=decode_over1ms;
    portEXIT_CRITICAL(&packet_lock);
    wmp_telemetry_printf("WMP_POOL query=%08lx free=%u pending=%u ready=%u held=%u borrowed=%u retired=%u refs=%u bytes=%u audio_stack_free=%u repair_pending=%u retired_repair=%u\n",query,
        counts[0],counts[1],counts[2],counts[3],counts[4],counts[5],counts[6],items?(unsigned)(WMP_FRAME_SLOTS*sizeof(audio_item)+WMP_PAYLOAD_BYTES+sizeof(payload_pool)):0u,
        (unsigned)audio_stack_free,counts[7],counts[8]);
    wmp_telemetry_printf("WMP_PAYLOAD query=%08lx capacity=%u available=%u largest=%u extents=%u slots=%u\n",query,items?WMP_PAYLOAD_BYTES:0u,payload_available,payload_largest,payload_extents,WMP_FRAME_SLOTS);
    wmp_telemetry_printf("WMP_LOANS query=%08lx original_refs=%lu oldest_original_us=%lu oldest_sequence=%llu oldest_retired_us=%lu age_from_admission=1\n",query,(unsigned long)original_refs,(unsigned long)oldest_original_us,(unsigned long long)oldest_sequence,(unsigned long)oldest_retired_us);
    wmp_telemetry_printf("WMP_DECODE query=%08lx calls=%lu total_wall_us=%llu max_wall_us=%lu min_wall_us=%lu over1ms=%lu\n",query,(unsigned long)calls,(unsigned long long)total_decode,(unsigned long)maximum,(unsigned long)minimum,(unsigned long)over1ms);
}
int wmp_audio_beacon(uint64_t source,uint32_t local) { beacon b={.source=source,.local=local};return timing && xQueueSend(timing,&b,0)==pdTRUE; }
void wmp_audio_service_resources(void) {
    int64_t now=esp_timer_get_time();
    if(resources_ready) {
        /* Read activity and the clock together. A newer cross-core offer must
         * never wrap an unsigned age into an apparent multi-minute idle gap. */
        portENTER_CRITICAL(&packet_lock);
        uint32_t age=(uint32_t)esp_timer_get_time()-last_offer_us;
        if(resources_ready && !bt_stats.streaming && !radio_stats.mode && !radio_stats.rx_active && !radio_stats.rx_claim && age>5000000u && age<UINT32_C(0x80000000))resources_stop=1;
        portEXIT_CRITICAL(&packet_lock);
        return;
    }
    portENTER_CRITICAL(&packet_lock);
    if(__atomic_load_n(&renderer_task,__ATOMIC_ACQUIRE) || resources_starting || !resources_wanted || now<resource_retry_us) { portEXIT_CRITICAL(&packet_lock);return; }
    resources_starting=1;portEXIT_CRITICAL(&packet_lock);
    audio_item *new_items=heap_caps_calloc(WMP_FRAME_SLOTS,sizeof(*new_items),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    uint8_t *new_payload=heap_caps_malloc(WMP_PAYLOAD_BYTES,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    wmp_pcm_blocks *new_blocks=heap_caps_calloc(1,sizeof(*new_blocks),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    pcm_runtime *new_signal=heap_caps_calloc(1,sizeof(*new_signal),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!new_items || !new_payload || !new_blocks || !new_signal) {
        heap_caps_free(new_signal);heap_caps_free(new_blocks);heap_caps_free(new_payload);heap_caps_free(new_items);++resource_failures;resource_retry_us=now+500000;__atomic_store_n(&resources_starting,0u,__ATOMIC_RELEASE);return;
    }
    portENTER_CRITICAL(&packet_lock);items=new_items;payload_storage=new_payload;dma_blocks=new_blocks;signal_runtime=new_signal;wmp_frame_pool_init(&packet_pool);wmp_payload_pool_init(&payload_pool);portEXIT_CRITICAL(&packet_lock);
    resources_stop=0;last_offer_us=(uint32_t)now;
    TaskHandle_t created=NULL;
    if(xTaskCreatePinnedToCore(player,"wmp_audio",2048,0,requested_priority,&created,1)!=pdPASS) {
        heap_caps_free(new_signal);heap_caps_free(new_blocks);heap_caps_free(new_payload);heap_caps_free(new_items);items=NULL;payload_storage=NULL;dma_blocks=NULL;signal_runtime=NULL;renderer_task=NULL;++resource_failures;resource_retry_us=now+500000;__atomic_store_n(&resources_starting,0u,__ATOMIC_RELEASE);return;
    }
    ++resource_starts;resources_wanted=0;__atomic_store_n(&renderer_task,created,__ATOMIC_RELEASE);__asm__ __volatile__("memw":::"memory");resources_ready=1;__atomic_store_n(&resources_starting,0u,__ATOMIC_RELEASE);xTaskNotifyGive(created);
}
void wmp_audio_preload(void) {
    /* Stream/role preparation context only. It must not depend on the serial
     * command loop returning from a long diagnostic print before intake. */
    if(resources_ready && !resources_stop)return;
    portENTER_CRITICAL(&packet_lock);last_offer_us=(uint32_t)esp_timer_get_time();
    if(resources_ready)resources_stop=0;else resources_wanted=1;
    portEXIT_CRITICAL(&packet_lock);wmp_audio_service_resources();
}
void wmp_audio_resource_stats(unsigned long query) {
    uint64_t ready;uint32_t version,epoch,warm_count,cold_count,retries,render_epoch;unsigned warm,shared;
    portENTER_CRITICAL(&packet_lock);ready=peer_preparation.ready_us;version=peer_preparation.version;epoch=peer_preparation.clock_epoch;warm=peer_preparation.warm;warm_count=preparation_warm;cold_count=preparation_cold;retries=preparation_retries;render_epoch=renderer_clock_epoch;shared=renderer_shared_clock;portEXIT_CRITICAL(&packet_lock);
    unsigned arena=items?(unsigned)(WMP_FRAME_SLOTS*sizeof(*items)+WMP_PAYLOAD_BYTES+sizeof(*dma_blocks)):0u;
    wmp_telemetry_printf("WMP_AUDIO_RESOURCES query=%08lx ready=%u wanted=%u stopping=%u starting=%u arena_bytes=%u pcm_bytes=%u codec_bytes=%u starts=%lu stops=%lu codec_starts=%lu codec_stops=%lu failures=%lu\n",query,resources_ready,resources_wanted,resources_stop,resources_starting,arena,signal_runtime?(unsigned)sizeof(*signal_runtime):0u,decoder_context?(unsigned)sizeof(*decoder_context):0u,(unsigned long)resource_starts,(unsigned long)resource_stops,(unsigned long)codec_starts,(unsigned long)codec_stops,(unsigned long)resource_failures);
    { wmp_codecs_open_stats o;wmp_codecs_stats(&o);
      wmp_telemetry_printf("WMP_CODECS query=%08lx active=%u play=%u bytes=%lu errors=%lu opens=%lu bad_args=%lu no_memory=%lu largest_at_fail=%lu last_codec=%u last_rate=%lu last_channels=%u%c",query,(unsigned)wmp_codecs_active(),(unsigned)(play_codec==0xffu?0u:play_codec),(unsigned long)wmp_codecs_bytes(),(unsigned long)wmp_codecs_errors(),(unsigned long)o.opens,(unsigned long)o.bad_args,(unsigned long)o.no_memory,(unsigned long)o.largest_at_fail,(unsigned)o.last_codec,(unsigned long)o.last_rate,(unsigned)o.last_channels,10); }
    wmp_telemetry_printf("WMP_PREP_POLICY query=%08lx version=%lu warm=%u ready_us=%llu clock_epoch=%lu renderer_clock_epoch=%lu renderer_shared=%u warm_requests=%lu cold_requests=%lu admission_retries=%lu\n",query,(unsigned long)version,warm,(unsigned long long)ready,(unsigned long)epoch,(unsigned long)render_epoch,shared,(unsigned long)warm_count,(unsigned long)cold_count,(unsigned long)retries);
}
void wmp_audio_init(void) {
    wmp_beacon_clock_reset(&peer_clock);wmp_mac_reset(&clock_unwrap);
    wmp_frame_pool_init(&packet_pool);wmp_payload_pool_init(&payload_pool);
    timing=xQueueCreate(8,sizeof(beacon));configASSERT(timing);
}
