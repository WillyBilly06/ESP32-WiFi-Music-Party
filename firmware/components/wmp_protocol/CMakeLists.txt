get_filename_component(WMP_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
idf_component_register(
    SRCS "${WMP_ROOT}/protocol/src/wmp_wire.c"
         "${WMP_ROOT}/protocol/src/wmp_audio_group.c"
         "${WMP_ROOT}/protocol/src/wmp_time.c"
         "${WMP_ROOT}/protocol/src/wmp_beacon_clock.c"
         "${WMP_ROOT}/protocol/src/wmp_copy_trace.c"
         "${WMP_ROOT}/protocol/src/wmp_frame_pool.c"
         "${WMP_ROOT}/protocol/src/wmp_payload_pool.c"
         "${WMP_ROOT}/protocol/src/wmp_playout.c"
         "${WMP_ROOT}/protocol/src/wmp_sbc_queue.c"
         "${WMP_ROOT}/protocol/src/wmp_nack.c"
         "${WMP_ROOT}/protocol/src/wmp_pcm_blocks.c"
         "${WMP_ROOT}/protocol/src/wmp_peer_lease.c"
         "${WMP_ROOT}/protocol/src/wmp_sbc.c"
         "${WMP_ROOT}/protocol/src/wmp_codec.c"
         "${WMP_ROOT}/protocol/src/wmp_latm.c"
    INCLUDE_DIRS "${WMP_ROOT}/protocol/include")
