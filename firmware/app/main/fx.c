/* Karaoke effects on the music: vocal remover, chorus, echo and reverb. Integer only.
 *
 * Signal: the decoded, EQ'd stereo stream (24-bit values in int32), before volume. Effects add a
 * wet signal to the dry one; the output pass that follows saturates to 24 bits.
 *
 * Memory: every effect owns one int16 ring that is allocated only while the effect is on, by
 * wmp_fx_service() on the main loop (never in the audio task), and only when at least
 * FX_HEAP_RESERVE bytes stay free afterwards. A refused effect stays off and reports it in the
 * status byte. Rings are sized for 48 kHz:
 *   echo    250 ms at fs/4, mono          ~6.0 KB
 *   reverb  4 combs + 2 allpasses at fs/2  ~6.4 KB
 *   chorus  20 ms at fs, mono             ~1.9 KB
 * Reverb and echo run decimated (their tails are dark anyway) and are interpolated back up.
 *
 * Party: the master's settings travel in its announce packets (radio.c); a follower plays the
 * master's settings while they keep arriving and its own settings otherwise, like party volume. */
#include "node.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include <string.h>

#define FX_HEAP_RESERVE 10240u
#define ECHO_MAX (48000u/4u*250u/1000u + 2u)   /* 3002 samples at 12 kHz */
#define CHORUS_MAX (48000u*20u/1000u + 4u)     /* 964 samples */
static const uint16_t comb_base[4]={558,594,639,678}; /* Freeverb tunings, halved (fs/2), at 44.1 kHz */
static const uint16_t allpass_base[2]={278,170};
#define REVERB_SCALE(n) ((uint32_t)(n)*48000u/44100u + 1u)
static uint32_t reverb_total(void) { uint32_t t=0;for(int i=0;i<4;i++)t+=REVERB_SCALE(comb_base[i]);for(int i=0;i<2;i++)t+=REVERB_SCALE(allpass_base[i]);return t; }

enum { FX_VOCAL=1u, FX_ECHO=2u, FX_REVERB=4u, FX_CHORUS=8u };
typedef struct { int16_t *ring;uint32_t capacity;volatile uint8_t ready;uint8_t refused; } fx_ring;
static fx_ring echo_ring,reverb_ring,chorus_ring;
static uint8_t local_cfg[WMP_FX_BYTES]={0,100,25,40,35,60,50,30,8,50,50}; /* all off; useful defaults */
static uint8_t party_cfg[WMP_FX_BYTES];static volatile int64_t party_at;
static volatile uint32_t cfg_version=1;
static volatile uint8_t fx_busy;
static volatile uint32_t fx_refusals,fx_allocations;

static int valid(const uint8_t c[WMP_FX_BYTES]) {
    return !(c[0]&~0x0fu) && c[1]<=100u && c[2]>=5u && c[2]<=25u && c[3]<=75u && c[4]<=100u &&
           c[5]<=100u && c[6]<=100u && c[7]<=100u && c[8]>=1u && c[8]<=50u && c[9]<=100u && c[10]<=100u;
}
int wmp_fx_set(const uint8_t cfg[WMP_FX_BYTES]) {
    if(!valid(cfg))return 0;
    memcpy(local_cfg,cfg,WMP_FX_BYTES);++cfg_version;wmp_settings_changed();return 1;
}
void wmp_fx_load(const uint8_t cfg[WMP_FX_BYTES]) { if(valid(cfg)) { memcpy(local_cfg,cfg,WMP_FX_BYTES);++cfg_version; } }
static int party_fresh(void) { return radio_stats.mode!=2u && party_at && esp_timer_get_time()-party_at<2000000; }
void wmp_fx_party(const uint8_t cfg[WMP_FX_BYTES]) {
    if(!valid(cfg))return;
    if(memcmp(party_cfg,cfg,WMP_FX_BYTES) || !party_fresh()) { memcpy(party_cfg,cfg,WMP_FX_BYTES);++cfg_version; }
    party_at=esp_timer_get_time();
}
static const uint8_t *effective(void) { return party_fresh()?party_cfg:local_cfg; }
void wmp_fx_announce(uint8_t out[1u+WMP_FX_BYTES]) { out[0]=1u;memcpy(out+1,local_cfg,WMP_FX_BYTES); }
void wmp_fx_get(uint8_t out[WMP_FX_BYTES+1u]) {
    /* The speaker's own settings, then a status byte: bits 0-3 vocal/echo/reverb/chorus running,
     * bit 4 the party master's settings are in use, bit 5 an effect was refused for lack of memory. */
    memcpy(out,local_cfg,WMP_FX_BYTES);
    const uint8_t *c=effective();
    out[WMP_FX_BYTES]=(uint8_t)(((c[0]&FX_VOCAL)?1u:0u)|((c[0]&FX_ECHO) && echo_ring.ready?2u:0u)|((c[0]&FX_REVERB) && reverb_ring.ready?4u:0u)|
        ((c[0]&FX_CHORUS) && chorus_ring.ready?8u:0u)|(party_fresh()?16u:0u)|(echo_ring.refused || reverb_ring.refused || chorus_ring.refused?32u:0u));
}

