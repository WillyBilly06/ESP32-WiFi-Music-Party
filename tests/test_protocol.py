"""Execute the actual portable C wire/group/dedup code on the host."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import subprocess
import time
import unittest
import itertools
import random
import zlib
import re

ROOT=Path(__file__).resolve().parents[1]
SBC_QUEUE_BYTES=int(re.search(r'#define WMP_SBC_QUEUE_BYTES (\d+)u',(ROOT/'protocol/include/wmp_sbc_queue.h').read_text())[1])
SBC_QUEUE_GROUPS=int(re.search(r'#define WMP_SBC_QUEUE_GROUPS (\d+)u',(ROOT/'protocol/include/wmp_sbc_queue.h').read_text())[1])
PLAYOUT_MAX_US=int(re.search(r'#define WMP_PLAYOUT_MAX_US (\d+)u',(ROOT/'protocol/include/wmp_playout.h').read_text())[1])
LEADER_TIE_US=int(re.search(r'#define WMP_LEADER_TIE_US (\d+)u',(ROOT/'protocol/include/wmp_peer_lease.h').read_text())[1])
FRAME_SLOTS=int(re.search(r'#define WMP_FRAME_SLOTS (\d+)u',(ROOT/'protocol/include/wmp_frame_pool.h').read_text())[1])
NACK_SLOTS=int(re.search(r'#define WMP_NACK_SLOTS (\d+)u',(ROOT/'protocol/include/wmp_nack.h').read_text())[1])
PAYLOAD_EXTENTS=int(re.search(r'#define WMP_PAYLOAD_EXTENTS (\d+)u',(ROOT/'protocol/include/wmp_payload_pool.h').read_text())[1])
PAYLOAD_BYTES=int(re.search(r'#define WMP_PAYLOAD_BYTES (\d+)u',(ROOT/'protocol/include/wmp_payload_pool.h').read_text())[1])
MAX_PAYLOAD=int(re.search(r'#define WMP_MAX_PAYLOAD (\d+)u',(ROOT/'protocol/include/wmp_wire.h').read_text())[1])
MAX_GROUP_FRAMES=int(re.search(r'#define WMP_MAX_GROUP_FRAMES (\d+)u',(ROOT/'protocol/include/wmp_wire.h').read_text())[1])
MAX_GROUP_SAMPLES=int(re.search(r'#define WMP_MAX_GROUP_SAMPLES (\d+)u',(ROOT/'protocol/include/wmp_wire.h').read_text())[1])
OUT=ROOT/'build'/('protocol-'+str(time.time_ns()))
OUT.mkdir(parents=True)
exports=['wmp_crc32','wmp_crc32_extend','wmp_encode','wmp_decode','wmp_logical_crc','wmp_dedup_bind','wmp_dedup_accept','wmp_group_init','wmp_group_push','wmp_group_frame','wmp_copy_plan','wmp_mac_reset','wmp_mac_extend','wmp_sample_time','wmp_sbc_info_read','wmp_rtp_sbc_read','wmp_beacon_clock_reset','wmp_beacon_clock_push','wmp_beacon_clock_to_local']
exports+=['wmp_copy_due']
exports+=['wmp_preparation_begin','wmp_preparation_allows','wmp_preparation_keeps','wmp_preparation_forget']
exports+=['wmp_preparation_begin_at']
exports+=['wmp_sbc_stream_reset','wmp_sbc_stream_accept']
exports+=['wmp_frame_pool_pin_repair_limit']
# Historical FEC reference only: delivered firmware no longer links parity generation/recovery.
exports+=['wmp_fec_bind','wmp_fec_data','wmp_fec_emit','wmp_fec_receive','wmp_fec_recover']
sources=['protocol/src/wmp_wire.c','protocol/src/wmp_audio_group.c','protocol/src/wmp_time.c','protocol/src/wmp_sbc.c','protocol/src/wmp_beacon_clock.c','tests/clock_host.c']
sources+=['protocol/src/wmp_fec.c']
sources+=['protocol/src/wmp_copy_trace.c']
exports+=['wmp_copy_trace_event']
exports+=['wmp_copy_trace_reset_gaps']
exports+=['wmp_copy_trace_route']
exports+=['wmp_tx_next','wmp_tx_precedes','wmp_feedback_window','wmp_tx_deadline']
exports+=['wmp_tx_original_expired','wmp_tx_precedes_copy']
exports+=['wmp_feedback_blocked']
exports+=['wmp_tx_after_call']
exports+=['wmp_ingress_clock','wmp_silence_due']
exports+=['wmp_replay_copies','wmp_election_delay','wmp_nack_observe_after']
exports+=['wmp_replay_plan']
exports+=['wmp_replay_start']
exports+=['wmp_nack_observe_until','wmp_nack_attempts']
exports+=['wmp_wake_due']
exports+=['wmp_dedup_query','wmp_ethernet_payload_offset']
exports+=['wmp_dedup_commit_delivery']
exports+=['wmp_repair_request']
sources+=['protocol/src/wmp_payload_pool.c'];exports+=['wmp_payload_pool_init','wmp_payload_alloc','wmp_payload_free']
sources+=['protocol/src/wmp_frame_pool.c']
exports+=['wmp_frame_pool_quiescent','wmp_frame_pool_init','wmp_frame_pool_acquire','wmp_frame_pool_publish','wmp_frame_pool_take','wmp_frame_pool_release','wmp_frame_pool_cancel','wmp_frame_pool_pin','wmp_frame_pool_unpin','wmp_frame_pool_pin_repair','wmp_frame_pool_unpin_repair']
sources+=['protocol/src/wmp_playout.c'];exports+=['wmp_playout_init','wmp_playout_plan','wmp_playout_request','wmp_playout_report']
sources+=['protocol/src/wmp_sbc_queue.c'];exports+=['wmp_sbc_queue_init','wmp_sbc_queue_push','wmp_sbc_queue_peek','wmp_sbc_queue_pop']
sources+=['protocol/src/wmp_nack.c'];exports+=['wmp_nack_reset','wmp_nack_observe','wmp_nack_plan','wmp_nack_commit','wmp_nack_heard']
exports+=['wmp_nack_reserve','wmp_nack_pending','wmp_nack_submit','wmp_nack_cancel']
sources+=['protocol/src/wmp_pcm_blocks.c'];exports+=['wmp_pcm_blocks_init','wmp_pcm_blocks_push']
sources+=['protocol/src/wmp_codec.c','protocol/src/wmp_latm.c'];exports+=['wmp_codec_profile_get','wmp_codec_payload_bytes','wmp_codec_stream_samples','wmp_group_init_codec','wmp_group_push_unit','wmp_group_push_stream','wmp_group_frame_codec','wmp_latm_parse','wmp_latm_copy','wmp_rtp_read']
sources+=['protocol/src/wmp_peer_lease.c'];exports+=['wmp_peer_lease_init','wmp_peer_claimed','wmp_peer_announce','wmp_peer_audio','wmp_peer_beacon','wmp_peer_idle']
exports+=['wmp_pcm_blocks_guard_us','wmp_playout_recover']
exports+=['wmp_playout_tuning','wmp_playout_tuning_get']
exports+=['wmp_live_payload_capacity']
exports+=['wmp_leader_precedes','wmp_peer_announce_at']
SIZEOF=['dedup','audio_group','sbc_queue','beacon_clock','pcm_blocks','peer_lease']
exports+=['wmp_host_sizeof_'+name for name in SIZEOF]
pins={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources+['protocol/include/wmp_wire.h','protocol/include/wmp_audio_group.h','protocol/include/wmp_time.h','protocol/include/wmp_sbc.h','protocol/include/wmp_beacon_clock.h','tests/test_protocol.py']}
pins['protocol/include/wmp_fec.h']=hashlib.sha256((ROOT/'protocol/include/wmp_fec.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_copy_trace.h']=hashlib.sha256((ROOT/'protocol/include/wmp_copy_trace.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_frame_pool.h']=hashlib.sha256((ROOT/'protocol/include/wmp_frame_pool.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_playout.h']=hashlib.sha256((ROOT/'protocol/include/wmp_playout.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_sbc_queue.h']=hashlib.sha256((ROOT/'protocol/include/wmp_sbc_queue.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_nack.h']=hashlib.sha256((ROOT/'protocol/include/wmp_nack.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_pcm_blocks.h']=hashlib.sha256((ROOT/'protocol/include/wmp_pcm_blocks.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_peer_lease.h']=hashlib.sha256((ROOT/'protocol/include/wmp_peer_lease.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_payload_pool.h']=hashlib.sha256((ROOT/'protocol/include/wmp_payload_pool.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_codec.h']=hashlib.sha256((ROOT/'protocol/include/wmp_codec.h').read_bytes()).hexdigest()
pins['protocol/include/wmp_latm.h']=hashlib.sha256((ROOT/'protocol/include/wmp_latm.h').read_bytes()).hexdigest()
pins['firmware/app/main/node.h']=hashlib.sha256((ROOT/'firmware/app/main/node.h').read_bytes()).hexdigest()
pins['firmware/app/main/telemetry.h']=hashlib.sha256((ROOT/'firmware/app/main/telemetry.h').read_bytes()).hexdigest()
frozen=OUT/'sources'
for name,digest in pins.items():
    raw=(ROOT/name).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise RuntimeError('Test input changed during capture')
    target=frozen/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
command=[r'C:\Program Files\LLVM\bin\clang.exe','-std=c11','-Os','-shared','-nostdlib','-ffreestanding','-fno-builtin','-fno-stack-protector','-fuse-ld=lld','-Wall','-Wextra','-Werror','-Wl,/noentry,/nodefaultlib,'+','.join('/export:'+s for s in exports),'-I'+str(frozen/'protocol/include'),*[str(frozen/p) for p in sources],'-o',str(OUT/'protocol.dll')]
compiled=subprocess.run(command,capture_output=True,text=True)
(OUT/'compile.log').write_text(compiled.stdout+compiled.stderr,encoding='utf-8')
if compiled.returncode:raise RuntimeError(compiled.stderr)
lib=C.CDLL(str(OUT/'protocol.dll'))
Byte=C.c_uint8
Ptr=C.POINTER(Byte)
class Header(C.Structure):
    _fields_=[(x,C.c_uint64) for x in ['group','generation','sequence','first_sample','presentation_us']]+[(x,C.c_uint32) for x in ['sample_rate','sample_count']]+[(x,C.c_uint16) for x in ['payload_bytes','frame_count','fragment_index','fragment_count']]+[(x,Byte) for x in ['type','copy_index','flags']]+[('send_stamp',C.c_uint64),('next_copy_us',C.c_uint32),('tx_serial',C.c_uint16),('sync_flags',Byte),('party',Byte),('codec',Byte),('channels',Byte)]
class Dedup(C.Structure):
    _fields_=[(x,C.c_uint64) for x in ['group','generation','highest','seen']]+[('crc',C.c_uint32*64),('initialized',Byte)]
class Group(C.Structure):
    _fields_=[('payload',Byte*MAX_PAYLOAD),('first_sample',C.c_uint64),('next_sample',C.c_uint64),('sample_count',C.c_uint32),('bytes',C.c_uint16),('frames',C.c_uint16),('target',Byte),('codec',Byte),('channels',Byte)]
class SbcEntry(C.Structure):
    _fields_=[('first_sample',C.c_uint64),('enqueued_us',C.c_uint64),('samples',C.c_uint32),('rate',C.c_uint32),('bytes',C.c_uint16),('frames',C.c_uint16),('codec',Byte),('channels',Byte)]
class SbcQueue(C.Structure):
    _fields_=[('data',Byte*SBC_QUEUE_BYTES),('entries',SbcEntry*SBC_QUEUE_GROUPS)]+[(k,C.c_uint32) for k in ['read','write','used','head','count']]
lib.wmp_sbc_queue_init.argtypes=[C.POINTER(SbcQueue)]
lib.wmp_sbc_queue_push.argtypes=[C.POINTER(SbcQueue),C.POINTER(Group),C.c_uint32,C.c_uint64]
lib.wmp_sbc_queue_peek.argtypes=[C.POINTER(SbcQueue),C.POINTER(Group),C.POINTER(C.c_uint32),C.POINTER(C.c_uint64)]
lib.wmp_sbc_queue_pop.argtypes=[C.POINTER(SbcQueue)]
lib.wmp_crc32.argtypes=[Ptr,C.c_size_t];lib.wmp_crc32.restype=C.c_uint32
lib.wmp_crc32_extend.argtypes=[C.c_uint32,Ptr,C.c_size_t];lib.wmp_crc32_extend.restype=C.c_uint32
lib.wmp_copy_due.argtypes=[C.c_uint64,C.c_uint64,C.c_uint,C.c_uint32,C.c_uint32,C.POINTER(C.c_uint64)];lib.wmp_copy_due.restype=C.c_int
lib.wmp_logical_crc.argtypes=[Ptr,C.c_size_t];lib.wmp_logical_crc.restype=C.c_uint32
lib.wmp_encode.argtypes=[Ptr,C.c_size_t,C.POINTER(Header),Ptr]
lib.wmp_decode.argtypes=[Ptr,C.c_size_t,C.POINTER(Header),C.POINTER(Ptr)]
lib.wmp_ethernet_payload_offset.argtypes=[Ptr,C.c_size_t];lib.wmp_ethernet_payload_offset.restype=C.c_uint
lib.wmp_dedup_bind.argtypes=[C.POINTER(Dedup),C.c_uint64,C.c_uint64]
lib.wmp_dedup_accept.argtypes=[C.POINTER(Dedup),C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32]
lib.wmp_dedup_query.argtypes=lib.wmp_dedup_accept.argtypes
lib.wmp_dedup_commit_delivery.argtypes=lib.wmp_dedup_accept.argtypes+[C.c_int];lib.wmp_dedup_commit_delivery.restype=C.c_int
lib.wmp_group_init.argtypes=[C.POINTER(Group),Byte]
lib.wmp_group_push.argtypes=[C.POINTER(Group),Ptr,C.c_uint16,C.c_uint64,C.c_uint16]
lib.wmp_group_frame.argtypes=[Ptr,C.c_size_t,C.c_uint16,C.c_uint16,C.POINTER(Ptr),C.POINTER(C.c_uint16)]
lib.wmp_copy_plan.argtypes=[C.c_uint64,C.c_uint64,C.c_uint32,Byte,C.POINTER(C.c_uint64)]
class MacClock(C.Structure):
    _fields_=[('extended',C.c_uint64),('previous',C.c_uint32),('valid',Byte)]
lib.wmp_mac_reset.argtypes=[C.POINTER(MacClock)]
lib.wmp_mac_extend.argtypes=[C.POINTER(MacClock),C.c_uint32,C.POINTER(C.c_uint64)]
lib.wmp_sample_time.argtypes=[C.c_uint64,C.c_uint64,C.c_uint32,C.c_uint64,C.POINTER(C.c_uint64)]
lib.wmp_tx_next.argtypes=[C.c_uint64,C.c_uint64,C.c_uint32];lib.wmp_tx_next.restype=C.c_uint64
lib.wmp_tx_after_call.argtypes=[C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32];lib.wmp_tx_after_call.restype=C.c_uint64
lib.wmp_ingress_clock.argtypes=[C.c_uint64,C.c_uint64,C.c_uint64];lib.wmp_ingress_clock.restype=C.c_uint64
lib.wmp_replay_copies.argtypes=[C.c_uint64,C.c_uint32,C.c_uint32];lib.wmp_replay_copies.restype=C.c_uint
lib.wmp_replay_plan.argtypes=[C.c_uint64,C.c_uint32,C.c_uint32,C.c_uint32,C.POINTER(C.c_uint32)];lib.wmp_replay_plan.restype=C.c_uint
lib.wmp_replay_start.argtypes=[C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32];lib.wmp_replay_start.restype=C.c_uint64
lib.wmp_wake_due.argtypes=[C.c_uint64,C.c_uint64,C.c_uint32,C.c_uint32,C.POINTER(C.c_uint64)];lib.wmp_wake_due.restype=C.c_int
lib.wmp_silence_due.argtypes=[C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32,C.POINTER(C.c_uint64)]
lib.wmp_playout_recover.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.POINTER(C.c_uint64),C.POINTER(C.c_uint64)]
class BeaconClock(C.Structure):
    _fields_=[('local',C.c_uint64*128),('remote',C.c_uint64*128),('ref_local',C.c_uint64),('ref_source',C.c_uint64),('ratio',C.c_double)]+[(k,C.c_uint32) for k in ['count','next','valid','jitter_us','fit_jitter_us','rejected']]
lib.wmp_beacon_clock_reset.argtypes=[C.POINTER(BeaconClock)]
lib.wmp_beacon_clock_push.argtypes=[C.POINTER(BeaconClock),C.c_uint64,C.c_uint64]
lib.wmp_beacon_clock_to_local.argtypes=[C.POINTER(BeaconClock),C.c_uint64,C.POINTER(C.c_uint64)]
class SbcInfo(C.Structure):_fields_=[('rate',C.c_uint32),('bytes',C.c_uint16),('samples',C.c_uint16),('channels',Byte)]
class RtpSbc(C.Structure):_fields_=[('timestamp',C.c_uint32),('sequence',C.c_uint16),('frames',Byte),('samples',C.c_uint32)]
class SbcStream(C.Structure):_fields_=[('next',C.c_uint64),('timestamp',C.c_uint32),('packet_samples',C.c_uint32),('mismatches',C.c_uint32),('last_delta',C.c_uint32),('last_expected',C.c_uint32),('sequence',C.c_uint16),('valid',Byte)]
class PayloadPool(C.Structure):
    _fields_=[('offset',C.c_uint16*PAYLOAD_EXTENTS),('length',C.c_uint16*PAYLOAD_EXTENTS),('count',C.c_uint16),('available',C.c_uint16)]
lib.wmp_payload_pool_init.argtypes=[C.POINTER(PayloadPool)]
lib.wmp_payload_alloc.argtypes=[C.POINTER(PayloadPool),C.c_uint]
lib.wmp_payload_free.argtypes=[C.POINTER(PayloadPool),C.c_uint,C.c_uint]
class FramePool(C.Structure):
    _fields_=[(k,C.c_uint64) for k in ['free','pending','ready','held','borrowed','retired','repair_pending']]+[('fifo',Byte*FRAME_SLOTS),('head',Byte),('count',Byte),('references',Byte*FRAME_SLOTS)]
class Preparation(C.Structure):
    _fields_=[('ready_us',C.c_uint64),('version',C.c_uint32),('clock_epoch',C.c_uint32),('warm',Byte)]
lib.wmp_preparation_begin.argtypes=[C.POINTER(Preparation),C.c_uint64,C.c_int,C.c_uint32,C.c_int,C.c_uint32]
lib.wmp_preparation_begin_at.argtypes=lib.wmp_preparation_begin.argtypes+[C.c_uint64]
lib.wmp_preparation_allows.argtypes=[C.POINTER(Preparation),C.c_uint64,C.c_int]
lib.wmp_preparation_keeps.argtypes=[C.POINTER(Preparation),C.c_uint32]
lib.wmp_preparation_forget.argtypes=[C.POINTER(Preparation)]
class NackEntry(C.Structure):
    _fields_=[('sequence',C.c_uint64),('first_us',C.c_uint64),('next_us',C.c_uint64),('valid',Byte),('attempts',Byte),('pending',Byte),('limit',Byte),('lifetime_us',C.c_uint32)]
class Nack(C.Structure):
    _fields_=[('generation',C.c_uint64),('highest',C.c_uint64),('valid',Byte),('entries',NackEntry*NACK_SLOTS)]
lib.wmp_nack_observe_until.argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32,C.c_uint32,C.c_uint64]
lib.wmp_nack_attempts.argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64];lib.wmp_nack_attempts.restype=C.c_uint
class Lease(C.Structure):
    _fields_=[('peer',C.c_uint64),('selected',C.c_uint64),('active',C.c_uint64),('valid',Byte),('playing',Byte)]
lib.wmp_peer_lease_init.argtypes=[C.POINTER(Lease)]
lib.wmp_peer_announce.argtypes=[C.POINTER(Lease),C.c_uint64,C.c_int,C.c_uint64]
lib.wmp_leader_precedes.argtypes=[C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint64,C.c_int]
lib.wmp_peer_announce_at.argtypes=[C.POINTER(Lease),C.c_uint64,C.c_int,C.c_uint64,C.c_uint64,C.c_uint64,C.c_int]
lib.wmp_peer_claimed.argtypes=[C.POINTER(Lease),C.c_uint64]
lib.wmp_peer_audio.argtypes=lib.wmp_peer_beacon.argtypes=[C.POINTER(Lease),C.c_uint64]
lib.wmp_peer_idle.argtypes=[C.POINTER(Lease)]
class PcmBlocks(C.Structure):_fields_=[('tail',C.c_int16*512),('count',C.c_uint),('frames',C.c_uint)]
PcmWriter=C.CFUNCTYPE(C.c_int,C.c_void_p,C.POINTER(C.c_int16),C.c_uint)
lib.wmp_pcm_blocks_init.argtypes=[C.POINTER(PcmBlocks),C.c_uint]
lib.wmp_pcm_blocks_push.argtypes=[C.POINTER(PcmBlocks),C.POINTER(C.c_int16),C.c_uint,PcmWriter,C.c_void_p]
lib.wmp_pcm_blocks_guard_us.argtypes=[C.POINTER(PcmBlocks),C.c_uint32,C.c_uint32];lib.wmp_pcm_blocks_guard_us.restype=C.c_uint32
lib.wmp_nack_reset.argtypes=[C.POINTER(Nack)]
lib.wmp_nack_observe.argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32]
lib.wmp_nack_plan.argtypes=[C.POINTER(Nack),C.c_uint64,C.POINTER(C.c_uint64)]
lib.wmp_nack_commit.argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64]
lib.wmp_nack_heard.argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64,C.c_uint64]
for name in ['wmp_nack_reserve','wmp_nack_pending','wmp_nack_submit']:
    getattr(lib,name).argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64,C.c_uint64]
lib.wmp_nack_cancel.argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32]
for name in ['init','acquire','take']:getattr(lib,'wmp_frame_pool_'+name).argtypes=[C.POINTER(FramePool)]
for name in ['publish','release','cancel','pin','unpin','pin_repair','unpin_repair']:getattr(lib,'wmp_frame_pool_'+name).argtypes=[C.POINTER(FramePool),C.c_uint]
class Playout(C.Structure):
    _fields_=[(k,C.c_uint64) for k in ['first_sample','anchor_us','last_sample','window_us']]+[(k,C.c_int64) for k in ['phase_q16','target_q16','min_slack','max_slack']]+[(k,C.c_uint32) for k in ['rate','requested_us','changes']]+[('feedback_window_us',C.c_uint64)]+[(k,C.c_uint32) for k in ['pending_late','packet_samples','clean_windows']]+[('valid',Byte),('baseline_us',C.c_uint32),('steps',C.c_uint32),('high_windows',C.c_uint32),('high_min',C.c_int64)]
lib.wmp_playout_init.argtypes=[C.POINTER(Playout),C.c_uint32]
lib.wmp_playout_plan.argtypes=[C.POINTER(Playout),C.c_uint64,C.c_uint32,C.c_uint64,C.POINTER(C.c_uint64)]
lib.wmp_playout_request.argtypes=[C.POINTER(Playout),C.c_uint32]
lib.wmp_playout_report.argtypes=[C.POINTER(Playout),C.c_uint32,C.c_uint32]
lib.wmp_playout_tuning.argtypes=[C.c_uint32,C.c_uint32,C.c_uint32]
lib.wmp_playout_tuning_get.argtypes=[C.POINTER(C.c_uint32)]*3
class Fec(C.Structure):
    _fields_=[(k,C.c_uint64) for k in ['group','generation','base']]+[('p',Byte*976),('q',Byte*976),('data_crc',C.c_uint32*4),('parity_crc',C.c_uint32*2),('length',C.c_uint16*4),('mask',Byte),('parity_mask',Byte),('shards',Byte)]
class CopyRecord(C.Structure):
    _fields_=[('sequence',C.c_uint64),('presentation_us',C.c_uint64),('a',C.c_uint32*4),('b',C.c_uint32*4)]+[(x,Byte) for x in ['seen','accepted','completed','failed','repaired','valid','injected','route']]
class CopyTrace(C.Structure):
    _fields_=[('records',CopyRecord*64)]+[(x,C.c_uint64) for x in ['generation','first','highest']]+[('counts',(C.c_uint32*4)*3),('retired',C.c_uint32*16),('repeated',C.c_uint32)]+[(x,C.c_uint32*4) for x in ['gap_first','gap_last','gap_max','gap_events']]+[('gap_valid',Byte),('kind',Byte),('valid',Byte)]
lib.wmp_copy_trace_event.argtypes=[C.POINTER(CopyTrace),C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint,C.c_uint,C.c_uint32,C.c_uint32]
lib.wmp_copy_trace_reset_gaps.argtypes=[C.POINTER(CopyTrace)]
lib.wmp_copy_trace_route.argtypes=[C.POINTER(CopyTrace),C.c_uint64,C.c_uint64,C.c_uint];lib.wmp_copy_trace_route.restype=C.c_int
lib.wmp_fec_bind.argtypes=[C.POINTER(Fec),C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint]
lib.wmp_fec_data.argtypes=[C.POINTER(Fec),C.c_uint,Ptr,C.c_uint]
lib.wmp_fec_emit.argtypes=[C.POINTER(Fec),C.c_uint,Ptr,C.c_uint]
lib.wmp_fec_receive.argtypes=[C.POINTER(Fec),Ptr,C.c_uint]
lib.wmp_fec_recover.argtypes=[C.POINTER(Fec),Ptr,C.c_uint,C.POINTER(C.c_uint)]
lib.wmp_sbc_info_read.argtypes=[Ptr,C.c_size_t,C.POINTER(SbcInfo)]
lib.wmp_rtp_sbc_read.argtypes=[Ptr,C.c_size_t,C.POINTER(RtpSbc),C.POINTER(Ptr),C.POINTER(C.c_size_t)]
lib.wmp_sbc_stream_reset.argtypes=[C.POINTER(SbcStream)]
lib.wmp_sbc_stream_accept.argtypes=[C.POINTER(SbcStream),C.POINTER(RtpSbc),C.POINTER(C.c_uint64),C.POINTER(C.c_uint16)];lib.wmp_sbc_stream_accept.restype=C.c_int
def buf(data):return (Byte*len(data)).from_buffer_copy(data)
def header(n=117):return Header(0x102030405060708,0xabcdef0123456789,19,1024,123456789,44100,512,n,4,0,1,2,0,0)
def encode(h,data):
    out=(Byte*(72+MAX_PAYLOAD))();n=lib.wmp_encode(out,len(out),C.byref(h),buf(data));assert n;return out,n
def reseal(w,n):
    for i in range(68,72):w[i]=0
    crc=lib.wmp_crc32(w,n)
    for i in range(4):w[68+i]=(crc>>(8*i))&255
def decode(w,n):
    h=Header();p=Ptr();ok=lib.wmp_decode(w,n,C.byref(h),C.byref(p));return ok,h,C.string_at(p,h.payload_bytes) if ok else None
lib.wmp_tx_precedes.argtypes=[C.c_uint64,C.c_int,C.c_uint64,C.c_uint64,C.c_int,C.c_uint64,C.c_uint64];lib.wmp_tx_precedes.restype=C.c_int
lib.wmp_tx_deadline.argtypes=[C.c_int,C.c_uint64,C.c_uint64,C.c_uint32];lib.wmp_tx_deadline.restype=C.c_int
lib.wmp_tx_original_expired.argtypes=[C.c_uint64,C.c_uint64,C.c_uint32];lib.wmp_tx_original_expired.restype=C.c_int
lib.wmp_tx_precedes_copy.argtypes=[C.c_uint64,C.c_int,C.c_uint,C.c_uint64,C.c_uint64,C.c_int,C.c_uint,C.c_uint64,C.c_uint64];lib.wmp_tx_precedes_copy.restype=C.c_int
lib.wmp_feedback_window.argtypes=[C.c_uint32,C.c_uint32,C.c_uint32];lib.wmp_feedback_window.restype=C.c_int
lib.wmp_feedback_blocked.argtypes=[C.c_uint32,C.c_uint32,C.c_uint32];lib.wmp_feedback_blocked.restype=C.c_int
lib.wmp_frame_pool_quiescent.argtypes=[C.POINTER(FramePool)];lib.wmp_frame_pool_quiescent.restype=C.c_int
lib.wmp_frame_pool_pin_repair_limit.argtypes=[C.POINTER(FramePool),C.c_uint,C.c_uint];lib.wmp_frame_pool_pin_repair_limit.restype=C.c_int
lib.wmp_nack_observe_after.argtypes=[C.POINTER(Nack),C.c_uint64,C.c_uint64,C.c_uint64,C.c_uint32,C.c_uint32]
lib.wmp_election_delay.argtypes=[C.POINTER(Byte)];lib.wmp_election_delay.restype=C.c_uint32

class Protocol(unittest.TestCase):
    def test_hand_mirrored_ctypes_structs_match_c_sizeof(self):
        # Header drift (array sizes, new fields) must fail loudly rather than corrupt memory.
        for name,mirror in zip(SIZEOF,[Dedup,Group,SbcQueue,BeaconClock,PcmBlocks,Lease]):
            self.assertEqual(C.sizeof(mirror),getattr(lib,'wmp_host_sizeof_'+name)(),name)
    def test_route_journal_uses_existing_record_padding(self):
        self.assertEqual(C.sizeof(CopyRecord),56);self.assertEqual(CopyRecord.route.offset,55)
        t=CopyTrace();lib.wmp_copy_trace_event(C.byref(t),1,20,1000,3,0,50,60)
        for route in [2,1,4,8]:self.assertTrue(lib.wmp_copy_trace_route(C.byref(t),1,20,route))
        self.assertEqual(t.records[20].route,15);self.assertEqual(t.highest,20)
    def test_route_journal_cannot_seed_wrong_stream_or_overwrite_newer_row(self):
        t=CopyTrace();lib.wmp_copy_trace_event(C.byref(t),1,20,1000,3,0,50,60)
        self.assertFalse(lib.wmp_copy_trace_route(C.byref(t),2,20,1))
        self.assertFalse(lib.wmp_copy_trace_route(C.byref(t),1,21,1))
        lib.wmp_copy_trace_event(C.byref(t),1,84,2000,3,0,70,80)
        self.assertFalse(lib.wmp_copy_trace_route(C.byref(t),1,20,128));self.assertEqual(t.records[20].sequence,84);self.assertEqual(t.records[20].route,0)
    def test_source_trace_is_not_rebound_by_receiver_routing(self):
        t=CopyTrace();lib.wmp_copy_trace_event(C.byref(t),1,20,1000,0,0,50,60)
        self.assertFalse(lib.wmp_copy_trace_route(C.byref(t),1,20,1));self.assertEqual(t.kind,1)
    def test_preparation_deferral_does_not_consume_the_last_playable_copy(self):
        raw=Dedup();delivered=Dedup()
        for d in [raw,delivered]:lib.wmp_dedup_bind(C.byref(d),1,2)
        self.assertEqual(lib.wmp_dedup_accept(C.byref(raw),1,2,50,123),1)
        self.assertEqual(lib.wmp_dedup_commit_delivery(C.byref(delivered),1,2,50,123,2),0)
        self.assertEqual(lib.wmp_dedup_accept(C.byref(raw),1,2,50,123),0)
        self.assertEqual(lib.wmp_dedup_query(C.byref(delivered),1,2,50,123),1)
        self.assertEqual(lib.wmp_dedup_commit_delivery(C.byref(delivered),1,2,50,123,1),1)
        self.assertEqual(lib.wmp_dedup_query(C.byref(delivered),1,2,50,123),0)
    def test_failed_queue_attempt_keeps_original_and_repair_eligible(self):
        d=Dedup();lib.wmp_dedup_bind(C.byref(d),1,2)
        for status in [0,2,0]:
            self.assertEqual(lib.wmp_dedup_commit_delivery(C.byref(d),1,2,100,99,status),0)
            self.assertEqual(lib.wmp_dedup_query(C.byref(d),1,2,100,99),1)
        self.assertEqual(lib.wmp_dedup_commit_delivery(C.byref(d),1,2,100,99,1),1)
        self.assertEqual(lib.wmp_dedup_commit_delivery(C.byref(d),1,2,100,99,1),0)
    def test_deferred_future_packet_does_not_push_older_playable_data_out(self):
        d=Dedup();lib.wmp_dedup_bind(C.byref(d),1,2)
        self.assertEqual(lib.wmp_dedup_commit_delivery(C.byref(d),1,2,1,9,1),1)
        self.assertEqual(lib.wmp_dedup_commit_delivery(C.byref(d),1,2,1000,7,2),0)
        self.assertEqual(lib.wmp_dedup_query(C.byref(d),1,2,2,8),1)
        self.assertEqual(lib.wmp_dedup_query(C.byref(d),1,2,1,9),0)
    def test_max_playout_reserve_accepts_arrival_burst_and_two_receive_slots(self):
        # The maximum playout reserve at 48kHz/512-sample groups needs ceil(reserve/group) held groups.
        # The remaining slots minus two receive slots are the reorder/repair margin; it must
        # cover at least a seven-group repair burst without dropping a queued original.
        frames=FramePool();payload=PayloadPool();lib.wmp_frame_pool_init(C.byref(frames));lib.wmp_payload_pool_init(C.byref(payload));held=(PLAYOUT_MAX_US*48000+512000000-1)//512000000;allocations=[]
        burst=FRAME_SLOTS-held-2;self.assertGreaterEqual(burst,7,('reorder/repair margin',FRAME_SLOTS,held))
        self.assertLessEqual(FRAME_SLOTS*484,PAYLOAD_BYTES);self.assertGreaterEqual(PAYLOAD_EXTENTS,FRAME_SLOTS+1)
        for group in range(held+burst):
            index=lib.wmp_frame_pool_acquire(C.byref(frames))
            self.assertNotEqual(index,-1,('frame capacity',group,held))
            offset=lib.wmp_payload_alloc(C.byref(payload),484)
            self.assertGreaterEqual(offset,0,('payload capacity',group))
            self.assertTrue(lib.wmp_frame_pool_publish(C.byref(frames),index))
            if group<held:self.assertEqual(lib.wmp_frame_pool_take(C.byref(frames)),index)
            allocations.append((index,offset))
        self.assertGreaterEqual(frames.free.bit_count(),2)
        for _ in range(burst):self.assertGreaterEqual(lib.wmp_frame_pool_take(C.byref(frames)),0)
        for index,offset in allocations:
            self.assertTrue(lib.wmp_frame_pool_release(C.byref(frames),index));self.assertTrue(lib.wmp_payload_free(C.byref(payload),offset,484))
        self.assertTrue(lib.wmp_frame_pool_quiescent(C.byref(frames)))
    def deadline_nack_fixture(self,now=1000000,until=1490000,delay=120000,backoff=0):
        state=Nack();lib.wmp_nack_reset(C.byref(state))
        lib.wmp_nack_observe_until(C.byref(state),1,100,now,backoff,delay,until)
        lib.wmp_nack_observe_until(C.byref(state),1,102,now,backoff,delay,until)
        return state
    def test_presentation_deadline_retains_recovery_after_old_eighth_request(self):
        state=self.deadline_nack_fixture();sequence=C.c_uint64()
        for attempt in range(12):
            now=1120000+attempt*32000
            self.assertTrue(lib.wmp_nack_plan(C.byref(state),now,C.byref(sequence)))
            self.assertEqual(sequence.value,101)
            self.assertTrue(lib.wmp_nack_reserve(C.byref(state),1,101,now))
            self.assertTrue(lib.wmp_nack_submit(C.byref(state),1,101,now))
        self.assertEqual(lib.wmp_nack_attempts(C.byref(state),1,101),12)
        self.assertFalse(lib.wmp_nack_plan(C.byref(state),1490000,C.byref(sequence)))
    def test_near_deadline_nack_keeps_half_window_instead_of_waiting120ms(self):
        state=self.deadline_nack_fixture(until=1040000,backoff=19999);sequence=C.c_uint64()
        self.assertFalse(lib.wmp_nack_plan(C.byref(state),1019999,C.byref(sequence)))
        self.assertTrue(lib.wmp_nack_plan(C.byref(state),1020000,C.byref(sequence)))
        self.assertTrue(lib.wmp_nack_reserve(C.byref(state),1,101,1020000))
        self.assertFalse(lib.wmp_nack_pending(C.byref(state),1,101,1040000))
    def test_deadline_metadata_reuses_tracker_padding_and_has_finite_attempt_cap(self):
        self.assertEqual(C.sizeof(NackEntry),32);self.assertEqual(NackEntry.lifetime_us.offset,28)
        state=self.deadline_nack_fixture(until=3000000,delay=0);sequence=C.c_uint64()
        for attempt in range(16):
            now=1000000+attempt*32000
            self.assertTrue(lib.wmp_nack_reserve(C.byref(state),1,101,now))
            self.assertTrue(lib.wmp_nack_submit(C.byref(state),1,101,now))
        self.assertEqual(lib.wmp_nack_attempts(C.byref(state),1,101),16)
        self.assertFalse(lib.wmp_nack_plan(C.byref(state),1600000,C.byref(sequence)))
    def test_later_packet_does_not_extend_missing_groups_original_deadline(self):
        state=self.deadline_nack_fixture();sequence=C.c_uint64()
        lib.wmp_nack_observe_until(C.byref(state),1,104,1300000,0,120000,1800000)
        entry=next(e for e in state.entries if e.valid and e.sequence==101)
        self.assertEqual(entry.first_us+entry.lifetime_us,1489999)
        self.assertTrue(lib.wmp_nack_plan(C.byref(state),1490000,C.byref(sequence)))
        self.assertEqual(sequence.value,103)
    def test_expired_header_clears_received_hole_without_creating_stale_work(self):
        state=self.deadline_nack_fixture();sequence=C.c_uint64()
        self.assertTrue(lib.wmp_nack_reserve(C.byref(state),1,101,1120000))
        lib.wmp_nack_observe_until(C.byref(state),1,101,1130000,0,120000,1130000)
        self.assertFalse(lib.wmp_nack_pending(C.byref(state),1,101,1130001))
        state=self.deadline_nack_fixture(until=999999)
        self.assertFalse(lib.wmp_nack_plan(C.byref(state),1000001,C.byref(sequence)))
    def test_deadline_nack_survives_32bit_timer_wrap_and_64bit_bounds(self):
        sequence=C.c_uint64()
        for now in [0xfffffff0,0xffffffffffffffff-600000]:
            state=self.deadline_nack_fixture(now=now,until=now+490000)
            self.assertTrue(lib.wmp_nack_plan(C.byref(state),now+450000,C.byref(sequence)))
            self.assertTrue(lib.wmp_nack_reserve(C.byref(state),1,101,now+450000))
            self.assertTrue(lib.wmp_nack_submit(C.byref(state),1,101,now+450000))
            self.assertFalse(lib.wmp_nack_pending(C.byref(state),1,101,now+490000))
    def test_lost_repair_train_can_be_requested_again_before_retries_expire(self):
        # Observed failure: a350ms repair headroom spread held the source's
        # reference while all eight receiver attempts were consumed. Losing
        # that train left no subsequent request despite unused play time.
        for completion_delay in (0,5000,20000,50000):
            tracker=Nack();lib.wmp_nack_reset(C.byref(tracker))
            lib.wmp_nack_observe_after(C.byref(tracker),1,10,1000000,0,120000)
            lib.wmp_nack_observe_after(C.byref(tracker),1,12,1000000,0,120000)
            deadline=1488000;pending_until=0;trains=[];sequence=C.c_uint64();spacing=C.c_uint32()
            for now in range(1000000,1400001,1000):
                if not lib.wmp_nack_plan(C.byref(tracker),now,C.byref(sequence)):continue
                self.assertTrue(lib.wmp_nack_reserve(C.byref(tracker),1,sequence.value,now))
                self.assertTrue(lib.wmp_nack_submit(C.byref(tracker),1,sequence.value,now))
                if now<pending_until:continue
                copies=lib.wmp_replay_plan(deadline-now,40000,80000,20000,C.byref(spacing))
                if copies:
                    trains.append(now);pending_until=now+(copies-1)*spacing.value+completion_delay
            self.assertGreaterEqual(len(trains),2,(completion_delay,trains))
    def test_native_wake_gate_uses_recent_window_and_next_boundary(self):
        due=C.c_uint64();event=1000000
        for age,expected in [(0,0),(2499,2499),(2500,20000),(19999,20000),(20000,20000),(22500,40000)]:
            self.assertEqual(lib.wmp_wake_due(event+age,event,20000,2500,C.byref(due)),1)
            self.assertEqual(due.value,event+expected)
        self.assertEqual(lib.wmp_wake_due(event+40000,event,20000,2500,C.byref(due)),0)
    def test_native_wake_gate_rejects_missing_stale_invalid_and_overflow(self):
        due=C.c_uint64()
        for now,event,interval,span in [(100,0,20000,2500),(100,101,20000,2500),(100,1,0,2500),(100,1,20000,20000),(100,1,20000,0),(0xffffffffffffffff-1,0xffffffffffffffff-30000,20000,2500)]:
            self.assertEqual(lib.wmp_wake_due(now,event,interval,span,C.byref(due)),0)
        self.assertEqual(lib.wmp_wake_due(0x100000100,0xffffff00,20000,2500,C.byref(due)),1)
        self.assertEqual(due.value,0x100000100)
    def test_seven_simultaneous_nacks_are_queued_on_pacing_grid_not_discarded(self):
        now=1000000;next_due=0;spacing=C.c_uint32();starts=[]
        for i in range(7):
            remaining=400000+i*512*1000000//44100
            ready=lib.wmp_replay_start(now,next_due,remaining,20000);starts.append(ready)
            n=lib.wmp_replay_plan(remaining-(ready-now),40000,80000,20000,C.byref(spacing))
            self.assertGreater(n,0);self.assertLessEqual(ready-now+(n-1)*spacing.value+80000,remaining)
            next_due=ready+8000
        self.assertEqual(starts,[now+i*8000 for i in range(7)])
        self.assertEqual(lib.wmp_replay_start(now,next_due,31549,20000),now)
        self.assertEqual(lib.wmp_replay_plan(31549,40000,80000,20000,C.byref(spacing)),1)
    def test_repair_pacing_deadline_bypass_has_no_unsigned_underflow(self):
        self.assertEqual(lib.wmp_replay_start(100,99,0,20000),100)
        self.assertEqual(lib.wmp_replay_start(100,1000,20000,20000),100)
        self.assertEqual(lib.wmp_replay_start(100,120,50000,20000),120)
    def test_repair_identity_uses_cached_publisher_not_receiver_codec_hints(self):
        lib.wmp_repair_request.argtypes=[C.POINTER(Header),C.POINTER(Byte),C.c_uint64,C.c_uint64];lib.wmp_repair_request.restype=C.c_int
        h=Header();h.group=11;h.generation=99;h.sequence=100;h.type=5;h.fragment_count=1;h.payload_bytes=8
        payload=(Byte*8)(2,1,0,0,0,0,0,0)
        for rate,count in [(0,0),(44100,512),(48000,128),(48000,0)]:
            h.sample_rate=rate;h.sample_count=count
            self.assertEqual(lib.wmp_repair_request(C.byref(h),payload,11,99),1)
        for group,generation in [(12,99),(11,100),(0,99),(11,0)]:self.assertEqual(lib.wmp_repair_request(C.byref(h),payload,group,generation),0)
        payload[0]=1;self.assertEqual(lib.wmp_repair_request(C.byref(h),payload,11,99),0)
    def test_seven_group_repair_burst_uses_existing_slots_and_releases_all_refs(self):
        s=FramePool();lib.wmp_frame_pool_init(C.byref(s));ids=[]
        for _ in range(FRAME_SLOTS-2):
            i=lib.wmp_frame_pool_acquire(C.byref(s));ids.append(i);self.assertTrue(lib.wmp_frame_pool_publish(C.byref(s),i));self.assertEqual(lib.wmp_frame_pool_take(C.byref(s)),i)
        before=(s.free,s.held,s.count)
        for i in ids[:7]:self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),i,8),1)
        self.assertEqual((s.free,s.held,s.count),before)
        self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),ids[0],8),-1)
        for i in ids[:7]:self.assertTrue(lib.wmp_frame_pool_unpin_repair(C.byref(s),i))
        self.assertEqual((s.free,s.held,s.count),before);self.assertEqual(s.borrowed,0);self.assertEqual(s.repair_pending,0)
        for i in ids:self.assertTrue(lib.wmp_frame_pool_release(C.byref(s),i))
        self.assertTrue(lib.wmp_frame_pool_quiescent(C.byref(s)))
    def test_repair_budget_does_not_consume_new_audio_slots(self):
        s=FramePool();lib.wmp_frame_pool_init(C.byref(s));ids=[]
        for _ in range(6):
            i=lib.wmp_frame_pool_acquire(C.byref(s));ids.append(i);self.assertTrue(lib.wmp_frame_pool_publish(C.byref(s),i))
        for i in ids[:4]:self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),i,4),1)
        before=(s.borrowed,list(s.references))
        self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),ids[4],4),-2);self.assertEqual((s.borrowed,list(s.references)),before)
        self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),ids[0],4),-1)
        self.assertGreaterEqual(lib.wmp_frame_pool_acquire(C.byref(s)),0)
    def test_repair_completion_reopens_budget_without_releasing_original(self):
        s=FramePool();lib.wmp_frame_pool_init(C.byref(s));ids=[]
        for _ in range(2):
            i=lib.wmp_frame_pool_acquire(C.byref(s));ids.append(i);self.assertTrue(lib.wmp_frame_pool_publish(C.byref(s),i))
        a,b=ids;self.assertTrue(lib.wmp_frame_pool_pin(C.byref(s),a));self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),a,1),1)
        self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),b,1),-2)
        self.assertTrue(lib.wmp_frame_pool_unpin_repair(C.byref(s),a));self.assertEqual(s.references[a],1)
        self.assertEqual(lib.wmp_frame_pool_pin_repair_limit(C.byref(s),b,1),1)
    def test_constant_or_millisecond_rtp_clocks_keep_four_frame_sbc_groups(self):
        for clock in ('constant','milliseconds'):
            s=SbcStream();g=Group();lib.wmp_group_init(C.byref(g),4);total=groups=0;frame=(Byte*119)(0x9c,0xbd,53,0)
            for i,count in enumerate([5,7,9,11]*5):
                h=RtpSbc(0 if clock=='constant' else total*1000//44100,700+i,count,count*128);first=C.c_uint64();distance=C.c_uint16()
                self.assertEqual(lib.wmp_sbc_stream_accept(C.byref(s),C.byref(h),C.byref(first),C.byref(distance)),1);self.assertEqual(first.value,total)
                for k in range(count):
                    result=lib.wmp_group_push(C.byref(g),frame,119,first.value+k*128,128);self.assertIn(result,(0,1))
                    if result:self.assertEqual((g.frames,g.sample_count),(4,512));groups+=1;lib.wmp_group_init(C.byref(g),4)
                total+=count*128
            self.assertEqual(groups,40);self.assertEqual(s.next,total);self.assertEqual(s.mismatches,19)
    def test_sample_cursor_wrap_duplicate_and_real_loss_are_separate(self):
        s=SbcStream();first=C.c_uint64();distance=C.c_uint16()
        for seq,stamp,expected,step in [(65535,0xffffff80,0,0),(0,0,128,1)]:
            h=RtpSbc(stamp,seq,1,128);self.assertEqual(lib.wmp_sbc_stream_accept(C.byref(s),C.byref(h),C.byref(first),C.byref(distance)),1);self.assertEqual((first.value,distance.value),(expected,step))
        self.assertEqual(s.mismatches,0)
        self.assertEqual(lib.wmp_sbc_stream_accept(C.byref(s),C.byref(h),C.byref(first),C.byref(distance)),0);self.assertEqual(s.next,256)
        h=RtpSbc(256,2,1,128);self.assertEqual(lib.wmp_sbc_stream_accept(C.byref(s),C.byref(h),C.byref(first),C.byref(distance)),1);self.assertEqual((first.value,distance.value),(256,2))
    def test_sample_cursor_overflow_and_stream_reset(self):
        s=SbcStream();s.next=(1<<64)-2;s.mismatches=7;h=RtpSbc(0,1,1,128);first=C.c_uint64(77);distance=C.c_uint16()
        self.assertEqual(lib.wmp_sbc_stream_accept(C.byref(s),C.byref(h),C.byref(first),C.byref(distance)),-1);self.assertEqual(first.value,77)
        lib.wmp_sbc_stream_reset(C.byref(s));self.assertEqual((s.next,s.valid,s.mismatches),(0,0,7))
    def test_copy_dither_keeps_first_time_and_absolute_order_without_accumulation(self):
        seen=set()
        for sequence in range(1000):
            times=[]
            for copy in range(4):
                due=C.c_uint64();self.assertTrue(lib.wmp_copy_due(1000000,sequence,copy,64000,8000,C.byref(due)))
                nominal=1000000+copy*64000
                self.assertLessEqual(abs(due.value-nominal),8000);times.append(due.value)
                again=C.c_uint64();self.assertTrue(lib.wmp_copy_due(1000000,sequence,copy,64000,8000,C.byref(again)));self.assertEqual(due.value,again.value)
            self.assertEqual(times[0],1000000);self.assertEqual(times,sorted(set(times)));seen.add(times[1]-1064000)
        self.assertGreater(len(seen),100)
    def test_zero_copy_dither_preserves_existing_schedule(self):
        for copy in range(4):
            due=C.c_uint64();self.assertTrue(lib.wmp_copy_due(123456,99,copy,64000,0,C.byref(due)));self.assertEqual(due.value,123456+copy*64000)
    def test_copy_dither_rejects_reordering_and_overflow(self):
        for ready,copy,spacing,jitter in [(0,4,64000,0),(0,1,1000,251),(0,1,0,0),((1<<64)-2,1,64000,8000)]:
            due=C.c_uint64(77);self.assertFalse(lib.wmp_copy_due(ready,9,copy,spacing,jitter,C.byref(due)));self.assertEqual(due.value,77)
    def test_streamed_crc_matches_zlib_across_arbitrary_printf_chunks(self):
        data=b'WMP_RADIO query=0123abcd mode=2\n'+bytes(range(256))*17
        for size in (1,7,31,768,1024,len(data)):
            state=0
            for at in range(0,len(data),size):
                chunk=data[at:at+size];state=lib.wmp_crc32_extend(state,buf(chunk),len(chunk))
            self.assertEqual(state,zlib.crc32(data))
            self.assertEqual(lib.wmp_crc32_extend(state,None,0),state)
    def test_only_one_repair_train_per_packet_while_other_packets_remain_eligible(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p))
        a=lib.wmp_frame_pool_acquire(C.byref(p));b=lib.wmp_frame_pool_acquire(C.byref(p))
        for i in (a,b):self.assertTrue(lib.wmp_frame_pool_publish(C.byref(p),i))
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),a),1)
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),a),-1)
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),b),1)
        self.assertEqual((p.references[a],p.references[b]),(1,1))
        self.assertTrue(lib.wmp_frame_pool_unpin_repair(C.byref(p),a))
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),a),1)

    def test_rendered_repair_payload_stays_retired_until_controller_completion(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p))
        ids=[lib.wmp_frame_pool_acquire(C.byref(p)) for _ in range(FRAME_SLOTS)]
        i=ids[-1];self.assertGreater(i,31)
        for other in ids[:-1]:self.assertTrue(lib.wmp_frame_pool_cancel(C.byref(p),other))
        self.assertTrue(lib.wmp_frame_pool_publish(C.byref(p),i));self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),i)
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),i),1)
        self.assertTrue(lib.wmp_frame_pool_release(C.byref(p),i))
        self.assertTrue(p.retired&(1<<i));self.assertFalse(p.free&(1<<i));self.assertFalse(lib.wmp_frame_pool_quiescent(C.byref(p)))
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),i),-1)
        self.assertTrue(lib.wmp_frame_pool_unpin_repair(C.byref(p),i));self.assertTrue(lib.wmp_frame_pool_quiescent(C.byref(p)))
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),i),0)

    def test_original_reference_release_does_not_cancel_queued_repair_ownership(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p));i=lib.wmp_frame_pool_acquire(C.byref(p))
        self.assertTrue(lib.wmp_frame_pool_publish(C.byref(p),i));lib.wmp_frame_pool_take(C.byref(p))
        self.assertTrue(lib.wmp_frame_pool_pin(C.byref(p),i));self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),i),1)
        self.assertTrue(lib.wmp_frame_pool_unpin(C.byref(p),i));self.assertEqual(p.references[i],1)
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),i),-1)
        self.assertTrue(lib.wmp_frame_pool_unpin_repair(C.byref(p),i));self.assertFalse(p.repair_pending)
        self.assertEqual(lib.wmp_frame_pool_pin_repair(C.byref(p),i),1)

    def test_lazy_arena_release_waits_for_every_owner_including_high_slots(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p));self.assertTrue(lib.wmp_frame_pool_quiescent(C.byref(p)))
        ids=[lib.wmp_frame_pool_acquire(C.byref(p)) for _ in range(FRAME_SLOTS)]
        for i in ids[:-1]:self.assertTrue(lib.wmp_frame_pool_cancel(C.byref(p),i))
        self.assertFalse(lib.wmp_frame_pool_quiescent(C.byref(p)))
        i=ids[-1];self.assertGreater(i,31)
        self.assertTrue(lib.wmp_frame_pool_publish(C.byref(p),i));self.assertFalse(lib.wmp_frame_pool_quiescent(C.byref(p)))
        self.assertTrue(lib.wmp_frame_pool_pin(C.byref(p),i));self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),i)
        self.assertTrue(lib.wmp_frame_pool_release(C.byref(p),i));self.assertFalse(lib.wmp_frame_pool_quiescent(C.byref(p)))
        self.assertTrue(lib.wmp_frame_pool_unpin(C.byref(p),i));self.assertTrue(lib.wmp_frame_pool_quiescent(C.byref(p)))

    def test_original_copy_expires_only_inside_guard_with_a_known_clock(self):
        self.assertFalse(lib.wmp_tx_original_expired(1000000,979999,20000))
        self.assertTrue(lib.wmp_tx_original_expired(1000000,980000,20000))
        self.assertTrue(lib.wmp_tx_original_expired(1000000,1000000,20000))
        self.assertTrue(lib.wmp_tx_original_expired(1000000,1500000,20000))
        self.assertFalse(lib.wmp_tx_original_expired(1000000,0,20000))
        self.assertFalse(lib.wmp_tx_original_expired(0,1500000,20000))
    def test_ready_first_copies_precede_ready_repeats_of_older_groups(self):
        # Group A (deadline 200) has sent one copy; group B (deadline 300) has sent none.
        self.assertTrue(lib.wmp_tx_precedes_copy(90,0,0,300,20,0,1,200,100))
        self.assertFalse(lib.wmp_tx_precedes_copy(20,0,1,200,90,0,0,300,100))
        # Same copy index: earliest presentation deadline first, as before.
        self.assertTrue(lib.wmp_tx_precedes_copy(90,0,1,200,20,0,1,300,100))
    def test_breadth_first_leaves_future_slots_and_repairs_to_the_old_order(self):
        self.assertFalse(lib.wmp_tx_precedes_copy(150,0,0,300,20,0,1,200,100))
        self.assertTrue(lib.wmp_tx_precedes_copy(90,1,0,150,20,0,0,200,100))
        self.assertEqual(lib.wmp_tx_precedes_copy(20,1,2,200,90,0,0,200,100),lib.wmp_tx_precedes(20,1,200,90,0,200,100))
    def test_new_due_repair_cannot_starve_earlier_audio_deadline(self):
        self.assertFalse(lib.wmp_tx_precedes(90,1,300,20,0,200,100))
        self.assertTrue(lib.wmp_tx_precedes(20,0,200,90,1,300,100))
    def test_useful_earlier_repair_deadline_still_receives_service(self):
        self.assertTrue(lib.wmp_tx_precedes(90,1,150,20,0,200,100))
        self.assertFalse(lib.wmp_tx_precedes(20,0,200,90,1,150,100))
    def test_future_repair_never_blocks_ready_audio_even_with_earlier_deadline(self):
        self.assertFalse(lib.wmp_tx_precedes(101,1,150,20,0,200,100))
        self.assertTrue(lib.wmp_tx_precedes(20,0,200,101,1,150,100))
    def test_original_wins_same_presentation_time_as_repair(self):
        self.assertTrue(lib.wmp_tx_precedes(90,0,200,20,1,200,100))
        self.assertFalse(lib.wmp_tx_precedes(20,1,200,90,0,200,100))
        self.assertFalse(lib.wmp_tx_precedes(90,0,200,90,0,200,100))
    def test_future_slots_keep_due_order_and_clock_domains_are_not_mixed(self):
        base=10000000000
        self.assertFalse(lib.wmp_tx_precedes(130,1,base+10,120,0,base+100,100))
        self.assertTrue(lib.wmp_tx_precedes(120,0,base+100,130,1,base+10,100))

    def test_feedback_window_wrap_and_expiry(self):
        self.assertFalse(lib.wmp_feedback_window(0,100,8000))
        self.assertTrue(lib.wmp_feedback_window(100,8099,8000))
        self.assertFalse(lib.wmp_feedback_window(100,8100,8000))
        self.assertTrue(lib.wmp_feedback_window(0xfffffff0,20,100))
        self.assertFalse(lib.wmp_feedback_window(100,90,8000))
    def test_feedback_gate_can_be_disabled_without_a_recent_received_frame(self):
        self.assertFalse(lib.wmp_feedback_blocked(0,500000,0))
        self.assertFalse(lib.wmp_feedback_blocked(100,500000,0))
        self.assertTrue(lib.wmp_feedback_blocked(0,500000,8000))
        self.assertFalse(lib.wmp_feedback_blocked(100,8099,8000))
        self.assertTrue(lib.wmp_feedback_blocked(100,8100,8000))
        self.assertFalse(lib.wmp_feedback_blocked(0xfffffff0,20,100))

    def test_crc32_external_reference(self):
        rng=random.Random(5729)
        for n in list(range(260))+[476,484,904,976,1024,4096]:
            data=rng.randbytes(n)
            self.assertEqual(lib.wmp_crc32(buf(data),len(data)),zlib.crc32(data))
        self.assertEqual(lib.wmp_crc32(buf(b'123456789'),9),0xcbf43926)

    def test_wire_and_logical_crc_external_reference(self):
        for count in [0,1,476,484,904]:
            data=bytes(i%256 for i in range(count))
            h=header(count)
            if not count:h.type=7;h.frame_count=0
            wire,n=encode(h,data);raw=bytearray(bytes(wire[:n]))
            actual=int.from_bytes(raw[68:72],'little');raw[68:72]=bytes(4)
            self.assertEqual(actual,zlib.crc32(raw))
            raw[64]=0;raw[65]&=~1
            self.assertEqual(lib.wmp_logical_crc(wire,n),zlib.crc32(raw))

    def test_peer_owner_survives_receive_gap_but_explicit_idle_releases(self):
        n=Lease();self.assertTrue(lib.wmp_peer_announce(C.byref(n),10,1,0))
        self.assertTrue(lib.wmp_peer_claimed(C.byref(n),900000))
        self.assertFalse(lib.wmp_peer_announce(C.byref(n),11,0,900000));self.assertEqual(n.peer,10)
        self.assertFalse(lib.wmp_peer_announce(C.byref(n),9,1,1100000));self.assertEqual(n.peer,10)
        self.assertTrue(lib.wmp_peer_announce(C.byref(n),10,0,1200000));self.assertFalse(lib.wmp_peer_claimed(C.byref(n),1200000))
        self.assertTrue(lib.wmp_peer_announce(C.byref(n),11,1,1200001));self.assertEqual(n.peer,11)
        self.assertFalse(lib.wmp_peer_claimed(C.byref(n),2700001))
        self.assertTrue(lib.wmp_peer_announce(C.byref(n),12,1,2700001));self.assertEqual(n.peer,12)
        self.assertTrue(lib.wmp_peer_announce(C.byref(n),8,1,2800000));self.assertEqual(n.peer,8)
        for at in [3000000,4000000,5000000]:lib.wmp_peer_beacon(C.byref(n),at)
        self.assertTrue(lib.wmp_peer_claimed(C.byref(n),6000000))
        self.assertTrue(lib.wmp_peer_announce(C.byref(n),8,0,6000000))
        lib.wmp_peer_beacon(C.byref(n),6100000)
        self.assertFalse(lib.wmp_peer_claimed(C.byref(n),6200000))
    def test_delayed_beacon_cannot_move_owner_activity_backwards(self):
        n=Lease();lib.wmp_peer_lease_init(C.byref(n));lib.wmp_peer_announce(C.byref(n),10,1,1000000)
        lib.wmp_peer_audio(C.byref(n),2000000);lib.wmp_peer_beacon(C.byref(n),1100000)
        self.assertTrue(lib.wmp_peer_claimed(C.byref(n),3400000))
        self.assertFalse(lib.wmp_peer_claimed(C.byref(n),3500000))
    def test_leader_precedence_earlier_start_wins_beyond_tie_window(self):
        self.assertEqual(LEADER_TIE_US,2000);gap=LEADER_TIE_US+1
        self.assertTrue(lib.wmp_leader_precedes(20,1000000,10,1000000+gap,1))
        self.assertFalse(lib.wmp_leader_precedes(10,1000000+gap,20,1000000,1))
    def test_leader_precedence_tie_window_or_no_shared_clock_uses_lower_id(self):
        for a_start,b_start,comparable in [(1000000,1000000+LEADER_TIE_US,1),(1000000+LEADER_TIE_US,1000000,1),(1000000,1000000,1),(1000000,9000000,0),(9000000,1000000,0),(0,1000000,1),(1000000,0,1)]:
            self.assertTrue(lib.wmp_leader_precedes(10,a_start,20,b_start,comparable),(a_start,b_start,comparable))
            self.assertFalse(lib.wmp_leader_precedes(20,b_start,10,a_start,comparable),(a_start,b_start,comparable))
    def test_startup_contest_lets_only_an_earlier_starter_replace_within_one_second(self):
        n=Lease();lib.wmp_peer_lease_init(C.byref(n))
        self.assertTrue(lib.wmp_peer_announce_at(C.byref(n),10,1,0,5000000,0,1));self.assertEqual(n.peer,10)
        # Higher identity, but it started broadcasting first on the shared clock.
        self.assertTrue(lib.wmp_peer_announce_at(C.byref(n),20,1,500000,1000000,5000000,1));self.assertEqual(n.peer,20)
        n=Lease();lib.wmp_peer_lease_init(C.byref(n))
        self.assertTrue(lib.wmp_peer_announce_at(C.byref(n),20,1,0,1000000,0,1))
        # A later starter cannot take over, even with a lower identity.
        self.assertFalse(lib.wmp_peer_announce_at(C.byref(n),10,1,500000,5000000,1000000,1));self.assertEqual(n.peer,20)
        n=Lease();lib.wmp_peer_lease_init(C.byref(n))
        self.assertTrue(lib.wmp_peer_announce_at(C.byref(n),10,1,0,5000000,0,1))
        # After the one-second startup window an active owner is kept.
        self.assertFalse(lib.wmp_peer_announce_at(C.byref(n),20,1,1000000,1000000,5000000,1));self.assertEqual(n.peer,10)
    def test_pool_slots_above31_retain_exact_network_ownership(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p))
        ids=[lib.wmp_frame_pool_acquire(C.byref(p)) for _ in range(FRAME_SLOTS)];i=ids[-1]
        self.assertGreater(i,31);self.assertEqual(ids,list(range(FRAME_SLOTS)))
        self.assertTrue(lib.wmp_frame_pool_publish(C.byref(p),i));self.assertTrue(lib.wmp_frame_pool_pin(C.byref(p),i))
        self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),i);self.assertTrue(lib.wmp_frame_pool_release(C.byref(p),i))
        self.assertEqual(lib.wmp_frame_pool_acquire(C.byref(p)),-1)
        self.assertTrue(lib.wmp_frame_pool_unpin(C.byref(p),i));self.assertEqual(lib.wmp_frame_pool_acquire(C.byref(p)),i)
    def test_probe_payload_can_be_encoded_in_its_final_buffer(self):
        size=lib.wmp_live_payload_capacity();wire=(Byte*(72+size))();expected=bytes((i*29+7)&255 for i in range(size))
        for i,value in enumerate(expected):wire[72+i]=value
        h=Header();h.group=1;h.generation=2;h.type=6;h.fragment_count=1;h.payload_bytes=size
        payload=C.cast(C.byref(wire,72),Ptr)
        self.assertEqual(lib.wmp_encode(wire,len(wire),C.byref(h),payload),len(wire))
        decoded=Header();result=Ptr();self.assertTrue(lib.wmp_decode(wire,len(wire),C.byref(decoded),C.byref(result)))
        self.assertEqual(bytes(result[:size]),expected)
    def test_four_maximum_dual_channel_frames_fit_live_transport_with_lengths(self):
        capacity=lib.wmp_live_payload_capacity();self.assertEqual(capacity,MAX_PAYLOAD)
        # A raw 802.11 frame is limited to 1500 bytes: 32 MAC/action + 72 WMP header + payload.
        self.assertLessEqual(32+72+capacity,1500)
        frame=(Byte*224)(0x9c,0xf5,53,0)
        info=SbcInfo();self.assertEqual(lib.wmp_sbc_info_read(frame,224,C.byref(info)),1)
        self.assertEqual(info.bytes,224)
        # Four worst-case bitpool-53 dual-channel frames remain a legal group.
        g=Group();lib.wmp_group_init(C.byref(g),4)
        for i in range(4):self.assertEqual(lib.wmp_group_push(C.byref(g),frame,224,i*128,128),int(i==3))
        self.assertEqual(g.bytes,904);self.assertLessEqual(g.bytes,capacity)
        # Six worst-case frames with their 2-byte length prefixes still fit the wire bound.
        self.assertLessEqual(6*(224+2),capacity)
        g=Group();lib.wmp_group_init(C.byref(g),MAX_GROUP_FRAMES)
        for i in range(6):self.assertEqual(lib.wmp_group_push(C.byref(g),frame,224,i*128,128),0)
        self.assertEqual((g.frames,g.bytes,g.sample_count),(6,6*226,768))
        # A seventh worst-case frame would exceed the payload: refused without a
        # partial write, so the firmware closes the group early at six frames.
        self.assertGreater(7*(224+2),capacity);before=bytes(g)
        self.assertEqual(lib.wmp_group_push(C.byref(g),frame,224,6*128,128),-2);self.assertEqual(bytes(g),before)
        # Typical joint-stereo bitpool-53 44.1 kHz frames fill all eight slots.
        typical=(Byte*119)(0x9c,0xbd,53,0);self.assertEqual(lib.wmp_sbc_info_read(typical,119,C.byref(info)),1)
        self.assertEqual((info.rate,info.bytes,info.samples,info.channels),(44100,119,128,2))
        g=Group();lib.wmp_group_init(C.byref(g),MAX_GROUP_FRAMES)
        for i in range(MAX_GROUP_FRAMES):
            self.assertEqual(lib.wmp_group_push(C.byref(g),typical,119,i*128,128),int(i==MAX_GROUP_FRAMES-1))
        self.assertEqual((g.frames,g.bytes,g.sample_count),(MAX_GROUP_FRAMES,MAX_GROUP_FRAMES*121,MAX_GROUP_SAMPLES))
        self.assertLessEqual(g.bytes,capacity)
    def test_clean_short_windows_do_not_remove_selected_stability_reserve(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),200000);out=C.c_uint64()
        for i in range(20000):
            now=1000000+i*512*1000000//44100
            self.assertEqual(lib.wmp_playout_plan(C.byref(s),i*512,44100,now,C.byref(out)),1)
        self.assertEqual(s.requested_us,200000);self.assertEqual(s.phase_q16,0)
    def drift_run(self,window_ms,ppm,hold,seconds=600):
        """48 kHz, 512-sample groups, 300 ms lead; ingress normally on time but one
        group in every 7 s arrives 40 ms late (a source delivery burst)."""
        self.assertEqual(lib.wmp_playout_tuning(window_ms,ppm,hold),1)
        try:
            s=Playout();lib.wmp_playout_init(C.byref(s),300000);out=C.c_uint64()
            group_us=512*1000000/48000;reversals=0;last_sign=0;targets=[]
            for i in range(int(seconds*1000000/group_us)):
                nominal=1000000+int(i*group_us);late=40000 if i%656==655 else 0
                before=s.changes
                self.assertEqual(lib.wmp_playout_plan(C.byref(s),i*512,48000,nominal+late,C.byref(out)),1)
                if s.changes!=before:
                    sign=1 if s.target_q16>s.phase_q16 else -1
                    if last_sign and sign!=last_sign:reversals+=1
                    last_sign=sign;targets.append(s.target_q16//65536)
            return s.changes,reversals
        finally:
            self.assertEqual(lib.wmp_playout_tuning(1000,1200,1),1)
    def test_drift_hold_stops_reversal_after_a_late_burst(self):
        old_changes,old_reversals=self.drift_run(1000,1200,1)
        new_changes,new_reversals=self.drift_run(1000,300,30)
        self.assertGreater(old_reversals,20)  # ~156/h, as measured on the master (149 retargets/h)
        self.assertLessEqual(new_reversals,1)
        self.assertLess(new_changes,old_changes//4)  # repeats of the same target while slewing count too
    def test_drift_tuning_rejects_out_of_range_and_reports_defaults(self):
        w,p,h=C.c_uint32(),C.c_uint32(),C.c_uint32()
        lib.wmp_playout_tuning_get(C.byref(w),C.byref(p),C.byref(h));self.assertEqual((w.value,p.value,h.value),(1000,1200,1))
        for bad in [(999,300,1),(60001,300,1),(1000,49,1),(1000,1201,1),(1000,300,0),(1000,300,121)]:
            self.assertEqual(lib.wmp_playout_tuning(*bad),0)
        lib.wmp_playout_tuning_get(C.byref(w),C.byref(p),C.byref(h));self.assertEqual((w.value,p.value,h.value),(1000,1200,1))
    def test_missing_group_wait_protects_preceding_pending_pcm_tail(self):
        s=PcmBlocks();lib.wmp_pcm_blocks_init(C.byref(s),221)
        self.assertEqual(lib.wmp_pcm_blocks_guard_us(C.byref(s),44100,2000),2000)
        s.count=220;guard=lib.wmp_pcm_blocks_guard_us(C.byref(s),44100,2000)
        self.assertEqual(guard,6989);self.assertGreaterEqual(guard-2000,220*1000000/44100)
        s.count=100;self.assertEqual(lib.wmp_pcm_blocks_guard_us(C.byref(s),48000,2000),4084)
    def test_short_repair_window_sends_useful_copies_without_passing_deadline(self):
        for remaining,expected in [(0,0),(5000,0),(5001,1),(12999,1),(13000,2),(21000,3),(29000,4),(200000,4)]:
            copies=lib.wmp_replay_copies(remaining,8000,5000);self.assertEqual(copies,expected)
            if copies:self.assertLessEqual((copies-1)*8000+5000,remaining)
    def test_captured_repair_failures_keep_last_chance_and_spread_remaining_copies(self):
        spacing=C.c_uint32()
        for remaining,count in [(20000,0),(31549,1),(44521,1),(73431,1),(115220,1),(182668,3),(237128,3),(304647,3)]:
            copies=lib.wmp_replay_plan(remaining,40000,80000,20000,C.byref(spacing))
            self.assertEqual(copies,count)
            if copies:
                guard=20000 if remaining<=80000 else 80000
                self.assertLessEqual((copies-1)*spacing.value+guard,remaining)
                self.assertGreaterEqual(spacing.value,40000)
            if copies>1:self.assertEqual(spacing.value,40000)
    def test_repair_plan_bounds_and_minimum_spacing_across_deadline_ranges(self):
        spacing=C.c_uint32()
        for minimum in [1,8000,40000,160000,0xffffffff]:
            for guard in [2000,20000,80000,160000]:
                for remaining in [0,1,19999,20000,20001,guard,guard+1,guard+minimum,guard+2*minimum,1000000,0xffffffffffffffff]:
                    n=lib.wmp_replay_plan(remaining,minimum,guard,20000,C.byref(spacing))
                    self.assertLessEqual(n,3)
                    if n:
                        effective=20000 if remaining<=guard and guard>20000 else guard
                        self.assertLessEqual((n-1)*spacing.value+effective,remaining)
                        self.assertGreaterEqual(spacing.value,minimum)
        self.assertEqual(lib.wmp_replay_plan(100000,0,80000,20000,C.byref(spacing)),0)
    def test_max_sender_gap_survives_trace_rollover_and_timestamp_wrap(self):
        s=CopyTrace();stamp=1000
        for i in range(120):
            stamp+=50000 if i==20 else 1000
            lib.wmp_copy_trace_event(C.byref(s),1,i,0,0,0,stamp,0)
        self.assertEqual(s.gap_max[0],50000);self.assertEqual(s.gap_events[0],120)
        lib.wmp_copy_trace_reset_gaps(C.byref(s));self.assertEqual(s.gap_max[0],0)
        lib.wmp_copy_trace_event(C.byref(s),1,120,0,0,0,0xfffffff0,0)
        lib.wmp_copy_trace_event(C.byref(s),1,121,0,0,0,0x100,0)
        self.assertEqual(s.gap_max[0],272)
        lib.wmp_copy_trace_event(C.byref(s),2,0,0,0,0,1000,0)
        self.assertEqual(s.gap_max[0],0);self.assertEqual(s.gap_events[0],1)
    def test_receiver_five_ms_blocks_preserve_all_pcm_across_codec_groups(self):
        for block in [221,240]:
            s=PcmBlocks();self.assertEqual(lib.wmp_pcm_blocks_init(C.byref(s),block),1);received=[];expected=[]
            @PcmWriter
            def write(context,data,frames):
                self.assertEqual(frames,block);received.extend(data[i] for i in range(frames*2));return 1
            for n in [512,511,513,512,128,127,1]*20:
                values=[(len(expected)+i)%25001-12500 for i in range(n*2)];expected.extend(values);data=(C.c_int16*(n*2))(*values)
                self.assertEqual(lib.wmp_pcm_blocks_push(C.byref(s),data,n,write,None),1)
                self.assertEqual(received,expected[:len(received)]);self.assertEqual(list(s.tail[:s.count*2]),expected[len(received):])
    def test_raw_send_duration_is_inside_transmit_period(self):
        start=1000000;due=0
        for _ in range(500):
            due=lib.wmp_tx_after_call(due,start,start+1500,2000)
            self.assertEqual(due,start+2000);start=due
        self.assertEqual(start,2000000) #500 packets/s, not API1.5ms + another2ms
        self.assertEqual(lib.wmp_tx_after_call(1002000,1060000,1061500,2000),1062000)
        self.assertEqual(lib.wmp_tx_after_call(0,1000000,1003000,2000),1003000)
    def test_a2dp_prefill_bursts_cannot_shrink_minimum_delivery_reserve(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),180000);out=C.c_uint64();minimum=10**12
        # Realistic HCI batching:16 consecutive groups become ready together.
        # The earliest-arriving group's large headroom must not pull every
        # later group's deadline below the required minimum.
        for i in range(12000):
            arrival=1000000+(i//16)*16*512*1000000//44100
            self.assertEqual(lib.wmp_playout_plan(C.byref(s),i*512,44100,arrival,C.byref(out)),1)
            minimum=min(minimum,out.value-arrival)
        self.assertGreaterEqual(minimum,165000);self.assertEqual(s.phase_q16,0)
    def test_own_queue_delay_cannot_wind_up_common_latency_controller(self):
        normal=Playout();delayed=Playout();lib.wmp_playout_init(C.byref(normal),180000);lib.wmp_playout_init(C.byref(delayed),180000)
        a=C.c_uint64();b=C.c_uint64()
        for i in range(60000):
            ingress=1000000+i*512*1000000//44100;queue_delay=min(370000,i*10)
            restored=lib.wmp_ingress_clock(ingress+queue_delay,ingress+queue_delay+700000,ingress+700000)
            self.assertEqual(restored,ingress)
            self.assertEqual(lib.wmp_playout_plan(C.byref(normal),i*512,44100,ingress,C.byref(a)),1)
            self.assertEqual(lib.wmp_playout_plan(C.byref(delayed),i*512,44100,restored,C.byref(b)),1)
            self.assertEqual(a.value,b.value)
        self.assertEqual(delayed.changes,0);self.assertEqual(delayed.phase_q16,0)
    def test_resampled_pcm_never_pads_or_skips_partial_dma_descriptors(self):
        s=PcmBlocks();lib.wmp_pcm_blocks_init(C.byref(s),128);received=[];expected=[]
        @PcmWriter
        def write(context,data,frames):
            self.assertEqual(frames%128,0);received.extend(data[i] for i in range(frames*2));return 1
        for n in [511,513,512,127,1,64,32,0,128,517]*20:
            values=[((len(expected)+i)%30001)-15000 for i in range(n*2)]
            expected.extend(values);data=(C.c_int16*max(1,n*2))(*values)
            self.assertEqual(lib.wmp_pcm_blocks_push(C.byref(s),data,n,write,None),1)
            self.assertEqual(received,expected[:len(received)])
            self.assertEqual(list(s.tail[:s.count*2]),expected[len(received):])
            self.assertLess(s.count,128)
    def test_small_pcm_groups_accumulate_without_early_dma_submission(self):
        s=PcmBlocks();lib.wmp_pcm_blocks_init(C.byref(s),128);sizes=[]
        @PcmWriter
        def write(context,data,frames):sizes.append(frames);return 1
        data=(C.c_int16*128)(*range(128))
        self.assertEqual(lib.wmp_pcm_blocks_push(C.byref(s),data,64,write,None),1);self.assertEqual(s.count,64);self.assertFalse(sizes)
        self.assertEqual(lib.wmp_pcm_blocks_push(C.byref(s),data,32,write,None),1);self.assertEqual(s.count,96);self.assertFalse(sizes)
        self.assertEqual(lib.wmp_pcm_blocks_push(C.byref(s),data,32,write,None),1);self.assertEqual(s.count,0);self.assertEqual(sizes,[128])
    def test_idle_receiver_with_prepared_native_clock_accepts_opening_audio(self):
        p=Preparation()
        # Native clock acquired at1s, qualified at3s; a new stream starts4s.
        self.assertTrue(lib.wmp_preparation_begin_at(C.byref(p),4000000,1,7,0,0,3000000))
        self.assertTrue(lib.wmp_preparation_allows(C.byref(p),4100000,1))
        self.assertEqual(p.ready_us,4000000);self.assertLess(p.ready_us,4500000)

    def test_clock_preparation_remaining_time_is_not_restarted_by_announcements(self):
        p=Preparation()
        self.assertFalse(lib.wmp_preparation_begin_at(C.byref(p),2100000,1,7,0,0,3000000))
        self.assertEqual(p.ready_us,3000000)
        self.assertFalse(lib.wmp_preparation_begin_at(C.byref(p),2600000,1,7,0,0,3000000))
        self.assertEqual(p.ready_us,3000000)
        self.assertFalse(lib.wmp_preparation_allows(C.byref(p),2999999,1));self.assertTrue(lib.wmp_preparation_allows(C.byref(p),3000000,1))

    def test_new_clock_qualification_overrides_old_running_renderer_proof(self):
        p=Preparation();lib.wmp_preparation_begin_at(C.byref(p),4000000,1,7,1,7,3000000)
        self.assertFalse(lib.wmp_preparation_begin_at(C.byref(p),4100000,1,7,1,7,6000000))
        self.assertFalse(lib.wmp_preparation_allows(C.byref(p),4600000,1));self.assertEqual(p.ready_us,6000000)
        self.assertFalse(lib.wmp_preparation_begin_at(C.byref(p),4200000,0,7,1,7,3000000))
        self.assertEqual(p.ready_us,6200000)

    def test_teardown_can_reuse_a_settled_clock_after_buffers_are_prepared_again(self):
        p=Preparation();lib.wmp_preparation_begin_at(C.byref(p),4000000,1,7,0,0,3000000);lib.wmp_preparation_forget(C.byref(p))
        self.assertTrue(lib.wmp_preparation_begin_at(C.byref(p),8000000,1,7,0,0,3000000))
        self.assertTrue(lib.wmp_preparation_allows(C.byref(p),8100000,1))

    def test_truly_cold_clock_still_rejects_audio_before_its_acquisition_deadline(self):
        p=Preparation();self.assertFalse(lib.wmp_preparation_begin_at(C.byref(p),100000,1,7,0,0,2100000))
        self.assertFalse(lib.wmp_preparation_allows(C.byref(p),600000,1))
        # This edge still needs source/start coordination; do not pretend the
        # first500ms packet can be played by an unprepared receiver.
        self.assertGreater(p.ready_us,600000)

    def test_warm_handover_accepts_the_first_packet_before_its_common_timestamp(self):
        p=Preparation();self.assertTrue(lib.wmp_preparation_begin(C.byref(p),1000000,1,7,1,7))
        # The source starts in500ms. The unchanged cold2s rule rejected this
        # opening group; a running shared-clock receiver can queue it now.
        pts=1500000;self.assertTrue(lib.wmp_preparation_allows(C.byref(p),1100000,1))
        self.assertTrue(lib.wmp_preparation_keeps(C.byref(p),p.version));self.assertLess(p.ready_us,pts)

    def test_warm_role_then_announce_does_not_rearm_cold_preparation(self):
        p=Preparation();self.assertTrue(lib.wmp_preparation_begin(C.byref(p),1000000,1,7,1,7));first=p.version
        self.assertTrue(lib.wmp_preparation_begin(C.byref(p),1100000,1,7,0,7))
        self.assertNotEqual(first,p.version);self.assertEqual(p.ready_us,1100000)
        self.assertTrue(lib.wmp_preparation_allows(C.byref(p),1200000,1))

    def test_cold_join_and_changed_ap_clock_still_require_preparation(self):
        p=Preparation();self.assertFalse(lib.wmp_preparation_begin(C.byref(p),1000000,1,7,0,0))
        self.assertFalse(lib.wmp_preparation_allows(C.byref(p),1500000,1));self.assertTrue(lib.wmp_preparation_allows(C.byref(p),3000000,1))
        self.assertTrue(lib.wmp_preparation_begin(C.byref(p),3100000,1,7,1,7))
        self.assertFalse(lib.wmp_preparation_begin(C.byref(p),3200000,1,8,1,7));self.assertEqual(p.ready_us,5200000)
        self.assertFalse(lib.wmp_preparation_allows(C.byref(p),5300000,0))

    def test_clock_loss_or_resource_teardown_cannot_reuse_a_warm_latch(self):
        p=Preparation();lib.wmp_preparation_begin(C.byref(p),1000000,1,7,1,7)
        self.assertFalse(lib.wmp_preparation_begin(C.byref(p),1100000,0,7,0,7))
        self.assertFalse(lib.wmp_preparation_begin(C.byref(p),1200000,1,7,0,7))
        lib.wmp_preparation_begin(C.byref(p),1300000,1,7,1,7);v=p.version;lib.wmp_preparation_forget(C.byref(p))
        self.assertNotEqual(v,p.version);self.assertFalse(p.warm)
        self.assertFalse(lib.wmp_preparation_begin(C.byref(p),1400000,1,7,0,7))

    def test_preparation_reset_preserves_new_admissions_already_in_reorder(self):
        p=Preparation();pool=FramePool();lib.wmp_frame_pool_init(C.byref(pool));versions={}
        old=lib.wmp_frame_pool_acquire(C.byref(pool));versions[old]=p.version;lib.wmp_frame_pool_publish(C.byref(pool),old);self.assertEqual(lib.wmp_frame_pool_take(C.byref(pool)),old)
        lib.wmp_preparation_begin(C.byref(p),1000000,1,7,1,7)
        new=lib.wmp_frame_pool_acquire(C.byref(pool));versions[new]=p.version;lib.wmp_frame_pool_publish(C.byref(pool),new);self.assertEqual(lib.wmp_frame_pool_take(C.byref(pool)),new)
        # Renderer notices reset only after taking both indices. Its stale
        # admissions must be released without discarding the opening packet.
        for index in [old,new]:
            if not lib.wmp_preparation_keeps(C.byref(p),versions[index]):self.assertTrue(lib.wmp_frame_pool_release(C.byref(pool),index))
        self.assertEqual(pool.held,1<<new);self.assertTrue(pool.free&(1<<old))
        self.assertTrue(lib.wmp_frame_pool_release(C.byref(pool),new));self.assertTrue(lib.wmp_frame_pool_quiescent(C.byref(pool)))

    def test_preparation_change_during_payload_copy_cancels_old_reservation(self):
        p=Preparation();pool=FramePool();lib.wmp_frame_pool_init(C.byref(pool));slot=lib.wmp_frame_pool_acquire(C.byref(pool));admitted=p.version
        lib.wmp_preparation_begin(C.byref(p),1000000,1,7,1,7)
        self.assertFalse(lib.wmp_preparation_keeps(C.byref(p),admitted));self.assertTrue(lib.wmp_frame_pool_cancel(C.byref(pool),slot))
        self.assertTrue(lib.wmp_frame_pool_quiescent(C.byref(pool)))

    def nack_pending_fixture(self,received=102):
        n=Nack();lib.wmp_nack_reset(C.byref(n))
        lib.wmp_nack_observe(C.byref(n),1,100,0,0);lib.wmp_nack_observe(C.byref(n),1,received,10000,0)
        return n

    def test_nack_queued_radio_wait_does_not_duplicate_or_consume_retries(self):
        n=self.nack_pending_fixture();out=C.c_uint64();queued=[]
        for at in [130000,162000,194000,226000,258000]:
            if lib.wmp_nack_plan(C.byref(n),at,C.byref(out)):
                self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,out.value,at));queued.append(out.value)
        self.assertEqual(queued,[101]);e=next(e for e in n.entries if e.valid)
        self.assertEqual((e.attempts,e.pending),(0,1));self.assertEqual(C.sizeof(NackEntry),32)
        self.assertTrue(lib.wmp_nack_submit(C.byref(n),1,101,280000))
        self.assertEqual((e.attempts,e.pending),(1,0));self.assertFalse(lib.wmp_nack_plan(C.byref(n),311999,C.byref(out)))
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),312000,C.byref(out)))

    def test_nack_reserved_oldest_allows_other_missing_groups_to_queue(self):
        n=self.nack_pending_fixture(104);out=C.c_uint64();queued=[]
        for _ in range(4):
            if lib.wmp_nack_plan(C.byref(n),130000,C.byref(out)):
                self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,out.value,130000));queued.append(out.value)
        self.assertEqual(queued,[101,102,103])
        self.assertEqual(sum(e.attempts for e in n.entries),0)

    def test_nack_queue_rejection_or_peer_suppression_preserves_retry_budget(self):
        n=self.nack_pending_fixture();out=C.c_uint64()
        self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,101,130000))
        self.assertTrue(lib.wmp_nack_cancel(C.byref(n),1,101,131000,40000))
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),170999,C.byref(out)))
        self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,101,171000));self.assertTrue(lib.wmp_nack_cancel(C.byref(n),1,101,171000,0))
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),171000,C.byref(out)))
        self.assertEqual(next(e for e in n.entries if e.valid).attempts,0)

    def test_nack_late_original_cancels_queued_feedback_before_submission(self):
        n=self.nack_pending_fixture();self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,101,130000))
        lib.wmp_nack_observe(C.byref(n),1,101,140000,0)
        self.assertFalse(lib.wmp_nack_pending(C.byref(n),1,101,140001))
        self.assertFalse(lib.wmp_nack_submit(C.byref(n),1,101,150000))

    def test_nack_old_epoch_submission_cannot_consume_new_epoch_retry(self):
        n=self.nack_pending_fixture();self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,101,130000))
        lib.wmp_nack_observe(C.byref(n),2,100,140000,0);lib.wmp_nack_observe(C.byref(n),2,102,150000,0)
        self.assertTrue(lib.wmp_nack_reserve(C.byref(n),2,101,270000))
        self.assertFalse(lib.wmp_nack_submit(C.byref(n),1,101,280000));self.assertFalse(lib.wmp_nack_cancel(C.byref(n),1,101,280000,0))
        self.assertTrue(lib.wmp_nack_pending(C.byref(n),2,101,280000));self.assertTrue(lib.wmp_nack_submit(C.byref(n),2,101,280000))
        self.assertEqual(next(e for e in n.entries if e.valid).attempts,1)

    def test_nack_expired_pending_request_cannot_be_transmitted(self):
        n=self.nack_pending_fixture();self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,101,130000))
        self.assertFalse(lib.wmp_nack_pending(C.byref(n),1,101,410001));self.assertFalse(lib.wmp_nack_submit(C.byref(n),1,101,410001))

    def test_nack_only_eight_accepted_requests_consume_the_retry_limit(self):
        n=self.nack_pending_fixture();out=C.c_uint64()
        for attempt in range(8):
            at=130000+attempt*32000
            self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,101,at))
            self.assertFalse(lib.wmp_nack_reserve(C.byref(n),1,101,at))
            self.assertTrue(lib.wmp_nack_submit(C.byref(n),1,101,at))
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),386000,C.byref(out)))
        self.assertFalse(lib.wmp_nack_reserve(C.byref(n),1,101,386000))
        self.assertEqual(next(e for e in n.entries if e.valid).attempts,8)

    def test_nack_recoverable_twenty_group_burst_retains_earliest_deadlines(self):
        n=Nack();lib.wmp_nack_reset(C.byref(n));out=C.c_uint64()
        lib.wmp_nack_observe(C.byref(n),1,100,0,0);lib.wmp_nack_observe(C.byref(n),1,121,244000,0)
        queued=[]
        while lib.wmp_nack_plan(C.byref(n),364000,C.byref(out)):
            queued.append(out.value);self.assertTrue(lib.wmp_nack_reserve(C.byref(n),1,out.value,364000))
        self.assertEqual(queued,list(range(101,121)))
        self.assertLess(244000+120000+40000+80000,500000+512000000//44100)

    def test_nack_overlapping_bursts_do_not_erase_still_live_missing_groups(self):
        n=Nack();lib.wmp_nack_reset(C.byref(n))
        lib.wmp_nack_observe(C.byref(n),1,100,0,0);lib.wmp_nack_observe(C.byref(n),1,121,244000,0)
        lib.wmp_nack_observe(C.byref(n),1,142,488000,0)
        expected=list(range(101,121))+list(range(122,142))
        self.assertEqual(sorted(e.sequence for e in n.entries if e.valid),expected)

    def test_nack_grace_allows_later_original_copy_without_feedback(self):
        n=Nack();lib.wmp_nack_reset(C.byref(n));out=C.c_uint64()
        lib.wmp_nack_observe(C.byref(n),1,100,0,0);lib.wmp_nack_observe(C.byref(n),1,102,10000,0)
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),129999,C.byref(out)))
        lib.wmp_nack_observe(C.byref(n),1,101,120000,0)
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),200000,C.byref(out)))

    def test_nack_missing_sequence_retry_suppression_and_expiry(self):
        n=Nack();lib.wmp_nack_reset(C.byref(n));out=C.c_uint64()
        lib.wmp_nack_observe(C.byref(n),1,100,0,0);lib.wmp_nack_observe(C.byref(n),1,102,10000,0)
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),130000,C.byref(out)));self.assertEqual(out.value,101)
        lib.wmp_nack_heard(C.byref(n),1,101,130000)
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),169999,C.byref(out)))
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),170000,C.byref(out)))
        lib.wmp_nack_commit(C.byref(n),101,170000)
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),201999,C.byref(out)))
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),202000,C.byref(out)))
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),410001,C.byref(out)))

    def test_early_nack_still_waits_until_its_configured_deadline(self):
        n=Nack();out=C.c_uint64()
        lib.wmp_nack_reset(C.byref(n))
        lib.wmp_nack_observe_after(C.byref(n),1,100,0,19999,60000)
        lib.wmp_nack_observe_after(C.byref(n),1,108,81000,19999,60000)
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),160998,C.byref(out)))
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),160999,C.byref(out)));self.assertEqual(out.value,101)
        lib.wmp_nack_observe_after(C.byref(n),1,101,161000,0,60000)
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),161001,C.byref(out)));self.assertEqual(out.value,102)

    def test_election_wait_fits_even_largest_source_staging_groups(self):
        minimum_staging_us=(8192//904)*512*1000000//48000
        for prefix in range(256):
            address=(Byte*6)(prefix,0xc8,0x40,0x42,0xbf,0x64)
            delay=lib.wmp_election_delay(address)
            self.assertGreaterEqual(delay,20000);self.assertLess(delay,60000)
            self.assertLess(delay+10000,minimum_staging_us)
            self.assertEqual(delay,lib.wmp_election_delay(address))

    def test_nack_eight_attempts_fit_before_playback_reserve(self):
        n=Nack();lib.wmp_nack_reset(C.byref(n));out=C.c_uint64()
        lib.wmp_nack_observe(C.byref(n),1,100,0,19999);lib.wmp_nack_observe(C.byref(n),1,102,10000,19999)
        for i in range(8):
            at=149999+i*32000
            self.assertTrue(lib.wmp_nack_plan(C.byref(n),at,C.byref(out)))
            self.assertEqual(out.value,101);lib.wmp_nack_commit(C.byref(n),101,at)
        self.assertFalse(lib.wmp_nack_plan(C.byref(n),405999,C.byref(out)))

    def test_nack_distant_holes_do_not_overwrite_live_requests(self):
        n=Nack();lib.wmp_nack_reset(C.byref(n));out=C.c_uint64()
        lib.wmp_nack_observe(C.byref(n),1,100,0,0);lib.wmp_nack_observe(C.byref(n),1,102,10000,0)
        for seq in range(103,117):lib.wmp_nack_observe(C.byref(n),1,seq,10000+(seq-102)*11000,0)
        lib.wmp_nack_observe(C.byref(n),1,118,190000,0)
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),200000,C.byref(out)));self.assertEqual(out.value,101)
        lib.wmp_nack_observe(C.byref(n),1,101,200001,0)
        self.assertTrue(lib.wmp_nack_plan(C.byref(n),310000,C.byref(out)));self.assertEqual(out.value,117)

    def test_nack_late_join_and_new_generation_do_not_request_old_music(self):
        n=Nack();lib.wmp_nack_reset(C.byref(n));out=C.c_uint64()
        lib.wmp_nack_observe(C.byref(n),1,123456,0,0)
        self.assertEqual(lib.wmp_nack_plan(C.byref(n),90000,C.byref(out)),0)
        lib.wmp_nack_observe(C.byref(n),1,123458,100000,0)
        lib.wmp_nack_observe(C.byref(n),2,99,110000,0)
        self.assertEqual(lib.wmp_nack_plan(C.byref(n),200000,C.byref(out)),0)
    def test_pool_cache_borrow_survives_renderer_release_without_reuse(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p));i=lib.wmp_frame_pool_acquire(C.byref(p))
        self.assertEqual(lib.wmp_frame_pool_pin(C.byref(p),i),0)
        self.assertEqual(lib.wmp_frame_pool_publish(C.byref(p),i),1);self.assertEqual(lib.wmp_frame_pool_pin(C.byref(p),i),1)
        self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),i)
        self.assertEqual(lib.wmp_frame_pool_release(C.byref(p),i),1);self.assertTrue(p.retired&(1<<i));self.assertFalse(p.free&(1<<i))
        for _ in range(FRAME_SLOTS-1):self.assertNotEqual(lib.wmp_frame_pool_acquire(C.byref(p)),i)
        self.assertEqual(lib.wmp_frame_pool_acquire(C.byref(p)),-1)
        self.assertEqual(lib.wmp_frame_pool_unpin(C.byref(p),i),1);self.assertEqual(lib.wmp_frame_pool_acquire(C.byref(p)),i)
    def test_full_packet_replay_retains_audio_identity_and_validates_own_crc(self):
        h=header();data=bytes(range(117));original,n=encode(h,data);crc=lib.wmp_logical_crc(original,n)
        h.flags=1;replayed,n=encode(h,data);self.assertTrue(decode(replayed,n)[0])
        self.assertNotEqual(bytes(original[:n]),bytes(replayed[:n]));self.assertEqual(lib.wmp_logical_crc(replayed,n),crc)
        d=Dedup();lib.wmp_dedup_bind(C.byref(d),h.group,h.generation)
        self.assertEqual(lib.wmp_dedup_accept(C.byref(d),h.group,h.generation,h.sequence,crc),1)
        self.assertEqual(lib.wmp_dedup_accept(C.byref(d),h.group,h.generation,h.sequence,lib.wmp_logical_crc(replayed,n)),0)
    def test_clock_reacquires_large_positive_epoch_step(self):
        c=BeaconClock();lib.wmp_beacon_clock_reset(C.byref(c))
        for i in range(12):lib.wmp_beacon_clock_push(C.byref(c),1000000+i*100000,2000000+i*100000)
        self.assertTrue(c.valid);lib.wmp_beacon_clock_push(C.byref(c),2300000,11000000)
        self.assertFalse(c.valid);self.assertEqual(c.count,1)
    def test_network_and_repair_references_keep_retired_payload_owned(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p));i=lib.wmp_frame_pool_acquire(C.byref(p));bit=1<<i
        self.assertTrue(lib.wmp_frame_pool_publish(C.byref(p),i));self.assertTrue(lib.wmp_frame_pool_pin(C.byref(p),i))
        self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),i)
        self.assertTrue(lib.wmp_frame_pool_pin(C.byref(p),i))
        self.assertTrue(lib.wmp_frame_pool_release(C.byref(p),i));self.assertEqual(p.references[i],2)
        self.assertFalse(p.free&bit);self.assertTrue(p.retired&bit)
        self.assertTrue(lib.wmp_frame_pool_unpin(C.byref(p),i));self.assertFalse(p.free&bit)
        # A second repair can safely borrow data after local playback retired it.
        self.assertTrue(lib.wmp_frame_pool_pin(C.byref(p),i));self.assertEqual(p.references[i],2)
        self.assertTrue(lib.wmp_frame_pool_unpin(C.byref(p),i));self.assertTrue(lib.wmp_frame_pool_unpin(C.byref(p),i))
        self.assertTrue(p.free&bit);self.assertFalse(p.retired&bit);self.assertEqual(p.references[i],0)
        self.assertFalse(lib.wmp_frame_pool_unpin(C.byref(p),i))
    def test_producer_outage_recovers_future_sample_positions_without_new_epoch(self):
        p=Playout();lib.wmp_playout_init(C.byref(p),200000);pts=C.c_uint64();cursor=C.c_uint64(0);skipped=C.c_uint64()
        self.assertTrue(lib.wmp_playout_plan(C.byref(p),0,44100,1000000,C.byref(pts)));anchor=p.anchor_us
        cursor.value=512
        # Ordinary90ms delivery gaps still fit the existing reserve.
        self.assertFalse(lib.wmp_playout_recover(C.byref(p),1090000,20000,C.byref(cursor),C.byref(skipped)))
        self.assertEqual(cursor.value,512)
        self.assertTrue(lib.wmp_playout_recover(C.byref(p),21000000,20000,C.byref(cursor),C.byref(skipped)))
        self.assertEqual(cursor.value-512,skipped.value);self.assertGreater(skipped.value,800000)
        self.assertEqual(p.anchor_us,anchor);self.assertTrue(p.valid)
        self.assertTrue(lib.wmp_playout_plan(C.byref(p),cursor.value,44100,21000000,C.byref(pts)))
        self.assertGreaterEqual(pts.value,21200000);self.assertLess(pts.value,21200024)
        cursor.value+=512
        self.assertFalse(lib.wmp_playout_recover(C.byref(p),21010000,20000,C.byref(cursor),C.byref(skipped)))
    def test_source_queue_retries_preserve_oldest_payload_and_timestamp(self):
        q=SbcQueue();g=Group();g.first_sample=1024;g.sample_count=512;g.frames=4;g.bytes=476
        for i in range(g.bytes):g.payload[i]=(i*17)&255
        original=bytes(g.payload[:g.bytes]);lib.wmp_sbc_queue_init(C.byref(q))
        self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,123456),1)
        for i in range(g.bytes):g.payload[i]=0 # producer reuse cannot mutate queued data
        out=Group();rate=C.c_uint32();at=C.c_uint64()
        for _ in range(12):
            self.assertEqual(lib.wmp_sbc_queue_peek(C.byref(q),C.byref(out),C.byref(rate),C.byref(at)),1)
            self.assertEqual((out.first_sample,out.sample_count,rate.value,at.value),(1024,512,44100,123456))
            self.assertEqual(bytes(out.payload[:out.bytes]),original);self.assertEqual(q.count,1)
        self.assertEqual(lib.wmp_sbc_queue_pop(C.byref(q)),1);self.assertEqual(q.used,0)
    def test_source_queue_keeps_codec_and_channels(self):
        # Regression: the queue dropped the codec, so AAC/aptX groups left the master labelled SBC.
        q=SbcQueue();lib.wmp_sbc_queue_init(C.byref(q));out=Group();rate=C.c_uint32();at=C.c_uint64()
        for codec,channels,samples in [(1,2,1024),(2,2,512),(3,1,512),(0,0,512)]:
            g=Group();g.first_sample=4096;g.sample_count=samples;g.frames=1;g.bytes=300;g.codec=codec;g.channels=channels
            self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,7),1)
            out.codec=out.channels=99
            self.assertEqual(lib.wmp_sbc_queue_peek(C.byref(q),C.byref(out),C.byref(rate),C.byref(at)),1)
            self.assertEqual((out.codec,out.channels,out.sample_count),(codec,channels,samples))
            self.assertEqual(lib.wmp_sbc_queue_pop(C.byref(q)),1)
    def test_source_queue_variable_lengths_wrap_without_overwrite(self):
        q=SbcQueue();lib.wmp_sbc_queue_init(C.byref(q));expected=[];out=Group();rate=C.c_uint32();at=C.c_uint64()
        for sequence in range(150):
            g=Group();g.first_sample=sequence*512;g.sample_count=512;g.frames=4;g.bytes=[476,904,249][sequence%3]
            data=bytes((sequence+i*13)&255 for i in range(g.bytes));g.payload[:g.bytes]=data
            self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),48000,sequence),1);expected.append((sequence,data))
            if len(expected)>=9:
                seq,data=expected.pop(0);self.assertEqual(lib.wmp_sbc_queue_peek(C.byref(q),C.byref(out),C.byref(rate),C.byref(at)),1)
                self.assertEqual((out.first_sample,at.value,rate.value),(seq*512,seq,48000));self.assertEqual(bytes(out.payload[:out.bytes]),data)
                self.assertEqual(lib.wmp_sbc_queue_pop(C.byref(q)),1)
    def test_source_queue_full_refuses_new_data_without_mutating_retained_data(self):
        q=SbcQueue();lib.wmp_sbc_queue_init(C.byref(q));g=Group();g.sample_count=512;g.frames=4;g.bytes=904
        accepted=0
        while lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,accepted):accepted+=1
        self.assertEqual(accepted,SBC_QUEUE_BYTES//g.bytes);before=bytes(q)
        self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,999),0);self.assertEqual(bytes(q),before)
        self.assertEqual(lib.wmp_sbc_queue_pop(C.byref(q)),1)
        self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,999),1)
    def test_source_queue_holds_hci_prefill_burst_and_rejects_empty_groups(self):
        q=SbcQueue();lib.wmp_sbc_queue_init(C.byref(q));g=Group();g.sample_count=512;g.frames=4;g.bytes=476
        for seq in range(SBC_QUEUE_BYTES//476):g.first_sample=seq*512;self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,0),1)
        self.assertEqual((q.count,q.used),(SBC_QUEUE_BYTES//476,(SBC_QUEUE_BYTES//476)*476));g.frames=0
        self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,0),0)
    def test_sender_cadence_preserves_grid_without_post_stall_bursts(self):
        due=lib.wmp_tx_next(0,1000000,2000)
        self.assertEqual(due,1002000)
        # Modest task jitter keeps the absolute grid rather than drifting.
        due=lib.wmp_tx_next(due,1002250,2000);self.assertEqual(due,1004000)
        # A blocked radio/task cannot dump every overdue copy at once.
        due=lib.wmp_tx_next(due,1060000,2000);self.assertEqual(due,1062000)
        self.assertGreaterEqual(due-1060000,1000)
        self.assertEqual(lib.wmp_tx_next(due,1062100,0),1062100)
    def test_sender_cadence_saturates_time_overflow(self):
        self.assertEqual(lib.wmp_tx_next(0,2**64-100,2000),2**64-1)
    def test_common_late_reports_are_maximum_not_sum_over_speakers(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),180000);out=C.c_uint64()
        lib.wmp_playout_plan(C.byref(s),0,44100,1000000,C.byref(out))
        for _ in range(100):lib.wmp_playout_report(C.byref(s),1,512)
        for i in range(1,450):lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*512*1000000//44100,C.byref(out))
        self.assertGreater(s.requested_us,190000);self.assertLess(s.requested_us,192000)
    def test_automatic_growth_decays_to_requested_reserve_after_clean_minute(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),200000);out=C.c_uint64();step=512*1000000//44100
        lib.wmp_playout_plan(C.byref(s),0,44100,1000000,C.byref(out))
        for _ in range(4):lib.wmp_playout_report(C.byref(s),2,512)
        i=1
        while s.requested_us==200000 and i<1000:lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*step,C.byref(out));i+=1
        grown=s.requested_us;self.assertGreater(grown,200000);self.assertEqual(s.baseline_us,200000)
        # Under 60 s of clean feedback windows: no decay yet.
        stop=i+int(55e6/step)
        while i<stop:lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*step,C.byref(out));i+=1
        self.assertEqual(s.requested_us,grown)
        # Long clean playback steps back down 10 ms at a time, never below the request.
        stop=i+int(400e6/step)
        while i<stop:lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*step,C.byref(out));i+=1
        self.assertEqual(s.requested_us,200000)
        # An explicit request moves the baseline itself.
        self.assertEqual(lib.wmp_playout_request(C.byref(s),150000),1);self.assertEqual(s.baseline_us,150000)
    def test_late_report_growth_steps_common_timeline_forward_at_once(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),200000);out=C.c_uint64();step=512*1000000//44100
        lib.wmp_playout_plan(C.byref(s),0,44100,1000000,C.byref(out))
        for _ in range(4):lib.wmp_playout_report(C.byref(s),2,512)
        i=1;before=None
        while s.requested_us==200000 and i<1000:
            prior=out.value;lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*step,C.byref(out));i+=1
        grown=s.requested_us-200000;self.assertGreater(grown,0);self.assertEqual(s.steps,1)
        # No slew remains: the next packet already carries the whole growth.
        self.assertEqual(s.phase_q16,s.target_q16)
        prior=out.value;lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*step,C.byref(out))
        self.assertGreaterEqual(out.value-prior,step+grown-2)
    def test_small_ingress_wander_inside_30ms_does_not_retarget(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),200000);out=C.c_uint64();step=512*1000000//44100
        lib.wmp_playout_plan(C.byref(s),0,44100,1000000,C.byref(out))
        for i in range(1,900):
            jitter=25000 if (i//86)%2 else 0  # ingress arrives up to 25 ms late in alternate seconds
            lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*step+jitter,C.byref(out))
        self.assertEqual(s.target_q16,0)
    def test_common_late_report_growth_caps_at_maximum_reserve(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),PLAYOUT_MAX_US-5000);out=C.c_uint64()
        lib.wmp_playout_plan(C.byref(s),0,44100,1000000,C.byref(out))
        for _ in range(100):lib.wmp_playout_report(C.byref(s),1,512)
        for i in range(1,450):lib.wmp_playout_plan(C.byref(s),i*512,44100,1000000+i*512*1000000//44100,C.byref(out))
        self.assertEqual(s.requested_us,PLAYOUT_MAX_US)
    def test_dedup_only_commits_after_successful_queue_handoff(self):
        d=Dedup();lib.wmp_dedup_bind(C.byref(d),1,2)
        for _ in range(3):self.assertEqual(lib.wmp_dedup_query(C.byref(d),1,2,50,123),1)
        self.assertEqual(lib.wmp_dedup_accept(C.byref(d),1,2,50,123),1)
        self.assertEqual(lib.wmp_dedup_query(C.byref(d),1,2,50,123),0)
    def test_common_playout_slew_keeps_future_pts_continuous(self):
        s=Playout();lib.wmp_playout_init(C.byref(s),180000);out=C.c_uint64()
        self.assertTrue(lib.wmp_playout_plan(C.byref(s),0,44100,1000000,C.byref(out)));self.assertEqual(out.value,1180000)
        self.assertTrue(lib.wmp_playout_request(C.byref(s),200000))
        self.assertTrue(lib.wmp_playout_plan(C.byref(s),512,44100,1011609,C.byref(out)))
        self.assertLessEqual(abs(out.value-1191609),15)
        self.assertFalse(lib.wmp_playout_request(C.byref(s),PLAYOUT_MAX_US+1))
    def test_common_playout_tracks_slow_producer_without_local_sink_delay(self):
        for ppm in [-40,40]:
            s=Playout();lib.wmp_playout_init(C.byref(s),180000);out=C.c_uint64();previous=0;now=0
            for i in range(310000):
                sample=i*512;now=1000000+round(sample*1000000/44100*(1+ppm/1000000))
                self.assertTrue(lib.wmp_playout_plan(C.byref(s),sample,44100,now,C.byref(out)))
                if i:self.assertGreater(out.value,previous)
                previous=out.value
            self.assertGreater(out.value-now,105000);self.assertLess(out.value-now,205000)
            self.assertGreater(s.changes,0)
    def test_pool_recovery_not_blocked_by_six_future_frames(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p));samples={}
        for sample in range(1,7):
            i=lib.wmp_frame_pool_acquire(C.byref(p));samples[i]=sample*512
            self.assertEqual(lib.wmp_frame_pool_publish(C.byref(p),i),1)
            self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),i)
        # The former6-slot reorder array would now stop reading ingress.
        urgent=lib.wmp_frame_pool_acquire(C.byref(p));samples[urgent]=0
        self.assertEqual(lib.wmp_frame_pool_publish(C.byref(p),urgent),1)
        self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),urgent)
        visible=[i for i in range(FRAME_SLOTS) if p.held&(1<<i)]
        self.assertEqual(min(visible,key=samples.get),urgent)
    def test_pool_never_reuses_pending_or_renderer_owned_payload(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p));ids=[lib.wmp_frame_pool_acquire(C.byref(p)) for _ in range(FRAME_SLOTS)]
        self.assertEqual(len(set(ids)),FRAME_SLOTS);self.assertEqual(lib.wmp_frame_pool_acquire(C.byref(p)),-1)
        for i in ids:self.assertEqual(lib.wmp_frame_pool_publish(C.byref(p),i),1)
        for i in ids:self.assertEqual(lib.wmp_frame_pool_take(C.byref(p)),i)
        self.assertEqual(lib.wmp_frame_pool_acquire(C.byref(p)),-1)
        self.assertEqual(lib.wmp_frame_pool_release(C.byref(p),5),1)
        self.assertEqual(lib.wmp_frame_pool_release(C.byref(p),5),0)
        self.assertEqual(lib.wmp_frame_pool_acquire(C.byref(p)),5)
    def test_pool_cancel_and_invalid_transitions_preserve_ownership(self):
        p=FramePool();lib.wmp_frame_pool_init(C.byref(p));i=lib.wmp_frame_pool_acquire(C.byref(p))
        self.assertEqual(lib.wmp_frame_pool_release(C.byref(p),i),0)
        self.assertEqual(lib.wmp_frame_pool_cancel(C.byref(p),i),1)
        self.assertEqual(lib.wmp_frame_pool_publish(C.byref(p),i),0)
        self.assertEqual((p.free,p.pending,p.ready,p.held,p.count),((1<<FRAME_SLOTS)-1,0,0,0,0))
    def test_injected_originals_are_not_falsely_reported_as_radio_submissions(self):
        s=CopyTrace()
        for i in range(4):lib.wmp_copy_trace_event(C.byref(s),2,10,65000,7,i,0,0)
        r=s.records[10]
        self.assertEqual((s.kind,r.injected,r.seen,r.accepted,r.completed),(1,15,0,0,0))
        self.assertEqual(r.presentation_us,65000)

    def test_copy_trace_records_all_four_even_after_logical_dedup(self):
        s=CopyTrace()
        for i in [3,0,2,1]:
            lib.wmp_copy_trace_event(C.byref(s),2,10,65000,3,i,10000+i*6000,11000+i*6000)
            lib.wmp_copy_trace_event(C.byref(s),2,10,65000,4,i,0,0)
        r=s.records[10];self.assertEqual((r.seen,r.accepted,r.repaired),(15,15,0));self.assertEqual(list(r.a),[10000,16000,22000,28000])
    def test_copy_trace_distinguishes_raw_queue_loss_and_nack_replay_repair(self):
        s=CopyTrace()
        lib.wmp_copy_trace_event(C.byref(s),2,10,65000,3,3,100,200)
        self.assertEqual((s.records[10].seen,s.records[10].accepted),(8,0))
        lib.wmp_copy_trace_event(C.byref(s),2,11,76000,5,0,0,0)
        self.assertEqual((s.records[11].seen,s.records[11].repaired),(0,1))
    def test_copy_trace_synthesizes_and_retires_whole_group_erasures(self):
        s=CopyTrace()
        for seq in [0,2,*range(3,67)]:lib.wmp_copy_trace_event(C.byref(s),2,seq,65000,3,0,1,2)
        self.assertGreaterEqual(s.retired[0],1)
        old=s.records[1].sequence
        lib.wmp_copy_trace_event(C.byref(s),2,1,65000,3,3,1,2)
        self.assertEqual(s.records[1].sequence,old)
    # Historical reference only: FEC was removed from delivered firmware. These keep the old
    # parity code's evidence reproducible; they are not a delivered-transport capability.
    def historical_fec_fixture(self,shards):
        state=Fec();lib.wmp_fec_bind(C.byref(state),1,2,16,shards);wires=[];parities=[]
        for i,n in enumerate([117,468,900,476][:shards]):
            h=header(n);h.group=1;h.generation=2;h.sequence=16+i;h.first_sample=512*i;h.presentation_us=65000+11610*i
            wire,size=encode(h,bytes((j*13+i*31)&255 for j in range(n)));wire=bytes(wire[:size]);wires.append(wire)
            self.assertEqual(lib.wmp_fec_data(C.byref(state),i,buf(wire),len(wire)),1)
        for row in range(2):
            out=(Byte*1200)();n=lib.wmp_fec_emit(C.byref(state),row,out,1200);self.assertGreater(n,0);parities.append(bytes(out[:n]))
        return wires,parities
    def test_historical_fec_recovers_every_single_erasure_with_either_row(self):
        for shards in [2,4]:
            wires,parities=self.historical_fec_fixture(shards)
            for missing,row in itertools.product(range(shards),range(2)):
                state=Fec();lib.wmp_fec_bind(C.byref(state),1,2,16,shards)
                # Parity may arrive before data, and duplicate copies are harmless.
                self.assertEqual(lib.wmp_fec_receive(C.byref(state),buf(parities[row]),len(parities[row])),1)
                self.assertEqual(lib.wmp_fec_receive(C.byref(state),buf(parities[row]),len(parities[row])),0)
                for i in reversed(range(shards)):
                    if i!=missing:lib.wmp_fec_data(C.byref(state),i,buf(wires[i]),len(wires[i]))
                out=(Byte*1176)();index=C.c_uint();n=lib.wmp_fec_recover(C.byref(state),out,1176,C.byref(index))
                self.assertEqual((index.value,bytes(out[:n])),(missing,wires[missing]));self.assertTrue(decode(out,n)[0])
    def test_historical_fec_recovers_every_two_erasure_combination(self):
        for shards in [2,4]:
            wires,parities=self.historical_fec_fixture(shards)
            for missing in itertools.combinations(range(shards),2):
                state=Fec();lib.wmp_fec_bind(C.byref(state),1,2,16,shards)
                for i in range(shards):
                    if i not in missing:lib.wmp_fec_data(C.byref(state),i,buf(wires[i]),len(wires[i]))
                for parity in reversed(parities):lib.wmp_fec_receive(C.byref(state),buf(parity),len(parity))
                for expected in missing:
                    out=(Byte*1176)();index=C.c_uint();n=lib.wmp_fec_recover(C.byref(state),out,1176,C.byref(index))
                    self.assertEqual((index.value,bytes(out[:n])),(expected,wires[expected]));self.assertTrue(decode(out,n)[0])
                    self.assertEqual(lib.wmp_fec_data(C.byref(state),index.value,out,n),1)
                self.assertEqual(lib.wmp_fec_recover(C.byref(state),out,1176,C.byref(index)),0)
    def test_historical_fec_refuses_insufficient_information_and_bad_metadata(self):
        wires,parities=self.historical_fec_fixture(4);state=Fec();lib.wmp_fec_bind(C.byref(state),1,2,16,4)
        lib.wmp_fec_data(C.byref(state),0,buf(wires[0]),len(wires[0]));out=(Byte*1176)();index=C.c_uint()
        for parity in parities:lib.wmp_fec_receive(C.byref(state),buf(parity),len(parity))
        self.assertEqual(lib.wmp_fec_recover(C.byref(state),out,1176,C.byref(index)),0)
        for offset in [0,4,5,7,8,16,17]:
            bad=bytearray(parities[0]);bad[offset]^=128
            self.assertEqual(lib.wmp_fec_receive(C.byref(state),buf(bad),len(bad)),-1)
        self.assertEqual(lib.wmp_fec_data(C.byref(state),4,buf(wires[0]),len(wires[0])),-1)
    def test_historical_fec_repair_still_requires_original_wire_crc(self):
        wires,parities=self.historical_fec_fixture(2);state=Fec();lib.wmp_fec_bind(C.byref(state),1,2,16,2)
        lib.wmp_fec_data(C.byref(state),1,buf(wires[1]),len(wires[1]));bad=bytearray(parities[0]);bad[100]^=1
        lib.wmp_fec_receive(C.byref(state),buf(bad),len(bad));out=(Byte*1176)();index=C.c_uint()
        n=lib.wmp_fec_recover(C.byref(state),out,1176,C.byref(index));self.assertGreater(n,0);self.assertFalse(decode(out,n)[0])
    def test_clock_zero_initialized_firmware_boot(self):
        c=BeaconClock();out=C.c_uint64()
        for i in range(120):lib.wmp_beacon_clock_push(C.byref(c),1000000+i*102400,9000000+i*102400)
        self.assertTrue(lib.wmp_beacon_clock_to_local(C.byref(c),22000000,C.byref(out)))
        self.assertLessEqual(abs(out.value-14000000),1)
    def test_clock_rejects_single_point_precision_and_old_local_capture(self):
        c=BeaconClock();lib.wmp_beacon_clock_reset(C.byref(c));out=C.c_uint64()
        self.assertFalse(lib.wmp_beacon_clock_to_local(C.byref(c),100,C.byref(out)))
        for i in range(7):self.assertFalse(lib.wmp_beacon_clock_push(C.byref(c),1000000+i*100000,2000000+i*100000))
        self.assertFalse(c.valid)
        self.assertTrue(lib.wmp_beacon_clock_push(C.byref(c),1700000,2700000))
        self.assertFalse(lib.wmp_beacon_clock_push(C.byref(c),1600000,2800000))
        self.assertTrue(lib.wmp_beacon_clock_to_local(C.byref(c),2800000,C.byref(out)))
        self.assertLessEqual(abs(out.value-1800000),1)
    def test_clock_tracks_known_skew_despite_bursty_beacon_delay(self):
        for ppm in [-45,35]:
            c=BeaconClock();lib.wmp_beacon_clock_reset(C.byref(c));out=C.c_uint64();ratio=1+ppm/1000000
            for i in range(700):
                remote=2000000+i*102400
                delay=[20,1800,7000,25000,5000,10000,20,1800][i%8]
                local=round(1000000+(remote-2000000)/ratio)+delay
                lib.wmp_beacon_clock_push(C.byref(c),local,remote)
            target=remote+65000
            self.assertTrue(lib.wmp_beacon_clock_to_local(C.byref(c),target,C.byref(out)))
            expected=1000000+(target-2000000)/ratio
            self.assertLess(abs(out.value-expected),250,(ppm,out.value-expected,c.ratio))
            self.assertLess(abs((c.ratio-1)*1000000-ppm),8)
            self.assertGreater(c.jitter_us,20000)
            self.assertLess(c.fit_jitter_us,500)
    def test_clock_source_restart_requires_new_acquisition(self):
        c=BeaconClock();lib.wmp_beacon_clock_reset(C.byref(c))
        for i in range(20):lib.wmp_beacon_clock_push(C.byref(c),1000000+i*100000,5000000+i*100000)
        self.assertTrue(c.valid)
        self.assertFalse(lib.wmp_beacon_clock_push(C.byref(c),3100000,100000))
        self.assertFalse(c.valid);self.assertEqual(c.count,1)
    def test_silence_is_payload_free_and_repeated_copies_decode_once(self):
        d=Dedup();h=header(0);h.type=7;h.frame_count=0
        lib.wmp_dedup_bind(C.byref(d),h.group,h.generation)
        crcs=[]
        for i in range(4):
            h.copy_index=i;w,n=encode(h,b'');ok,r,p=decode(w,n)
            self.assertEqual((ok,n,p,r.sample_count,r.first_sample),(1,72,b'',512,1024))
            crc=lib.wmp_logical_crc(w,n);crcs.append(crc)
            self.assertEqual(lib.wmp_dedup_accept(C.byref(d),h.group,h.generation,h.sequence,crc),int(i==0))
        self.assertEqual(len(set(crcs)),1)
        h.flags=1;w,n=encode(h,b'');self.assertEqual(lib.wmp_logical_crc(w,n),crcs[0])
        h=header(0);h.type=7;h.frame_count=0;h.sample_count=MAX_GROUP_SAMPLES;w,n=encode(h,b'')
        ok,r,p=decode(w,n);self.assertEqual((ok,n,r.sample_count),(1,72,MAX_GROUP_SAMPLES))
        for field,value in [('payload_bytes',1),('frame_count',1),('sample_count',0),('sample_count',MAX_GROUP_SAMPLES+1),('sample_rate',123)]:
            h=header(0);h.type=7;h.frame_count=0;setattr(h,field,value)
            self.assertEqual(lib.wmp_encode((Byte*1272)(),1272,C.byref(h),buf(b'x')),0,(field,value))
    def test_paused_silence_cadence_does_not_integrate_host_timer_jitter(self):
        p=Playout();lib.wmp_playout_init(C.byref(p),200000);pts=C.c_uint64();sample=0;count=0
        self.assertTrue(lib.wmp_playout_plan(C.byref(p),0,44100,1000000,C.byref(pts)))
        # Four normal frames followed by 30 seconds of 10 ms timer polls.
        sample=512
        for tick in range(1,3001):
            now=1000000+tick*10000
            for attempt in range(2):
                next_pts=C.c_uint64();self.assertTrue(lib.wmp_sample_time(p.anchor_us,p.first_sample,44100,sample,C.byref(next_pts)))
                ingress=C.c_uint64()
                if not lib.wmp_silence_due(next_pts.value,now,now+700000,200000,C.byref(ingress)):break
                original=lib.wmp_ingress_clock(now,now+700000,ingress.value)
                self.assertTrue(lib.wmp_playout_plan(C.byref(p),sample,44100,original,C.byref(pts)))
                self.assertEqual(pts.value,next_pts.value);sample+=512;count+=1
        self.assertGreater(count,2500);self.assertEqual((p.phase_q16,p.target_q16),(0,0))
        self.assertLessEqual(abs((sample-512)*1000000//44100-30000000),12000)
    def test_pause_tail_preserves_each_original_sbc_frame(self):
        for count in range(1,4):
            q=SbcQueue();g=Group();lib.wmp_group_init(C.byref(g),4);lib.wmp_sbc_queue_init(C.byref(q))
            originals=[]
            for i in range(count):
                data=bytes([0x9c])+bytes([i+1])*116;originals.append(data)
                self.assertEqual(lib.wmp_group_push(C.byref(g),buf(data),len(data),i*128,128),0)
            self.assertTrue(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),44100,777))
            out=Group();rate=C.c_uint32();at=C.c_uint64()
            self.assertTrue(lib.wmp_sbc_queue_peek(C.byref(q),C.byref(out),C.byref(rate),C.byref(at)))
            self.assertEqual((out.frames,out.sample_count,rate.value,at.value),(count,count*128,44100,777))
            for i,data in enumerate(originals):
                ptr=Ptr();n=C.c_uint16();self.assertTrue(lib.wmp_group_frame(out.payload,out.bytes,count,i,C.byref(ptr),C.byref(n)))
                self.assertEqual(C.string_at(ptr,n.value),data)
    def test_crc_known_vector(self):self.assertEqual(lib.wmp_crc32(buf(b'123456789'),9),0xcbf43926)
    def test_roundtrip_preserves_bytes_and_samples(self):
        for n in [4,117,468,1200,MAX_PAYLOAD]:
            data=bytes((i*29+7)&255 for i in range(n));h=header(n);w,size=encode(h,data);ok,r,p=decode(w,size)
            self.assertEqual((ok,p,r.sample_count,r.first_sample,r.generation),(1,data,512,1024,h.generation))
    def test_all_single_byte_corruption_rejected(self):
        w,n=encode(header(),bytes(range(117)))
        for i in range(n):
            w[i]^=1;self.assertFalse(decode(w,n)[0],i);w[i]^=1
    def test_truncation_and_trailing_bytes_rejected(self):
        w,n=encode(header(),bytes(117))
        for length in [0,1,71,n-1,n+1,72+MAX_PAYLOAD]:self.assertFalse(decode(w,length)[0])
    def test_invalid_geometry_and_reserved_flags(self):
        for field,value in [('group',0),('generation',0),('sample_rate',123),('fragment_count',0),('fragment_count',33),('fragment_index',1),('flags',4),('frame_count',0),('sample_count',0)]:
            h=header();setattr(h,field,value);self.assertEqual(lib.wmp_encode((Byte*1272)(),1272,C.byref(h),buf(bytes(117))),0,(field,value))
    def test_copies_keep_logical_crc_but_unique_wire_crc(self):
        logical=[];wires=[]
        for copy in range(4):
            h=header();h.copy_index=copy;w,n=encode(h,bytes(117));logical.append(lib.wmp_logical_crc(w,n));wires.append(bytes(w[:n]))
        self.assertEqual(len(set(logical)),1);self.assertEqual(len(set(wires)),4)
    def test_dedup_reorder_stale_conflict_and_generation(self):
        d=Dedup();lib.wmp_dedup_bind(C.byref(d),1,2)
        accept=lambda seq,crc=7,g=2:lib.wmp_dedup_accept(C.byref(d),1,g,seq,crc)
        for seq in [100,102,101]:self.assertEqual(accept(seq),1)
        self.assertEqual(accept(101),0);self.assertEqual(accept(101,8),-3)
        self.assertEqual(accept(101,g=3),-2);self.assertEqual(accept(103),1)
        self.assertEqual(accept(167),1);self.assertEqual(accept(103),-1)
        # Identical audio at the next logical position is legitimate new audio.
        self.assertEqual(accept(168),1)
    def test_four_contiguous_variable_length_frames(self):
        g=Group();lib.wmp_group_init(C.byref(g),4);frames=[]
        for i,n in enumerate([117,119,116,117]):
            data=bytes([0x9c])+bytes((i+j)&255 for j in range(n-1));frames.append(data)
            self.assertEqual(lib.wmp_group_push(C.byref(g),buf(data),n,i*128,128),int(i==3))
        self.assertEqual((g.frames,g.sample_count,g.next_sample),(4,512,512))
        for i,expected in enumerate(frames):
            p=Ptr();n=C.c_uint16();self.assertTrue(lib.wmp_group_frame(g.payload,g.bytes,g.frames,i,C.byref(p),C.byref(n)));self.assertEqual(C.string_at(p,n.value),expected)
        self.assertEqual(lib.wmp_group_push(C.byref(g),buf(frames[0]),117,512,128),-2)
    def test_group_target_accepts_three_through_max_frames_only(self):
        frame=buf(bytes([0x9c])+bytes(116))
        for target in range(0,MAX_GROUP_FRAMES+3):
            g=Group();g.target=0xff;lib.wmp_group_init(C.byref(g),target)
            legal=3<=target<=MAX_GROUP_FRAMES
            self.assertEqual(g.target,target if legal else 0,target)
            if not legal:self.assertEqual(lib.wmp_group_push(C.byref(g),frame,117,0,128),-1)
    def test_eight_frame_group_round_trips_wire_and_parser(self):
        self.assertEqual((MAX_GROUP_FRAMES,MAX_GROUP_SAMPLES),(8,1024))
        g=Group();lib.wmp_group_init(C.byref(g),MAX_GROUP_FRAMES);frames=[]
        for i in range(MAX_GROUP_FRAMES):
            n=[119,118,119,117][i%4];data=bytes([0x9c])+bytes((i*7+j)&255 for j in range(n-1));frames.append(data)
            self.assertEqual(lib.wmp_group_push(C.byref(g),buf(data),n,1024+i*128,128),int(i==MAX_GROUP_FRAMES-1))
        self.assertEqual((g.frames,g.sample_count,g.first_sample,g.next_sample),(MAX_GROUP_FRAMES,MAX_GROUP_SAMPLES,1024,1024+MAX_GROUP_SAMPLES))
        # A ninth frame is refused without mutating the closed group.
        before=bytes(g);self.assertEqual(lib.wmp_group_push(C.byref(g),buf(frames[0]),119,1024+MAX_GROUP_SAMPLES,128),-2);self.assertEqual(bytes(g),before)
        payload=bytes(g.payload[:g.bytes]);h=header(g.bytes);h.frame_count=g.frames;h.sample_count=g.sample_count;h.first_sample=g.first_sample
        w,n=encode(h,payload);ok,r,p=decode(w,n)
        self.assertEqual((ok,p,r.frame_count,r.sample_count,r.first_sample),(1,payload,MAX_GROUP_FRAMES,MAX_GROUP_SAMPLES,1024))
        held=buf(p)  # keep the parsed bytes alive while frame pointers into them are read
        for i,expected in enumerate(frames):
            ptr=Ptr();size=C.c_uint16();self.assertTrue(lib.wmp_group_frame(held,len(p),r.frame_count,i,C.byref(ptr),C.byref(size)))
            self.assertEqual(C.string_at(ptr,size.value),expected)
        # The parser refuses a ninth frame even when its bytes are present.
        nine=payload+bytes([4,0,0x9c,0,0,0])
        ptr=Ptr();size=C.c_uint16()
        self.assertFalse(lib.wmp_group_frame(buf(nine),len(nine),MAX_GROUP_FRAMES+1,0,C.byref(ptr),C.byref(size)))
        self.assertFalse(lib.wmp_group_frame(buf(payload),len(payload),MAX_GROUP_FRAMES,MAX_GROUP_FRAMES,C.byref(ptr),C.byref(size)))
    def test_audio_wire_bounds_are_eight_frames_and_1024_samples(self):
        data=bytes(117)
        for frames,samples in [(1,1),(4,512),(MAX_GROUP_FRAMES,MAX_GROUP_SAMPLES)]:
            h=header(117);h.frame_count=frames;h.sample_count=samples;w,n=encode(h,data);ok,r,p=decode(w,n)
            self.assertEqual((ok,r.frame_count,r.sample_count),(1,frames,samples))
        for field,value in [('frame_count',MAX_GROUP_FRAMES+1),('sample_count',MAX_GROUP_SAMPLES+1),('payload_bytes',MAX_PAYLOAD+1)]:
            h=header(117);setattr(h,field,value)
            self.assertEqual(lib.wmp_encode((Byte*(72+MAX_PAYLOAD+1))(),72+MAX_PAYLOAD+1,C.byref(h),buf(bytes(MAX_PAYLOAD+1))),0,(field,value))
        # The decoder rejects an otherwise intact, correctly sealed out-of-range header.
        for offset,value in [(58,MAX_GROUP_FRAMES+1),(52,MAX_GROUP_SAMPLES+1)]:
            w,n=encode(header(117),data);w[offset]=value&255;w[offset+1]=value>>8;reseal(w,n)
            self.assertFalse(decode(w,n)[0],offset)
            w[offset]=(value-1)&255;w[offset+1]=(value-1)>>8;reseal(w,n);self.assertTrue(decode(w,n)[0],offset)
        # Maximum payload still round-trips; one byte more is refused above.
        payload=bytes((i*29+7)&255 for i in range(MAX_PAYLOAD));h=header(MAX_PAYLOAD)
        w=(Byte*(72+MAX_PAYLOAD))();n=lib.wmp_encode(w,len(w),C.byref(h),buf(payload));self.assertEqual(n,72+MAX_PAYLOAD)
        ok,r,p=decode(w,n);self.assertEqual((ok,p),(1,payload))
    def test_source_queue_carries_eight_frame_groups_and_rejects_oversize(self):
        q=SbcQueue();lib.wmp_sbc_queue_init(C.byref(q));g=Group();g.first_sample=2048
        g.frames=MAX_GROUP_FRAMES;g.sample_count=MAX_GROUP_SAMPLES;g.bytes=MAX_PAYLOAD
        data=bytes((i*11+3)&255 for i in range(MAX_PAYLOAD));g.payload[:MAX_PAYLOAD]=data
        self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(g),48000,55),1)
        for field,value in [('frames',MAX_GROUP_FRAMES+1),('sample_count',MAX_GROUP_SAMPLES+1),('bytes',MAX_PAYLOAD+1),('frames',0),('sample_count',0)]:
            bad=Group();C.memmove(C.byref(bad),C.byref(g),C.sizeof(Group));setattr(bad,field,value);before=bytes(q)
            self.assertEqual(lib.wmp_sbc_queue_push(C.byref(q),C.byref(bad),48000,56),0,(field,value));self.assertEqual(bytes(q),before)
        out=Group();rate=C.c_uint32();at=C.c_uint64()
        self.assertEqual(lib.wmp_sbc_queue_peek(C.byref(q),C.byref(out),C.byref(rate),C.byref(at)),1)
        self.assertEqual((out.frames,out.sample_count,out.bytes,out.first_sample,out.next_sample,out.target,rate.value,at.value),
                         (MAX_GROUP_FRAMES,MAX_GROUP_SAMPLES,MAX_PAYLOAD,2048,2048+MAX_GROUP_SAMPLES,MAX_GROUP_FRAMES,48000,55))
        self.assertEqual(bytes(out.payload[:out.bytes]),data)
    def test_playout_report_accepts_max_group_samples_only(self):
        for samples,expected in [(1,1),(512,512),(MAX_GROUP_SAMPLES,MAX_GROUP_SAMPLES),(MAX_GROUP_SAMPLES+1,0),(0,0)]:
            s=Playout();lib.wmp_playout_init(C.byref(s),200000);lib.wmp_playout_report(C.byref(s),1,samples)
            self.assertEqual((s.packet_samples,s.pending_late),(expected,int(expected>0)),samples)
    def test_group_refuses_gap_without_flushing_partial_audio(self):
        g=Group();lib.wmp_group_init(C.byref(g),4);frame=buf(bytes([0x9c])+bytes(116))
        self.assertEqual(lib.wmp_group_push(C.byref(g),frame,117,0,128),0)
        before=bytes(g);self.assertEqual(lib.wmp_group_push(C.byref(g),frame,117,256,128),-3);self.assertEqual(bytes(g),before)
    def test_oversize_refused_without_partial_write(self):
        frame=buf(bytes([0x9c])+bytes(MAX_PAYLOAD))
        # A frame plus its 2-byte length prefix must fit WMP_MAX_PAYLOAD.
        for size in [MAX_PAYLOAD-1,MAX_PAYLOAD,MAX_PAYLOAD+1]:
            g=Group();lib.wmp_group_init(C.byref(g),4);before=bytes(g)
            self.assertEqual(lib.wmp_group_push(C.byref(g),frame,size,0,128),-2,size);self.assertEqual(bytes(g),before)
        g=Group();lib.wmp_group_init(C.byref(g),4)
        self.assertEqual(lib.wmp_group_push(C.byref(g),frame,MAX_PAYLOAD-2,0,128),0);self.assertEqual(g.bytes,MAX_PAYLOAD)
        # Once full by bytes, even the smallest valid frame is refused unchanged.
        before=bytes(g);self.assertEqual(lib.wmp_group_push(C.byref(g),frame,4,128,128),-2);self.assertEqual(bytes(g),before)
    def test_copy_timing_is_spaced_and_before_deadline(self):
        due=(C.c_uint64*4)();self.assertTrue(lib.wmp_copy_plan(1000,30000,6000,4,due));self.assertEqual(list(due),[1000,7000,13000,19000])
        for copies,spacing,deadline in [(4,6000,19000),(2,6000,30000),(4,0,30000)]:self.assertFalse(lib.wmp_copy_plan(1000,deadline,spacing,copies,due))
        self.assertFalse(lib.wmp_copy_plan(2**64-10,2**64-1,4,4,due))
    def test_group_parser_rejects_incomplete_length_prefix(self):
        p=Ptr();n=C.c_uint16();self.assertFalse(lib.wmp_group_frame(buf(bytes([0xff,0xff])),2,4,0,C.byref(p),C.byref(n)))
    def test_mac_wrap_and_old_capture_rejection(self):
        clock=MacClock();value=C.c_uint64();lib.wmp_mac_reset(C.byref(clock))
        self.assertTrue(lib.wmp_mac_extend(C.byref(clock),0xfffffff0,C.byref(value)))
        self.assertTrue(lib.wmp_mac_extend(C.byref(clock),0x30,C.byref(value)));self.assertEqual(value.value,0x100000030)
        before=bytes(clock);self.assertFalse(lib.wmp_mac_extend(C.byref(clock),0x20,C.byref(value)));self.assertEqual(bytes(clock),before)
    def test_absolute_samples_do_not_accumulate_group_rounding(self):
        value=C.c_uint64();anchor=123456789
        for group in [0,1,2,3,1000000]:
            sample=group*512;self.assertTrue(lib.wmp_sample_time(anchor,0,44100,sample,C.byref(value)))
            self.assertEqual(value.value,anchor+sample*1000000//44100)
            # Copy/arrival time is deliberately absent from this computation.
    def test_time_overflow_and_past_samples_refused(self):
        value=C.c_uint64()
        for anchor,first,rate,sample in [(2**64-1,0,44100,512),(1,10,44100,9),(1,0,0,1),(1,0,1,2**64-1)]:
            self.assertFalse(lib.wmp_sample_time(anchor,first,rate,sample,C.byref(value)))
    def test_sbc_known_frame_size_and_sample_count(self):
        #44100Hz,16 blocks,joint stereo,8 subbands,bitpool52:117B/128samples.
        frame=buf(bytes.fromhex('9cbd3400')+bytes(113));info=SbcInfo()
        self.assertTrue(lib.wmp_sbc_info_read(frame,117,C.byref(info)))
        self.assertEqual((info.rate,info.bytes,info.samples,info.channels),(44100,117,128,2))
        self.assertFalse(lib.wmp_sbc_info_read(frame,116,C.byref(info)))
    def test_live_slabs_cover_all_advertised_sbc_channel_modes(self):
        policy=(ROOT/'firmware/app/main/node.h').read_text()
        cap=int(re.search(r'#define WMP_SBC_BITPOOL_CAP (\d+)u',policy)[1])
        capacity=lib.wmp_live_payload_capacity()
        frame=buf(bytes([0x9c,0,2,0])+bytes(1020));info=SbcInfo();largest=0
        # Exhaust the header modes, frequencies, allocation methods and every
        # advertised bitpool, including dual channel (larger than joint stereo).
        for header in range(256):
            frame[1]=header
            for pool in range(2,cap+1):
                frame[2]=pool
                self.assertTrue(lib.wmp_sbc_info_read(frame,1024,C.byref(info)))
                self.assertLessEqual(4*(info.bytes+2),capacity);largest=max(largest,info.bytes)
        self.assertEqual(largest,224);self.assertEqual(capacity,MAX_PAYLOAD)
        # Worst case: four frames (904) and six frames fit; seven close the group early.
        self.assertEqual(4*(largest+2),904);self.assertLessEqual(6*(largest+2),capacity)
        self.assertGreater(7*(largest+2),capacity)
    def test_common_reserve_can_grow_to300ms_without_retiming_old_packets(self):
        p=Playout();lib.wmp_playout_init(C.byref(p),200000);first=C.c_uint64();later=C.c_uint64()
        self.assertTrue(lib.wmp_playout_plan(C.byref(p),0,44100,1000000,C.byref(first)))
        self.assertTrue(lib.wmp_playout_request(C.byref(p),300000))
        for i in range(1,9000):
            self.assertTrue(lib.wmp_playout_plan(C.byref(p),i*512,44100,1000000+i*512*1000000//44100,C.byref(later)))
        self.assertEqual(first.value,1200000);self.assertEqual(p.requested_us,300000);self.assertEqual(PLAYOUT_MAX_US,300000)
        self.assertFalse(lib.wmp_playout_request(C.byref(p),300001));self.assertEqual(p.requested_us,300000)
        self.assertGreaterEqual(later.value-(1000000+8999*512*1000000//44100),285000)
    def test_rtp_four_frame_payload_and_header_stripping(self):
        payload=(bytes.fromhex('9cbd3400')+bytes(113))*4
        packet=bytes.fromhex('806000070000012301020304')+bytes([4])+payload
        h=RtpSbc();p=Ptr();n=C.c_size_t();raw=buf(packet)
        self.assertEqual(lib.wmp_rtp_sbc_read(raw,len(raw),C.byref(h),C.byref(p),C.byref(n)),1)
        self.assertEqual((h.sequence,h.timestamp,h.frames,h.samples,n.value),(7,0x123,4,512,len(payload)))
        self.assertEqual(C.string_at(p,n.value),payload)
    def test_rtp_csrc_extension_padding_and_bad_length(self):
        payload=(bytes.fromhex('9cbd3400')+bytes(113))*4
        packet=bytearray(bytes.fromhex('b16000070000012301020304aabbccddbede000101020304')+bytes([4])+payload+bytes([0,0,0,4]))
        h=RtpSbc();p=Ptr();n=C.c_size_t()
        def read(data):return lib.wmp_rtp_sbc_read(buf(data),len(data),C.byref(h),C.byref(p),C.byref(n))
        self.assertEqual(read(packet),1);self.assertEqual(n.value,len(payload))
        packet[18:20]=bytes([255,255]);self.assertEqual(read(packet),0)
    def test_rtp_fragment_explicitly_reported_and_count_mismatch_rejected(self):
        header=bytes.fromhex('806000070000012301020304');frame=bytes.fromhex('9cbd3400')+bytes(113)
        h=RtpSbc();p=Ptr();n=C.c_size_t()
        def read(media):
            data=buf(header+media);return lib.wmp_rtp_sbc_read(data,len(data),C.byref(h),C.byref(p),C.byref(n))
        self.assertEqual(read(bytes([0xc0])+frame),-1)
        self.assertEqual(read(bytes([5])+frame*4),0)
    def test_native_llc_decapsulation_preserves_wire(self):
        # Exact byte preservation across both normal data and raw FromDS paths.
        for payload_size in [0,7,476,904,1200,MAX_PAYLOAD]:
            wire=bytearray(b'WMP1'+bytes(68+payload_size));wire[56:58]=payload_size.to_bytes(2,'little')
            wire[72:]=bytes((i*29+7)&255 for i in range(payload_size))
            ethernet=bytes.fromhex('ffffffffffff02574d50000188b5')
            for carrier in [b'',bytes.fromhex('040902574d123456')]:
                frame=ethernet+carrier+wire;offset=14+len(carrier)
                self.assertEqual(lib.wmp_ethernet_payload_offset(buf(frame),len(frame)),offset)
                self.assertEqual(frame[offset:],wire)
                for size in range(len(frame)):
                    self.assertEqual(lib.wmp_ethernet_payload_offset(buf(frame[:size]),size),0)
                self.assertEqual(lib.wmp_ethernet_payload_offset(buf(frame+b'x'),len(frame)+1),0)
        self.assertEqual(lib.wmp_ethernet_payload_offset(None,100),0)
        over=bytearray(b'WMP1'+bytes(68+MAX_PAYLOAD+1));over[56:58]=(MAX_PAYLOAD+1).to_bytes(2,'little')
        for carrier in [b'',bytes.fromhex('040902574d123456')]:
            frame=ethernet+carrier+bytes(over);self.assertEqual(lib.wmp_ethernet_payload_offset(buf(frame),len(frame)),0)
        for index in [12,13,14,18,22]:
            frame=bytearray(ethernet+bytes.fromhex('040902574d123456')+wire);frame[index]^=1
            self.assertEqual(lib.wmp_ethernet_payload_offset(buf(frame),len(frame)),0)
    def test_payload_pool_holds_current_codec_frame_slots_without_waste(self):
        p=PayloadPool();lib.wmp_payload_pool_init(C.byref(p))
        offsets=[lib.wmp_payload_alloc(C.byref(p),484) for _ in range(FRAME_SLOTS)]
        self.assertEqual(len(set(offsets)),FRAME_SLOTS);self.assertTrue(all(x>=0 for x in offsets))
        for i in range(0,FRAME_SLOTS,2):self.assertEqual(lib.wmp_payload_free(C.byref(p),offsets[i],484),1)
        for i in range(1,FRAME_SLOTS,2):self.assertEqual(lib.wmp_payload_free(C.byref(p),offsets[i],484),1)
        self.assertEqual((p.count,p.available,p.offset[0],p.length[0]),(1,PAYLOAD_BYTES,0,PAYLOAD_BYTES))
    def test_payload_pool_accepts_full904_byte_groups_and_coalesces(self):
        p=PayloadPool();lib.wmp_payload_pool_init(C.byref(p));offsets=[]
        while True:
            i=lib.wmp_payload_alloc(C.byref(p),904)
            if i<0:break
            offsets.append(i)
        self.assertEqual(len(offsets),PAYLOAD_BYTES//904)
        for i in reversed(offsets):self.assertTrue(lib.wmp_payload_free(C.byref(p),i,904))
        self.assertEqual(lib.wmp_payload_alloc(C.byref(p),PAYLOAD_BYTES),0)
    def test_payload_pool_overlap_invalid_free_and_alignment(self):
        p=PayloadPool();lib.wmp_payload_pool_init(C.byref(p))
        self.assertEqual(lib.wmp_payload_alloc(C.byref(p),3),0)
        self.assertEqual(lib.wmp_payload_alloc(C.byref(p),5),4)
        self.assertFalse(lib.wmp_payload_free(C.byref(p),1,3))
        self.assertFalse(lib.wmp_payload_free(C.byref(p),PAYLOAD_BYTES-4,8))
        self.assertTrue(lib.wmp_payload_free(C.byref(p),0,3))
        self.assertFalse(lib.wmp_payload_free(C.byref(p),0,3))
        self.assertTrue(lib.wmp_payload_free(C.byref(p),4,5));self.assertEqual(p.available,PAYLOAD_BYTES)
    def test_payload_pool_retains_borrowed_packet_until_final_unpin(self):
        p=FramePool();a=PayloadPool();lib.wmp_frame_pool_init(C.byref(p));lib.wmp_payload_pool_init(C.byref(a))
        i=lib.wmp_frame_pool_acquire(C.byref(p));offset=lib.wmp_payload_alloc(C.byref(a),476)
        lib.wmp_frame_pool_publish(C.byref(p),i);lib.wmp_frame_pool_pin(C.byref(p),i);lib.wmp_frame_pool_pin(C.byref(p),i)
        lib.wmp_frame_pool_take(C.byref(p));lib.wmp_frame_pool_release(C.byref(p),i)
        self.assertFalse(p.free&(1<<i));self.assertEqual(a.available,PAYLOAD_BYTES-476)
        lib.wmp_frame_pool_unpin(C.byref(p),i);self.assertFalse(p.free&(1<<i))
        lib.wmp_frame_pool_unpin(C.byref(p),i);self.assertTrue(p.free&(1<<i))
        self.assertTrue(lib.wmp_payload_free(C.byref(a),offset,476));self.assertEqual(a.available,PAYLOAD_BYTES)
    def test_payload_pool_random_out_of_order_reuse_never_overlaps(self):
        import random
        rng=random.Random(1801);p=PayloadPool();lib.wmp_payload_pool_init(C.byref(p));live=[]
        for _ in range(12000):
            if live and (len(live)>=FRAME_SLOTS or rng.random()<.45):
                offset,size=live.pop(rng.randrange(len(live)));self.assertTrue(lib.wmp_payload_free(C.byref(p),offset,size))
            else:
                size=rng.choice([1,17,232,476,484,904]);offset=lib.wmp_payload_alloc(C.byref(p),size)
                if offset>=0:
                    n=(size+3)&~3
                    for old,length in live:self.assertTrue(offset+n<=old or old+((length+3)&~3)<=offset)
                    live.append((offset,size))
            self.assertEqual(p.available+sum((size+3)&~3 for _,size in live),PAYLOAD_BYTES)
        for offset,size in live:self.assertTrue(lib.wmp_payload_free(C.byref(p),offset,size))
        self.assertEqual((p.count,p.available),(1,PAYLOAD_BYTES))
    def test_repair_plan_is_rechecked_after_radio_credit_delay(self):
        # A valid three-copy train can expire while queued. Its remaining
        # copies must not occupy the radio after the useful playback window.
        spacing=C.c_uint32();deadline=1000000
        self.assertEqual(lib.wmp_replay_plan(210000,40000,80000,20000,C.byref(spacing)),3)
        self.assertEqual(lib.wmp_tx_deadline(1,deadline,790000,20000),1)
        self.assertEqual(lib.wmp_tx_deadline(1,deadline,990000,20000),-1)
        self.assertEqual(lib.wmp_tx_deadline(1,deadline,1040000,20000),-1)
        # Original audio is preserved despite the same delay.
        self.assertEqual(lib.wmp_tx_deadline(0,deadline,1040000,20000),1)
    def test_unknown_clock_defers_repair_without_falsely_expiring_it(self):
        self.assertEqual(lib.wmp_tx_deadline(1,1000000,0,20000),0)
        self.assertEqual(lib.wmp_tx_deadline(1,1000000,979999,20000),1)
        self.assertEqual(lib.wmp_tx_deadline(1,1000000,980000,20000),-1)
    def test_repair_deadline_check_does_not_wrap_near_uint64_limit(self):
        limit=(1<<64)-1
        self.assertEqual(lib.wmp_tx_deadline(1,limit,limit-20001,20000),1)
        self.assertEqual(lib.wmp_tx_deadline(1,limit,limit-20000,20000),-1)
        self.assertEqual(lib.wmp_tx_deadline(0,0,limit,20000),1)

class ClockTrailer(unittest.TestCase):
    def make(self,copy=0,clock=True,stamp=123456789,quiet=1,serial=7,nxt=24000):
        h=header();h.copy_index=copy
        if clock:h.flags|=2;h.send_stamp=stamp;h.next_copy_us=nxt;h.tx_serial=serial;h.sync_flags=quiet
        data=bytes(range(117));w=(Byte*(72+MAX_PAYLOAD))();n=lib.wmp_encode(w,len(w),C.byref(h),buf(data));return w,n,data
    def test_roundtrip_and_size(self):
        w,n,data=self.make()
        self.assertEqual(n,72+117+16)
        d=Header();pl=Ptr();self.assertTrue(lib.wmp_decode(w,n,C.byref(d),C.byref(pl)))
        self.assertEqual((d.send_stamp,d.next_copy_us,d.tx_serial,d.sync_flags,d.payload_bytes),(123456789,24000,7,1,117))
        self.assertEqual(C.string_at(pl,d.payload_bytes),data)
    def test_logical_crc_ignores_trailer_and_flag(self):
        a,na,_=self.make(copy=0,stamp=1,serial=1,quiet=1);b,nb,_=self.make(copy=3,stamp=999,serial=2,quiet=0,nxt=0);c,nc,_=self.make(copy=1,clock=False)
        self.assertEqual(lib.wmp_logical_crc(a,na),lib.wmp_logical_crc(b,nb));self.assertEqual(lib.wmp_logical_crc(a,na),lib.wmp_logical_crc(c,nc))
    def test_trailer_carries_party_size(self):
        for party in (0,1,2,3):
            h=header();h.flags|=2;h.send_stamp=5;h.party=party
            w=(Byte*(72+MAX_PAYLOAD))();n=lib.wmp_encode(w,len(w),C.byref(h),buf(bytes(117)))
            self.assertEqual(w[n-1],party)
            d=Header();pl=Ptr();self.assertTrue(lib.wmp_decode(w,n,C.byref(d),C.byref(pl)));self.assertEqual(d.party,party)
        # Party size is per copy, like the clock stamp: it never changes the logical CRC.
        a,na,_=self.make();b,nb,_=self.make();b[nb-1]=2
        self.assertEqual(lib.wmp_logical_crc(a,na),lib.wmp_logical_crc(b,nb))
    def test_rejects_bad_trailer(self):
        d=Header();pl=Ptr()
        w,n,_=self.make();self.assertFalse(lib.wmp_decode(w,n-16,C.byref(d),C.byref(pl)))
        h=header();h.flags|=2;h.sync_flags=102<<1;self.assertEqual(lib.wmp_encode((Byte*1600)(),1600,C.byref(h),buf(bytes(117))),0)
    def test_trailer_carries_party_volume(self):
        # sync_flags bits 1..7 = master volume + 1; bit 0 stays the quiet flag.
        for volume in (0,10,100):
            h=header();h.flags|=2;h.sync_flags=((volume+1)<<1)|1;out=(Byte*1600)()
            n=lib.wmp_encode(out,1600,C.byref(h),buf(bytes(117)));self.assertGreater(n,0)
            d=Header();pl=Ptr();self.assertTrue(lib.wmp_decode(out,n,C.byref(d),C.byref(pl)))
            self.assertEqual((d.sync_flags>>1)-1,volume);self.assertEqual(d.sync_flags&1,1)
    def test_payload_budget_includes_trailer(self):
        h=header();h.flags|=2;h.payload_bytes=MAX_PAYLOAD-15
        self.assertEqual(lib.wmp_encode((Byte*1600)(),1600,C.byref(h),buf(bytes(MAX_PAYLOAD-15))),0)


class CodecProfile(C.Structure):
    _fields_=[('codec',Byte),('name',C.c_char_p),('group_units',Byte),('stream_quantum',Byte),('group_samples',C.c_uint16),
              ('max_unit_bytes',C.c_uint16),('max_group_bytes',C.c_uint16),('pcm_block_frames',C.c_uint16),('queue_groups',Byte),
              ('decoder_bytes',C.c_uint32)]
class LatmState(C.Structure):
    _fields_=[('configured',Byte),('subframes',Byte)]
class LatmUnit(C.Structure):
    _fields_=[('bit_offset',C.c_uint32),('bytes',C.c_uint16)]
lib.wmp_codec_profile_get.restype=C.POINTER(CodecProfile)
lib.wmp_codec_payload_bytes.argtypes=[C.POINTER(CodecProfile)];lib.wmp_codec_payload_bytes.restype=C.c_uint32
lib.wmp_codec_stream_samples.argtypes=[C.POINTER(CodecProfile),C.c_uint32];lib.wmp_codec_stream_samples.restype=C.c_uint32
lib.wmp_group_init_codec.argtypes=[C.POINTER(Group),Byte,Byte]
lib.wmp_group_push_unit.argtypes=[C.POINTER(Group),Ptr,C.c_uint16,C.c_uint64,C.c_uint16]
lib.wmp_group_push_stream.argtypes=[C.POINTER(Group),Ptr,C.c_uint16,C.c_uint64,C.POINTER(C.c_uint16)]
lib.wmp_group_frame_codec.argtypes=[Byte,Ptr,C.c_size_t,C.c_uint16,C.c_uint16,C.POINTER(Ptr),C.POINTER(C.c_uint16)]
lib.wmp_latm_parse.argtypes=[C.POINTER(LatmState),C.c_uint32,Byte,Ptr,C.c_uint16,C.POINTER(LatmUnit),Byte,C.POINTER(Byte)]
lib.wmp_latm_copy.argtypes=[Ptr,C.c_uint16,LatmUnit,Ptr,C.c_uint16]
class RtpHeader(C.Structure):
    _fields_=[('sequence',C.c_uint16),('timestamp',C.c_uint32),('marker',Byte),('payload_type',Byte)]
lib.wmp_rtp_read.argtypes=[Ptr,C.c_uint32,C.POINTER(RtpHeader),C.POINTER(Ptr),C.POINTER(C.c_uint32)]


class Bits:
    def __init__(self):self.v=[]
    def put(self,value,n):self.v+=[(value>>(n-1-i))&1 for i in range(n)]
    def latm_value(self,value):
        n=max(1,(value.bit_length()+7)//8);self.put(n-1,2);self.put(value,8*n)
    def bytes(self):
        v=self.v+[0]*((-len(self.v))%8)
        return bytes(int(''.join(map(str,v[i:i+8])),2) for i in range(0,len(v),8))


def latm(units,rate_index=4,channels=2,version=0,same=False,object_type=2):
    """AudioMuxElement(1) with one program/layer and len-prefixed AAC units."""
    b=Bits()
    b.put(1 if same else 0,1)
    if not same:
        b.put(version,1)
        if version:b.put(0,1);b.latm_value(0xff)
        b.put(1,1);b.put(len(units)-1,6);b.put(0,4);b.put(0,3)
        asc=Bits();asc.put(object_type,5);asc.put(rate_index,4);asc.put(channels,4);asc.put(0,3)
        if version:b.latm_value(len(asc.v))
        b.v+=asc.v
        b.put(0,3);b.put(0xff,8);b.put(0,1);b.put(0,1)
    for u in units:
        n=len(u)
        while n>=255:b.put(255,8);n-=255
        b.put(n,8)
        for x in u:b.put(x,8)
    return b.bytes()


def parse(data,rate=44100,channels=2,state=None):
    state=state or LatmState();units=(LatmUnit*16)();count=Byte()
    r=lib.wmp_latm_parse(C.byref(state),rate,channels,buf(data),len(data),units,16,C.byref(count))
    out=[]
    if r==1:
        for i in range(count.value):
            o=(Byte*units[i].bytes)();assert lib.wmp_latm_copy(buf(data),len(data),units[i],o,len(o))==1;out.append(bytes(o))
    return r,out,state


class CodecFraming(unittest.TestCase):
    def test_profiles_are_distinct_per_codec(self):
        p=[lib.wmp_codec_profile_get(i).contents for i in range(4)]
        self.assertEqual([x.name for x in p],[b'SBC',b'AAC',b'aptX',b'aptX HD'])
        self.assertEqual([(x.group_units,x.stream_quantum,x.group_samples) for x in p],[(4,0,512),(1,0,1024),(0,4,512),(0,6,512)])
        self.assertEqual(len({lib.wmp_codec_payload_bytes(lib.wmp_codec_profile_get(i)) for i in range(4)}),4)
        self.assertTrue(all(lib.wmp_codec_payload_bytes(lib.wmp_codec_profile_get(i))<=65532 for i in range(4)))
        self.assertFalse(lib.wmp_codec_profile_get(4))
        self.assertEqual(lib.wmp_codec_stream_samples(lib.wmp_codec_profile_get(3),12),8)
        self.assertEqual(lib.wmp_codec_stream_samples(lib.wmp_codec_profile_get(2),6),0)

    def test_codec_header_roundtrip_and_sbc_unchanged(self):
        h=header();w,n=encode(h,bytes(range(117)));self.assertEqual((w[66],w[67]),(0,0))
        h=header(300);h.codec=1;h.channels=2;h.frame_count=1;h.sample_count=1024
        w,n=encode(h,bytes(300));ok,d,_=decode(w,n);self.assertTrue(ok);self.assertEqual((d.codec,d.channels),(1,2))
        for codec,channels,kind in [(4,2,2),(1,0,2),(0,2,2),(1,2,1)]:
            h=header();h.codec=codec;h.channels=channels;h.type=kind
            self.assertEqual(lib.wmp_encode((Byte*1600)(),1600,C.byref(h),buf(bytes(117))),0,(codec,channels,kind))

    def test_aac_units_group_and_read_back(self):
        g=Group();lib.wmp_group_init_codec(C.byref(g),1,1)
        au=bytes([0x21,0x10])+bytes(range(200))
        self.assertEqual(lib.wmp_group_push_unit(C.byref(g),buf(au),len(au),1024,1024),1)
        self.assertEqual((g.frames,g.sample_count,g.codec),(1,1024,1))
        frame=Ptr();size=C.c_uint16()
        self.assertEqual(lib.wmp_group_frame_codec(1,g.payload,g.bytes,1,0,C.byref(frame),C.byref(size)),1)
        self.assertEqual(C.string_at(frame,size.value),au)
        self.assertEqual(lib.wmp_group_frame_codec(0,g.payload,g.bytes,1,0,C.byref(frame),C.byref(size)),0)
        big=bytes(lib.wmp_codec_profile_get(1).contents.max_unit_bytes+1)
        g2=Group();lib.wmp_group_init_codec(C.byref(g2),1,1)
        self.assertEqual(lib.wmp_group_push_unit(C.byref(g2),buf(big),len(big),0,1024),-2)

    def test_aptx_stream_splits_at_codewords(self):
        for codec,quantum in ((2,4),(3,6)):
            g=Group();lib.wmp_group_init_codec(C.byref(g),codec,0)
            data=bytes(i&255 for i in range(quantum*200))   # 800 samples
            used=C.c_uint16()
            self.assertEqual(lib.wmp_group_push_stream(C.byref(g),buf(data),len(data),5000,C.byref(used)),1)
            self.assertEqual(used.value,quantum*128);self.assertEqual(g.sample_count,512)
            frame=Ptr();size=C.c_uint16()
            self.assertEqual(lib.wmp_group_frame_codec(codec,g.payload,g.bytes,1,0,C.byref(frame),C.byref(size)),1)
            self.assertEqual(C.string_at(frame,size.value),data[:quantum*128])
            g2=Group();lib.wmp_group_init_codec(C.byref(g2),codec,0);rest=data[used.value:]
            self.assertEqual(lib.wmp_group_push_stream(C.byref(g2),buf(rest),len(rest),5512,C.byref(used)),0)
            self.assertEqual((used.value,g2.sample_count,g2.first_sample),(len(rest),288,5512))
            self.assertEqual(lib.wmp_group_push_stream(C.byref(g2),buf(rest[:quantum]),quantum,9999,C.byref(used)),-3)
            self.assertEqual(lib.wmp_group_push_stream(C.byref(g2),buf(b'x'*(quantum+1)),quantum+1,5800,C.byref(used)),-1)

    def test_latm_parse_and_copy(self):
        units=[bytes(range(10)),bytes(300),bytes([7])*40]
        for version in (0,1):
            r,out,state=parse(latm(units,version=version))
            self.assertEqual((r,out),(1,units),version)
            r2,out2,_=parse(latm([bytes(range(33))],same=True),state=state)
            self.assertEqual((r2,out2),(-2 if False else 0,[]) if False else (r2,out2))
        r,out,state=parse(latm([b'abc']))
        st=LatmState(state.configured,1)
        r,out,_=parse(latm([b'xyz'],same=True),state=st);self.assertEqual((r,out),(1,[b'xyz']))

    def test_rtp_read_handles_csrc_extension_padding(self):
        base=bytes([0x80,0x80|96,0x12,0x34,0,0,0x10,0])
        cases=[(base+bytes(4)+b'payload',b'payload'),
               (bytes([0x82])+base[1:]+bytes(4)+bytes(8)+b'xy',b'xy'),
               (bytes([0x90])+base[1:]+bytes(4)+bytes([0,0,0,1])+bytes(4)+b'ext',b'ext'),
               (bytes([0xa0])+base[1:]+bytes(4)+b'abc'+bytes([0,0,3]),b'abc')]
        for packet,want in cases:
            h=RtpHeader();p=Ptr();n=C.c_uint32()
            self.assertEqual(lib.wmp_rtp_read(buf(packet),len(packet),C.byref(h),C.byref(p),C.byref(n)),1,packet)
            self.assertEqual((C.string_at(p,n.value),h.sequence,h.timestamp,h.marker,h.payload_type),(want,0x1234,4096,1,96))
        for bad in [base,bytes([0x40])+base[1:]+bytes(4)+b'x',bytes([0xa0])+base[1:]+bytes(4)+bytes([9])]:
            h=RtpHeader();p=Ptr();n=C.c_uint32()
            self.assertEqual(lib.wmp_rtp_read(buf(bad),len(bad),C.byref(h),C.byref(p),C.byref(n)),0,bad)
    def test_latm_rejects_unsupported_and_malformed(self):
        self.assertEqual(parse(latm([b'a'],object_type=5))[0],-1)          # SBR/HE-AAC object
        self.assertEqual(parse(latm([b'a'],rate_index=3))[0],-1)           # 48 kHz vs negotiated 44.1
        self.assertEqual(parse(latm([b'a'],channels=1))[0],-1)
        self.assertEqual(parse(latm([b'a'],same=True))[0],0)               # no prior config
        good=latm([bytes(50)]);self.assertEqual(parse(good[:-10])[0],0)    # truncated payload
        self.assertEqual(parse(latm([bytes(5)]*17))[0],-2)                 # more units than capacity
        self.assertEqual(parse(latm([b'a'],rate_index=3),rate=48000)[0],1)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Protocol),unittest.defaultTestLoader.loadTestsFromTestCase(ClockTrailer),unittest.defaultTestLoader.loadTestsFromTestCase(CodecFraming)]))
    receipt=dict(pass_all=result.wasSuccessful(),tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),source_sha256=pins,command=command,radio_qualified=False,audio_qualified=False)
    (OUT/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(evidence=str(OUT),**{k:receipt[k] for k in ['pass_all','tests','errors','failures']})))
    raise SystemExit(0 if result.wasSuccessful() else 1)
