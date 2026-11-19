#ifndef WMP_WIRE_H
#define WMP_WIRE_H
#include <stdint.h>
#include <stddef.h>
#define WMP_HEADER_BYTES 72u
/* Groups carry up to 8 original SBC frames (about 23 ms at 44.1 kHz): half the
 * transmissions of 4-frame groups. A raw 802.11 frame is limited to 1500 bytes
 * (esp_wifi_80211_tx): 32 bytes of MAC/action header + 72 + payload. */
#define WMP_MAX_GROUP_FRAMES 8u
#define WMP_MAX_GROUP_SAMPLES 1024u
#define WMP_MAX_PAYLOAD 1392u
#define WMP_MAX_WIRE (WMP_HEADER_BYTES + WMP_MAX_PAYLOAD)
enum { WMP_ANNOUNCE=1, WMP_AUDIO=2, WMP_PARITY=3, WMP_SYNC=4, WMP_SYNC_REPLY=5, WMP_PROBE=6,WMP_SILENCE=7 };
enum { WMP_FLAG_REPLAY=1, WMP_FLAG_CLOCK=2 };
/* WMP_FLAG_CLOCK: a 16-byte clock trailer follows the payload (inside the payload budget):
 * u64 send_stamp (source clock when encoded), u32 next_copy_us (time until this group's next
 * scheduled copy, 0 = none/unknown), u16 tx_serial, u8 sync_flags, u8 party (speakers the
 * master currently hears in its party, counting itself: 1, 2, or 3 meaning three or more;
 * 0 = unknown).
 * Per-copy data: excluded from the logical CRC, like copy_index. */
#define WMP_CLOCK_TRAILER 16u
enum { WMP_SYNC_QUIET=1 }; /* encoded while no other frame was queued for the radio */
/* sync_flags bits 1..7: the master's party volume + 1 (0 = unknown, 1..101 = 0..100 %), so every
 * follower plays at the master's volume like a JBL party. Per copy, outside the logical CRC. */
#define WMP_SYNC_VOLUME_SHIFT 1u
#define WMP_SYNC_VOLUME_MAX 101u
static inline uint8_t wmp_sync_volume_encode(unsigned percent) { return (uint8_t)((percent>100u?101u:percent+1u)<<WMP_SYNC_VOLUME_SHIFT); }
/* Returns the volume percent, or -1 when the field is absent. */
static inline int wmp_sync_volume_decode(uint8_t sync_flags) { unsigned v=sync_flags>>WMP_SYNC_VOLUME_SHIFT;return v?(int)v-1:-1; }
typedef struct {
    uint64_t group,generation,sequence,first_sample,presentation_us;
    uint32_t sample_rate,sample_count;
    uint16_t payload_bytes,frame_count,fragment_index,fragment_count;
    uint8_t type,copy_index,flags;
    uint64_t send_stamp; /* valid with WMP_FLAG_CLOCK */
    uint32_t next_copy_us;
    uint16_t tx_serial;
    uint8_t sync_flags;
    uint8_t party;  /* clock trailer byte 15, see above */
    /* Header bytes 66..67 (formerly reserved zero): codec (wmp_codec.h) and channel count.
     * SBC writes 0/0, so SBC frames are byte-identical to the previous wire format. */
    uint8_t codec,channels;
} wmp_header;
/* CRC32/IEEE, an integrity check rather than authentication. */
uint32_t wmp_crc32(const uint8_t *data,size_t bytes);
uint32_t wmp_crc32_extend(uint32_t previous,const uint8_t *data,size_t bytes);
/* Encodes fields explicitly; no packed-struct ABI or SBC modification. */
int wmp_encode(uint8_t *out,size_t capacity,const wmp_header *header,const uint8_t *payload);
int wmp_decode(const uint8_t *wire,size_t bytes,wmp_header *out,const uint8_t **payload);
/* Native Ethernet RX after LLC removal: direct raw-data WMP or tagged carrier.
 * Returns14/22 or0. CRC/semantic validation still belongs to wmp_decode. */
unsigned wmp_ethernet_payload_offset(const uint8_t *frame,size_t bytes);
/* Copy index is deliberately excluded; retransmission keeps logical identity. */
uint32_t wmp_logical_crc(const uint8_t *wire,size_t bytes);
/* After wire CRC validation: receiver codec hints do not authorize/deny a
 * cached repair. The publisher owns the actual packet metadata. */
int wmp_repair_request(const wmp_header *header,const uint8_t *payload,uint64_t group,uint64_t generation);
typedef struct {
    uint64_t group,generation,highest,seen;
    uint32_t crc[64];
    uint8_t initialized;
} wmp_dedup;
void wmp_dedup_bind(wmp_dedup *state,uint64_t group,uint64_t generation);
/* Call only after full SDU reassembly and integrity validation.
 * 1=new,0=identical duplicate,-1=outside window,-2=wrong stream,-3=conflict. */
int wmp_dedup_accept(wmp_dedup *state,uint64_t group,uint64_t generation,uint64_t sequence,uint32_t logical_crc);
/* Same admission decision without marking delivery; commit only after handoff. */
int wmp_dedup_query(const wmp_dedup *state,uint64_t group,uint64_t generation,uint64_t sequence,uint32_t logical_crc);
/* Commit delivery only for an actual queue insertion (status1). A full
 * queue/status0 or preparation deferral/status2 must permit another copy. */
int wmp_dedup_commit_delivery(wmp_dedup *state,uint64_t group,uint64_t generation,uint64_t sequence,uint32_t logical_crc,int enqueue_status);
#endif