/* Main loop: allocate rings for enabled effects, release rings of disabled ones once the audio
 * task is provably outside fx processing (it raises fx_busy before reading `ready`). */
static void ring_service(fx_ring *r,int want,uint32_t samples) {
    if(want && !r->ring) {
        uint32_t bytes=samples*sizeof(int16_t);
        size_t free=heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
        if(free<bytes+FX_HEAP_RESERVE || heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)<bytes) {
            if(!r->refused) { ++fx_refusals; }
            r->refused=1;return;
        }
        int16_t *p=heap_caps_calloc(samples,sizeof(int16_t),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
        if(!p) { if(!r->refused) { ++fx_refusals; } r->refused=1;return; }
        r->ring=p;r->capacity=samples;r->refused=0;++fx_allocations;__sync_synchronize();r->ready=1;
    } else if(!want) {
        r->refused=0;
        if(r->ring) {
            if(r->ready) { r->ready=0;__sync_synchronize();return; } /* free on a later pass */
            if(fx_busy)return;
            heap_caps_free(r->ring);r->ring=NULL;r->capacity=0;
        }
    }
}
void wmp_fx_service(void) {
    static int64_t next;int64_t now=esp_timer_get_time();if(now<next)return;next=now+20000;
    uint8_t f=effective()[0];
    ring_service(&echo_ring,(f&FX_ECHO)!=0,ECHO_MAX);
    ring_service(&reverb_ring,(f&FX_REVERB)!=0,reverb_total());
    ring_service(&chorus_ring,(f&FX_CHORUS)!=0,CHORUS_MAX);
}
void wmp_fx_stats(unsigned long query) {
    uint8_t s[WMP_FX_BYTES+1u];wmp_fx_get(s);const uint8_t *c=effective();
    wmp_telemetry_printf("WMP_FX query=%08lx flags=%u status=%u party=%u allocations=%lu refusals=%lu bytes=%lu vocal=%u echo=%u/%u/%u reverb=%u/%u/%u chorus=%u/%u/%u\n",query,
        c[0],s[WMP_FX_BYTES],party_fresh(),(unsigned long)fx_allocations,(unsigned long)fx_refusals,
        (unsigned long)((echo_ring.capacity+reverb_ring.capacity+chorus_ring.capacity)*2u),c[1],c[2],c[3],c[4],c[5],c[6],c[7],c[8],c[9],c[10]);
}

/* ---- audio task ---- */
static inline int32_t sat16(int32_t v) { return v>32767?32767:(v<-32768?-32768:v); }
/* x/256 and x/2 rounded toward zero: inside a feedback loop this only ever removes energy, so tails
 * decay to exactly 0 instead of settling in a small limit cycle (plain >> rounds toward -inf). */
