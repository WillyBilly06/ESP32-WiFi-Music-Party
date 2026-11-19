# Codec plan: AAC, aptX, LDAC with lazy per-codec allocation (2026-11-18)

User request: "slowly integrate other audio codecs like aptX, AAC, and even LDAC ... do lazy allocations where it
only loads what that current codec's config need to use and store everything else in flash, do the same for
everything."

## What exists

- BTstack v1.8.3 negotiates SBC, MPEG-2/4 AAC (`A2DP_SUBEVENT_SIGNALING_MEDIA_CODEC_MPEG_AAC_CONFIGURATION`) and
  vendor codecs (`..._OTHER_CONFIGURATION`, vendor id + codec id; `avdtp_get_source_stream_endpoint_for_media_codec_other`).
  It ships only an SBC decoder. aptX, aptX HD and LDAC are vendor codecs:
  aptX 0x0000004F/0x0001, aptX HD 0x000000D7/0x0024, LDAC 0x0000012D/0x00AA.
- Memory on the boards (ESP32-D0WD-V3, no PSRAM): about 49 KB free heap, 29 KB minimum observed while playing.
  Code and const tables already live in flash (XIP); RAM is the constraint.

## Order (slowly, one codec per step, each measured before the next)

1. **Lazy allocation groundwork.** One codec slot: allocate the active codec's decoder state and buffers when
   AVDTP configures the stream, free them when the stream closes or the codec changes. Followers allocate the
   codec named by the broadcast stream, not a fixed SBC instance. Measure free heap per codec.
2. **AAC** (iPhones and most Android phones). Decoder candidates: Espressif `esp_audio_codec` (Espressif SDK
   component), Helix AAC (fixed-point, small RAM), fdk-aac (larger). AAC-LC 44.1/48 kHz, up to 320 kbps.
3. **aptX / aptX HD** (Qualcomm Android). Decoder: libopenaptx (LGPL-2.1, pinned upstream). Small state,
   fixed 4:1 ADPCM, 352/529 kbps. aptX has no frame header loss recovery; copies/repair matter more.
4. **LDAC** (Sony/Android). No official open decoder; only reverse-engineered decoders exist. 990 kbps on Bluetooth
   plus Wi-Fi rebroadcast is not realistic on one shared ESP32 radio; 330 kbps is the only plausible mode.
   Feasibility study first (decoder availability/licence, CPU per frame, heap), no commitment.

## Broadcast transport choice (per codec)

- Rebroadcast the phone's original codec frames (bytes preserved, every speaker decodes the same codec): best
  quality, no re-encode; airtime scales with bitrate (4 copies of 320 kbps AAC is about 1.3 Mbps of payload).
- Or decode on the master and re-encode SBC for the broadcast: one decoder on followers, extra CPU and a second
  lossy step on the master. Decide per codec from measured airtime and CPU.

## Rules that change (need the user's confirmation before delivery)

- AGENTS.md: "Preserve original SBC bytes" applies to SBC streams; other codecs need their own preservation rule.
- Third-party decoders must be public pinned upstream dependencies (allowed by AGENTS.md); licences recorded here.