static inline int32_t q8(int32_t v) { return v>=0?v>>8:-((-v)>>8); }
static inline int32_t half(int32_t v) { return v>=0?v>>1:-((-v)>>1); }
typedef struct {
    uint32_t version,rate;uint8_t flags;
    int32_t vocal_k,vocal_amt,lp1,lp2;                        /* vocal remover */
    uint32_t e_len,e_pos;int32_t e_acc,e_prev,e_cur,e_lp,e_fb,e_mix;uint8_t e_phase; /* echo */
    uint32_t c_len[4],c_off[4],c_pos[4],a_len[2],a_off[2],a_pos[2];
    int32_t c_store[4],r_fb,r_damp,r_mix,r_in_prev,rl_prev,rl_cur,rr_prev,rr_cur;uint8_t r_phase; /* reverb */
    uint32_t ch_pos,ch_phase,ch_inc,ch_base_q8,ch_span_q8;int32_t ch_mix;  /* chorus */
} fx_state;
static fx_state st;
static void configure(const uint8_t *c,uint32_t rate,uint8_t ready_mask) {
    int reset=st.rate!=rate;st.rate=rate;st.flags=(uint8_t)(c[0]&ready_mask);
    st.vocal_k=(int32_t)(74120000u/rate);st.vocal_amt=(int32_t)c[1]*256/100; /* one-pole ~180 Hz, twice */
    uint32_t e_fs=rate/4u;st.e_len=e_fs*(uint32_t)c[2]*10u/1000u;if(st.e_len+2u>ECHO_MAX)st.e_len=ECHO_MAX-2u;
    st.e_fb=(int32_t)c[3]*256/100;st.e_mix=(int32_t)c[4]*180/100; /* 100 % mix = 0.7 x wet: no clipping at full feedback */
    uint32_t off=0;
    for(int i=0;i<4;i++) { st.c_len[i]=(uint32_t)comb_base[i]*rate/44100u;st.c_off[i]=off;off+=REVERB_SCALE(comb_base[i]); }
    for(int i=0;i<2;i++) { st.a_len[i]=(uint32_t)allpass_base[i]*rate/44100u;st.a_off[i]=off;off+=REVERB_SCALE(allpass_base[i]); }
    st.r_fb=179+(int32_t)c[5]*(250-179)/100;   /* Q8 feedback 0.70..0.98 */
    st.r_damp=(int32_t)c[6]*230/100;           /* Q8 damping */
    st.r_mix=(int32_t)c[7]*256/100;
    st.ch_inc=(uint32_t)(((uint64_t)c[8]*429496730u)/rate); /* c[8] x 0.1 Hz, phase 2^32 */
    st.ch_base_q8=rate*7u/1000u*256u;st.ch_span_q8=rate*(uint32_t)c[9]*8u/100u*256u/1000u; /* 7 ms + up to 8 ms */
    st.ch_mix=(int32_t)c[10]*256/100;
    if(reset) { st.e_pos=0;for(int i=0;i<4;i++)st.c_pos[i]=0;for(int i=0;i<2;i++)st.a_pos[i]=0;st.ch_pos=0; }
}
int wmp_fx_active(void) { return st.flags!=0; }
/* In IRAM like the EQ: from flash, the cache that core 0 shares with the Wi-Fi/BT code made it ~3x slower. */
void IRAM_ATTR wmp_fx_process(int32_t *pcm,unsigned frames,uint32_t rate) {
    fx_busy=1;__sync_synchronize();
    const uint8_t *c=effective();
    uint8_t ready_mask=(uint8_t)(FX_VOCAL|(echo_ring.ready?FX_ECHO:0u)|(reverb_ring.ready?FX_REVERB:0u)|(chorus_ring.ready?FX_CHORUS:0u));
    uint32_t v=cfg_version;
    if(v!=st.version || rate!=st.rate || (c[0]&ready_mask)!=st.flags) {
        uint8_t before=st.flags;configure(c,rate,ready_mask);st.version=v;
        /* An effect that just (re)started begins from a cleared ring and state. */
        uint8_t started=(uint8_t)(st.flags&~before);
        if(started&FX_ECHO) { memset(echo_ring.ring,0,echo_ring.capacity*2u);st.e_pos=0;st.e_acc=st.e_prev=st.e_cur=st.e_lp=0;st.e_phase=0; }
        if(started&FX_REVERB) { memset(reverb_ring.ring,0,reverb_ring.capacity*2u);memset(st.c_store,0,sizeof(st.c_store));for(int i=0;i<4;i++)st.c_pos[i]=0;for(int i=0;i<2;i++)st.a_pos[i]=0;st.r_in_prev=st.rl_prev=st.rl_cur=st.rr_prev=st.rr_cur=0;st.r_phase=0; }
        if(started&FX_CHORUS) { memset(chorus_ring.ring,0,chorus_ring.capacity*2u);st.ch_pos=0; }
        if(started&FX_VOCAL)st.lp1=st.lp2=0;
    }
    uint8_t f=st.flags;
    if(!f) { fx_busy=0;return; }
    int16_t *eb=echo_ring.ring,*rb=reverb_ring.ring,*cb=chorus_ring.ring;
    for(unsigned i=0;i<frames;i++) {
        int32_t l=pcm[i*2u],r=pcm[i*2u+1u];
        if(f&FX_VOCAL) {
            int32_t mid=(l+r)>>1,side=(l-r)>>1;
            st.lp1+=((mid-st.lp1)>>4)*st.vocal_k>>12;st.lp2+=((st.lp1-st.lp2)>>4)*st.vocal_k>>12;
            mid-=((mid-st.lp2)>>8)*st.vocal_amt;
            l=mid+side;r=mid-side;
        }
        int32_t m16=sat16(((l+r)>>1)>>8),dl=l,dr=r;
        if(f&FX_CHORUS) {
            cb[st.ch_pos]=(int16_t)m16;
            uint32_t pl=st.ch_phase,pr=st.ch_phase+0x40000000u;st.ch_phase+=st.ch_inc;
            uint32_t tl=pl>>15,tr=pr>>15;tl=tl<65536u?tl:131071u-tl;tr=tr<65536u?tr:131071u-tr; /* triangle 0..65535 */
            int32_t out[2];uint32_t t[2]={tl,tr};
            for(int k=0;k<2;k++) {
                uint32_t d=st.ch_base_q8+(((t[k]>>4)*(st.ch_span_q8>>4))>>8),whole=d>>8,frac=d&255u;
                uint32_t a=st.ch_pos>=whole?st.ch_pos-whole:st.ch_pos+CHORUS_MAX-whole,b=a?a-1u:CHORUS_MAX-1u;
                out[k]=((int32_t)cb[a]*(int32_t)(256u-frac)+(int32_t)cb[b]*(int32_t)frac)>>8;
            }
            if(++st.ch_pos==CHORUS_MAX)st.ch_pos=0;
            dl+=((out[0]*st.ch_mix)>>8)<<8;dr+=((out[1]*st.ch_mix)>>8)<<8;
        }
        if(f&FX_ECHO) {
            st.e_acc+=m16;
            if(++st.e_phase==4u) {
                st.e_phase=0;int32_t x=st.e_acc>>2;st.e_acc=0;
                uint32_t rd=st.e_pos>=st.e_len?st.e_pos-st.e_len:st.e_pos+ECHO_MAX-st.e_len;int32_t y=eb[rd];
                st.e_lp+=half(y-st.e_lp);                       /* darken each repeat */
                eb[st.e_pos]=(int16_t)sat16(x+q8(st.e_lp*st.e_fb));if(++st.e_pos==ECHO_MAX)st.e_pos=0;
                st.e_prev=st.e_cur;st.e_cur=y;
            }
            int32_t e=st.e_prev+(((st.e_cur-st.e_prev)*(int32_t)st.e_phase)>>2);
            int32_t w=((e*st.e_mix)>>8)<<8;dl+=w;dr+=w;
        }
        if(f&FX_REVERB) {
            if(++st.r_phase==2u) {
                st.r_phase=0;int32_t x=(m16+st.r_in_prev)>>5;   /* average of two, x1/16: combs at 0.98 feedback gain ~50x (Freeverb uses 0.015) */
                int32_t sum=0;
                for(int k=0;k<4;k++) {
                    int16_t *line=rb+st.c_off[k];int32_t y=line[st.c_pos[k]];
                    st.c_store[k]=q8(y*(256-st.r_damp)+st.c_store[k]*st.r_damp);
                    line[st.c_pos[k]]=(int16_t)sat16(x+q8(st.c_store[k]*st.r_fb));
                    if(++st.c_pos[k]>=st.c_len[k]) { st.c_pos[k]=0; }
                    sum+=y;
                }
                int32_t s=sum>>2,outl=s,outr=s;
                for(int k=0;k<2;k++) {
                    int16_t *line=rb+st.a_off[k];int32_t b=line[st.a_pos[k]];
                    int32_t o=b-outl;line[st.a_pos[k]]=(int16_t)sat16(outl+half(b));if(++st.a_pos[k]>=st.a_len[k])st.a_pos[k]=0;
                    if(k==0) { outr=o; }            /* right = after one allpass, left = after two */
                    outl=o;
                }
                st.rl_prev=st.rl_cur;st.rl_cur=outl;st.rr_prev=st.rr_cur;st.rr_cur=outr;
            } else st.r_in_prev=m16;
            int32_t wl=st.rl_prev+(((st.rl_cur-st.rl_prev)*(int32_t)st.r_phase)>>1),wr=st.rr_prev+(((st.rr_cur-st.rr_prev)*(int32_t)st.r_phase)>>1);
            dl+=((wl*st.r_mix)>>8)<<11;dr+=((wr*st.r_mix)>>8)<<11;  /* x8 of the 16-bit scale */
        }
        pcm[i*2u]=dl;pcm[i*2u+1u]=dr;
    }
    fx_busy=0;
}
